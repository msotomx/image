from django.db.models import F, Sum, IntegerField
from django.db.models.functions import Coalesce
from django.db import transaction
from django.utils import timezone

from web.models import Imagen, Pago, PaqueteImagen, Sector, UnidadVenta

class CreditosInsuficientesError(Exception):
    """El Sector no tiene créditos disponibles."""


def obtener_creditos_disponibles(sector_id: int) -> int:
    """
    Calcula los créditos disponibles de un Sector.

    Disponibles = comprados - reservados - usados.
    """

    resultado = (
        Pago.objects
        .filter(sector_id=sector_id)
        .aggregate(
            disponibles=Coalesce(
                Sum(
                    F("paquetes_pagados")
                    - F("creditos_reservados")
                    - F("creditos_usados"),
                    output_field=IntegerField(),
                ),
                0,
                output_field=IntegerField(),
            )
        )
    )

    return resultado["disponibles"]

# la función validar_creditos_disponibles() devuelve el número de créditos que podrán reservarse, 
# no el número de imágenes.
def validar_creditos_disponibles(sector_id: int) -> int:
    """
    Determina cuántos créditos se pueden reservar
    para procesar una fotografía.

    0 créditos: rechaza.
    1 crédito: autoriza 3 imágenes.
    2 o más: autoriza 6 imágenes.

    Esta función no realiza ninguna reserva.
    """

    disponibles = obtener_creditos_disponibles(sector_id)

    if disponibles <= 0:
        raise CreditosInsuficientesError(
            "El Sector no tiene créditos disponibles."
        )

    return min(disponibles, 2)

@transaction.atomic
def reservar_creditos(
    unidad_venta_id: int,
    codigo_proceso: str,
    imagen_original,
) -> list[PaqueteImagen]:
    """
    Reserva uno o dos créditos del Sector.

    Asigna los créditos por antigüedad de Pago
    y crea un PaqueteImagen por cada crédito.

    Toda la operación es atómica.
    """

    if not codigo_proceso or not codigo_proceso.strip():
        raise ValueError("El código de proceso es obligatorio.")

    if not imagen_original:
        raise ValueError("La imagen original es obligatoria.")

    unidad = UnidadVenta.objects.get(pk=unidad_venta_id)

    # Serializar reservas de todas las Unidades de Venta
    # pertenecientes al mismo Sector.
    Sector.objects.select_for_update().get(pk=unidad.sector_id)

    # Evitar que un mismo proceso reserve créditos dos veces.
    if PaqueteImagen.objects.filter(
        codigo_proceso=codigo_proceso
    ).exists():
        raise ValueError("El código de proceso ya existe.")

    pagos = list(
        Pago.objects.select_for_update()
        .filter(sector_id=unidad.sector_id)
        .order_by("fecha_pago", "pk")
    )

    disponibles = sum(
        pago.creditos_disponibles
        for pago in pagos
    )

    if disponibles <= 0:
        raise CreditosInsuficientesError(
            "El Sector no tiene créditos disponibles."
        )

    cantidad_reservar = min(disponibles, 2)
    paquetes = []

    for pago in pagos:
        while (
            pago.creditos_disponibles > 0
            and len(paquetes) < cantidad_reservar
        ):
            numero_paquete = len(paquetes) + 1

            paquete = PaqueteImagen.objects.create(
                numero_paquete=numero_paquete,
                unidad_venta=unidad,
                pago=pago,
                codigo_proceso=codigo_proceso,
                imagen_original=imagen_original.name,
                estado=PaqueteImagen.EstadoPaquete.RECIBIDO,
                estado_credito=(
                    PaqueteImagen.EstadoCredito.RESERVADO
                ),
            )

            actualizados = (
                Pago.objects
                .filter(
                    pk=pago.pk,
                    creditos_reservados__lt=(
                        F("paquetes_pagados")
                        - F("creditos_usados")
                    ),
                )
                .update(
                    creditos_reservados=(
                        F("creditos_reservados") + 1
                    )
                )
            )

            if actualizados != 1:
                raise CreditosInsuficientesError(
                    "No fue posible reservar el crédito."
                )

            pago.creditos_reservados += 1
            paquetes.append(paquete)

        if len(paquetes) == cantidad_reservar:
            break

    if len(paquetes) != cantidad_reservar:
        raise CreditosInsuficientesError(
            "No fue posible completar la reserva."
        )

    return paquetes

