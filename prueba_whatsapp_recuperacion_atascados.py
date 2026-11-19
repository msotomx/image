from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    recuperar_evento_whatsapp_atascado,
)


class PruebaFinalizada(Exception):
    pass


try:
    with transaction.atomic():

        ahora = timezone.now()
        identificador = uuid4().hex

        escenarios = [
            ("RECUPERABLE", "PROCESANDO", 15, 2),
            ("AGOTADO", "PROCESANDO", 15, 5),
            ("RECIENTE", "PROCESANDO", 3, 2),
            ("PROCESADO", "PROCESADO", 15, 2),
        ]

        ids = {}

        for nombre, estado, minutos, intentos in escenarios:

            evento = EventoWhatsApp.objects.create(
                clave_evento=f"prueba_recuperacion_{identificador}_{nombre}",
                tipo=EventoWhatsApp.Tipo.ESTADO,
                mensaje_id=f"wamid.prueba.{identificador}.{nombre}",
                contenido={},
                estado_procesamiento=estado,
                intentos_procesamiento=intentos,
                fecha_ultimo_intento=ahora - timedelta(minutes=minutos),
            )

            ids[nombre] = evento.pk

        resultados = {}

        for nombre, _, _, _ in escenarios:
            resultados[nombre] = recuperar_evento_whatsapp_atascado(
                ids[nombre],
                minutos=10,
            )

        estados = {
            nombre: EventoWhatsApp.objects.get(pk=pk)
            for nombre, pk in ids.items()
        }

        assert resultados["RECUPERABLE"] is True
        assert estados["RECUPERABLE"].estado_procesamiento == "PENDIENTE"
        assert estados["RECUPERABLE"].intentos_procesamiento == 2
        assert estados["RECUPERABLE"].fecha_proximo_intento is not None

        assert resultados["AGOTADO"] is True
        assert estados["AGOTADO"].estado_procesamiento == "ERROR"
        assert estados["AGOTADO"].intentos_procesamiento == 5
        assert estados["AGOTADO"].fecha_proximo_intento is None

        assert resultados["RECIENTE"] is False
        assert estados["RECIENTE"].estado_procesamiento == "PROCESANDO"

        assert resultados["PROCESADO"] is False
        assert estados["PROCESADO"].estado_procesamiento == "PROCESADO"

        print("PRUEBA VERDE - RECUPERACION WHATSAPP")
        print("Evento recuperable vuelve a PENDIENTE:", True)
        print("Evento con 5 intentos pasa a ERROR:", True)
        print("Evento reciente permanece PROCESANDO:", True)
        print("Evento PROCESADO permanece intacto:", True)
        print("Intentos conservados:", True)

        # Deshacer todos los cambios de la prueba.
        raise PruebaFinalizada()


except PruebaFinalizada:
    print("Transacción revertida:", True)
    print("Evento real PK 4 modificado:", False)
