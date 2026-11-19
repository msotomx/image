from web.services.whatsapp_eventos import extraer_token_whatsapp
from web.services.whatsapp_qr import validar_token_whatsapp
from web.services.entrega import obtener_imagenes_para_entrega


def validar_solicitud_whatsapp(
    texto: str,
    telefono_remitente: str,
):
    """
    Valida una solicitud recibida por WhatsApp.

    Comprueba la referencia firmada y que el remitente
    sea el cliente final registrado en la operación.

    No envía fotografías ni modifica créditos.
    """

    token = extraer_token_whatsapp(texto)

    codigo_proceso = validar_token_whatsapp(token)

    telefono_autorizado = validar_remitente_whatsapp(
        codigo_proceso=codigo_proceso,
        telefono_remitente=telefono_remitente,
    )

    imagenes = obtener_imagenes_para_entrega(
        codigo_proceso
    )

    return codigo_proceso, telefono_autorizado, imagenes


from web.models import PaqueteImagen

def normalizar_whatsapp_mexico(numero: str) -> str:
    """
    Normaliza números mexicanos al formato 52 + 10 dígitos.
    """

    digitos = "".join(
        c for c in (numero or "")
        if c.isdigit()
    )

    if len(digitos) == 10:
        return "52" + digitos

    if len(digitos) == 12 and digitos.startswith("52"):
        return digitos

    if len(digitos) == 13 and digitos.startswith("521"):
        return "52" + digitos[3:]

    raise ValueError(
        "El número de WhatsApp no tiene un formato mexicano válido."
    )

def validar_remitente_whatsapp(
    codigo_proceso: str,
    telefono_remitente: str,
) -> str:
    """
    Comprueba que el mensaje provenga del número
    proporcionado por el cliente final.

    No modifica registros ni consume créditos.
    """

    paquetes = list(
        PaqueteImagen.objects.filter(
            codigo_proceso=codigo_proceso
        )
    )

    if not paquetes:
        raise ValueError("La operación no existe.")

    unidades = {
        paquete.unidad_venta_id
        for paquete in paquetes
    }

    if len(unidades) != 1:
        raise ValueError(
            "La operación pertenece a diferentes unidades de venta."
        )

    destinatarios = {
        normalizar_whatsapp_mexico(
            paquete.whatsapp_destino
        )
        for paquete in paquetes
    }

    if len(destinatarios) != 1:
        raise ValueError(
            "La operación tiene diferentes destinatarios WhatsApp."
        )

    telefono_autorizado = destinatarios.pop()

    telefono_recibido = normalizar_whatsapp_mexico(
        telefono_remitente
    )

    if telefono_autorizado != telefono_recibido:
        raise ValueError(
            "El número remitente no coincide con el registrado por el cliente."
        )

    return telefono_autorizado
