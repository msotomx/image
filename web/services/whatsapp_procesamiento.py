from django.utils import timezone
from django.db.models import F, Q
from django.conf import settings
from datetime import timedelta
from django.db import transaction

from web.models import EventoWhatsApp

def evento_whatsapp_protegido(evento_id: int) -> bool:
    """
    Identifica eventos reservados que no deben modificarse.
    """
    ids_protegidos = getattr(
        settings,
        "WHATSAPP_EVENTOS_PROTEGIDOS",
        (4,),
    )

    return evento_id in ids_protegidos


@transaction.atomic
def reclamar_evento_whatsapp(evento_id: int) -> int | None:
    """
    Reclama un evento pendiente y devuelve el número
    exacto del intento reclamado.

    Devuelve None si no pudo reclamarlo.
    """
    # No reclamar eventos reservados.
    if evento_whatsapp_protegido(evento_id):
        return None

    ahora = timezone.now()

    evento = (
        EventoWhatsApp.objects.select_for_update()
        .filter(
            pk=evento_id,
            estado_procesamiento=(
                EventoWhatsApp.EstadoProcesamiento.PENDIENTE
            ),
            intentos_procesamiento__lt=5,
        )
        .filter(
            Q(fecha_proximo_intento__isnull=True)
            | Q(fecha_proximo_intento__lte=ahora)
        )
        .first()
    )

    if evento is None:
        return None

    evento.intentos_procesamiento += 1
    evento.estado_procesamiento = (
        EventoWhatsApp.EstadoProcesamiento.PROCESANDO
    )
    evento.fecha_ultimo_intento = ahora
    evento.fecha_proximo_intento = None
    evento.mensaje_error = ""

    evento.save(
        update_fields=[
            "intentos_procesamiento",
            "estado_procesamiento",
            "fecha_ultimo_intento",
            "fecha_proximo_intento",
            "mensaje_error",
        ]
    )

    return evento.intentos_procesamiento

def confirmar_evento_whatsapp(
    evento_id: int,
    numero_intento: int,
) -> bool:
    """
    Confirma exclusivamente el intento que reclamó
    el evento.

    Impide que un trabajador antiguo confirme
    un intento posterior.
    """
    # Impedir modificaciones a eventos reservados.
    if evento_whatsapp_protegido(evento_id):
        return False

    if numero_intento < 1:
        raise ValueError(
            "El número de intento debe ser mayor que cero."
        )

    actualizados = EventoWhatsApp.objects.filter(
        pk=evento_id,
        estado_procesamiento=(
            EventoWhatsApp.EstadoProcesamiento.PROCESANDO
        ),
        intentos_procesamiento=numero_intento,
    ).update(
        estado_procesamiento=(
            EventoWhatsApp.EstadoProcesamiento.PROCESADO
        ),
        fecha_procesamiento=timezone.now(),
        mensaje_error="",
    )

    return actualizados == 1

def registrar_error_evento_whatsapp(
    evento_id: int,
    mensaje_error: str,
    numero_intento: int,
) -> bool:
    """
    Registra un error únicamente para el intento
    que reclamó el evento.

    Impide que un trabajador antiguo modifique
    un intento posterior.
    """
    # Impedir modificaciones a eventos reservados.
    if evento_whatsapp_protegido(evento_id):
        return False

    mensaje = str(mensaje_error).strip()

    if not mensaje:
        raise ValueError(
            "Es obligatorio indicar el motivo del error."
        )

    if numero_intento < 1:
        raise ValueError(
            "El número de intento debe ser mayor que cero."
        )

    actualizados = EventoWhatsApp.objects.filter(
        pk=evento_id,
        estado_procesamiento=(
            EventoWhatsApp.EstadoProcesamiento.PROCESANDO
        ),
        intentos_procesamiento=numero_intento,
    ).update(
        estado_procesamiento=(
            EventoWhatsApp.EstadoProcesamiento.ERROR
        ),
        fecha_procesamiento=timezone.now(),
        mensaje_error=mensaje,
    )

    return actualizados == 1

def reactivar_evento_whatsapp(evento_id: int) -> bool:
    """
    Reactiva un evento fallido para un nuevo procesamiento.

    Solo permite ERROR -> PENDIENTE.

    No envía fotografías ni modifica créditos.

    IMPORTANTE:
    Debe invocarse únicamente después de verificar
    que reintentar el evento es seguro.
    """
    # Impedir la reactivación de eventos reservados.
    if evento_whatsapp_protegido(evento_id):
        return False

    actualizados = EventoWhatsApp.objects.filter(
        pk=evento_id,
        estado_procesamiento=(
            EventoWhatsApp.EstadoProcesamiento.ERROR
        ),
    ).update(
        estado_procesamiento=(
            EventoWhatsApp.EstadoProcesamiento.PENDIENTE
        ),
        fecha_procesamiento=None,
        mensaje_error="",
    )

    return actualizados == 1

