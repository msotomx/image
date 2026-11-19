from uuid import uuid4
from django.db import transaction

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    obtener_eventos_whatsapp_pendientes,
)


with transaction.atomic():

    eventos = {}

    for tipo in (
        EventoWhatsApp.Tipo.MENSAJE,
        EventoWhatsApp.Tipo.ESTADO,
        EventoWhatsApp.Tipo.ESTADO,
    ):
        evento = EventoWhatsApp.objects.create(
            clave_evento=uuid4().hex,
            tipo=tipo,
            mensaje_id=f"wamid.PRUEBA.{uuid4().hex}",
            telefono="5216561234567",
            estado_mensaje="sent" if tipo == EventoWhatsApp.Tipo.ESTADO else "",
            contenido={"status": "sent"} if tipo == EventoWhatsApp.Tipo.ESTADO else {},
        )

        eventos.setdefault(tipo, []).append(evento.pk)

    # Consultar sin modificar eventos.
    seleccionados = obtener_eventos_whatsapp_pendientes(
        limite=100000,
        tipo=EventoWhatsApp.Tipo.ESTADO,
    )

    ids = {evento.pk for evento in seleccionados}

    # Los dos eventos ESTADO temporales deben aparecer.
    for pk in eventos[EventoWhatsApp.Tipo.ESTADO]:
        assert pk in ids

    # El evento MENSAJE temporal debe quedar excluido.
    for pk in eventos[EventoWhatsApp.Tipo.MENSAJE]:
        assert pk not in ids

    # Ningún evento seleccionado debe ser MENSAJE.
    assert all(
        evento.tipo == EventoWhatsApp.Tipo.ESTADO
        for evento in seleccionados
    )

    print("PRUEBA VERDE - FILTRO TIPO WHATSAPP")
    print("Eventos ESTADO temporales seleccionados:", 2)
    print("Eventos MENSAJE temporales excluidos:", 1)
    print("Todos los seleccionados son ESTADO:", True)

    # Revertir los registros temporales.
    transaction.set_rollback(True)
