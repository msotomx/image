from uuid import uuid4

from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp
from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
    registrar_imagen_enviada_whatsapp,
)


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-ENVIADA-{uuid4().hex}",
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

    # Simular aceptación de Meta.
    meta_id = f"wamid.PRUEBA.{uuid4().hex}"

    assert confirmar_aceptacion_imagen_whatsapp(
        entrega_imagen.pk,
        meta_id,
    ) is True

    # Primera notificación sent.
    primera = registrar_imagen_enviada_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert primera is True
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.ENVIADA
    assert entrega_imagen.meta_message_id == meta_id

    # Segunda notificación sent.
    segunda = registrar_imagen_enviada_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert segunda is False
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.ENVIADA

    # Identificador desconocido.
    desconocido = registrar_imagen_enviada_whatsapp(
        f"wamid.DESCONOCIDO.{uuid4().hex}"
    )

    assert desconocido is False

    # Identificador vacío.
    try:
        registrar_imagen_enviada_whatsapp("")
    except ValueError:
        pass
    else:
        raise AssertionError(
            "Se permitió un identificador vacío."
        )

    print("PRUEBA VERDE - ESTADO ENVIADA WHATSAPP")
    print("Primera notificación:", primera)
    print("Segunda notificación:", segunda)
    print("ID desconocido:", desconocido)
    print("Estado:", entrega_imagen.estado)

    # Revertir registros temporales.
    transaction.set_rollback(True)
