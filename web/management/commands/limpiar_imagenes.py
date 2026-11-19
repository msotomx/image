from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q

from web.models import PaqueteImagen
from web.services.creditos import cancelar_operacion
from web.services.limpieza import eliminar_archivos_operacion


class Command(BaseCommand):
    help = "Cancela operaciones vencidas y elimina fotografías después de 12 horas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula la limpieza sin cancelar operaciones ni eliminar archivos.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "MODO SIMULACIÓN: no se modificarán créditos ni archivos."
                )
            )
        
        limite = timezone.now() - timedelta(
            hours=settings.HORAS_CONSERVACION_IMAGENES
        )

        # 1. Cancelar operaciones LISTO vencidas.
        operaciones_pendientes = list(
            PaqueteImagen.objects.filter(
                estado=PaqueteImagen.EstadoPaquete.LISTO,
                estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
                fecha_fin_proceso__lte=limite,
            )
            .values_list("codigo_proceso", flat=True)
            .distinct()
        )

        canceladas = 0
        errores = 0

        for codigo_proceso in operaciones_pendientes:
            if dry_run:
                self.stdout.write(
                    f"[SIMULACIÓN] Se cancelaría: {codigo_proceso}"
                )
                continue

            try:
                cancelar_operacion(codigo_proceso)
                canceladas += 1
                self.stdout.write(
                    f"Operación cancelada: {codigo_proceso}"
                )
            except Exception as exc:
                errores += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"Error al cancelar {codigo_proceso}: {exc}"
                    )
                )

        # 2. Limpiar archivos de operaciones vencidas.
        operaciones_limpieza = list(
            PaqueteImagen.objects.filter(
                estado__in=[
                    PaqueteImagen.EstadoPaquete.CANCELADO,
                    PaqueteImagen.EstadoPaquete.CERRADO,
                ],
                fecha_fin_proceso__lte=limite,
            )
            .filter(
                Q(imagen_original__isnull=False)
                & ~Q(imagen_original="")
                | (
                    Q(imagenes__imagen_procesada__isnull=False)
                    & ~Q(imagenes__imagen_procesada="")
                )
            )
            .order_by("codigo_proceso")
            .values_list("codigo_proceso", flat=True)
            .distinct()
        )
        
        operaciones_limpiadas = 0
        archivos_eliminados = 0

        for codigo_proceso in operaciones_limpieza:
            if dry_run:
                self.stdout.write(
                    f"[SIMULACIÓN] Se revisarían archivos de: {codigo_proceso}"
                )
                continue

            try:
                cantidad = eliminar_archivos_operacion(codigo_proceso)
                archivos_eliminados += cantidad
                operaciones_limpiadas += 1

                self.stdout.write(
                    f"Archivos eliminados de {codigo_proceso}: {cantidad}"
                )

            except Exception as exc:
                errores += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"Error al limpiar {codigo_proceso}: {exc}"
                    )
                )
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Simulación terminada. "
                    f"Operaciones por cancelar: {len(operaciones_pendientes)}. "
                    f"Operaciones identificadas para limpieza: "
                    f"{len(operaciones_limpieza)}. "
                    f"No se realizaron cambios."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Operaciones canceladas: {canceladas}. "
                f"Operaciones limpiadas: {operaciones_limpiadas}. "
                f"Archivos eliminados: {archivos_eliminados}. "
                f"Errores: {errores}"
            )
        )
