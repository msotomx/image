from django.conf import settings
from web.models import IntentoEntregaImagen

from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
    registrar_error_envio_imagen_whatsapp,
    registrar_envio_imagen_incierto_whatsapp,
    confirmar_entrega_whatsapp_completa,
)

from web.services.whatsapp_meta import (
    subir_imagen_whatsapp_meta,
    enviar_imagen_whatsapp_meta,
    WhatsAppMetaError,
    WhatsAppMetaResultadoIncierto,
)
import logging

logger = logging.getLogger(__name__)


def enviar_fotografia_whatsapp(
    *,
    entrega_imagen_id: int,
    access_token: str,
    phone_number_id: str,
) -> str | None:
    """
    Envía una fotografía previamente preparada.

    Retorna el message_id de Meta si se registra correctamente.
    Retorna None si la fotografía no puede reclamarse.

    No modifica créditos.
    """
    # Bloquear cualquier envío real antes de modificar estados.
    if not settings.WHATSAPP_ENVIO_HABILITADO:
        raise RuntimeError(
            "El envío real de WhatsApp está deshabilitado."
        )

    if not access_token or not phone_number_id:
        raise ValueError(
            "Faltan credenciales de Meta WhatsApp."
        )

    if not reclamar_imagen_whatsapp(entrega_imagen_id):
        return None

    fase = "PREPARACION"
    meta_message_id = None

    try:
        entrega = (
            IntentoEntregaImagen.objects
            .select_related("intento", "imagen")
            .get(pk=entrega_imagen_id)
        )

        imagen = entrega.imagen

        if not imagen.imagen_procesada:
            raise ValueError("La fotografía procesada no existe.")

        if not imagen.seleccionada:
            raise ValueError("La fotografía no está seleccionada.")

        if imagen.estado_proceso not in (
            imagen.EstadoProceso.PROCESADA,
            imagen.EstadoProceso.ENVIADA,
        ):
            raise ValueError("La fotografía no está procesada.")

        with imagen.imagen_procesada.open("rb") as archivo:
            fase = "CARGA"

            media_id = subir_imagen_whatsapp_meta(
                access_token=access_token,
                phone_number_id=phone_number_id,
                archivo=archivo,
            )

        fase = "ENVIO"

        meta_message_id = enviar_imagen_whatsapp_meta(
            access_token=access_token,
            phone_number_id=phone_number_id,
            telefono_destino=entrega.intento.destinatario,
            media_id=media_id,
        )

        fase = "CONFIRMACION"

        confirmado = confirmar_aceptacion_imagen_whatsapp(
            entrega_imagen_id,
            meta_message_id,
        )

        if not confirmado:
            raise RuntimeError(
                "Meta aceptó el mensaje, pero no se pudo registrar "
                "su aceptación en la base de datos. "
                f"meta_message_id={meta_message_id}"
            )
            
        # Intentar cerrar la entrega general.
        # Solo se cerrará cuando todas las fotografías
        # tengan aceptación registrada por Meta.
        try:
            confirmar_entrega_whatsapp_completa(entrega.intento_id)
        except Exception:
            logger.exception(
                "Error al cerrar automáticamente la entrega WhatsApp. "
                "intento_id=%s, entrega_imagen_id=%s, meta_message_id=%s",
                entrega.intento_id,
                entrega_imagen_id,
                meta_message_id,
            )
        
        return meta_message_id
    
    except Exception as exc:
        mensaje = f"Fase {fase}: {exc}"

        if fase in ("ENVIO", "CONFIRMACION"):
            if isinstance(exc, WhatsAppMetaError) and fase == "ENVIO":
                registrar_error_envio_imagen_whatsapp(
                    entrega_imagen_id,
                    mensaje,
                )
            else:
                registrar_envio_imagen_incierto_whatsapp(
                    entrega_imagen_id,
                    mensaje,
                    meta_message_id=meta_message_id,
                )                
        else:
            registrar_error_envio_imagen_whatsapp(
                entrega_imagen_id,
                mensaje,
            )

        return None
