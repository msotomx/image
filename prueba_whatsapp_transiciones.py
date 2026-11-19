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
    registrar_imagen_entregada_whatsapp,
    registrar_imagen_fallida_whatsapp,
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
        clave_idempotencia=f"PRUEBA-WA-TRANSICIONES-{uuid4().hex}",
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

    # CASO 1: Notificación sent tardía.
    assert registrar_imagen_entregada_whatsapp(meta_id) is True

    entrega.refresh_from_db()
    fecha_entrega_original = entrega.fecha_entrega

    evento_sent = EventoWhatsApp.objects.create(
        clave_evento=uuid4().hex,
        tipo=EventoWhatsApp.Tipo.ESTADO,
        mensaje_id=meta_id,
        telefono="5216561234567",
        estado_mensaje="sent",
        contenido={"id": meta_id, "status": "sent"},
    )

    resultado_sent = procesar_estado_whatsapp(evento_sent.pk)

    evento_sent.refresh_from_db()
    entrega.refresh_from_db()

    assert resultado_sent is True
    assert (
        evento_sent.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PROCESADO
    )
    assert entrega.estado == IntentoEntregaImagen.Estado.ENTREGADA
    assert entrega.fecha_entrega == fecha_entrega_original

    print("CASO 1 - Notificación tardía")
    print("Resultado:", resultado_sent)
    print("Estado evento:", evento_sent.estado_procesamiento)
    print("Estado fotografía:", entrega.estado)

    # CASO 2: Notificación read incompatible.
    # Usamos el mismo registro temporal para simular
    # un fallo confirmado de Meta.

    entrega.estado = IntentoEntregaImagen.Estado.ACEPTADA
    entrega.save(update_fields=["estado"])

    assert registrar_imagen_fallida_whatsapp(
        meta_id,
        "Fallo definitivo confirmado por Meta",
    ) is True

    evento_read = EventoWhatsApp.objects.create(
        clave_evento=uuid4().hex,
        tipo=EventoWhatsApp.Tipo.ESTADO,
        mensaje_id=meta_id,
        telefono="5216561234567",
        estado_mensaje="read",
        contenido={"id": meta_id, "status": "read"},
    )

    resultado_read = procesar_estado_whatsapp(evento_read.pk)

    evento_read.refresh_from_db()
    entrega.refresh_from_db()

    assert resultado_read is False
    assert (
        evento_read.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.ERROR
    )
    assert "Transición WhatsApp incompatible" in evento_read.mensaje_error

    assert entrega.estado == IntentoEntregaImagen.Estado.ERROR
    assert (
        entrega.mensaje_error
        == "Fallo definitivo confirmado por Meta"
    )

    print()
    print("CASO 2 - Transición incompatible")
    print("Resultado:", resultado_read)
    print("Estado evento:", evento_read.estado_procesamiento)
    print("Estado fotografía:", entrega.estado)
    print("Inconsistencia registrada:", bool(evento_read.mensaje_error))

    print()
    print("PRUEBA VERDE - TRANSICIONES WHATSAPP")

    transaction.set_rollback(True)
