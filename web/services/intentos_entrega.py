from django.db import transaction
from django.utils import timezone

from web.models import IntentoEntrega, PaqueteImagen


@transaction.atomic
def crear_intento_entrega(
    codigo_proceso: str,
    canal: str,
    destinatario: str,
    clave_idempotencia: str,
) -> IntentoEntrega:
    """
    Crea un intento de entrega o recupera uno existente.

    No envía mensajes.
    No consume créditos.
    No modifica imágenes.
    """

    if canal not in IntentoEntrega.Canal.values:
        raise ValueError("Canal de entrega no válido.")

    destinatario = destinatario.strip()
    clave_idempotencia = clave_idempotencia.strip()

    if not destinatario:
        raise ValueError("El destinatario es obligatorio.")

    if not clave_idempotencia:
        raise ValueError("La clave de idempotencia es obligatoria.")

    if not PaqueteImagen.objects.filter(
        codigo_proceso=codigo_proceso,
    ).exists():
        raise ValueError("La operación no existe.")

    intento, creado = IntentoEntrega.objects.get_or_create(
        clave_idempotencia=clave_idempotencia,
        defaults={
            "codigo_proceso": codigo_proceso,
            "canal": canal,
            "destinatario": destinatario,
            "estado": IntentoEntrega.Estado.PENDIENTE,
        },
    )

    if not creado and (
        intento.codigo_proceso != codigo_proceso
        or intento.canal != canal
        or intento.destinatario != destinatario
    ):
        raise ValueError(
            "La clave de idempotencia pertenece a otra entrega."
        )

    return intento

def iniciar_intento_entrega(clave_idempotencia: str) -> bool:
    """
    Reclama un intento pendiente para iniciar su envío.

    True: el intento pasó de PENDIENTE a ENVIANDO.
    False: el intento ya no estaba pendiente.

    No envía mensajes ni modifica créditos.
    """

    actualizado = IntentoEntrega.objects.filter(
        clave_idempotencia=clave_idempotencia,
        estado=IntentoEntrega.Estado.PENDIENTE,
    ).update(
        estado=IntentoEntrega.Estado.ENVIANDO,
        fecha_inicio_envio=timezone.now(),
    )

    if actualizado:
        return True

    if not IntentoEntrega.objects.filter(
        clave_idempotencia=clave_idempotencia,
    ).exists():
        raise ValueError("El intento de entrega no existe.")

    return False

def confirmar_intento_entrega(clave_idempotencia: str) -> bool:
    """
    Confirma que el proveedor aceptó el envío.

    True: cambió de ENVIANDO a ENVIADO.
    False: ya estaba ENVIADO.

    No envía mensajes ni modifica créditos.
    """

    with transaction.atomic():
        intento = IntentoEntrega.objects.select_for_update().filter(
            clave_idempotencia=clave_idempotencia,
        ).first()

        if intento is None:
            raise ValueError("El intento de entrega no existe.")

        if intento.estado == IntentoEntrega.Estado.ENVIADO:
            return False

        if intento.estado != IntentoEntrega.Estado.ENVIANDO:
            raise ValueError(
                "El intento de entrega no está en estado ENVIANDO."
            )

        intento.estado = IntentoEntrega.Estado.ENVIADO
        intento.fecha_envio = timezone.now()
        intento.mensaje_error = ""

        intento.save(
            update_fields=[
                "estado",
                "fecha_envio",
                "mensaje_error",
            ]
        )

        return True

def registrar_error_entrega(
    clave_idempotencia: str,
    mensaje_error: str,
) -> bool:
    """
    Registra un fallo durante el envío.

    True: cambió de ENVIANDO a ERROR.
    False: ya estaba en ERROR.

    No modifica créditos ni fotografías.
    """

    mensaje_error = mensaje_error.strip()

    if not mensaje_error:
        raise ValueError("El mensaje de error es obligatorio.")

    with transaction.atomic():
        intento = IntentoEntrega.objects.select_for_update().filter(
            clave_idempotencia=clave_idempotencia,
        ).first()

        if intento is None:
            raise ValueError("El intento de entrega no existe.")

        if intento.estado == IntentoEntrega.Estado.ERROR:
            return False

        if intento.estado != IntentoEntrega.Estado.ENVIANDO:
            raise ValueError(
                "El intento de entrega no está en estado ENVIANDO."
            )

        intento.estado = IntentoEntrega.Estado.ERROR
        intento.mensaje_error = mensaje_error
        intento.fecha_envio = None

        intento.save(
            update_fields=[
                "estado",
                "mensaje_error",
                "fecha_envio",
            ]
        )

        return True

