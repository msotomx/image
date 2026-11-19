from uuid import uuid4

from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp
from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
)


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-ACEPTACION-{uuid4().hex}",
    )

    cantidad = preparar_imagenes_whatsapp(intento.pk)
    assert cantidad > 0

    entrega_imagen = IntentoEntregaImagen.objects.filter(
        intento=intento
    ).first()

    # Iniciar intento.
    intento.estado = IntentoEntrega.Estado.ENVIANDO
    intento.save(update_fields=["estado"])

    # Reclamar fotografía.
    assert reclamar_imagen_whatsapp(entrega_imagen.pk) is True

    # Confirmar aceptación de Meta.
    meta_id = f"wamid.PRUEBA.{uuid4().hex}"

    primera = confirmar_aceptacion_imagen_whatsapp(
        entrega_imagen.pk,
        meta_id,
    )

    entrega_imagen.refresh_from_db()

    assert primera is True
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.ACEPTADA
    assert entrega_imagen.meta_message_id == meta_id
    assert entrega_imagen.fecha_aceptacion is not None

    fecha_original = entrega_imagen.fecha_aceptacion

    # Intentar sobrescribir el identificador.
    segunda = confirmar_aceptacion_imagen_whatsapp(
        entrega_imagen.pk,
        "wamid.NO_DEBE_REEMPLAZAR",
    )

    entrega_imagen.refresh_from_db()

    assert segunda is False
    assert entrega_imagen.meta_message_id == meta_id
    assert entrega_imagen.fecha_aceptacion == fecha_original

    # Identificador vacío: debe rechazarse.
    try:
        confirmar_aceptacion_imagen_whatsapp(
            entrega_imagen.pk,
            "",
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "Se permitió un identificador vacío."
        )

    print("PRUEBA VERDE - ACEPTACION META WHATSAPP")
    print("Primera confirmación:", primera)
    print("Segunda confirmación:", segunda)
    print("Estado:", entrega_imagen.estado)
    print("Meta ID conservado:", entrega_imagen.meta_message_id == meta_id)

    # Revertir registros temporales.
    transaction.set_rollback(True)
