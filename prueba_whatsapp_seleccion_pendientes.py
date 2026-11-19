from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    obtener_eventos_whatsapp_pendientes,
)


E = EventoWhatsApp.EstadoProcesamiento

with transaction.atomic():

    ahora = timezone.now()
    eventos = {}

    casos = [
        ("disponible", E.PENDIENTE, 0, None),
        ("espera_cumplida", E.PENDIENTE, 2, ahora - timedelta(seconds=1)),
        ("espera_futura", E.PENDIENTE, 1, ahora + timedelta(minutes=5)),
        ("agotado", E.PENDIENTE, 5, None),
        ("procesando", E.PROCESANDO, 1, None),
        ("procesado", E.PROCESADO, 1, None),
        ("error", E.ERROR, 1, None),
    ]

    for nombre, estado, intentos, proximo in casos:

        evento = EventoWhatsApp.objects.create(
            clave_evento=uuid4().hex,
            tipo=EventoWhatsApp.Tipo.ESTADO,
            mensaje_id=f"wamid.PRUEBA.{uuid4().hex}",
            telefono="5216561234567",
            estado_mensaje="sent",
            contenido={"status": "sent"},
            estado_procesamiento=estado,
            intentos_procesamiento=intentos,
            fecha_proximo_intento=proximo,
        )

        eventos[nombre] = evento.pk

    # Obtener únicamente nuestros eventos temporales
    # de la selección general.
    resultados = obtener_eventos_whatsapp_pendientes(
        limite=100000
    )

    ids = {evento.pk for evento in resultados}

    assert eventos["disponible"] in ids
    assert eventos["espera_cumplida"] in ids

    for nombre in (
        "espera_futura",
        "agotado",
        "procesando",
        "procesado",
        "error",
    ):
        assert eventos[nombre] not in ids, nombre

    # Comprobar que el límite solicitado se respeta.
    limitados = obtener_eventos_whatsapp_pendientes(
        limite=1
    )

    assert len(limitados) <= 1

    # Comprobar validación del límite.
    try:
        obtener_eventos_whatsapp_pendientes(limite=0)
    except ValueError:
        limite_invalido_rechazado = True
    else:
        limite_invalido_rechazado = False

    assert limite_invalido_rechazado is True

    print("PRUEBA VERDE - SELECCION PENDIENTES WHATSAPP")
    print("Eventos temporales creados:", len(eventos))
    print("Eventos disponibles detectados:", 2)
    print("Eventos excluidos correctamente:", 5)
    print("Límite de resultados respetado:", True)
    print("Límite inválido rechazado:", limite_invalido_rechazado)

    # Revertir todos los eventos temporales.
    transaction.set_rollback(True)
