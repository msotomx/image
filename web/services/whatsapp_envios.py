from django.utils import timezone
from django.db.models import Q

from web.models import IntentoEntrega, IntentoEntregaImagen


def reclamar_imagen_whatsapp(entrega_imagen_id: int) -> bool:
    """
    Reclama una fotografía pendiente para su envío.

    True: PENDIENTE -> ENVIANDO.
    False: la fotografía no estaba pendiente
           o el intento no estaba ENVIANDO.

    No envía mensajes ni modifica créditos.
    """

    actualizados = IntentoEntregaImagen.objects.filter(
        pk=entrega_imagen_id,
        estado=IntentoEntregaImagen.Estado.PENDIENTE,
        intento__canal=IntentoEntrega.Canal.WHATSAPP,
        intento__estado=IntentoEntrega.Estado.ENVIANDO,
    ).update(
        estado=IntentoEntregaImagen.Estado.ENVIANDO,
        fecha_inicio_envio=timezone.now(),
        mensaje_error="",
    )

    return actualizados == 1

def confirmar_aceptacion_imagen_whatsapp(
    entrega_imagen_id: int,
    meta_message_id: str,
) -> bool:
    """
    Registra que Meta aceptó el mensaje de una fotografía.

    Permite:
        ENVIANDO -> ACEPTADA
        INCIERTA -> ACEPTADA (confirmación tardía)

    No confirma entrega al destinatario.
    No realiza nuevos envíos ni modifica créditos.
    """    
    meta_message_id = (meta_message_id or "").strip()

    if not meta_message_id:
        raise ValueError(
            "El identificador de mensaje de Meta es obligatorio."
        )

    actualizados = (
        IntentoEntregaImagen.objects
        .filter(
            Q(meta_message_id__isnull=True)
            | Q(meta_message_id=meta_message_id),
            pk=entrega_imagen_id,
            estado__in=[
                IntentoEntregaImagen.Estado.ENVIANDO,
                IntentoEntregaImagen.Estado.INCIERTA,
            ],
            intento__canal=IntentoEntrega.Canal.WHATSAPP,
            intento__estado__in=[
                IntentoEntrega.Estado.ENVIANDO,
                IntentoEntrega.Estado.ERROR,
            ],
        )
        .update(
            estado=IntentoEntregaImagen.Estado.ACEPTADA,
            meta_message_id=meta_message_id,
            fecha_aceptacion=timezone.now(),
            mensaje_error="",
        )
    )

    return actualizados == 1

def registrar_imagen_enviada_whatsapp(
    meta_message_id: str,
) -> bool:
    """
    Registra la notificación 'sent' de Meta.

    Solo permite ACEPTADA -> ENVIADA.

    True: estado actualizado.
    False: mensaje inexistente o transición no aplicable.

    No modifica créditos ni envía mensajes.
    """

    meta_message_id = (meta_message_id or "").strip()

    if not meta_message_id:
        raise ValueError(
            "El identificador de mensaje de Meta es obligatorio."
        )

    actualizados = IntentoEntregaImagen.objects.filter(
        meta_message_id=meta_message_id,
        estado__in=[
            IntentoEntregaImagen.Estado.ACEPTADA,
            IntentoEntregaImagen.Estado.INCIERTA,
        ],
        intento__canal=IntentoEntrega.Canal.WHATSAPP,
    ).update(
        estado=IntentoEntregaImagen.Estado.ENVIADA,
        mensaje_error="",
    )

    return actualizados == 1

def registrar_imagen_entregada_whatsapp(
    meta_message_id: str,
) -> bool:
    """
    Registra la notificación 'delivered' de Meta.

    Permite:
        ACEPTADA -> ENTREGADA
        ENVIADA -> ENTREGADA

    No retrocede estados ni modifica créditos.
    """

    meta_message_id = (meta_message_id or "").strip()

    if not meta_message_id:
        raise ValueError(
            "El identificador de mensaje de Meta es obligatorio."
        )

    actualizados = IntentoEntregaImagen.objects.filter(
        meta_message_id=meta_message_id,
        estado__in=[
            IntentoEntregaImagen.Estado.ACEPTADA,
            IntentoEntregaImagen.Estado.ENVIADA,
            IntentoEntregaImagen.Estado.INCIERTA,
        ],
        intento__canal=IntentoEntrega.Canal.WHATSAPP,
    ).update(
        estado=IntentoEntregaImagen.Estado.ENTREGADA,
        fecha_entrega=timezone.now(),
        mensaje_error="",
    )

    return actualizados == 1

