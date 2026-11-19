from datetime import timedelta
from django.utils import timezone
from django.conf import settings

from web.models import Imagen, PaqueteImagen


def eliminar_archivos_operacion(codigo_proceso: str) -> int:
    """
    Elimina los archivos físicos de una operación.

    Conserva los registros de PaqueteImagen e Imagen.
    No modifica créditos ni estados de operación.

    Retorna la cantidad de archivos eliminados.
    """

    paquetes = list(
        PaqueteImagen.objects.filter(
            codigo_proceso=codigo_proceso
        )
    )

    if not paquetes:
        raise ValueError("La operación no existe.")

    if any(
        paquete.estado not in (
            PaqueteImagen.EstadoPaquete.CANCELADO,
            PaqueteImagen.EstadoPaquete.CERRADO,
        )
        for paquete in paquetes
    ):
        raise ValueError(
            "La operación debe estar cancelada o cerrada."
        )

    limite = timezone.now() - timedelta(
        hours=settings.HORAS_CONSERVACION_IMAGENES
    )

    if any(
        paquete.fecha_fin_proceso is None
        or paquete.fecha_fin_proceso > limite
        for paquete in paquetes
    ):
        raise ValueError(
            "La operación todavía no cumple "
            "12 horas desde el fin del procesamiento."
        )

    imagenes = list(
        Imagen.objects.filter(paquete__in=paquetes)
    )

    archivos = {}

    # Recopilar originales y variantes sin duplicar rutas.
    for paquete in paquetes:
        if paquete.imagen_original:
            campo = paquete.imagen_original
            archivos[(campo.storage, campo.name)] = campo

    for imagen in imagenes:
        if imagen.imagen_procesada:
            campo = imagen.imagen_procesada
            archivos[(campo.storage, campo.name)] = campo

    # Eliminar archivos físicos.
    eliminados = 0

    for campo in archivos.values():
        if campo.storage.exists(campo.name):
            campo.delete(save=False)
            eliminados += 1

    # Limpiar referencias en la base de datos.
    PaqueteImagen.objects.filter(
        pk__in=[paquete.pk for paquete in paquetes]
    ).update(imagen_original="")

    Imagen.objects.filter(
        pk__in=[imagen.pk for imagen in imagenes]
    ).update(imagen_procesada=None)

    return eliminados
