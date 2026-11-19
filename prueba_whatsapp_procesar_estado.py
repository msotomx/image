from uuid import uuid4

from django.db import transaction

from web.models import (
    EventoWhatsApp,
    IntentoEntrega,
    IntentoEntregaImagen,
)
from web.services.whatsapp_imagenes import (
    preparar_imagenes_whatsapp,
)
from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
)
from web.services.whatsapp_estados import (
    procesar_estado_whatsapp,
)


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-ESTADO-{uuid4().hex}",
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

    # Simular evento de estado recibido de Meta.
    evento = EventoWhatsApp.objects.create(
        clave_evento=uuid4().hex,
        tipo=EventoWhatsApp.Tipo.ESTADO,
        mensaje_id=meta_id,
        telefono="5216561234567",
        estado_mensaje="sent",
        contenido={
            "id": meta_id,
            "status": "sent",
        },
    )

    # Primera ejecución.
    primera = procesar_estado_whatsapp(evento.pk)

    evento.refresh_from_db()
    entrega_imagen.refresh_from_db()

    assert primera is True
    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PROCESADO
    )
    assert evento.fecha_procesamiento is not None
    assert (
        entrega_imagen.estado
        == IntentoEntregaImagen.Estado.ENVIADA
    )

    # Segunda ejecución: no debe procesarlo nuevamente.
    segunda = procesar_estado_whatsapp(evento.pk)

    evento.refresh_from_db()
    entrega_imagen.refresh_from_db()

    assert segunda is False
    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PROCESADO
    )
    assert (
        entrega_imagen.estado
        == IntentoEntregaImagen.Estado.ENVIADA
    )

    print("PRUEBA VERDE - PROCESAMIENTO ESTADO WHATSAPP")
    print("Primera ejecución:", primera)
    print("Segunda ejecución:", segunda)
    print("Estado evento:", evento.estado_procesamiento)
    print("Estado fotografía:", entrega_imagen.estado)

    # Revertir registros temporales.
    transaction.set_rollback(True)
