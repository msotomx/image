import logging

from django.conf import settings

from web.models import IntentoEntrega
from web.services.whatsapp_ejecutor import (
    ejecutar_solicitud_whatsapp,
)

logger = logging.getLogger(__name__)


def ejecutar_lote_solicitudes_whatsapp(
    access_token: str,
    phone_number_id: str,
    limite: int = 50,
) -> dict:
    """
    Ejecuta solicitudes normales pendientes de WhatsApp.

    No procesa reintentos automáticos.
    No modifica créditos.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    # No consultar ni modificar solicitudes si el envío está deshabilitado.
    if not settings.WHATSAPP_ENVIO_HABILITADO:
        raise RuntimeError(
            "El envío real de WhatsApp está deshabilitado."
        )

    if not access_token or not phone_number_id:
        raise ValueError(
            "Faltan credenciales de Meta WhatsApp."
        )

    intentos = list(
        IntentoEntrega.objects.filter(
            canal=IntentoEntrega.Canal.WHATSAPP,
            estado=IntentoEntrega.Estado.PENDIENTE,
            clave_idempotencia__startswith="WHATSAPP-",
        )
        .order_by("fecha_creacion", "pk")
        .values_list("pk", flat=True)[:limite]
    )

    resumen = {
        "seleccionados": len(intentos),
        "iniciados": 0,
        "omitidos": 0,
        "errores": 0,
    }

    for intento_id in intentos:
        try:
            resultado = ejecutar_solicitud_whatsapp(
                intento_id=intento_id,
                access_token=access_token,
                phone_number_id=phone_number_id,
            )

            if resultado["iniciado"]:
                resumen["iniciados"] += 1
            else:
                resumen["omitidos"] += 1

        except Exception:
            logger.exception(
                "Error ejecutando solicitud WhatsApp ID=%s",
                intento_id,
            )
            resumen["errores"] += 1

    return resumen
