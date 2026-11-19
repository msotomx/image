from django.core.management.base import BaseCommand, CommandError

from web.services.whatsapp_trabajador import (
    procesar_lote_estados_whatsapp,
)


class Command(BaseCommand):
    help = "Procesa notificaciones de estado de WhatsApp recibidas desde Meta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite",
            type=int,
            default=50,
            help="Máximo de eventos por lote.",
        )

    def handle(self, *args, **options):
        limite = options["limite"]

        if limite < 1:
            raise CommandError(
                "El límite debe ser mayor que cero."
            )

        resultado = procesar_lote_estados_whatsapp(
            limite=limite,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Estados WhatsApp procesados: {resultado}"
            )
        )
