from django.core.management.base import BaseCommand, CommandError

from web.services.whatsapp_ciclo_envios import (
    procesar_ciclo_envios_whatsapp,
)


class Command(BaseCommand):
    help = "Procesa solicitudes nuevas y reintentos de WhatsApp."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite",
            type=int,
            default=50,
            help="Máximo de solicitudes por lote.",
        )

    def handle(self, *args, **options):
        limite = options["limite"]

        try:
            resultado = procesar_ciclo_envios_whatsapp(
                limite=limite,
            )
        except (ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Ciclo WhatsApp finalizado: {resultado}"
            )
        )
