import logging

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    obtener_eventos_whatsapp_pendientes,
)
from web.services.whatsapp_estados import (
    procesar_estado_whatsapp,
)
logger = logging.getLogger(__name__)


def procesar_lote_estados_whatsapp(limite: int = 50) -> dict:
    """
    Procesa un lote de notificaciones de estado de Meta.

    No procesa mensajes entrantes.
    No envía fotografías.
    No consume créditos.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    # Seleccionar exclusivamente eventos ESTADO disponibles.
    eventos = obtener_eventos_whatsapp_pendientes(
        limite=limite,
        tipo=EventoWhatsApp.Tipo.ESTADO,
    )

    resumen = {
        "seleccionados": 0,
        "procesados": 0,
        "no_procesados": 0,
    }

    for evento in eventos:

        # Protección adicional.
        if evento.tipo != EventoWhatsApp.Tipo.ESTADO:
            continue

        resumen["seleccionados"] += 1

        try:
            resultado = procesar_estado_whatsapp(evento.pk)

        except Exception:
            logger.exception(
                "Error inesperado procesando evento WhatsApp ID=%s",
                evento.pk,
            )
            resultado = False

        if resultado:
            resumen["procesados"] += 1
        else:
            resumen["no_procesados"] += 1

    return resumen

