from django.conf import settings

from web.services.whatsapp_solicitudes_trabajador import (
    ejecutar_lote_solicitudes_whatsapp,
)
from web.services.whatsapp_reintentos_trabajador import (
    procesar_ciclo_recuperacion_whatsapp,
)


def procesar_ciclo_envios_whatsapp(limite: int = 50) -> dict:
    """
    Coordina solicitudes nuevas y reintentos automáticos.

    No procesa eventos del webhook.
    No consume créditos.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    if not settings.WHATSAPP_ENVIO_HABILITADO:
        raise RuntimeError(
            "El envío real de WhatsApp está deshabilitado."
        )

    access_token = settings.WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID

    if not access_token or not phone_number_id:
        raise ValueError(
            "Faltan credenciales de Meta WhatsApp."
        )

    solicitudes = ejecutar_lote_solicitudes_whatsapp(
        access_token=access_token,
        phone_number_id=phone_number_id,
        limite=limite,
    )

    recuperacion = procesar_ciclo_recuperacion_whatsapp(
        access_token=access_token,
        phone_number_id=phone_number_id,
        limite=limite,
    )

    return {
        "solicitudes": solicitudes,
        "recuperacion": recuperacion,
    }