# poner variable el numero de creditos 
def calcular_creditos_a_consumir(numero_imagenes: int) -> int:
    """
    Calcula los créditos necesarios para entregar
    entre 1 y 6 imágenes seleccionadas.
    """

    if not isinstance(numero_imagenes, int) or isinstance(numero_imagenes, bool):
        raise ValueError("La cantidad de imágenes debe ser un entero.")

    if not 1 <= numero_imagenes <= 6:
        raise ValueError("Se deben seleccionar entre 1 y 6 imágenes.")

    return (numero_imagenes + 2) // 3

@transaction.atomic
def consumir_creditos_reservados(
    codigo_proceso: str,
    numero_imagenes: int,
    respetar_seleccion: bool = False,
) -> None:
    """
    Convierte reservas en consumo definitivo.

    Función interna de contabilidad.
    No sustituye el cierre completo de la operación.
    """

    creditos_necesarios = calcular_creditos_a_consumir(
        numero_imagenes
    )

    referencia = (
        PaqueteImagen.objects
        .filter(codigo_proceso=codigo_proceso)
        .select_related("unidad_venta")
        .first()
    )

    if referencia is None:
        raise ValueError("El proceso no existe.")

    sector_id = referencia.unidad_venta.sector_id

    Sector.objects.select_for_update().get(pk=sector_id)

    paquetes = list(
        PaqueteImagen.objects.select_for_update()
        .filter(codigo_proceso=codigo_proceso)
        .order_by("numero_paquete", "pk")
    )

    if not 1 <= len(paquetes) <= 2:
        raise ValueError("Cantidad de paquetes inválida.")

    if any(
        paquete.unidad_venta.sector_id != sector_id
        or paquete.estado_credito
        != PaqueteImagen.EstadoCredito.RESERVADO
        for paquete in paquetes
    ):
        raise ValueError("La operación no tiene reservas válidas.")

    if creditos_necesarios > len(paquetes):
        raise ValueError(
            "Las imágenes seleccionadas exceden los créditos reservados."
        )

    # Identificar los paquetes que contienen imágenes seleccionadas.
    paquetes_seleccionados = set(
        Imagen.objects.filter(
            paquete_id__in=[paquete.pk for paquete in paquetes],
            seleccionada=True,
        ).values_list("paquete_id", flat=True).distinct()
    )

    if respetar_seleccion:
        if not paquetes_seleccionados:
            raise ValueError(
                "La operación no tiene imágenes seleccionadas."
            )
    
    pagos = {
        pago.pk: pago
        for pago in (
            Pago.objects.select_for_update()
            .filter(pk__in=[p.pago_id for p in paquetes])
            .order_by("fecha_pago", "pk")
        )
    }

    if len(pagos) != len({p.pago_id for p in paquetes}):
        raise ValueError("No se encontraron todos los Pagos.")

    if any(pago.sector_id != sector_id for pago in pagos.values()):
        raise ValueError("El Pago no pertenece al Sector.")

    # Los pagos conservan el orden FIFO original.
    paquetes.sort(
        key=lambda p: (
            pagos[p.pago_id].fecha_pago,
            pagos[p.pago_id].pk,
            p.numero_paquete,
        )
    )

    # Créditos financieros que se consumirán siguiendo FIFO.
    pagos_consumo = [
        paquete.pago_id
        for paquete in paquetes[:creditos_necesarios]
    ]

    # Paquetes que efectivamente utilizaron los créditos.
    if respetar_seleccion:
        # Priorizar paquetes con imágenes seleccionadas.
        # Si hay más paquetes seleccionados que créditos
        # necesarios, conservar solamente los necesarios,
        # siguiendo el orden FIFO previamente establecido.
        paquetes_con_seleccion = [
            paquete
            for paquete in paquetes
            if paquete.pk in paquetes_seleccionados
        ]

        paquetes_consumo = paquetes_con_seleccion[
            :creditos_necesarios
        ]

        # Completar con paquetes restantes si fuera necesario.
        if len(paquetes_consumo) < creditos_necesarios:
            paquetes_consumidos_ids = {
                paquete.pk for paquete in paquetes_consumo
            }

            paquetes_consumo.extend(
                paquete
                for paquete in paquetes
                if paquete.pk not in paquetes_consumidos_ids
            )

            paquetes_consumo = paquetes_consumo[
                :creditos_necesarios
            ]
    else:
        paquetes_consumo = paquetes[:creditos_necesarios]

    # Primero liberar todas las reservas originales.
    for paquete in paquetes:
        pago = pagos[paquete.pago_id]

        if pago.creditos_reservados < 1:
            raise ValueError("Reserva inconsistente.")

        pago.creditos_reservados -= 1

    # Aplicar el consumo financiero FIFO y asociar
    # cada paquete utilizado con el pago correspondiente.
    for paquete, pago_id in zip(
        paquetes_consumo,
        pagos_consumo,
    ):
        pago = pagos[pago_id]
        pago.creditos_usados += 1

        # Conservar el Pago original del paquete.
        paquete.estado_credito = (
            PaqueteImagen.EstadoCredito.CONSUMIDO
        )

    # Los paquetes sin imágenes seleccionadas se liberan.
    paquetes_consumidos_ids = {
        paquete.pk for paquete in paquetes_consumo
    }

    for paquete in paquetes:
        if paquete.pk not in paquetes_consumidos_ids:
            paquete.estado_credito = (
                PaqueteImagen.EstadoCredito.LIBERADO
            )
    
    # Persistir los saldos de todos los pagos afectados.
    for pago in pagos.values():
        pago.pago_consumido = (
            pago.creditos_usados == pago.paquetes_pagados
        )

        pago.save(
            update_fields=[
                "creditos_reservados",
                "creditos_usados",
                "pago_consumido",
            ]
        )

    # Persistir el estado y la asignación financiera.
    for paquete in paquetes:
        paquete.save(
            update_fields=["estado_credito"]
        )

