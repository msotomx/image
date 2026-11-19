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

    for numero in range(1, 6):

        # Reclamar el evento.
        assert reclamar_evento_whatsapp(evento.pk) is True

        evento.refresh_from_db()
        assert evento.intentos_procesamiento == numero

        antes = timezone.now()

        # Liberar o finalizar el evento.
        assert liberar_evento_whatsapp(evento.pk) is True

        despues = timezone.now()
        evento.refresh_from_db()

        if numero < 5:

            assert (
                evento.estado_procesamiento
                == EventoWhatsApp.EstadoProcesamiento.PENDIENTE
            )

            assert evento.fecha_proximo_intento is not None

            assert (
                antes + timedelta(seconds=60)
                <= evento.fecha_proximo_intento
                <= despues + timedelta(seconds=60)
            )

            # No debe poder reclamarse durante la espera.
            assert reclamar_evento_whatsapp(evento.pk) is False

            # Simular que transcurrieron los 60 segundos.
            EventoWhatsApp.objects.filter(pk=evento.pk).update(
                fecha_proximo_intento=(
                    timezone.now() - timedelta(seconds=1)
                )
            )

        else:

            assert (
                evento.estado_procesamiento
                == EventoWhatsApp.EstadoProcesamiento.ERROR
            )

            assert evento.fecha_proximo_intento is None
            assert "5 intentos" in evento.mensaje_error

    # El sexto intento debe estar bloqueado.
    sexto = reclamar_evento_whatsapp(evento.pk)

    evento.refresh_from_db()

    assert sexto is False
    assert evento.intentos_procesamiento == 5

    # No debe ser posible liberar nuevamente un evento ERROR.
    assert liberar_evento_whatsapp(evento.pk) is False

    print("PRUEBA VERDE - REINTENTOS WHATSAPP")
    print("Intentos registrados:", evento.intentos_procesamiento)
    print("Estado final:", evento.estado_procesamiento)
    print("Próximo intento:", evento.fecha_proximo_intento)
    print("Sexto intento permitido:", sexto)
    print("Error registrado:", evento.mensaje_error)

    # Revertir todos los registros temporales.
    transaction.set_rollback(True)
