from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.entrega import obtener_imagenes_para_entrega
from web.services.intentos_entrega import crear_solicitud_envio_whatsapp


@transaction.atomic
def preparar_imagenes_whatsapp(intento_id: int) -> int:
    """
    Prepara una sola vez las fotografías de un intento WhatsApp.

    Si ya existen fotografías asociadas, devuelve su cantidad
    sin modificar la selección original.

    No envía mensajes ni modifica créditos.
    """

    intento = (
        IntentoEntrega.objects
        .select_for_update()
        .get(pk=intento_id)
    )

    if intento.canal != IntentoEntrega.Canal.WHATSAPP:
        raise ValueError(
            "El intento no corresponde al canal WhatsApp."
        )

    if intento.estado != IntentoEntrega.Estado.PENDIENTE:
        raise ValueError(
            "Solo pueden prepararse intentos pendientes."
        )

    # Si el intento ya tiene fotografías, no modificar
    # el conjunto preparado anteriormente.
    cantidad_existente = IntentoEntregaImagen.objects.filter(
        intento=intento,
    ).count()

    if cantidad_existente > 0:
        return cantidad_existente

    # Primera preparación: validar operación y fotografías.
    imagenes = obtener_imagenes_para_entrega(
        intento.codigo_proceso
    )

    for imagen in imagenes:
        IntentoEntregaImagen.objects.create(
            intento=intento,
            imagen=imagen,
        )

    return len(imagenes)

@transaction.atomic
def preparar_solicitud_envio_whatsapp(
    codigo_proceso: str,
    destinatario: str,
    clave_solicitud: str,
) -> tuple[int, int]:
    """
    Crea o recupera una solicitud WhatsApp y prepara sus fotografías.

    Una solicitud nueva puede incluir fotografías ya enviadas
    en solicitudes anteriores.

    Devuelve (intento_id, cantidad_imagenes).
    No envía mensajes ni consume créditos.
    """

    intento = crear_solicitud_envio_whatsapp(
        codigo_proceso=codigo_proceso,
        destinatario=destinatario,
        clave_solicitud=clave_solicitud,
    )

    if intento.estado == IntentoEntrega.Estado.PENDIENTE:
        cantidad = preparar_imagenes_whatsapp(intento.pk)
    else:
        cantidad = IntentoEntregaImagen.objects.filter(
            intento=intento,
        ).count()

    return intento.pk, cantidad