def evaluar_reintento_entrega(codigo_proceso: str, canal: str) -> str:
    """
    Evalúa el estado del último intento de una operación.

    Retorna:
      SIN_INTENTOS: no existe historial.
      ENVIADO: ya existe un envío confirmado.
      EN_PROCESO: existe un intento pendiente o enviándose.
      REVISION_MANUAL: el último intento terminó en ERROR.

    No realiza envíos ni modifica registros.
    """

    if canal not in IntentoEntrega.Canal.values:
        raise ValueError("Canal de entrega no válido.")

    if not PaqueteImagen.objects.filter(
        codigo_proceso=codigo_proceso,
    ).exists():
        raise ValueError("La operación no existe.")

    intentos = IntentoEntrega.objects.filter(
        codigo_proceso=codigo_proceso,
        canal=canal,
    )

    if intentos.filter(
        estado=IntentoEntrega.Estado.ENVIADO,
    ).exists():
        return "ENVIADO"

    if intentos.filter(
        estado__in=[
            IntentoEntrega.Estado.PENDIENTE,
            IntentoEntrega.Estado.ENVIANDO,
        ],
    ).exists():
        return "EN_PROCESO"

    if intentos.filter(
        estado=IntentoEntrega.Estado.ERROR,
    ).exists():
        return "REVISION_MANUAL"

    return "SIN_INTENTOS"

def autorizar_reintento_entrega(
    codigo_proceso: str,
    canal: str,
    destinatario: str,
    usuario,
    motivo: str,
) -> IntentoEntrega:
    """
    Autoriza manualmente un nuevo intento de entrega.

    No envía mensajes.
    No modifica créditos ni fotografías.
    """

    from uuid import uuid4

    from django.utils import timezone

    if canal not in IntentoEntrega.Canal.values:
        raise ValueError("Canal de entrega no válido.")

    if (
        usuario is None
        or not getattr(usuario, "is_authenticated", False)
        or not getattr(usuario, "is_active", False)
        or not getattr(usuario, "is_staff", False)
    ):
        raise ValueError(
            "El usuario no está autorizado para reintentar entregas."
        )

    destinatario = destinatario.strip()
    motivo = motivo.strip()

    if not destinatario:
        raise ValueError("El destinatario es obligatorio.")

    if not motivo:
        raise ValueError("El motivo de autorización es obligatorio.")

    with transaction.atomic():

        paquetes = list(
            PaqueteImagen.objects.select_for_update().filter(
                codigo_proceso=codigo_proceso,
            )
        )

        if not paquetes:
            raise ValueError("La operación no existe.")

        if any(
            paquete.estado != PaqueteImagen.EstadoPaquete.CERRADO
            for paquete in paquetes
        ):
            raise ValueError(
                "La operación todavía no está cerrada."
            )

        intentos = IntentoEntrega.objects.filter(
            codigo_proceso=codigo_proceso,
            canal=canal,
        )

        if intentos.filter(
            estado=IntentoEntrega.Estado.ENVIADO,
        ).exists():
            raise ValueError(
                "La operación ya tiene una entrega confirmada."
            )

        if intentos.filter(
            estado__in=[
                IntentoEntrega.Estado.PENDIENTE,
                IntentoEntrega.Estado.ENVIANDO,
            ],
        ).exists():
            raise ValueError(
                "La operación ya tiene un intento en proceso."
            )

        if not intentos.filter(
            estado=IntentoEntrega.Estado.ERROR,
        ).exists():
            raise ValueError(
                "No existe un intento fallido para autorizar."
            )

        nuevo_intento = IntentoEntrega.objects.create(
            codigo_proceso=codigo_proceso,
            canal=canal,
            destinatario=destinatario,
            estado=IntentoEntrega.Estado.PENDIENTE,
            clave_idempotencia=(
                f"{canal}-{codigo_proceso}-"
                f"REINTENTO-{uuid4().hex}"
            ),
            autorizado_por=usuario,
            fecha_autorizacion=timezone.now(),
            motivo_autorizacion=motivo,
        )

        return nuevo_intento