@transaction.atomic
def cerrar_operacion(
    codigo_proceso: str,
    imagenes_ids: list[int],
) -> None:
    """
    La función realiza las operaciones necesarias:
    1. Valida que las imágenes estén procesadas.
    2. Registra las imágenes seleccionadas.
    3. Convierte los créditos reservados en utilizados.
    4. Libera las reservas sobrantes.
    5. Cambia los paquetes al estado CERRADO.
    """

    if not codigo_proceso or not codigo_proceso.strip():
        raise ValueError("El código de proceso es obligatorio.")

    referencia = (
        PaqueteImagen.objects
        .filter(codigo_proceso=codigo_proceso)
        .select_related("unidad_venta")
        .first()
    )

    if referencia is None:
        raise ValueError("El proceso no existe.")

    Sector.objects.select_for_update().get(
        pk=referencia.unidad_venta.sector_id
    )

    paquetes = list(
        PaqueteImagen.objects.select_for_update()
        .filter(codigo_proceso=codigo_proceso)
        .order_by("numero_paquete", "pk")
    )

    if any(
        paquete.estado == PaqueteImagen.EstadoPaquete.CERRADO
        for paquete in paquetes
    ):
        raise ValueError("La operación ya está cerrada.")

    existen_imagenes_procesadas = Imagen.objects.filter(
        paquete_id__in=[paquete.pk for paquete in paquetes],
        estado_proceso=Imagen.EstadoProceso.PROCESADA,
    ).exists()

    if not existen_imagenes_procesadas:
        raise ValueError(
            "La operación no tiene imágenes procesadas para seleccionar."
        )

    validar_imagenes_seleccionadas(
        codigo_proceso=codigo_proceso,
        imagenes_ids=imagenes_ids,
    )

    # Verificar que los paquetes todavía tengan
    # sus créditos reservados.
    if any(
        paquete.estado_credito
        != PaqueteImagen.EstadoCredito.RESERVADO
        for paquete in paquetes
    ):
        raise ValueError(
            "La operación no tiene reservas válidas."
        )

    creditos_necesarios = calcular_creditos_a_consumir(
        len(imagenes_ids)
    )

    if creditos_necesarios > len(paquetes):
        raise ValueError(
            "Las imágenes seleccionadas exceden "
            "los créditos reservados."
        )

    # Registrar únicamente la selección confirmada.
    Imagen.objects.filter(
        paquete__codigo_proceso=codigo_proceso,
    ).update(seleccionada=False)

    Imagen.objects.filter(
        pk__in=imagenes_ids,
        paquete__codigo_proceso=codigo_proceso,
    ).update(seleccionada=True)

    # Consumir créditos y liberar reservas sobrantes.
    consumir_creditos_reservados(
        codigo_proceso=codigo_proceso,
        numero_imagenes=len(imagenes_ids),
        respetar_seleccion=True,
    )

    # Cerrar todos los paquetes del proceso.
    fecha_cierre = timezone.now()

    PaqueteImagen.objects.filter(
        pk__in=[paquete.pk for paquete in paquetes],
    ).update(
        estado=PaqueteImagen.EstadoPaquete.CERRADO,
        fecha_cierre=fecha_cierre,
    )

    # Las siguientes etapas implementarán la selección,
    # el consumo de créditos y el cierre definitivo.

