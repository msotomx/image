from uuid import uuid4

from django.db import transaction

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    liberar_evento_whatsapp,
)


class PruebaFinalizada(Exception):
    pass


try:
    with transaction.atomic():

        identificador = uuid4().hex

        evento = EventoWhatsApp.objects.create(
            clave_evento=f"prueba_liberacion_{identificador}",
            tipo=EventoWhatsApp.Tipo.ESTADO,
            mensaje_id=f"wamid.prueba.{identificador}",
            contenido={},
            estado_procesamiento=(
                EventoWhatsApp.EstadoProcesamiento.PROCESANDO
            ),
            intentos_procesamiento=2,
        )

        # Trabajador antiguo intenta liberar el intento 1.
        resultado = liberar_evento_whatsapp(
            evento.pk,
            numero_intento=1,
        )

        evento.refresh_from_db()

        assert resultado is False
        assert evento.estado_procesamiento == (
            EventoWhatsApp.EstadoProcesamiento.PROCESANDO
        )
        assert evento.intentos_procesamiento == 2
        assert evento.fecha_proximo_intento is None

        print("PRUEBA VERDE - LIBERACION OBSOLETA WHATSAPP")
        print("Intento antiguo rechazado:", True)
        print("Evento permanece PROCESANDO:", True)
        print("Número de intento conservado:", True)
        print("Fecha de reintento sin modificar:", True)

        # Revertir todos los registros temporales.
        raise PruebaFinalizada()


except PruebaFinalizada:
    print("Transacción revertida:", True)
    print("Evento real PK 4 modificado:", False)
