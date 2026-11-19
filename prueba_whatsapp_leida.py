from uuid import uuid4

from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp
from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
    registrar_imagen_enviada_whatsapp,
    registrar_imagen_entregada_whatsapp,
    registrar_imagen_leida_whatsapp,
)


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"


with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-LEIDA-{uuid4().hex}",
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

    # Caso 1: flujo normal.
    assert registrar_imagen_enviada_whatsapp(meta_id) is True
    assert registrar_imagen_entregada_whatsapp(meta_id) is True

    primera = registrar_imagen_leida_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert primera is True
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.LEIDA
    assert entrega_imagen.fecha_lectura is not None

    fecha_original = entrega_imagen.fecha_lectura

    # Caso 2: notificación read duplicada.
    segunda = registrar_imagen_leida_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert segunda is False
    assert entrega_imagen.fecha_lectura == fecha_original

    # Caso 3: delivered llega después de read.
    entregada_tardia = registrar_imagen_entregada_whatsapp(
        meta_id
    )

    # Caso 4: sent llega después de read.
    enviada_tardia = registrar_imagen_enviada_whatsapp(
        meta_id
    )

    entrega_imagen.refresh_from_db()

    assert entregada_tardia is False
    assert enviada_tardia is False
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.LEIDA

    # Caso 5: read llega directamente después de ACEPTADA.
    # Simulación aislada dentro de la transacción de prueba.
    entrega_imagen.estado = IntentoEntregaImagen.Estado.ACEPTADA
    entrega_imagen.fecha_lectura = None
    entrega_imagen.save(
        update_fields=["estado", "fecha_lectura"]
    )

    fuera_orden = registrar_imagen_leida_whatsapp(meta_id)

    entrega_imagen.refresh_from_db()

    assert fuera_orden is True
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.LEIDA
    assert entrega_imagen.fecha_lectura is not None

    print("PRUEBA VERDE - ESTADO LEIDA WHATSAPP")
    print("Primera notificación:", primera)
    print("Notificación duplicada:", segunda)
    print("Delivered tardía:", entregada_tardia)
    print("Sent tardía:", enviada_tardia)
    print("Read fuera de orden:", fuera_orden)
    print("Estado final:", entrega_imagen.estado)

    # Revertir todos los cambios temporales.
    transaction.set_rollback(True)
