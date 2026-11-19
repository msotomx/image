from uuid import uuid4

from django.db import IntegrityError, transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.entrega import obtener_imagenes_para_entrega


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

imagenes = obtener_imagenes_para_entrega(codigo_proceso)

assert len(imagenes) > 0, "No hay fotografías disponibles."

imagen = imagenes[0]

with transaction.atomic():

    # Crear dos intentos temporales.
    intentos = []

    for _ in range(2):
        intento = IntentoEntrega.objects.create(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.WHATSAPP,
            destinatario="5216561234567",
            clave_idempotencia=f"PRUEBA-WA-UNIQUE-{uuid4().hex}",
        )
        intentos.append(intento)

    # Crear dos registros con Meta ID NULL.
    registros = [
        IntentoEntregaImagen.objects.create(
            intento=intento,
            imagen=imagen,
        )
        for intento in intentos
    ]

    assert all(
        registro.meta_message_id is None
        for registro in registros
    )

    # Asignar un identificador al primero.
    meta_id = f"wamid.PRUEBA.{uuid4().hex}"

    primero = registros[0]
    primero.meta_message_id = meta_id
    primero.save(update_fields=["meta_message_id"])

    # Intentar repetirlo en el segundo.
    duplicado_rechazado = False

    try:
        with transaction.atomic():
            segundo = registros[1]
            segundo.meta_message_id = meta_id
            segundo.save(update_fields=["meta_message_id"])
    except IntegrityError:
        duplicado_rechazado = True

    assert duplicado_rechazado, (
        "La base de datos permitió duplicar meta_message_id."
    )

    registros[1].refresh_from_db()

    assert registros[1].meta_message_id is None

    print("PRUEBA VERDE - UNICIDAD META MESSAGE ID")
    print("Múltiples NULL permitidos: True")
    print("Identificador duplicado rechazado:", duplicado_rechazado)
    print("Segundo registro conserva NULL: True")

    # Revertir todos los registros temporales.
    transaction.set_rollback(True)
