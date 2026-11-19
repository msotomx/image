import logging

from web.models import IntentoEntrega
from web.services.whatsapp_ejecutor import (
    ejecutar_solicitud_whatsapp,
)
from web.services.whatsapp_recuperacion import (
    preparar_lote_recuperacion_whatsapp,
)

logger = logging.getLogger(__name__)


def ejecutar_lote_reintentos_whatsapp(
    access_token: str,
    phone_number_id: str,
    limite: int = 50,
) -> dict:
    """
    Ejecuta reintentos automáticos previamente preparados.

    No crea solicitudes ni consume créditos.
    No procesa solicitudes normales de las Unidades de Venta.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    intentos = list(
        IntentoEntrega.objects.filter(
            canal=IntentoEntrega.Canal.WHATSAPP,
            estado=IntentoEntrega.Estado.PENDIENTE,
            clave_idempotencia__startswith="WA-RETRY-",
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
                "Error ejecutando reintento WhatsApp ID=%s",
                intento_id,
            )
            resumen["errores"] += 1

    return resumen

def procesar_ciclo_recuperacion_whatsapp(
    access_token: str,
    phone_number_id: str,
    limite: int = 50,
) -> dict:
    """
    Prepara y ejecuta reintentos automáticos de WhatsApp.

    Respeta las reglas de recuperación existentes:
    máximo 3 reintentos adicionales y espera mínima
    de 5 minutos.

    No modifica créditos.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    preparacion = preparar_lote_recuperacion_whatsapp(
        limite=limite,
    )

    ejecucion = ejecutar_lote_reintentos_whatsapp(
        access_token=access_token,
        phone_number_id=phone_number_id,
        limite=limite,
    )

    return {
        "preparacion": preparacion,
        "ejecucion": ejecucion,
    }
