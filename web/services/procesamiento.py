import uuid
import random
from pathlib import Path
from types import SimpleNamespace

from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from web.models import Fondo, PaqueteImagen, Imagen
from web.services.creditos import reservar_creditos
from web.services.image_processor import generar_variantes

def recibir_imagen(
    unidad_venta_id: int,
    codigo_proceso: str,
    imagen_original,
):
    """
    Guarda una fotografía original una sola vez
    y reserva los créditos correspondientes.
    """

    if not imagen_original:
        raise ValueError("La imagen original es obligatoria.")

    extension = Path(imagen_original.name).suffix.lower()

    nombre_archivo = (
        f"imagenes/originales/"
        f"{uuid.uuid4().hex}{extension}"
    )

    ruta_guardada = None

    try:
        with transaction.atomic():
            ruta_guardada = default_storage.save(
                nombre_archivo,
                imagen_original,
            )

            paquetes = reservar_creditos(
                unidad_venta_id=unidad_venta_id,
                codigo_proceso=codigo_proceso,
                imagen_original=SimpleNamespace(
                    name=ruta_guardada
                ),
            )

            return paquetes

    except Exception:
        if ruta_guardada:
            default_storage.delete(ruta_guardada)
        raise

def seleccionar_fondos(cantidad: int) -> list[Fondo]:
    """
    Selecciona aleatoriamente 3 o 6 fondos activos.

    No repite fondos dentro de una misma operación.
    """

    if cantidad not in (3, 6):
        raise ValueError(
            "La cantidad de fondos debe ser 3 o 6."
        )

    fondos_disponibles = list(
        Fondo.objects.filter(
            activo=True
        ).order_by("pk")
    )

    if len(fondos_disponibles) < cantidad:
        raise ValueError(
            f"No hay suficientes fondos activos. "
            f"Se requieren {cantidad}."
        )

    return random.sample(
        fondos_disponibles,
        cantidad,
    )


