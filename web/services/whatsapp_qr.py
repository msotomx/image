import base64
from io import BytesIO

import qrcode
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing


TOKEN_SALT = "image-engine-whatsapp-entrega"
TOKEN_MAX_AGE = 15 * 60  # 15 minutos


def generar_token_whatsapp(codigo_proceso: str) -> str:
    """
    Genera un token firmado para identificar una operación.
    """
    if not codigo_proceso or not codigo_proceso.strip():
        raise ValueError("El código de proceso es obligatorio.")

    return signing.dumps(
        {"codigo_proceso": codigo_proceso.strip()},
        salt=TOKEN_SALT,
    )


def validar_token_whatsapp(token: str) -> str:
    """
    Valida la firma y el vencimiento del token.
    Devuelve el código de proceso.
    """
    try:
        datos = signing.loads(
            token,
            salt=TOKEN_SALT,
            max_age=TOKEN_MAX_AGE,
        )
    except signing.BadSignature as exc:
        raise ValueError(
            "El enlace de WhatsApp es inválido o ha vencido."
        ) from exc

    codigo_proceso = datos.get("codigo_proceso")

    if not isinstance(codigo_proceso, str) or not codigo_proceso:
        raise ValueError("El token no contiene una operación válida.")

    return codigo_proceso


def generar_enlace_whatsapp(codigo_proceso: str) -> str:
    """
    Genera un enlace para abrir WhatsApp con un mensaje
    que identifica la operación.
    """
    numero = settings.IMAGE_ENGINE_WHATSAPP_NUMBER

    if not numero or not numero.isdigit():
        raise ValueError(
            "El número empresarial de WhatsApp no está configurado."
        )

    token = generar_token_whatsapp(codigo_proceso)

    mensaje = (
        "Hola, quiero recibir mis fotografías de IMAGE ENGINE. "
        f"Referencia: {token}"
    )

    parametros = urlencode({"text": mensaje})

    return f"https://wa.me/{numero}?{parametros}"

def generar_qr_whatsapp(codigo_proceso: str) -> str:
    """
    Genera un código QR de WhatsApp en formato
    PNG codificado en Base64.

    No guarda archivos en disco.
    No modifica la base de datos.
    No consume créditos.
    """
    enlace = generar_enlace_whatsapp(codigo_proceso)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )

    qr.add_data(enlace)
    qr.make(fit=True)

    imagen = qr.make_image(
        fill_color="black",
        back_color="white",
    )

    buffer = BytesIO()
    imagen.save(buffer, format="PNG")

    return base64.b64encode(
        buffer.getvalue()
    ).decode("ascii")
