from types import SimpleNamespace
from unittest.mock import patch

from web.models import EventoWhatsApp
from web.services.whatsapp_trabajador import (
    procesar_lote_estados_whatsapp,
)


eventos = [
    SimpleNamespace(
        pk=900001,
        tipo=EventoWhatsApp.Tipo.ESTADO,
    ),
    SimpleNamespace(
        pk=900002,
        tipo=EventoWhatsApp.Tipo.ESTADO,
    ),
    SimpleNamespace(
        pk=900003,
        tipo=EventoWhatsApp.Tipo.ESTADO,
    ),
]


def procesador_simulado(evento_id):

    if evento_id == 900002:
        raise RuntimeError("Error simulado de procesamiento")

    return True


with patch(
    "web.services.whatsapp_trabajador.obtener_eventos_whatsapp_pendientes",
    return_value=eventos,
) as seleccion:

    with patch(
        "web.services.whatsapp_trabajador.procesar_estado_whatsapp",
        side_effect=procesador_simulado,
    ) as procesador:

        resumen = procesar_lote_estados_whatsapp(limite=10)

        assert resumen == {
            "seleccionados": 3,
            "procesados": 2,
            "no_procesados": 1,
        }

        seleccion.assert_called_once_with(
            limite=10,
            tipo=EventoWhatsApp.Tipo.ESTADO,
        )

        ids_procesados = [
            llamada.args[0]
            for llamada in procesador.call_args_list
        ]

        assert ids_procesados == [
            900001,
            900002,
            900003,
        ]

        assert procesador.call_count == 3


print("PRUEBA VERDE - TRABAJADOR EXCEPCIONES WHATSAPP")
print("Eventos seleccionados:", resumen["seleccionados"])
print("Eventos procesados:", resumen["procesados"])
print("Eventos no procesados:", resumen["no_procesados"])
print("Tercer evento procesado:", 900003 in ids_procesados)
print("Procesador real ejecutado:", False)
