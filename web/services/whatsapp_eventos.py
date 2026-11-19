import hashlib
import json

from web.models import EventoWhatsApp

def _generar_clave_evento(*partes):
    """
    Genera una clave SHA-256 para identificar
    un evento de WhatsApp.
    """
    contenido = json.dumps(
        partes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        contenido.encode("utf-8")
    ).hexdigest()


def registrar_eventos_whatsapp(evento):
    """
    Registra los mensajes y estados recibidos
    desde el webhook de Meta.

    No procesa fotografías ni consume créditos.
    """
    registrados = 0

    for entrada in evento.get("entry", []):
        for cambio in entrada.get("changes", []):
            if cambio.get("field") != "messages":
                continue

            datos = cambio.get("value", {})

            for mensaje in datos.get("messages", []):
                mensaje_id = mensaje.get("id")

                if not mensaje_id:
                    continue

                clave = _generar_clave_evento(
                    "MENSAJE",
                    mensaje_id,
                )

                _, creado = EventoWhatsApp.objects.get_or_create(
                    clave_evento=clave,
                    defaults={
                        "tipo": EventoWhatsApp.Tipo.MENSAJE,
                        "mensaje_id": mensaje_id,
                        "telefono": mensaje.get("from", ""),
                        "contenido": mensaje,
                    },
                )

                if creado:
                    registrados += 1

            for estado in datos.get("statuses", []):
                mensaje_id = estado.get("id")
                estado_mensaje = estado.get("status", "")
                marca_tiempo = estado.get("timestamp", "")

                if not mensaje_id or not estado_mensaje:
                    continue

                clave = _generar_clave_evento(
                    "ESTADO",
                    mensaje_id,
                    estado_mensaje,
                    marca_tiempo,
                )

                _, creado = EventoWhatsApp.objects.get_or_create(
                    clave_evento=clave,
                    defaults={
                        "tipo": EventoWhatsApp.Tipo.ESTADO,
                        "mensaje_id": mensaje_id,
                        "telefono": estado.get("recipient_id", ""),
                        "estado_mensaje": estado_mensaje,
                        "contenido": estado,
                    },
                )

                if creado:
                    registrados += 1

    return registrados

def extraer_token_whatsapp(texto: str) -> str:
    """
    Extrae el token de un mensaje recibido
    desde el QR de IMAGE ENGINE.
    """
    if not isinstance(texto, str):
        raise ValueError("El mensaje no contiene texto válido.")

    marcador = "Referencia:"

    if marcador not in texto:
        raise ValueError(
            "El mensaje no contiene una referencia."
        )

    token = texto.split(marcador, 1)[1].strip()

    if not token:
        raise ValueError(
            "La referencia de WhatsApp está vacía."
        )

    if any(caracter.isspace() for caracter in token):
        raise ValueError(
            "La referencia contiene caracteres inválidos."
        )

    return token
