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
        clave_idempotencia=f"PRUEBA-WA-EVENTO-FAILED-{uuid4().hex}",
    )

    cantidad = preparar_imagenes_whatsapp(intento.pk)
    assert cantidad > 0

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

    # Simular webhook failed de Meta.
    evento = EventoWhatsApp.objects.create(
        clave_evento=uuid4().hex,
        tipo=EventoWhatsApp.Tipo.ESTADO,
        mensaje_id=meta_id,
        telefono="5216561234567",
        estado_mensaje="failed",
        contenido={
            "id": meta_id,
            "status": "failed",
            "errors": [
                {
                    "code": 131026,
                    "title": "Message undeliverable",
                    "error_data": {
                        "details": "Recipient phone number unavailable"
                    },
                }
            ],
        },
    )

    # Primera ejecución.
    primera = procesar_estado_whatsapp(evento.pk)

    evento.refresh_from_db()
    entrega.refresh_from_db()

    assert primera is True

    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PROCESADO
    )

    assert entrega.estado == IntentoEntregaImagen.Estado.ERROR

    error_original = entrega.mensaje_error

    assert "131026" in error_original
    assert "Message undeliverable" in error_original
    assert "Recipient phone number unavailable" in error_original

    # Segunda ejecución: no debe reprocesarse.
    segunda = procesar_estado_whatsapp(evento.pk)

    evento.refresh_from_db()
    entrega.refresh_from_db()

    assert segunda is False
    assert entrega.mensaje_error == error_original
    assert entrega.estado == IntentoEntregaImagen.Estado.ERROR

    print("PRUEBA VERDE - EVENTO FAILED WHATSAPP")
    print("Primera ejecución:", primera)
    print("Segunda ejecución:", segunda)
    print("Estado evento:", evento.estado_procesamiento)
    print("Estado fotografía:", entrega.estado)
    print("Error registrado:", entrega.mensaje_error)

    # Revertir todos los registros temporales.
    transaction.set_rollback(True)
