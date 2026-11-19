import time
from django.conf import settings
from django.core.management.base import CommandError

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Worker automático de envíos WhatsApp."

    def handle(self, *args, **options):

        if not settings.WHATSAPP_ENVIO_HABILITADO:
            raise CommandError(
                "Worker WhatsApp no iniciado: "
                "los envíos reales están deshabilitados."
            )
        
        self.stdout.write(
            self.style.SUCCESS(
                "IMAGE ENGINE - Worker WhatsApp iniciado."
            )
        )

        self.stdout.write(
            "Revisando solicitudes WhatsApp cada 60 segundos."
        )

        try:
            while True:

                try:
                    call_command(
                        "procesar_envios_whatsapp",
                        limite=50,
                    )

                except Exception as exc:
                    self.stderr.write(
                        self.style.ERROR(
                            f"Error en worker WhatsApp: {exc}"
                        )
                    )

                time.sleep(60)

        except KeyboardInterrupt:
            self.stdout.write(
                self.style.WARNING(
                    "\nIMAGE ENGINE - Worker WhatsApp detenido."
                )
            )
