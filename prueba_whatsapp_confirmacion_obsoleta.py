from uuid import uuid4

from django.db import transaction

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    confirmar_evento_whatsapp,
    registrar_error_evento_whatsapp,
)


class PruebaFinalizada(Exception):
    pass


try:
    with transaction.atomic():

        identificador = uuid4().hex

        evento = EventoWhatsApp.objects.create(
            clave_evento=f"prueba_confirmacion_{identificador}",
            tipo=EventoWhatsApp.Tipo.ESTADO,
            mensaje_id=f"wamid.prueba.{identificador}",
            contenido={},
            estado_procesamiento=(
                EventoWhatsApp.EstadoProcesamiento.PROCESANDO
            ),
            intentos_procesamiento=2,
        )

        # Trabajador antiguo intenta confirmar el intento 1.
        confirmado = confirmar_evento_whatsapp(
            evento.pk,
            numero_intento=1,
        )

        # Trabajador antiguo intenta registrar un error.
        error_registrado = registrar_error_evento_whatsapp(
            evento.pk,
            "Error simulado de trabajador antiguo",
            numero_intento=1,
        )

        evento.refresh_from_db()

        assert confirmado is False
        assert error_registrado is False

        assert evento.estado_procesamiento == (
            EventoWhatsApp.EstadoProcesamiento.PROCESANDO
        )

        assert evento.intentos_procesamiento == 2
        assert evento.fecha_procesamiento is None
        assert not evento.mensaje_error

        print("PRUEBA VERDE - CONFIRMACION OBSOLETA WHATSAPP")
        print("Confirmación antigua rechazada:", True)
        print("Registro de error antiguo rechazado:", True)
        print("Evento permanece PROCESANDO:", True)
        print("Número de intento conservado:", True)
        print("Mensaje de error sin modificar:", True)

        # Revertir los datos temporales.
        raise PruebaFinalizada()


except PruebaFinalizada:
    print("Transacción revertida:", True)
    print("Evento real PK 4 modificado:", False)
