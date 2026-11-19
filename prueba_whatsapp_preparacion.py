# Archivo Temporal - borrar
from uuid import uuid4
from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.entrega import obtener_imagenes_para_entrega
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

imagenes = obtener_imagenes_para_entrega(codigo_proceso)

assert len(imagenes) > 0, "No hay fotografías disponibles."

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-PREPARACION-{uuid4().hex}",
    )

    primera = preparar_imagenes_whatsapp(intento.pk)

    assert primera == len(imagenes)

    segunda = preparar_imagenes_whatsapp(intento.pk)

    assert segunda == primera

    registros = IntentoEntregaImagen.objects.filter(
        intento=intento,
    )

    assert registros.count() == len(imagenes)

    assert all(
        registro.estado == IntentoEntregaImagen.Estado.PENDIENTE
        for registro in registros
    )

    print(
        "PRUEBA VERDE - PREPARACION WHATSAPP VALIDADA",
        "Fotografías:", registros.count(),
    )

    transaction.set_rollback(True)
