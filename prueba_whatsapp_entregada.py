from uuid import uuid4

from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp
from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
    registrar_imagen_enviada_whatsapp,
    registrar_imagen_entregada_whatsapp,
)


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"


with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-ENTREGADA-{uuid4().hex}",
    )

    cantidad = preparar_imagenes_whatsapp(intento.pk)
    assert cantidad > 0

    entrega_imagen = IntentoEntregaImagen.objects.filter(
        intento=intento
    ).first()

    intento.estado = IntentoEntrega.Estado.ENVIANDO
    intento.save(update_fields=["estado"])

    assert reclamar_imagen_whatsapp(entrega_imagen.pk) is True

    meta_id = f"wamid.PRUEBA.{uuid4().hex}"

    assert confirmar_aceptacion_imagen_whatsapp(
        entrega_imagen.pk,
        meta_id,
    ) is True

    # Caso 1: ENVIADA -> ENTREGADA
    assert registrar_imagen_enviada_whatsapp(meta_id) is True

    primera = registrar_imagen_entregada_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert primera is True
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.ENTREGADA
    assert entrega_imagen.fecha_entrega is not None

    fecha_original = entrega_imagen.fecha_entrega

    # Caso 2: notificación delivered duplicada
    segunda = registrar_imagen_entregada_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert segunda is False
    assert entrega_imagen.fecha_entrega == fecha_original

    # Caso 3: notificación sent tardía
    tardia = registrar_imagen_enviada_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert tardia is False
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.ENTREGADA

    # Caso 4: delivered llega antes de sent
    entrega_imagen.estado = IntentoEntregaImagen.Estado.ACEPTADA
    entrega_imagen.fecha_entrega = None
    entrega_imagen.save(
        update_fields=["estado", "fecha_entrega"]
    )

    fuera_orden = registrar_imagen_entregada_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert fuera_orden is True
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.ENTREGADA
    assert entrega_imagen.fecha_entrega is not None

    print("PRUEBA VERDE - ESTADO ENTREGADA WHATSAPP")
    print("Primera notificación:", primera)
    print("Notificación duplicada:", segunda)
    print("Notificación sent tardía:", tardia)
    print("Delivered fuera de orden:", fuera_orden)
    print("Estado final:", entrega_imagen.estado)

    # Revertir todos los cambios temporales.
    transaction.set_rollback(True)
