from uuid import uuid4

from django.db import transaction

from web.models import IntentoEntrega, IntentoEntregaImagen
from web.services.entrega import obtener_imagenes_para_entrega
from web.services.whatsapp_imagenes import preparar_imagenes_whatsapp


codigo_proceso = "18DAB23719674DE48CB29AB53F0D9E70"

imagenes = obtener_imagenes_para_entrega(codigo_proceso)

assert len(imagenes) > 0, "No hay fotografías disponibles."

with transaction.atomic():

    intento = IntentoEntrega.objects.create(
        codigo_proceso=codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario="5216561234567",
        clave_idempotencia=f"PRUEBA-WA-FIJA-{uuid4().hex}",
    )

    # Primera preparación.
    primera = preparar_imagenes_whatsapp(intento.pk)

    ids_originales = set(
        IntentoEntregaImagen.objects.filter(
            intento=intento
        ).values_list("imagen_id", flat=True)
    )

    assert primera == len(imagenes)
    assert ids_originales == {imagen.pk for imagen in imagenes}

    # Segunda preparación.
    segunda = preparar_imagenes_whatsapp(intento.pk)

    ids_finales = set(
        IntentoEntregaImagen.objects.filter(
            intento=intento
        ).values_list("imagen_id", flat=True)
    )

    assert segunda == primera
    assert ids_finales == ids_originales

    print("PRUEBA VERDE - SELECCION WHATSAPP FIJA")
    print("Fotografías preparadas:", segunda)

    # Revertir todos los registros temporales.
    transaction.set_rollback(True)
