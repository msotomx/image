from django.db import transaction

from web.models import PaqueteImagen, IntentoEntrega
from web.services.whatsapp_solicitudes import (
    normalizar_whatsapp_mexico,
)


@transaction.atomic
def registrar_whatsapp_cliente(
    codigo_proceso: str,
    telefono: str,
) -> str:
    """
    Registra el WhatsApp del cliente final.

    Requiere una operación cerrada.
    Actualiza todos los paquetes del proceso.
    Impide cambiar el destinatario si ya existe
    una solicitud de entrega por WhatsApp.

    No consume créditos ni envía fotografías.
    """

    if not codigo_proceso or not codigo_proceso.strip():
        raise ValueError("El código de proceso es obligatorio.")

    codigo_proceso = codigo_proceso.strip()
    telefono_normalizado = normalizar_whatsapp_mexico(telefono)

    paquetes = list(
        PaqueteImagen.objects.select_for_update()
        .filter(codigo_proceso=codigo_proceso)
        .order_by("pk")
    )

    if not paquetes:
        raise ValueError("La operación no existe.")

    if any(
        paquete.estado != PaqueteImagen.EstadoPaquete.CERRADO
        for paquete in paquetes
    ):
        raise ValueError(
            "La operación debe estar cerrada antes "
            "de registrar el WhatsApp del cliente."
        )

    unidades = {paquete.unidad_venta_id for paquete in paquetes}

    if len(unidades) != 1:
        raise ValueError(
            "La operación pertenece a diferentes unidades de venta."
        )

    telefonos_registrados = {
        normalizar_whatsapp_mexico(paquete.whatsapp_destino)
        for paquete in paquetes
        if paquete.whatsapp_destino
    }

    if (
        telefonos_registrados
        and telefonos_registrados != {telefono_normalizado}
    ):
        if IntentoEntrega.objects.filter(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.WHATSAPP,
        ).exists():
            raise ValueError(
                "No se puede cambiar el WhatsApp del cliente "
                "porque ya existe una solicitud de entrega."
            )

    PaqueteImagen.objects.filter(
        pk__in=[paquete.pk for paquete in paquetes]
    ).update(
        whatsapp_destino=telefono_normalizado
    )

    return telefono_normalizado
