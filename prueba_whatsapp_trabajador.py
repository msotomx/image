from types import SimpleNamespace
from unittest.mock import patch

from web.models import EventoWhatsApp
from web.services.whatsapp_trabajador import (
    procesar_lote_estados_whatsapp,
)


# Eventos simulados: no existen en la base de datos.
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

# Simular dos procesamientos exitosos y uno no procesado.
resultados = {
    900001: True,
    900002: False,
    900003: True,
}

with patch(
    "web.services.whatsapp_trabajador.obtener_eventos_whatsapp_pendientes",
    return_value=eventos,
) as seleccion:

    with patch(
        "web.services.whatsapp_trabajador.procesar_estado_whatsapp",
        side_effect=lambda pk: resultados[pk],
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

        assert procesador.call_count == 3

        procesados_ids = [
            llamada.args[0]
            for llamada in procesador.call_args_list
        ]

        assert procesados_ids == [900001, 900002, 900003]

# Verificar que un límite inválido sea rechazado.
try:
    procesar_lote_estados_whatsapp(limite=0)
except ValueError:
    limite_rechazado = True
else:
    limite_rechazado = False

assert limite_rechazado is True

print("PRUEBA VERDE - TRABAJADOR WHATSAPP")
print("Eventos seleccionados:", resumen["seleccionados"])
print("Eventos procesados:", resumen["procesados"])
print("Eventos no procesados:", resumen["no_procesados"])
print("Límite inválido rechazado:", limite_rechazado)
print("Procesador real ejecutado:", False)