@transaction.atomic
def reclamar_intento_email(
    codigo_proceso: str,
    destinatario: str,
) -> IntentoEntrega | None:
    """
    Selecciona y reclama un intento de correo.

    Coordina la selección con las autorizaciones manuales
    mediante el bloqueo de los paquetes de la operación.

    Retorna el intento reclamado o None si no corresponde
    iniciar otro envío.

    No envía correos ni modifica créditos.
    """

    paquetes = list(
        PaqueteImagen.objects.select_for_update()
        .filter(codigo_proceso=codigo_proceso)
        .order_by("pk")
    )

    if not paquetes:
        raise ValueError("La operación no existe.")

    if any(
        paquete.estado != PaqueteImagen.EstadoPaquete.CERRADO
        for paquete in paquetes
    ):
        raise ValueError("La operación todavía no está cerrada.")

    intentos = IntentoEntrega.objects.filter(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.EMAIL,
    )

    # No iniciar un nuevo envío si ya existe uno
    # confirmado o actualmente en ejecución.
    if intentos.filter(
        estado__in=[
            IntentoEntrega.Estado.ENVIANDO,
            IntentoEntrega.Estado.ENVIADO,
        ],
    ).exists():
        return None

    # Priorizar un reintento autorizado.
    reintento = (
        intentos.filter(
            estado=IntentoEntrega.Estado.PENDIENTE,
            autorizado_por__isnull=False,
        )
        .order_by("-fecha_autorizacion", "-pk")
        .first()
    )

    if reintento is not None:
        if reintento.destinatario != destinatario:
            raise ValueError(
                "El destinatario no coincide con el reintento autorizado."
            )

        intento = reintento

    else:
        # Un intento fallido no puede reiniciarse sin
        # autorización manual.
        if intentos.filter(
            estado=IntentoEntrega.Estado.ERROR,
        ).exists():
            return None

        clave = f"EMAIL-{codigo_proceso}"

        intento = crear_intento_entrega(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario=destinatario,
            clave_idempotencia=clave,
        )

    if not iniciar_intento_entrega(
        intento.clave_idempotencia
    ):
        return None

    intento.estado = IntentoEntrega.Estado.ENVIANDO

    return intento

def obtener_intentos_entrega_pendientes_revision(
    minutos: int = 15,
):
    """
    Obtiene intentos ENVIANDO que superaron
    el tiempo permitido desde el inicio real del envío.

    No modifica estados ni consume créditos.
    """

    from datetime import timedelta
    from django.utils import timezone

    if minutos <= 0:
        raise ValueError(
            "Los minutos deben ser mayores que cero."
        )

    fecha_limite = timezone.now() - timedelta(
        minutes=minutos
    )

    return IntentoEntrega.objects.filter(
        estado=IntentoEntrega.Estado.ENVIANDO,
        fecha_inicio_envio__isnull=False,
        fecha_inicio_envio__lte=fecha_limite,
    ).order_by("fecha_inicio_envio", "pk")

def obtener_intentos_entrega_sin_fecha_inicio():
    """
    Identifica intentos ENVIANDO sin fecha de inicio.

    Son registros históricos que requieren
    revisión administrativa.

    No modifica estados ni créditos.
    """

    return IntentoEntrega.objects.filter(
        estado=IntentoEntrega.Estado.ENVIANDO,
        fecha_inicio_envio__isnull=True,
    ).order_by("fecha_creacion", "pk")

def obtener_intentos_para_revision_administrativa(
    minutos: int = 15,
):
    """
    Reúne los intentos de entrega que requieren
    revisión administrativa.

    Incluye:
    - Envíos ENVIANDO que superaron el tiempo permitido.
    - Envíos ENVIANDO sin fecha de inicio.

    No modifica registros ni consume créditos.
    """

    from datetime import timedelta

    from django.db.models import Q
    from django.utils import timezone

    if minutos <= 0:
        raise ValueError(
            "Los minutos deben ser mayores que cero."
        )

    fecha_limite = timezone.now() - timedelta(
        minutes=minutos
    )

    return IntentoEntrega.objects.filter(
        estado=IntentoEntrega.Estado.ENVIANDO,
    ).filter(
        Q(fecha_inicio_envio__lte=fecha_limite)
        | Q(fecha_inicio_envio__isnull=True)
    ).order_by("fecha_creacion", "pk")

def listar_entregas_pendientes_revision(
    minutos: int = 15,
) -> list[dict]:
    """
    Prepara los intentos que requieren revisión administrativa.

    No modifica estados, no envía fotografías
    y no consume créditos.
    """

    intentos = obtener_intentos_para_revision_administrativa(
        minutos=minutos
    )

    resultado = []

    for intento in intentos:
        motivo = (
            "SIN_FECHA_INICIO"
            if intento.fecha_inicio_envio is None
            else "TIEMPO_EXCEDIDO"
        )

        resultado.append({
            "id": intento.pk,
            "codigo_proceso": intento.codigo_proceso,
            "canal": intento.canal,
            "destinatario": intento.destinatario,
            "estado": intento.estado,
            "fecha_creacion": intento.fecha_creacion,
            "fecha_inicio_envio": intento.fecha_inicio_envio,
            "motivo_revision": motivo,
        })

    return resultado

