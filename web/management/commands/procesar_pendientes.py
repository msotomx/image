import logging
import time
from django.core.management import call_command
from django.core.management.base import BaseCommand

from web.models import PaqueteImagen
from web.services.procesamiento import procesar_variantes


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Procesa las fotografías pendientes de IMAGE ENGINE."

    def handle(self, *args, **options):

        codigos_pendientes = (
            PaqueteImagen.objects
            .filter(
                estado=PaqueteImagen.EstadoPaquete.RECIBIDO,
                estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
            )
            .order_by("codigo_proceso")
            .values_list("codigo_proceso", flat=True)
            .distinct()
        )

        codigos = list(codigos_pendientes)

        if not codigos:
            self.stdout.write(
                self.style.SUCCESS(
                    "No hay operaciones pendientes."
                )
            )
            return

        self.stdout.write(
            f"Operaciones pendientes: {len(codigos)}"
        )

        for codigo in codigos:

            try:
                imagenes = procesar_variantes(codigo)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Operación {codigo}: "
                        f"{len(imagenes)} imágenes generadas."
                    )
                )

            except Exception:
                logger.exception(
                    "Error procesando la operación %s",
                    codigo,
                )

                self.stderr.write(
                    self.style.ERROR(
                        f"Error procesando la operación {codigo}. "
                        "Consulta el registro de errores."
                    )
                )
