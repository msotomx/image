from unittest.mock import patch
from uuid import uuid4

from django.db import transaction

from web.models import (
    EventoWhatsApp,
    IntentoEntrega,
    IntentoEntregaImagen,
)
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp
from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
)
from web.services.whatsapp_estados import procesar_estado_whatsapp


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-ROLLBACK-{uuid4().hex}",
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

    evento = EventoWhatsApp.objects.create(
        clave_evento=uuid4().hex,
        tipo=EventoWhatsApp.Tipo.ESTADO,
        mensaje_id=meta_id,
        telefono="5216561234567",
        estado_mensaje="sent",
        contenido={"id": meta_id, "status": "sent"},
    )

    # Simular fallo al confirmar el evento.
    with patch(
        "web.services.whatsapp_estados.confirmar_evento_whatsapp",
        return_value=False,
    ):
        resultado = procesar_estado_whatsapp(evento.pk)

    evento.refresh_from_db()
    entrega_imagen.refresh_from_db()

    assert resultado is False

    # La fotografía debe conservar su estado anterior.
    assert (
        entrega_imagen.estado
        == IntentoEntregaImagen.Estado.ACEPTADA
    )

    # El evento debe registrar el fallo.
    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.ERROR
    )

    assert evento.mensaje_error

    print("PRUEBA VERDE - ROLLBACK WHATSAPP")
    print("Resultado del procesamiento:", resultado)
    print("Estado fotografía:", entrega_imagen.estado)
    print("Estado evento:", evento.estado_procesamiento)
    print("Error registrado:", bool(evento.mensaje_error))

    # Revertir todos los registros temporales.
    transaction.set_rollback(True)
