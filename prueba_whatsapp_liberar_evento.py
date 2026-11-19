from uuid import uuid4

from django.db import transaction

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

    # Caso 1: no puede liberarse si está PENDIENTE.
    inicial = liberar_evento_whatsapp(evento.pk)
    assert inicial is False

    # Reclamar evento.
    reclamado = reclamar_evento_whatsapp(evento.pk)
    assert reclamado is True

    evento.refresh_from_db()
    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PROCESANDO
    )

    # Caso 2: liberar el evento.
    primera = liberar_evento_whatsapp(evento.pk)
    assert primera is True

    evento.refresh_from_db()

    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PENDIENTE
    )
    assert evento.fecha_procesamiento is None
    assert evento.mensaje_error == ""

    # Caso 3: segunda liberación.
    segunda = liberar_evento_whatsapp(evento.pk)
    assert segunda is False

    evento.refresh_from_db()

    assert (
        evento.estado_procesamiento
        == EventoWhatsApp.EstadoProcesamiento.PENDIENTE
    )

    print("PRUEBA VERDE - LIBERACION EVENTO WHATSAPP")
    print("Liberación inicial:", inicial)
    print("Evento reclamado:", reclamado)
    print("Primera liberación:", primera)
    print("Segunda liberación:", segunda)
    print("Estado final:", evento.estado_procesamiento)

    # Revertir el evento temporal.
    transaction.set_rollback(True)
