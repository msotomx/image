from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    recuperar_evento_whatsapp_atascado,
    reclamar_evento_whatsapp,
    confirmar_evento_whatsapp,
    registrar_error_evento_whatsapp,
    liberar_evento_whatsapp,
)


class PruebaFinalizada(Exception):
    pass


try:
    with transaction.atomic():
        identificador = uuid4().hex

        evento = EventoWhatsApp.objects.create(
            clave_evento=f"prueba_generacion_{identificador}",
            tipo=EventoWhatsApp.Tipo.ESTADO,
            mensaje_id=f"wamid.prueba.{identificador}",
            contenido={},
            estado_procesamiento=(
                EventoWhatsApp.EstadoProcesamiento.PROCESANDO
            ),
            intentos_procesamiento=1,
            fecha_ultimo_intento=(
                timezone.now() - timedelta(minutes=15)
            ),
        )

        # Recuperar el intento 1.
        recuperado = recuperar_evento_whatsapp_atascado(
            evento.pk,
            minutos=10,
        )
        assert recuperado is True

        # La recuperación establece una espera de 60 segundos.
        # Adelantamos únicamente el evento temporal para la prueba.
        EventoWhatsApp.objects.filter(pk=evento.pk).update(
            fecha_proximo_intento=timezone.now() - timedelta(seconds=1)
        )

        # Un nuevo trabajador reclama el intento 2.
        numero_intento = reclamar_evento_whatsapp(evento.pk)
        assert numero_intento == 2

        # El trabajador antiguo intenta modificar el evento.
        confirmado = confirmar_evento_whatsapp(
            evento.pk, numero_intento=1
        )

        error_registrado = registrar_error_evento_whatsapp(
            evento.pk,
            "Error del trabajador antiguo",
            numero_intento=1,
        )

        liberado = liberar_evento_whatsapp(
            evento.pk, numero_intento=1
        )

        evento.refresh_from_db()

        assert confirmado is False
        assert error_registrado is False
        assert liberado is False

        assert evento.estado_procesamiento == (
            EventoWhatsApp.EstadoProcesamiento.PROCESANDO
        )
        assert evento.intentos_procesamiento == 2
        assert not evento.mensaje_error

        print("PRUEBA VERDE - RECUPERACION POR GENERACION WHATSAPP")
        print("Evento atascado recuperado:", recuperado)
        print("Nuevo intento reclamado:", numero_intento)
        print("Confirmación obsoleta rechazada:", not confirmado)
        print("Error obsoleto rechazado:", not error_registrado)
        print("Liberación obsoleta rechazada:", not liberado)
        print("Intento vigente conservado:", True)

        raise PruebaFinalizada()


except PruebaFinalizada:
    print("Transacción revertida:", True)
    print("Evento real PK 4 modificado:", False)
