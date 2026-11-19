import logging

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    reclamar_evento_whatsapp,
    confirmar_evento_whatsapp,
    registrar_error_evento_whatsapp,
)
from web.services.whatsapp_solicitudes import (
    validar_solicitud_whatsapp,
)
from web.services.whatsapp_imagenes import (
    preparar_solicitud_envio_whatsapp,
)
from web.services.whatsapp_procesamiento import liberar_evento_whatsapp

logger = logging.getLogger(__name__)


def procesar_mensaje_whatsapp(evento_id: int) -> bool:
    """
    Procesa una solicitud entrante de fotografías.

    Valida referencia, remitente y fotografías.
    Prepara la entrega sin enviarla.
    No consume créditos.
    """

    evento = EventoWhatsApp.objects.filter(
        pk=evento_id,
        tipo=EventoWhatsApp.Tipo.MENSAJE,
    ).first()

    if evento is None:
        return False

    numero_intento = reclamar_evento_whatsapp(evento_id)

    if numero_intento is None:
        return False

    try:
        contenido = evento.contenido

        if contenido.get("type") != "text":
            raise ValueError(
                "El mensaje recibido no es de texto."
            )

        texto = contenido.get("text", {}).get("body", "")

        codigo_proceso, telefono, imagenes = (
            validar_solicitud_whatsapp(
                texto=texto,
                telefono_remitente=evento.telefono,
            )
        )

        intento_id, cantidad = preparar_solicitud_envio_whatsapp(
            codigo_proceso=codigo_proceso,
            destinatario=telefono,
            clave_solicitud=evento.mensaje_id,
        )

        if cantidad != len(imagenes):
            logger.warning(
                "Diferencia de fotografías en intento %s: "
                "preparadas=%s, disponibles=%s",
                intento_id,
                cantidad,
                len(imagenes),
            )

        return confirmar_evento_whatsapp(
            evento_id,
            numero_intento,
        )

    except ValueError as error:
        logger.warning(
            "Solicitud WhatsApp rechazada. Evento=%s: %s",
            evento_id,
            error,
        )

        registrar_error_evento_whatsapp(
            evento_id,
            str(error),
            numero_intento,
        )

        return False

    except Exception:
        logger.exception(
            "Error inesperado procesando mensaje WhatsApp %s",
            evento_id,
        )

        liberar_evento_whatsapp(
            evento_id,
            numero_intento,
        )

        return False

