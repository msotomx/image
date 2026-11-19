from django.db import transaction
from django.contrib.auth import get_user_model
from web.models import (
    IntentoEntrega,
    IntentoEntregaImagen,
    Imagen,
    PaqueteImagen,
    UnidadVenta,
    Sector,
    Fondo,
)

with transaction.atomic():
    IntentoEntregaImagen.objects.filter(pk__in=[20, 21, 22, 23]).delete()
    IntentoEntrega.objects.filter(pk__in=[22, 23, 24, 25]).delete()
    Imagen.objects.filter(pk__in=[46, 47, 48, 49]).delete()
    PaqueteImagen.objects.filter(pk__in=[16, 18, 19, 20, 21]).delete()
    UnidadVenta.objects.filter(pk__in=[2, 4, 5, 6, 7, 8]).delete()
    Sector.objects.filter(pk__in=[2, 5, 6, 7]).delete()
    Fondo.objects.filter(pk__in=[7, 9, 10, 11]).delete()
    get_user_model().objects.filter(pk__in=[2, 4, 5, 8, 9, 10]).delete()

print("Limpieza finalizada correctamente")
