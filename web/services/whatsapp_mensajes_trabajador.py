import logging

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    obtener_eventos_whatsapp_pendientes,
)
from web.services.whatsapp_mensajes import (
    procesar_mensaje_whatsapp,
)

logger = logging.getLogger(__name__)


def procesar_lote_mensajes_whatsapp(limite: int = 50) -> dict:
    """
    Procesa solicitudes entrantes de clientes finales.

    No envía fotografías.
    No consume créditos.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    eventos = obtener_eventos_whatsapp_pendientes(
        limite=limite,
        tipo=EventoWhatsApp.Tipo.MENSAJE,
    )

    resumen = {
        "seleccionados": 0,
        "procesados": 0,
        "no_procesados": 0,
    }

    for evento in eventos:

        if evento.tipo != EventoWhatsApp.Tipo.MENSAJE:
            continue

        resumen["seleccionados"] += 1

        try:
            resultado = procesar_mensaje_whatsapp(evento.pk)

        except Exception:
            logger.exception(
                "Error inesperado procesando mensaje WhatsApp ID=%s",
                evento.pk,
            )
            resultado = False

        if resultado:
            resumen["procesados"] += 1
        else:
            resumen["no_procesados"] += 1

    return resumen
