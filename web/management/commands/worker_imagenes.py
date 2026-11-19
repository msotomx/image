import time

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Worker automático de procesamiento de imágenes."

    def handle(self, *args, **options):

        self.stdout.write(
            self.style.SUCCESS(
                "IMAGE ENGINE - Worker iniciado."
            )
        )

        self.stdout.write(
            "Revisando fotografías pendientes cada 10 segundos."
        )

        try:
            while True:

                try:
                    call_command("procesar_pendientes")

                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Error en el worker: {exc}"
                        )
                    )

                time.sleep(10)

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "\nIMAGE ENGINE - Worker detenido."
                )
            )
