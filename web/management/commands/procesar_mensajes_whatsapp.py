from django.core.management.base import BaseCommand, CommandError

from web.services.whatsapp_mensajes_trabajador import (
    procesar_lote_mensajes_whatsapp,
)


class Command(BaseCommand):
    help = "Procesa solicitudes WhatsApp entrantes de clientes finales."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limite",
            type=int,
            default=50,
            help="Cantidad máxima de mensajes por ejecución.",
        )

    def handle(self, *args, **options):
        limite = options["limite"]

        if limite < 1:
            raise CommandError(
                "El límite debe ser mayor que cero."
            )

        resumen = procesar_lote_mensajes_whatsapp(
            limite=limite,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Mensajes WhatsApp: "
                f"seleccionados={resumen['seleccionados']}, "
                f"procesados={resumen['procesados']}, "
                f"no_procesados={resumen['no_procesados']}"
            )
        )
