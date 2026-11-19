import requests
from django.conf import settings


class WhatsAppMetaError(Exception):
    """Meta rechazó la solicitud de envío."""


class WhatsAppMetaResultadoIncierto(Exception):
    """No podemos confirmar si Meta recibió la solicitud."""


def enviar_imagen_whatsapp_meta(
    *,
    access_token: str,
    phone_number_id: str,
    telefono_destino: str,
    media_id: str,
) -> str:
    """
    Envía una fotografía mediante WhatsApp Cloud API.

    Devuelve el identificador del mensaje aceptado por Meta.

    No modifica estados ni créditos.
    """
    if not settings.WHATSAPP_ENVIO_HABILITADO:
        raise RuntimeError(
            "El envío real de WhatsApp está deshabilitado."
        )

    if not all([
        access_token,
        phone_number_id,
        telefono_destino,
        media_id,
    ]):
        raise ValueError(
            "Faltan parámetros obligatorios para enviar la imagen."
        )

    endpoint = (
        "https://graph.facebook.com/v23.0/"
        f"{phone_number_id}/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefono_destino,
        "type": "image",
        "image": {
            "id": media_id,
        },
    }

    try:
        respuesta = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(5, 30),
        )

    except requests.RequestException as exc:
        raise WhatsAppMetaResultadoIncierto(
            "No fue posible confirmar si Meta recibió el envío."
        ) from exc

    if not respuesta.ok:
        raise WhatsAppMetaError(
            f"Meta rechazó el envío. HTTP {respuesta.status_code}"
        )

    try:
        datos = respuesta.json()
        meta_message_id = datos["messages"][0]["id"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise WhatsAppMetaResultadoIncierto(
            "Meta respondió, pero no se obtuvo el identificador del mensaje."
        ) from exc

    if not isinstance(meta_message_id, str) or not meta_message_id.strip():
        raise WhatsAppMetaResultadoIncierto(
            "Meta devolvió un identificador de mensaje inválido."
        )

    return meta_message_id.strip()

def subir_imagen_whatsapp_meta(
    *,
    access_token: str,
    phone_number_id: str,
    archivo,
) -> str:
    """
    Sube una fotografía a WhatsApp Cloud API.

    archivo: archivo abierto desde ImageField.open("rb").

    Devuelve el media_id generado por Meta.

    No envía mensajes ni modifica créditos.
    """
    if not settings.WHATSAPP_ENVIO_HABILITADO:
        raise RuntimeError(
            "El envío real de WhatsApp está deshabilitado."
        )

    if not access_token or not phone_number_id:
        raise ValueError(
            "Faltan credenciales de WhatsApp."
        )

    if archivo is None:
        raise ValueError(
            "El archivo de imagen es obligatorio."
        )

    endpoint = (
        "https://graph.facebook.com/v23.0/"
        f"{phone_number_id}/media"
    )

    try:
        respuesta = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
            },
            data={
                "messaging_product": "whatsapp",
            },
            files={
                "file": (
                    archivo.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                    archivo,
                    "image/jpeg",
                ),
            },
            timeout=(5, 60),
        )

    except requests.RequestException as exc:
        raise WhatsAppMetaResultadoIncierto(
            "No fue posible confirmar la carga de la imagen a Meta."
        ) from exc

    if not respuesta.ok:
        raise WhatsAppMetaError(
            f"Meta rechazó la carga de la imagen. HTTP {respuesta.status_code}"
        )

    try:
        datos = respuesta.json()
        media_id = datos["id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise WhatsAppMetaResultadoIncierto(
            "Meta respondió sin un identificador de medio válido."
        ) from exc

    if not isinstance(media_id, str) or not media_id.strip():
        raise WhatsAppMetaResultadoIncierto(
            "Meta devolvió un identificador de medio inválido."
        )

    return media_id.strip()