def validar_imagenes_seleccionadas(
    codigo_proceso: str,
    imagenes_ids: list[int],
) -> int:
    """
    Verifica que todas las imágenes seleccionadas
    pertenezcan al proceso y estén procesadas.

    Retorna la cantidad de imágenes válidas.
    No modifica la base de datos.
    """

    if not codigo_proceso or not codigo_proceso.strip():
        raise ValueError("El código de proceso es obligatorio.")

    if not isinstance(imagenes_ids, (list, tuple)):
        raise ValueError("La selección debe ser una lista de imágenes.")

    if not 1 <= len(imagenes_ids) <= 6:
        raise ValueError("Se deben seleccionar entre 1 y 6 imágenes.")

    if any(
        not isinstance(imagen_id, int)
        or isinstance(imagen_id, bool)
        for imagen_id in imagenes_ids
    ):
        raise ValueError("Los identificadores deben ser enteros.")

    if len(imagenes_ids) != len(set(imagenes_ids)):
        raise ValueError("La selección contiene imágenes duplicadas.")

    cantidad_validas = Imagen.objects.filter(
        pk__in=imagenes_ids,
        paquete__codigo_proceso=codigo_proceso,
        estado_proceso=Imagen.EstadoProceso.PROCESADA,
    ).count()

    if cantidad_validas != len(imagenes_ids):
        raise ValueError(
            "Una o más imágenes no pertenecen al proceso "
            "o todavía no están procesadas."
        )

    # Cada paquete permite seleccionar máximo 3 imágenes.
    from django.db.models import Count

    seleccion_por_paquete = (
        Imagen.objects
        .filter(
            pk__in=imagenes_ids,
            paquete__codigo_proceso=codigo_proceso,
        )
        .values("paquete_id")
        .annotate(total=Count("pk"))
    )

    if any(
        registro["total"] > 3
        for registro in seleccion_por_paquete
    ):
        raise ValueError(
            "Un paquete no puede tener más de 3 imágenes seleccionadas."
        )

    return cantidad_validas

@transaction.atomic
def cancelar_operacion(codigo_proceso: str) -> None:
    """
    Cancela una operación LISTO y libera sus créditos reservados.

    No elimina fotografías.
    No modifica créditos consumidos.
    Puede ejecutarse nuevamente sin duplicar liberaciones.
    """

    referencia = (
        PaqueteImagen.objects
        .filter(codigo_proceso=codigo_proceso)
        .select_related("unidad_venta")
        .first()
    )

    if referencia is None:
        raise ValueError("La operación no existe.")

    # Mantener el mismo orden de bloqueos que la reserva
    # y el cierre de operaciones.
    Sector.objects.select_for_update().get(
        pk=referencia.unidad_venta.sector_id
    )

    paquetes = list(
        PaqueteImagen.objects.select_for_update()
        .filter(codigo_proceso=codigo_proceso)
        .order_by("numero_paquete", "pk")
    )

    # Una operación cancelada no debe liberar créditos otra vez.
    if all(
        paquete.estado == PaqueteImagen.EstadoPaquete.CANCELADO
        for paquete in paquetes
    ):
        return

    if any(
        paquete.estado != PaqueteImagen.EstadoPaquete.LISTO
        or paquete.estado_credito != PaqueteImagen.EstadoCredito.RESERVADO
        for paquete in paquetes
    ):
        raise ValueError(
            "Solo se pueden cancelar operaciones LISTO "
            "con créditos reservados."
        )

    pagos = {
        pago.pk: pago
        for pago in (
            Pago.objects.select_for_update()
            .filter(pk__in=[paquete.pago_id for paquete in paquetes])
            .order_by("fecha_pago", "pk")
        )
    }

    if len(pagos) != len({paquete.pago_id for paquete in paquetes}):
        raise ValueError("No se encontraron todos los pagos.")

    for paquete in paquetes:
        pago = pagos[paquete.pago_id]

        if pago.sector_id != referencia.unidad_venta.sector_id:
            raise ValueError("El pago pertenece a otro Sector.")

        if pago.creditos_reservados < 1:
            raise ValueError("Reserva inconsistente.")

        pago.creditos_reservados -= 1

    for pago in pagos.values():
        pago.save(update_fields=["creditos_reservados"])

    for paquete in paquetes:
        paquete.estado = PaqueteImagen.EstadoPaquete.CANCELADO
        paquete.estado_credito = PaqueteImagen.EstadoCredito.LIBERADO

        paquete.save(
            update_fields=["estado", "estado_credito"]
        )
