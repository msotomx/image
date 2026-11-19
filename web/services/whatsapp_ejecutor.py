from django.db import transaction
from django.conf import settings
from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.intentos_entrega import iniciar_intento_entrega
from web.services.intentos_entrega import registrar_error_entrega
from web.services.whatsapp_entrega import enviar_fotografia_whatsapp


def ejecutar_solicitud_whatsapp(
    intento_id: int,
    access_token: str,
    phone_number_id: str,
) -> dict:
    """
    Ejecuta una solicitud WhatsApp una sola vez.

    No crea solicitudes, no prepara fotografías
    y no consume créditos.
    """

    # Bloquear envíos reales mientras no estén autorizados.
    if not settings.WHATSAPP_ENVIO_HABILITADO:
        raise RuntimeError(
            "El envío real de WhatsApp está deshabilitado."
        )

    if not access_token or not phone_number_id:
        raise ValueError(
            "Faltan credenciales de Meta WhatsApp."
        )
    
    intento = IntentoEntrega.objects.get(pk=intento_id)

    if intento.canal != IntentoEntrega.Canal.WHATSAPP:
        raise ValueError("El intento no corresponde a WhatsApp.")

    cantidad = IntentoEntregaImagen.objects.filter(
        intento=intento,
    ).count()

    if cantidad == 0:
        raise ValueError("El intento no tiene fotografías preparadas.")

    # Solo un ejecutor puede iniciar esta solicitud.
    if not iniciar_intento_entrega(intento.clave_idempotencia):
        return {
            "iniciado": False,
            "fotografias": 0,
            "aceptadas": 0,
            "fallidas": 0,
        }

    pendientes = list(
        IntentoEntregaImagen.objects.filter(
            intento=intento,
            estado=IntentoEntregaImagen.Estado.PENDIENTE,
        ).values_list("pk", flat=True)
    )

    aceptadas = 0
    fallidas = 0

    for entrega_imagen_id in pendientes:
        meta_message_id = enviar_fotografia_whatsapp(
            entrega_imagen_id=entrega_imagen_id,
            access_token=access_token,
            phone_number_id=phone_number_id,
        )

        if meta_message_id:
            aceptadas += 1
        else:
            fallidas += 1
    
    # Cerrar el intento si terminó con fotografías fallidas.
    cerrar_intento_whatsapp_con_errores(intento_id)

    return {
        "iniciado": True,
        "fotografias": len(pendientes),
        "aceptadas": aceptadas,
        "fallidas": fallidas,
    }

@transaction.atomic
def cerrar_intento_whatsapp_con_errores(intento_id: int) -> bool:
    """
    Cierra un intento WhatsApp con errores cuando
    ya no quedan fotografías pendientes o enviándose.

    No envía mensajes ni consume créditos.
    """

    intento = (
        IntentoEntrega.objects
        .select_for_update()
        .get(pk=intento_id)
    )

    if intento.canal != IntentoEntrega.Canal.WHATSAPP:
        raise ValueError("El intento no corresponde a WhatsApp.")

    if intento.estado != IntentoEntrega.Estado.ENVIANDO:
        return False

    imagenes = IntentoEntregaImagen.objects.filter(
        intento=intento,
    )

    if not imagenes.exists():
        return False

    if imagenes.filter(
        estado__in=[
            IntentoEntregaImagen.Estado.PENDIENTE,
            IntentoEntregaImagen.Estado.ENVIANDO,
        ]
    ).exists():
        return False

    if not imagenes.filter(
        estado__in=[
            IntentoEntregaImagen.Estado.ERROR,
            IntentoEntregaImagen.Estado.INCIERTA,
        ]
    ).exists():
        return False

    return registrar_error_entrega(
        intento.clave_idempotencia,
        "Entrega WhatsApp finalizada con fotografías "
        "en ERROR o INCIERTA.",
    )