@transaction.atomic
def liberar_evento_whatsapp(
    evento_id: int,
    numero_intento: int,
) -> bool:
    """
    Libera exclusivamente el intento que reclamó el evento.

    Intentos 1 a 4: PENDIENTE, con espera de 60 segundos.
    Intento 5: ERROR, por agotamiento de intentos.

    No reenvía fotografías ni modifica créditos.
    """
    # Impedir modificaciones a eventos reservados.
    if evento_whatsapp_protegido(evento_id):
        return False

    if numero_intento < 1:
        raise ValueError(
            "El número de intento debe ser mayor que cero."
        )

    evento = (
        EventoWhatsApp.objects
        .select_for_update()
        .filter(
            pk=evento_id,
            estado_procesamiento=(
                EventoWhatsApp.EstadoProcesamiento.PROCESANDO
            ),
            intentos_procesamiento=numero_intento,
        )
        .first()
    )

    if evento is None:
        return False

    ahora = timezone.now()

    if evento.intentos_procesamiento >= 5:
        evento.estado_procesamiento = (
            EventoWhatsApp.EstadoProcesamiento.ERROR
        )
        evento.fecha_proximo_intento = None
        evento.mensaje_error = (
            "Se agotaron los 5 intentos de procesamiento "
            "sin encontrar una fotografía asociada al mensaje de Meta."
        )
    else:
        evento.estado_procesamiento = (
            EventoWhatsApp.EstadoProcesamiento.PENDIENTE
        )
        evento.fecha_proximo_intento = ahora + timedelta(seconds=60)
        evento.mensaje_error = ""

    evento.fecha_procesamiento = None

    evento.save(
        update_fields=[
            "estado_procesamiento",
            "fecha_proximo_intento",
            "fecha_procesamiento",
            "mensaje_error",
        ]
    )

    return True

def obtener_eventos_whatsapp_pendientes(
    limite: int = 50,
    tipo: str | None = None,
):
    """
    Obtiene eventos WhatsApp disponibles para procesamiento.

    No reclama eventos ni modifica la base de datos.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    ahora = timezone.now()

    queryset = EventoWhatsApp.objects.filter(
        estado_procesamiento=(
            EventoWhatsApp.EstadoProcesamiento.PENDIENTE
        ),
        intentos_procesamiento__lt=5,
    ).filter(
        Q(fecha_proximo_intento__isnull=True)
        | Q(fecha_proximo_intento__lte=ahora)
    )

    if tipo is not None:
        queryset = queryset.filter(tipo=tipo)

    # Excluir los eventos configurados como protegidos.
    ids_protegidos = getattr(
        settings,
        "WHATSAPP_EVENTOS_PROTEGIDOS",
        (4,),
    )

    queryset = queryset.exclude(pk__in=ids_protegidos)
    
    return list(
        queryset.order_by("fecha_recepcion", "pk")[:limite]
    )

def obtener_eventos_whatsapp_atascados(
    minutos: int = 10,
    limite: int = 50,
):
    """
    Identifica eventos WhatsApp que llevan demasiado
    tiempo en PROCESANDO.

    No modifica registros.
    No ejecuta procesamientos.
    """

    if minutos < 1:
        raise ValueError("Los minutos deben ser mayores que cero.")

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    fecha_limite = timezone.now() - timedelta(minutes=minutos)

    return list(
        EventoWhatsApp.objects.filter(
            estado_procesamiento=(
                EventoWhatsApp.EstadoProcesamiento.PROCESANDO
            ),
            fecha_ultimo_intento__isnull=False,
            fecha_ultimo_intento__lte=fecha_limite,
        )
        .order_by("fecha_ultimo_intento", "pk")[:limite]
    )

def recuperar_evento_whatsapp_atascado(
    evento_id: int,
    minutos: int = 10,
) -> bool:
    """
    Recupera un evento que permanece demasiado tiempo
    en PROCESANDO.

    No ejecuta el procesamiento del evento.
    No envía fotografías.
    No consume créditos.
    """

    if minutos < 1:
        raise ValueError("Los minutos deben ser mayores que cero.")

    # No recuperar eventos configurados como protegidos.
    if evento_whatsapp_protegido(evento_id):
        return False

    fecha_limite = timezone.now() - timedelta(minutes=minutos)

    # Recuperar únicamente si no agotó los intentos.
    actualizados = EventoWhatsApp.objects.filter(
        pk=evento_id,
        estado_procesamiento=EventoWhatsApp.EstadoProcesamiento.PROCESANDO,
        fecha_ultimo_intento__isnull=False,
        fecha_ultimo_intento__lte=fecha_limite,
        intentos_procesamiento__lt=5,
    ).update(
        estado_procesamiento=EventoWhatsApp.EstadoProcesamiento.PENDIENTE,
        fecha_proximo_intento=timezone.now() + timedelta(seconds=60),
        fecha_procesamiento=None,
        mensaje_error="",
    )

    if actualizados:
        return True

    # Si agotó los cinco intentos, marcar ERROR.
    actualizados = EventoWhatsApp.objects.filter(
        pk=evento_id,
        estado_procesamiento=EventoWhatsApp.EstadoProcesamiento.PROCESANDO,
        fecha_ultimo_intento__isnull=False,
        fecha_ultimo_intento__lte=fecha_limite,
        intentos_procesamiento__gte=5,
    ).update(
        estado_procesamiento=EventoWhatsApp.EstadoProcesamiento.ERROR,
        fecha_proximo_intento=None,
        fecha_procesamiento=None,
        mensaje_error=(
            "Se agotaron los 5 intentos de procesamiento WhatsApp."
        ),
    )

    return actualizados == 1
