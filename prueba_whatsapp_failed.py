from uuid import uuid4

from django.db import transaction

from web.models import (
    IntentoEntrega,
    IntentoEntregaImagen,
)

from web.services.whatsapp_imagenes import (
    preparar_imagenes_whatsapp,
)

from web.services.whatsapp_envios import (
    reclamar_imagen_whatsapp,
    confirmar_aceptacion_imagen_whatsapp,
    registrar_imagen_enviada_whatsapp,
    registrar_imagen_entregada_whatsapp,
    registrar_imagen_fallida_whatsapp,
)


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-FAILED-{uuid4().hex}",
    )

    cantidad = preparar_imagenes_whatsapp(intento.pk)
    assert cantidad > 0

    entrega = IntentoEntregaImagen.objects.filter(
        intento=intento
    ).first()

    intento.estado = IntentoEntrega.Estado.ENVIANDO
    intento.save(update_fields=["estado"])

    assert reclamar_imagen_whatsapp(entrega.pk) is True

    meta_id = f"wamid.PRUEBA.{uuid4().hex}"

    assert confirmar_aceptacion_imagen_whatsapp(
        entrega.pk, meta_id
    ) is True

    # Caso 1: ACEPTADA -> ERROR
    resultado = registrar_imagen_fallida_whatsapp(
        meta_id,
        "Error confirmado por Meta",
    )

    assert resultado is True

    entrega.refresh_from_db()

    assert entrega.estado == IntentoEntregaImagen.Estado.ERROR
    assert entrega.mensaje_error == "Error confirmado por Meta"

    # Caso 2: failed duplicado
    duplicado = registrar_imagen_fallida_whatsapp(
        meta_id,
        "Error duplicado",
    )

    assert duplicado is False

    entrega.refresh_from_db()
    assert entrega.mensaje_error == "Error confirmado por Meta"

    print("ACEPTADA -> ERROR:", resultado)
    print("Duplicado rechazado:", duplicado)

    # Preparar nuevamente la fotografía solo para probar
    # otras transiciones dentro de esta transacción temporal.
    entrega.estado = IntentoEntregaImagen.Estado.ACEPTADA
    entrega.mensaje_error = ""
    entrega.save(update_fields=["estado", "mensaje_error"])

    # Caso 3: ENVIADA -> ERROR
    assert registrar_imagen_enviada_whatsapp(meta_id) is True

    resultado_enviada = registrar_imagen_fallida_whatsapp(
        meta_id,
        "Fallo posterior al envío",
    )

    assert resultado_enviada is True

    entrega.refresh_from_db()
    assert entrega.estado == IntentoEntregaImagen.Estado.ERROR

    print("ENVIADA -> ERROR:", resultado_enviada)

    # Preparar estado ENTREGADA exclusivamente para la prueba.
    entrega.estado = IntentoEntregaImagen.Estado.ACEPTADA
    entrega.mensaje_error = ""
    entrega.save(update_fields=["estado", "mensaje_error"])

    assert registrar_imagen_entregada_whatsapp(meta_id) is True

    # Caso 4: ENTREGADA no debe regresar a ERROR.
    resultado_entregada = registrar_imagen_fallida_whatsapp(
        meta_id,
        "Error tardío",
    )

    assert resultado_entregada is False

    entrega.refresh_from_db()

    assert entrega.estado == IntentoEntregaImagen.Estado.ENTREGADA
    assert entrega.mensaje_error == ""

    print("ENTREGADA -> ERROR rechazado:", resultado_entregada)

    print("PRUEBA VERDE - FAILED WHATSAPP")

    # Revertir todos los registros y cambios temporales.
    transaction.set_rollback(True)
