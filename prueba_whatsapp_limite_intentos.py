from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from web.models import EventoWhatsApp

from web.services.whatsapp_procesamiento import (
    reclamar_evento_whatsapp,
    liberar_evento_whatsapp,
)


with transaction.atomic():

    evento = EventoWhatsApp.objects.create(
        clave_evento=uuid4().hex,
        tipo=EventoWhatsApp.Tipo.ESTADO,
        mensaje_id=f"wamid.PRUEBA.{uuid4().hex}",
        telefono="5216561234567",
        estado_mensaje="sent",
        contenido={"status": "sent"},
    )

    # CASO 1: Primer reclamo.
    assert reclamar_evento_whatsapp(evento.pk) is True

    evento.refresh_from_db()

    assert evento.intentos_procesamiento == 1
    assert evento.fecha_ultimo_intento is not None
    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PROCESANDO
    )

    # CASO 2: No reclamar dos veces simultáneamente.
    assert reclamar_evento_whatsapp(evento.pk) is False

    evento.refresh_from_db()
    assert evento.intentos_procesamiento == 1

    # Liberar y establecer una espera futura.
    assert liberar_evento_whatsapp(evento.pk) is True

    fecha_futura = timezone.now() + timedelta(seconds=60)

    EventoWhatsApp.objects.filter(pk=evento.pk).update(
        fecha_proximo_intento=fecha_futura,
    )

    # CASO 3: Todavía no corresponde procesarlo.
    assert reclamar_evento_whatsapp(evento.pk) is False

    evento.refresh_from_db()
    assert evento.intentos_procesamiento == 1

    # Simular que terminó el período de espera.
    EventoWhatsApp.objects.filter(pk=evento.pk).update(
        fecha_proximo_intento=timezone.now() - timedelta(seconds=1),
    )

    assert reclamar_evento_whatsapp(evento.pk) is True

    evento.refresh_from_db()
    assert evento.intentos_procesamiento == 2

    # CASO 4: Llegar al quinto intento.
    for numero in (3, 4, 5):

        assert liberar_evento_whatsapp(evento.pk) is True

        assert reclamar_evento_whatsapp(evento.pk) is True

        evento.refresh_from_db()
        assert evento.intentos_procesamiento == numero

    # Liberar el quinto intento.
    assert liberar_evento_whatsapp(evento.pk) is True

    # El sexto intento debe rechazarse.
    sexto = reclamar_evento_whatsapp(evento.pk)

    evento.refresh_from_db()

    assert sexto is False
    assert evento.intentos_procesamiento == 5
    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PENDIENTE
    )

    print("PRUEBA VERDE - LIMITE INTENTOS WHATSAPP")
    print("Intentos registrados:", evento.intentos_procesamiento)
    print("Sexto intento permitido:", sexto)
    print("Estado final:", evento.estado_procesamiento)
    print("Fecha último intento registrada:", bool(evento.fecha_ultimo_intento))

    # Revertir todos los cambios temporales.
    transaction.set_rollback(True)
