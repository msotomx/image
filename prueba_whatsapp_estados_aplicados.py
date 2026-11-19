from web.models import IntentoEntregaImagen

from web.services.whatsapp_estados import (
    es_estado_whatsapp_ya_aplicado,
)


E = IntentoEntregaImagen.Estado

casos = [
    # Notificaciones ya aplicadas.
    ("sent", E.ENVIADA, True),
    ("sent", E.ENTREGADA, True),
    ("sent", E.LEIDA, True),
    ("delivered", E.ENTREGADA, True),
    ("delivered", E.LEIDA, True),
    ("read", E.LEIDA, True),
    ("failed", E.ERROR, True),

    # Notificaciones todavía no aplicadas.
    ("sent", E.ACEPTADA, False),
    ("sent", E.PENDIENTE, False),
    ("delivered", E.ACEPTADA, False),
    ("read", E.ACEPTADA, False),
    ("read", E.ENVIADA, False),
    ("failed", E.ENTREGADA, False),
    ("failed", E.LEIDA, False),

    # Estados terminales incompatibles.
    ("sent", E.ERROR, False),
    ("delivered", E.ERROR, False),
    ("read", E.ERROR, False),

    # Normalización y estados desconocidos.
    (" SENT ", E.ENTREGADA, True),
    ("desconocido", E.LEIDA, False),
]

for estado_meta, estado_imagen, esperado in casos:

    resultado = es_estado_whatsapp_ya_aplicado(
        estado_meta,
        estado_imagen,
    )

    assert resultado is esperado, (
        f"Falló: Meta={estado_meta}, "
        f"Imagen={estado_imagen}, "
        f"Esperado={esperado}, "
        f"Obtenido={resultado}"
    )

print("PRUEBA VERDE - ESTADOS WHATSAPP YA APLICADOS")
print("Casos verificados:", len(casos))
print("Todos los resultados coinciden:", True)