def registrar_imagen_leida_whatsapp(
    meta_message_id: str,
) -> bool:
    """
    Registra la notificación 'read' de Meta.

    Permite:
        ACEPTADA -> LEIDA
        ENVIADA -> LEIDA
        ENTREGADA -> LEIDA

    No retrocede estados ni modifica créditos.
    """

    meta_message_id = (meta_message_id or "").strip()

    if not meta_message_id:
        raise ValueError(
            "El identificador de mensaje de Meta es obligatorio."
        )

    actualizados = IntentoEntregaImagen.objects.filter(
        meta_message_id=meta_message_id,
        estado__in=[
            IntentoEntregaImagen.Estado.ACEPTADA,
            IntentoEntregaImagen.Estado.ENVIADA,
            IntentoEntregaImagen.Estado.ENTREGADA,
            IntentoEntregaImagen.Estado.INCIERTA,
        ],        
        intento__canal=IntentoEntrega.Canal.WHATSAPP,
    ).update(
        estado=IntentoEntregaImagen.Estado.LEIDA,
        fecha_lectura=timezone.now(),
        mensaje_error="",
    )

    return actualizados == 1

def registrar_imagen_fallida_whatsapp(
    meta_message_id: str,
    mensaje_error: str,
) -> bool:
    """
    Registra una notificación 'failed' confirmada por Meta.

    Permite ACEPTADA o ENVIADA -> ERROR.

    No reenvía fotografías ni modifica créditos.
    """

    meta_message_id = (meta_message_id or "").strip()
    mensaje_error = (mensaje_error or "").strip()

    if not meta_message_id:
        raise ValueError(
            "El identificador de Meta es obligatorio."
        )

    if not mensaje_error:
        raise ValueError(
            "El motivo del error es obligatorio."
        )

    actualizados = IntentoEntregaImagen.objects.filter(
        meta_message_id=meta_message_id,
        estado__in=[
            IntentoEntregaImagen.Estado.ACEPTADA,
            IntentoEntregaImagen.Estado.ENVIADA,
            IntentoEntregaImagen.Estado.INCIERTA,
        ],        
        intento__canal=IntentoEntrega.Canal.WHATSAPP,
    ).update(
        estado=IntentoEntregaImagen.Estado.ERROR,
        mensaje_error=mensaje_error,
    )

    return actualizados == 1

def registrar_error_envio_imagen_whatsapp(
    entrega_imagen_id: int,
    mensaje_error: str,
) -> bool:
    """
    Registra un fallo confirmado durante el envío HTTP.

    Solo permite ENVIANDO -> ERROR.
    No modifica el intento general ni los créditos.
    """

    mensaje_error = (mensaje_error or "").strip()

    if not mensaje_error:
        raise ValueError("El motivo del error es obligatorio.")

    actualizados = IntentoEntregaImagen.objects.filter(
        pk=entrega_imagen_id,
        estado=IntentoEntregaImagen.Estado.ENVIANDO,
        intento__canal=IntentoEntrega.Canal.WHATSAPP,
        intento__estado=IntentoEntrega.Estado.ENVIANDO,
    ).update(
        estado=IntentoEntregaImagen.Estado.ERROR,
        mensaje_error=mensaje_error,
    )

    return actualizados == 1


def registrar_envio_imagen_incierto_whatsapp(
    entrega_imagen_id: int,
    mensaje_error: str,
    meta_message_id: str | None = None,
) -> bool:
    """
    Registra un resultado incierto del envío.

    Solo permite ENVIANDO -> INCIERTA.
    Conserva el identificador de Meta cuando existe.

    No reenvía fotografías ni modifica créditos.
    """

    mensaje_error = (mensaje_error or "").strip()
    meta_message_id = (meta_message_id or "").strip() or None

    if not mensaje_error:
        raise ValueError("El motivo de incertidumbre es obligatorio.")

    filtros = {
        "pk": entrega_imagen_id,
        "estado": IntentoEntregaImagen.Estado.ENVIANDO,
        "intento__canal": IntentoEntrega.Canal.WHATSAPP,
        "intento__estado": IntentoEntrega.Estado.ENVIANDO,
        "meta_message_id__isnull": True,
    }

    cambios = {
        "estado": IntentoEntregaImagen.Estado.INCIERTA,
        "mensaje_error": mensaje_error,
    }

    if meta_message_id:
        cambios["meta_message_id"] = meta_message_id

    actualizados = (
        IntentoEntregaImagen.objects
        .filter(**filtros)
        .update(**cambios)
    )

    return actualizados == 1

