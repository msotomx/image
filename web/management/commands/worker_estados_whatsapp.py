import time
import logging

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Worker automático de estados de WhatsApp."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "IMAGE ENGINE - Worker de estados WhatsApp iniciado."
            )
        )

        self.stdout.write(
            "Revisando estados WhatsApp cada 30 segundos."
        )

        try:
            while True:
                try:
                    call_command(
                        "procesar_estados_whatsapp",
                        limite=50,
                    )

                except Exception:
                    logger.exception(
                        "Error procesando estados WhatsApp."
                    )

                time.sleep(30)

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "\nIMAGE ENGINE - Worker de estados detenido."
                )
            )
