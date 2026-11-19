from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.entrega import obtener_imagenes_para_entrega
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp
from web.services.whatsapp_envios import reclamar_imagen_whatsapp


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

imagenes = obtener_imagenes_para_entrega(codigo_proceso)

assert len(imagenes) > 0, "No hay fotografías disponibles."

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-RECLAMACION-{uuid4().hex}",
    )

    # Preparar fotografías.
    cantidad = preparar_imagenes_whatsapp(intento.pk)

    assert cantidad > 0

    entrega_imagen = IntentoEntregaImagen.objects.filter(
        intento=intento
    ).first()

    # El intento todavía está PENDIENTE.
    assert reclamar_imagen_whatsapp(entrega_imagen.pk) is False

    # Simular inicio del intento de entrega.
    intento.estado = IntentoEntrega.Estado.ENVIANDO
    intento.save(update_fields=["estado"])

    # Primera reclamación.
    primera = reclamar_imagen_whatsapp(entrega_imagen.pk)

    entrega_imagen.refresh_from_db()

    assert primera is True
    assert entrega_imagen.estado == IntentoEntregaImagen.Estado.ENVIANDO
    assert entrega_imagen.fecha_inicio_envio is not None
    assert entrega_imagen.fecha_inicio_envio <= timezone.now()

    fecha_original = entrega_imagen.fecha_inicio_envio

    # Segunda reclamación: debe rechazarse.
    segunda = reclamar_imagen_whatsapp(entrega_imagen.pk)

    entrega_imagen.refresh_from_db()

    assert segunda is False
    assert entrega_imagen.fecha_inicio_envio == fecha_original

    print("PRUEBA VERDE - RECLAMACION INDIVIDUAL WHATSAPP")
    print("Primera reclamación:", primera)
    print("Segunda reclamación:", segunda)
    print("Estado:", entrega_imagen.estado)

    # Revertir todos los cambios temporales.
    transaction.set_rollback(True)
