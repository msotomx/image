from uuid import uuid4
from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen

from web.services.whatsapp_imagenes import (
    preparar_imagenes_whatsapp,
)

from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
    registrar_imagen_enviada_whatsapp,
    registrar_imagen_entregada_whatsapp,
    registrar_imagen_leida_whatsapp,
    registrar_imagen_fallida_whatsapp,
)


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-ERROR-FINAL-{uuid4().hex}",
    )

    assert preparar_imagenes_whatsapp(intento.pk) > 0

    entrega = IntentoEntregaImagen.objects.filter(
        intento=intento
    ).first()

    intento.estado = IntentoEntrega.Estado.ENVIANDO
    intento.save(update_fields=["estado"])

    assert reclamar_imagen_whatsapp(entrega.pk) is True

    meta_id = f"wamid.PRUEBA.{uuid4().hex}"

    assert confirmar_aceptacion_imagen_whatsapp(
        entrega.pk, meta_id
    ) is True

    # Meta confirma un fallo.
    assert registrar_imagen_fallida_whatsapp(
        meta_id,
        "Meta confirmó fallo definitivo",
    ) is True

    # Llegan notificaciones tardías.
    enviado = registrar_imagen_enviada_whatsapp(meta_id)
    entregado = registrar_imagen_entregada_whatsapp(meta_id)
    leido = registrar_imagen_leida_whatsapp(meta_id)

    entrega.refresh_from_db()

    assert enviado is False
    assert entregado is False
    assert leido is False

    assert entrega.estado == IntentoEntregaImagen.Estado.ERROR
    assert entrega.mensaje_error == "Meta confirmó fallo definitivo"

    print("PRUEBA VERDE - ERROR DEFINITIVO WHATSAPP")
    print("Notificación sent tardía:", enviado)
    print("Notificación delivered tardía:", entregado)
    print("Notificación read tardía:", leido)
    print("Estado final fotografía:", entrega.estado)
    print("Error conservado:", entrega.mensaje_error)

    transaction.set_rollback(True)
