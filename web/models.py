from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator


class Estado(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    abreviacion = models.CharField(max_length=10, unique=True)

    def __str__(self):
        return self.nombre


class Ciudad(models.Model):
    codigo_ciudad = models.CharField(max_length=6, unique=True)
    estado = models.ForeignKey(Estado, on_delete=models.PROTECT, related_name='ciudades')
    nombre = models.CharField(max_length=120)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['estado', 'nombre'], name='uq_ciudad_estado_nombre')]

    def __str__(self):
        return f'{self.nombre}, {self.estado.abreviacion}'


class Sector(models.Model):
    ciudad = models.ForeignKey(Ciudad, on_delete=models.PROTECT, related_name='sectores')
    codigo_sector = models.CharField(max_length=9, unique=True) # codigo_ciudad + nnn consecutivo
    nombre = models.CharField(max_length=120)
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sector', null=True, blank=True)
    representante = models.CharField(max_length=160)
    telefono1 = models.CharField(max_length=25)
    telefono2 = models.CharField(max_length=25, blank=True)
    email = models.EmailField(blank=True)
    instagram = models.CharField(max_length=120, blank=True)
    direccion = models.TextField(blank=True)
    ine = models.ImageField(upload_to='sectores/ine/%Y/%m/', blank=True, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['ciudad', 'nombre'], name='uq_sector_ciudad_nombre')]

    @property
    def estado(self):
        return self.ciudad.estado

    def __str__(self):
        return f'{self.nombre} - {self.ciudad}'


class UnidadVenta(models.Model):
    sector = models.ForeignKey(Sector, on_delete=models.PROTECT, related_name='unidades_venta')
    nombre = models.CharField(max_length=160)
    whatsapp = models.CharField(max_length=25)
    comentarios = models.TextField(blank=True)

    def __str__(self):
        return f'{self.nombre} ({self.sector.nombre})'


class Precio(models.Model):
    fecha_inicio = models.DateField(unique=True)
    precio_vigente = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])

    class Meta:
        ordering = ['-fecha_inicio']

    def __str__(self):
        return f'{self.fecha_inicio}: {self.precio_vigente}'


class Pago(models.Model):
    codigo_pago = models.CharField(max_length=40, unique=True)
    sector = models.ForeignKey(Sector, on_delete=models.PROTECT, related_name='pagos')
    anio = models.PositiveSmallIntegerField()
    semana = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    importe_pagado = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    fecha_pago = models.DateTimeField(default=timezone.now)
    paquetes_pagados = models.PositiveIntegerField() # importe_pagado / precio
    creditos_usados = models.PositiveIntegerField(default=0) # 1 credito incluye  3 imagenes
    creditos_reservados = models.PositiveIntegerField(default=0)
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    pago_consumido = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Pago consumido",
        help_text="Indica que todos los créditos del pago fueron utilizados."
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(semana__gte=1, semana__lte=53),
                name="ck_pago_semana"
            ),
            models.CheckConstraint(
                condition=Q(
                    paquetes_pagados__gte=1,
                    creditos_usados__lte=(
                        F("paquetes_pagados") -
                        F("creditos_reservados")
                    )
                ),
                name="ck_pago_consumo"
            )
        ]

    @property
    def creditos_disponibles(self):
        return (
            self.paquetes_pagados
            - self.creditos_usados
            - self.creditos_reservados
        )

    def __str__(self):
        return self.codigo_pago


class PaqueteImagen(models.Model):

    class EstadoPaquete(models.TextChoices):
        RECIBIDO = "RECIBIDO", "Recibido"
        PROCESANDO = "PROCESANDO", "Procesando"
        LISTO = "LISTO", "Listo para seleccionar"
        CERRADO = "CERRADO", "Cerrado"
        ERROR = "ERROR", "Error"
        CANCELADO = "CANCELADO", "Cancelado"

    class EstadoCredito(models.TextChoices):
        NUEVO = "NUEVO", "Nuevo"
        RESERVADO = "RESERVADO", "Reservado"
        CONSUMIDO = "CONSUMIDO", "Consumido"
        LIBERADO = "LIBERADO", "Liberado"

    numero_paquete = models.PositiveSmallIntegerField()
    unidad_venta = models.ForeignKey(
        UnidadVenta,
        on_delete=models.PROTECT,
        related_name="paquetes"
    )

    pago = models.ForeignKey(
        Pago,
        on_delete=models.PROTECT,
        related_name="paquetes",
        null=True,
        blank=True
    )
    codigo_proceso = models.CharField(
        max_length=40,
        db_index=True
    ) # liga los 2 paquetes especialmente util cuando el Pago viene de diferentes Pagos
    imagen_original = models.ImageField(
        upload_to="imagenes/originales/%Y/%m/"
    )

    fecha_creacion = models.DateTimeField(
        default=timezone.now
    )

    estado = models.CharField(
        max_length=12,
        choices=EstadoPaquete.choices,
        default=EstadoPaquete.RECIBIDO
    )

    estado_credito = models.CharField(
        max_length=12,
        choices=EstadoCredito.choices,
        default=EstadoCredito.NUEVO
    )

    email_destino = models.EmailField(blank=True)

    whatsapp_destino = models.CharField(
        max_length=25,
        blank=True
    )

    fecha_cierre = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["codigo_proceso", "numero_paquete"],
                name="uq_proceso_numero_paquete"
            ),
        ]

    def __str__(self):
        return (
            f"{self.codigo_proceso} - "
            f"Paquete {self.numero_paquete}"
        )


class Fondo(models.Model):
    codigo_fondo = models.CharField(
        max_length=30,
        unique=True
    )
    imagen = models.ImageField(upload_to='fondos/%Y/%m/')

    def __str__(self):
        return self.codigo_fondo


class Imagen(models.Model):
# Cada registro de Imagen representará una de las seis imágenes generadas con un fondo diferente.
    class EstadoProceso(models.TextChoices):
        PROCESANDO = "PROCESANDO", "Procesando"
        PROCESADA = "PROCESADA", "Procesada"
        ERROR = "ERROR", "Error"
        ENVIADA = "ENVIADA", "Enviada"

    paquete = models.ForeignKey(
        PaqueteImagen,
        on_delete=models.PROTECT,
        related_name="imagenes"
    )

    codigo_imagen = models.CharField(
        max_length=25,
        unique=True
    )

    fondo = models.ForeignKey(
        Fondo,
        on_delete=models.PROTECT,
        related_name="imagenes"
    )

    imagen_procesada = models.ImageField(
        upload_to="imagenes/procesadas/%Y/%m/",
        blank=True,
        null=True
    )

    estado_proceso = models.CharField(
        max_length=12,
        choices=EstadoProceso.choices,
        default=EstadoProceso.PROCESANDO
    )

    seleccionada = models.BooleanField(
        default=False
    )

    fecha_envio = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["paquete", "fondo"],
                name="uq_imagen_paquete_fondo"
            )
        ]

    def __str__(self):
        return self.codigo_imagen
    