@transaction.atomic
def registrar_revision_entrega(
    intento_id: int,
    usuario,
    resultado: str,
    observaciones: str,
) -> IntentoEntrega:
    """
    Registra la revisión administrativa de un intento.

    No modifica el estado técnico ni autoriza reenvíos.
    """

    if not (
        usuario
        and usuario.is_authenticated
        and usuario.is_active
        and usuario.is_staff
    ):
        raise ValueError(
            "Se requiere un administrador activo."
        )

    resultados_validos = {
        valor
        for valor, _ in IntentoEntrega.ResultadoRevision.choices
    }

    if resultado not in resultados_validos:
        raise ValueError(
            "Resultado de revisión no válido."
        )

    observaciones = (observaciones or "").strip()

    if not observaciones:
        raise ValueError(
            "Las observaciones son obligatorias."
        )

    intento = (
        IntentoEntrega.objects
        .select_for_update()
        .filter(pk=intento_id)
        .first()
    )

    if intento is None:
        raise ValueError(
            "El intento de entrega no existe."
        )

    if intento.estado != IntentoEntrega.Estado.ENVIANDO:
        raise ValueError(
            "Solo pueden revisarse intentos en estado ENVIANDO."
        )

    if intento.fecha_revision is not None:
        raise ValueError(
            "Este intento ya tiene una revisión administrativa."
        )

    intento.revisado_por = usuario
    intento.fecha_revision = timezone.now()
    intento.resultado_revision = resultado
    intento.observaciones_revision = observaciones

    intento.save(
        update_fields=[
            "revisado_por",
            "fecha_revision",
            "resultado_revision",
            "observaciones_revision",
        ]
    )

    return intento

def obtener_resumen_envios(codigo_proceso: str) -> dict:
    """
    Devuelve el historial resumido de envíos de una operación.

    No modifica intentos, imágenes ni créditos.
    """
    from django.db.models import Count, Q

    resumen = IntentoEntrega.objects.filter(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.EMAIL,
    ).aggregate(
        total_intentos=Count("id"),
        envios_exitosos=Count(
            "id",
            filter=Q(estado=IntentoEntrega.Estado.ENVIADO),
        ),
        intentos_fallidos=Count(
            "id",
            filter=Q(estado=IntentoEntrega.Estado.ERROR),
        ),
    )

    resumen["reenvios"] = max(
        resumen["envios_exitosos"] - 1,
        0,
    )

    return resumen

def crear_solicitud_envio_email(
    codigo_proceso: str,
    destinatario: str,
    clave_solicitud: str,
) -> IntentoEntrega:
    """
    Crea o recupera un intento asociado a una solicitud
    específica de envío.

    La misma clave_solicitud no genera otro intento.
    Una nueva clave permite un reenvío.
    """

    codigo_proceso = (codigo_proceso or "").strip()
    destinatario = (destinatario or "").strip()
    clave_solicitud = (clave_solicitud or "").strip()

    if not clave_solicitud:
        raise ValueError(
            "La clave de solicitud es obligatoria."
        )

    clave_idempotencia = f"EMAIL-{clave_solicitud}"

    return crear_intento_entrega(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.EMAIL,
        destinatario=destinatario,
        clave_idempotencia=clave_idempotencia,
    )

def crear_solicitud_envio_whatsapp(
    codigo_proceso: str,
    destinatario: str,
    clave_solicitud: str,
) -> IntentoEntrega:
    """
    Crea o recupera una solicitud de envío WhatsApp.

    La misma clave_solicitud no duplica el intento.
    Una nueva clave permite reenviar fotografías.
    No realiza envíos ni consume créditos.
    """

    codigo_proceso = (codigo_proceso or "").strip()
    destinatario = (destinatario or "").strip()
    clave_solicitud = (clave_solicitud or "").strip()

    if not clave_solicitud:
        raise ValueError(
            "La clave de solicitud es obligatoria."
        )

    return crear_intento_entrega(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario=destinatario,
        clave_idempotencia=f"WHATSAPP-{clave_solicitud}",
    )