def procesar_variantes(codigo_proceso: str) -> list[Imagen]:
    """
    El método procesar_variantes() es el coordinador del procesamiento de imágenes.

    Su responsabilidad es tomar una fotografía original que ya fue recibida y 
    tiene créditos reservados, generar las versiones con diferentes fondos y 
    guardar los resultados.

    No realiza directamente la edición de la fotografía. 
    Para eso utiliza generar_variantes(), que es el servicio encargado del procesamiento con IA.
    
    Genera y almacena 3 o 6 variantes de una fotografía.
    No consume créditos.
    
    - Busca los paquetes: utiliza codigo_proceso para encontrar los paquetes con estado RECIBIDO
      y crédito RESERVADO.
    - Determina la cantidad: un paquete produce 3 imágenes; dos paquetes producen 6.
    - Selecciona los fondos: llama a seleccionar_fondos() para obtener fondos activos sin repetirlos.
    - Lee los archivos: obtiene los bytes de la fotografía original y de cada fondo seleccionado.
    - Procesa la fotografía: llama una sola vez a generar_variantes().
    - Guarda los resultados: crea cada registro Imagen, relacionándolo con su paquete y
      el fondo utilizado.
    - Actualiza el estado: cambia los paquetes de RECIBIDO a LISTO.

    Estados:
        El estado del paquete cambia a PROCESANDO antes de ejecutar la IA
        RECIBIDO -> PROCESANDO -> LISTO

    No consume créditos.
    """

    # FASE 1: Reclamar la operación con una transacción corta.
    with transaction.atomic():
        paquetes = list(
            PaqueteImagen.objects.select_for_update()
            .filter(codigo_proceso=codigo_proceso)
            .order_by("pk")
        )

        if not paquetes:
            raise ValueError(
                "No existen paquetes para esta operación."
            )

        if len(paquetes) not in (1, 2):
            raise ValueError(
                "La operación debe tener uno o dos paquetes."
            )

        # Idempotencia: operación terminada anteriormente.
        if all(
            paquete.estado == PaqueteImagen.EstadoPaquete.LISTO
            for paquete in paquetes
        ):
            imagenes = list(
                Imagen.objects.filter(
                    paquete__in=paquetes,
                ).order_by("pk")
            )

            if (
                len(imagenes) == len(paquetes) * 3
                and all(
                    imagen.estado_proceso
                    == Imagen.EstadoProceso.PROCESADA
                    and imagen.imagen_procesada.name
                    for imagen in imagenes
                )
            ):
                return imagenes

            raise ValueError(
                "La operación está LISTA, pero sus imágenes "
                "procesadas están incompletas."
            )

        # Otra solicitud ya reclamó esta operación.
        if any(
            paquete.estado
            == PaqueteImagen.EstadoPaquete.PROCESANDO
            for paquete in paquetes
        ):
            raise ValueError(
                "La operación ya está en procesamiento."
            )

        if not all(
            paquete.estado == PaqueteImagen.EstadoPaquete.RECIBIDO
            and paquete.estado_credito
            == PaqueteImagen.EstadoCredito.RESERVADO
            for paquete in paquetes
        ):
            raise ValueError(
                "La operación no está disponible para procesamiento."
            )

        PaqueteImagen.objects.filter(
            pk__in=[p.pk for p in paquetes]
        ).update(
            estado=PaqueteImagen.EstadoPaquete.PROCESANDO,
            fecha_inicio_proceso=timezone.now(),
            fecha_fin_proceso=None,
        )
    
    # FASE 2: Procesamiento fuera de la transacción.
    archivos_guardados = []

    try:
        cantidad = len(paquetes) * 3
        fondos = seleccionar_fondos(cantidad)

        with paquetes[0].imagen_original.open("rb") as archivo:
            original_bytes = archivo.read()

        fondos_bytes = []

        for fondo in fondos:
            with fondo.imagen.open("rb") as archivo:
                fondos_bytes.append(archivo.read())

        variantes = generar_variantes(
            original_bytes,
            fondos_bytes,
        )

        if len(variantes) != cantidad:
            raise ValueError(
                "El procesador devolvió una cantidad "
                "incorrecta de variantes."
            )

        # FASE 3: Guardar resultados y finalizar.
        with transaction.atomic():
            imagenes = []

            for indice, (fondo, variante) in enumerate(
                zip(fondos, variantes)
            ):
                paquete = paquetes[indice // 3]

                imagen = Imagen(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen=(
                        f"{codigo_proceso}-{indice + 1:02d}"
                    ),
                    estado_proceso=Imagen.EstadoProceso.PROCESANDO,
                )

                imagen.imagen_procesada.save(
                    f"{codigo_proceso}_{indice + 1:02d}.png",
                    ContentFile(variante),
                    save=False,
                )

                archivos_guardados.append(
                    imagen.imagen_procesada.name
                )

                imagen.estado_proceso = (
                    Imagen.EstadoProceso.PROCESADA
                )
                imagen.save()

                imagenes.append(imagen)

            PaqueteImagen.objects.filter(
                pk__in=[p.pk for p in paquetes]
            ).update(
                estado=PaqueteImagen.EstadoPaquete.LISTO,
                fecha_fin_proceso=timezone.now(),
            )

        return imagenes

    except Exception:
        # Los archivos físicos no participan en el rollback SQL.
        storage = Imagen._meta.get_field(
            "imagen_procesada"
        ).storage

        for nombre in archivos_guardados:
            storage.delete(nombre)

        # Permitir reintento después de un error.
        with transaction.atomic():
            PaqueteImagen.objects.filter(
                pk__in=[paquete.pk for paquete in paquetes],
                estado=PaqueteImagen.EstadoPaquete.PROCESANDO,
            ).update(
                estado=PaqueteImagen.EstadoPaquete.RECIBIDO
            )

        raise
