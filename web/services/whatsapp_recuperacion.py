from django.db import transaction
from datetime import timedelta
from django.utils import timezone

from web.services.whatsapp_envios import (
    obtener_imagenes_whatsapp_para_revision,
)
from web.models import IntentoEntrega
from web.models import IntentoEntregaImagen
from web.services.intentos_entrega import crear_intento_entrega

MAX_REINTENTOS_WHATSAPP = 3


def obtener_candidatas_recuperacion_whatsapp(limite: int = 50):
    """
    Obtiene hasta 'limite' fotografías elegibles para recuperación.

    Omite fotografías que ya generaron otro reintento,
    agotaron el máximo permitido o todavía deben esperar.

    No crea intentos, no envía mensajes ni consume créditos.
    """

    if limite < 1:
        raise ValueError("El límite debe ser mayor que cero.")

    candidatas = []

    for entrega in obtener_imagenes_whatsapp_para_revision():
        if siguiente_reintento_whatsapp(entrega.pk) is None:
            continue

        if not reintento_whatsapp_listo(entrega.pk):
            continue

        candidatas.append(entrega)

        if len(candidatas) >= limite:
            break

    return candidatas

def tiene_reintento_automatico_whatsapp(entrega_imagen_id: int) -> bool:
    """
    Comprueba si ya existe al menos un reintento automático
    asociado a una entrega individual.

    No modifica registros ni consume créditos.
    """

    prefijo = f"WA-RETRY-{entrega_imagen_id}-"

    return IntentoEntrega.objects.filter(
        canal=IntentoEntrega.Canal.WHATSAPP,
        clave_idempotencia__startswith=prefijo,
    ).exists()

def siguiente_reintento_whatsapp(entrega_imagen_id: int) -> int | None:
    """
    Devuelve el número del siguiente reintento automático.

    None significa que ya existe una recuperación iniciada
    o que se alcanzó el máximo permitido.

    No modifica registros ni realiza envíos.
    """

    entrega = IntentoEntregaImagen.objects.select_related(
        "intento"
    ).get(pk=entrega_imagen_id)

    if entrega.estado != IntentoEntregaImagen.Estado.ERROR:
        return None

    # Cada entrega individual puede originar una sola
    # solicitud de recuperación.
    if tiene_reintento_automatico_whatsapp(entrega.pk):
        return None

    clave = entrega.intento.clave_idempotencia

    if clave.startswith("WA-RETRY-"):
        try:
            numero_actual = int(clave.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            return None

        siguiente = numero_actual + 1
    else:
        siguiente = 1

    if siguiente > MAX_REINTENTOS_WHATSAPP:
        return None

    return siguiente

@transaction.atomic
def preparar_reintento_automatico_whatsapp(
    entrega_imagen_id: int,
) -> tuple[int, int] | None:
    """
    Prepara un reintento automático para una sola fotografía.

    Devuelve (intento_id, numero_reintento), o None
    si no corresponde recuperar la fotografía.

    No envía mensajes ni consume créditos.
    """

    entrega = (
        IntentoEntregaImagen.objects
        .select_for_update()
        .select_related("intento", "imagen")
        .get(pk=entrega_imagen_id)
    )

    # Respetar el intervalo mínimo antes de recuperar.
    if not reintento_whatsapp_listo(entrega.pk):
        return None
    
    numero = siguiente_reintento_whatsapp(entrega.pk)

    if numero is None:
        return None

    clave = f"WA-RETRY-{entrega.pk}-{numero}"

    intento = crear_intento_entrega(
        codigo_proceso=entrega.intento.codigo_proceso,
        canal=IntentoEntrega.Canal.WHATSAPP,
        destinatario=entrega.intento.destinatario,
        clave_idempotencia=clave,
    )

    IntentoEntregaImagen.objects.get_or_create(
        intento=intento,
        imagen=entrega.imagen,
    )

    return intento.pk, numero

INTERVALO_REINTENTO_WHATSAPP_MINUTOS = 5


def reintento_whatsapp_listo(entrega_imagen_id: int) -> bool:
    """
    Comprueba si transcurrió el intervalo mínimo
    desde el inicio del envío anterior.

    No modifica registros ni realiza envíos.
    """

    entrega = IntentoEntregaImagen.objects.get(
        pk=entrega_imagen_id
    )

    if entrega.estado != IntentoEntregaImagen.Estado.ERROR:
        return None

    referencia = (
        entrega.fecha_inicio_envio
        or entrega.fecha_creacion
    )

    return timezone.now() >= referencia + timedelta(
        minutes=INTERVALO_REINTENTO_WHATSAPP_MINUTOS
    )

def preparar_lote_recuperacion_whatsapp(limite: int = 50) -> dict:
    """
    Prepara recuperaciones de fotografías con ERROR o INCIERTA.

    No envía mensajes a Meta ni consume créditos.
    """

    candidatas = obtener_candidatas_recuperacion_whatsapp(
        limite=limite,
    )

    resumen = {
        "revisadas": len(candidatas),
        "preparadas": 0,
        "omitidas": 0,
        "errores": 0,
    }

    for entrega in candidatas:
        try:
            resultado = preparar_reintento_automatico_whatsapp(
                entrega.pk
            )
        except Exception:
            resumen["errores"] += 1
            continue

        if resultado is None:
            resumen["omitidas"] += 1
        else:
            resumen["preparadas"] += 1

    return resumen
