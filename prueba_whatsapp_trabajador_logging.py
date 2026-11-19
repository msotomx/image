import logging

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from web.models import EventoWhatsApp
from web.services.whatsapp_trabajador import (
    procesar_lote_estados_whatsapp,
)


eventos = [
    SimpleNamespace(pk=900001, tipo=EventoWhatsApp.Tipo.ESTADO),
    SimpleNamespace(pk=900002, tipo=EventoWhatsApp.Tipo.ESTADO),
    SimpleNamespace(pk=900003, tipo=EventoWhatsApp.Tipo.ESTADO),
]


def procesador_simulado(evento_id):
    if evento_id == 900002:
        raise RuntimeError("Error simulado de logging")
    return True


# Capturar el log en memoria, sin escribir archivos.
salida_log = StringIO()
handler = logging.StreamHandler(salida_log)
handler.setFormatter(
    logging.Formatter("%(levelname)s: %(message)s")
)

logger = logging.getLogger(
    "web.services.whatsapp_trabajador"
)

nivel_anterior = logger.level
logger.addHandler(handler)
logger.setLevel(logging.ERROR)

try:
    with patch(
        "web.services.whatsapp_trabajador.obtener_eventos_whatsapp_pendientes",
        return_value=eventos,
    ):
        with patch(
            "web.services.whatsapp_trabajador.procesar_estado_whatsapp",
            side_effect=procesador_simulado,
        ) as procesador:

            resumen = procesar_lote_estados_whatsapp(limite=10)

            assert procesador.call_count == 3

    contenido_log = salida_log.getvalue()

    assert "900002" in contenido_log
    assert "Error simulado de logging" in contenido_log
    assert "Traceback" in contenido_log

    assert resumen == {
        "seleccionados": 3,
        "procesados": 2,
        "no_procesados": 1,
    }

    print("PRUEBA VERDE - LOGGING TRABAJADOR WHATSAPP")
    print("Eventos seleccionados:", resumen["seleccionados"])
    print("Eventos procesados:", resumen["procesados"])
    print("Eventos no procesados:", resumen["no_procesados"])
    print("ID del evento registrado:", "900002" in contenido_log)
    print("Traceback registrado:", "Traceback" in contenido_log)
    print("Tercer evento procesado:", True)

finally:
    logger.removeHandler(handler)
    logger.setLevel(nivel_anterior)
    handler.close()
