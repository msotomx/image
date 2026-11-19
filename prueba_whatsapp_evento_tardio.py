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
        clave_idempotencia=f"PRUEBA-WA-TARDIO-{uuid4().hex}",
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

    # La fotografía ya fue entregada.
    assert registrar_imagen_entregada_whatsapp(
        meta_id
    ) is True

    entrega.refresh_from_db()

    fecha_entrega_original = entrega.fecha_entrega

    # Meta envía posteriormente una notificación sent.
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

    primera = procesar_estado_whatsapp(evento.pk)

    evento.refresh_from_db()
    entrega.refresh_from_db()

    assert primera is True

    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PROCESADO
    )

    assert (
        entrega.estado
        == IntentoEntregaImagen.Estado.ENTREGADA
    )

    assert entrega.fecha_entrega == fecha_entrega_original

    # No debe procesarse dos veces.
    segunda = procesar_estado_whatsapp(evento.pk)

    assert segunda is False

    entrega.refresh_from_db()

    assert (
        entrega.estado
        == IntentoEntregaImagen.Estado.ENTREGADA
    )

    print("PRUEBA VERDE - EVENTO TARDIO WHATSAPP")
    print("Primera ejecución:", primera)
    print("Segunda ejecución:", segunda)
    print("Estado evento:", evento.estado_procesamiento)
    print("Estado fotografía:", entrega.estado)
    print(
        "Fecha entrega conservada:",
        entrega.fecha_entrega == fecha_entrega_original,
    )

    transaction.set_rollback(True)
