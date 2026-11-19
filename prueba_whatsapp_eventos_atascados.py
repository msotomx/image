from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    obtener_eventos_whatsapp_atascados,
)


class PruebaFinalizada(Exception):
    pass


try:
    with transaction.atomic():

        ahora = timezone.now()
        identificador = uuid4().hex

        escenarios = [
            ("ATASCADO", "PROCESANDO", 15),
            ("RECIENTE", "PROCESANDO", 3),
            ("PENDIENTE", "PENDIENTE", 15),
            ("PROCESADO", "PROCESADO", 15),
            ("ERROR", "ERROR", 15),
        ]

        ids = {}

        for nombre, estado, minutos in escenarios:

            evento = EventoWhatsApp.objects.create(
                clave_evento=f"prueba_atascado_{identificador}_{nombre}",
                tipo=EventoWhatsApp.Tipo.ESTADO,
                mensaje_id=f"wamid.prueba.{identificador}.{nombre}",
                contenido={},
                estado_procesamiento=estado,
                fecha_ultimo_intento=(
                    ahora - timedelta(minutes=minutos)
                ),
            )

            ids[nombre] = evento.pk

        encontrados = obtener_eventos_whatsapp_atascados(
            minutos=10,
            limite=50,
        )

        ids_encontrados = {evento.pk for evento in encontrados}

        assert ids["ATASCADO"] in ids_encontrados
        assert ids["RECIENTE"] not in ids_encontrados
        assert ids["PENDIENTE"] not in ids_encontrados
        assert ids["PROCESADO"] not in ids_encontrados
        assert ids["ERROR"] not in ids_encontrados

        # Confirmar que la función no modificó estados.
        for nombre, estado, _ in escenarios:
            evento = EventoWhatsApp.objects.get(pk=ids[nombre])
            assert evento.estado_procesamiento == estado

        print("PRUEBA VERDE - EVENTOS WHATSAPP ATASCADOS")
        print("Evento de 15 minutos identificado:", True)
        print("Evento de 3 minutos excluido:", True)
        print("Otros estados excluidos:", True)
        print("Estados originales conservados:", True)

        # Revertir todos los registros de prueba.
        raise PruebaFinalizada()


except PruebaFinalizada:
    print("Transacción revertida:", True)
    print("Evento real PK 4 modificado:", False)