def obtener_imagenes_whatsapp_atascadas(minutos: int = 15):
    """
    Identifica fotografías WhatsApp que permanecen ENVIANDO
    más tiempo del permitido o carecen de fecha de inicio.

    No modifica estados, no reenvía mensajes
    y no consume créditos.
    """

    from datetime import timedelta
    from django.db.models import Q

    if minutos <= 0:
        raise ValueError(
            "Los minutos deben ser mayores que cero."
        )

    fecha_limite = timezone.now() - timedelta(
        minutes=minutos
    )

    return (
        IntentoEntregaImagen.objects
        .filter(
            estado=IntentoEntregaImagen.Estado.ENVIANDO,
            intento__canal=IntentoEntrega.Canal.WHATSAPP,
        )
        .filter(
            Q(fecha_inicio_envio__lte=fecha_limite)
            | Q(fecha_inicio_envio__isnull=True)
        )
        .select_related("intento", "imagen")
        .order_by("fecha_creacion", "pk")
    )

def recuperar_imagen_whatsapp_atascada(
    entrega_imagen_id: int,
    minutos: int = 15,
) -> bool:
    """
    Marca como INCIERTA una fotografía WhatsApp atascada.

    Comprueba el estado y la antigüedad en una única
    operación UPDATE para evitar condiciones de carrera.

    No reenvía mensajes ni modifica créditos.
    """

    from datetime import timedelta
    from django.db.models import Q

    if minutos <= 0:
        raise ValueError(
            "Los minutos deben ser mayores que cero."
        )

    fecha_limite = timezone.now() - timedelta(minutes=minutos)

    actualizados = (
        IntentoEntregaImagen.objects
        .filter(
            pk=entrega_imagen_id,
            estado=IntentoEntregaImagen.Estado.ENVIANDO,
            intento__canal=IntentoEntrega.Canal.WHATSAPP,
        )
        .filter(
            Q(fecha_inicio_envio__lte=fecha_limite)
            | Q(fecha_inicio_envio__isnull=True)
        )
        .update(
            estado=IntentoEntregaImagen.Estado.INCIERTA,
            mensaje_error=(
                "Envío interrumpido o sin confirmación después "
                "del tiempo permitido. Requiere revisión manual."
            ),
        )
    )

    return actualizados == 1

from django.db import transaction


@transaction.atomic
def confirmar_entrega_whatsapp_completa(intento_id: int) -> bool:
    """
    Confirma el intento general únicamente cuando Meta
    aceptó todas las fotografías preparadas.

    No envía mensajes ni modifica créditos.
    """

    intento = (
        IntentoEntrega.objects
        .select_for_update()
        .get(pk=intento_id)
    )

    if intento.canal != IntentoEntrega.Canal.WHATSAPP:
        raise ValueError("El intento no es de WhatsApp.")

    if intento.estado == IntentoEntrega.Estado.ENVIADO:
        return False

    if intento.estado != IntentoEntrega.Estado.ENVIANDO:
        raise ValueError("El intento no está en estado ENVIANDO.")

    fotografias = list(
        IntentoEntregaImagen.objects
        .select_for_update()
        .filter(intento=intento)
        .order_by("pk")
    )

    # No confirmar una entrega sin fotografías.
    if not fotografias:
        return False
    
    estados_aceptados = [
        IntentoEntregaImagen.Estado.ACEPTADA,
        IntentoEntregaImagen.Estado.ENVIADA,
        IntentoEntregaImagen.Estado.ENTREGADA,
        IntentoEntregaImagen.Estado.LEIDA,
    ]

    # Si alguna fotografía sigue pendiente, falló o
    # tiene resultado incierto, no cerrar la entrega.
    if any(
        fotografia.estado not in estados_aceptados
        or not fotografia.meta_message_id
        for fotografia in fotografias
    ):
        return False

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

def obtener_imagenes_whatsapp_para_revision():
    """
    Obtiene fotografías WhatsApp con error o resultado incierto.

    Incluye fotografías de intentos generales ENVIANDO
    o ENVIADO, porque Meta puede notificar un fallo
    después de aceptar inicialmente un mensaje.

    No modifica registros ni créditos.
    """

    return (
        IntentoEntregaImagen.objects
        .filter(
            intento__canal=IntentoEntrega.Canal.WHATSAPP,
            estado__in=[
                IntentoEntregaImagen.Estado.ERROR,
                IntentoEntregaImagen.Estado.INCIERTA,
            ],
        )
        .select_related(
            "intento",
            "imagen",
        )
        .order_by("fecha_creacion", "pk")
    )
