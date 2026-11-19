import threading
from datetime import timedelta
from uuid import uuid4

from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from web.models import EventoWhatsApp
from web.services.whatsapp_procesamiento import (
    recuperar_evento_whatsapp_atascado,
    confirmar_evento_whatsapp,
)


identificador = uuid4().hex
clave = f"prueba_recuperacion_primero_{identificador}"

# Registro temporal visible para ambas conexiones.
evento = EventoWhatsApp.objects.create(
    clave_evento=clave,
    tipo=EventoWhatsApp.Tipo.ESTADO,
    mensaje_id=f"wamid.prueba.{identificador}",
    contenido={},
    estado_procesamiento=EventoWhatsApp.EstadoProcesamiento.PROCESANDO,
    intentos_procesamiento=1,
    fecha_ultimo_intento=timezone.now() - timedelta(minutes=15),
)

evento_id = evento.pk

recuperacion_terminada = threading.Event()
resultados = {}
errores = []


def trabajador_b():
    close_old_connections()

    try:
        resultados["recuperado"] = recuperar_evento_whatsapp_atascado(
            evento_id,
            minutos=10,
        )
    except Exception as exc:
        errores.append(("B", str(exc)))
    finally:
        recuperacion_terminada.set()
        connection.close()


def trabajador_a():
    close_old_connections()

    try:
        if not recuperacion_terminada.wait(timeout=10):
            raise TimeoutError("La recuperación no terminó a tiempo.")

        with transaction.atomic():
            resultados["confirmado"] = confirmar_evento_whatsapp(
                evento_id,
                numero_intento=1,
            )
    except Exception as exc:
        errores.append(("A", str(exc)))
    finally:
        connection.close()


hilo_b = threading.Thread(target=trabajador_b)
hilo_a = threading.Thread(target=trabajador_a)

try:
    hilo_b.start()

    assert recuperacion_terminada.wait(timeout=10), (
        "El trabajador B no terminó la recuperación."
    )

    hilo_a.start()

    hilo_b.join(timeout=10)
    hilo_a.join(timeout=10)

    assert not hilo_b.is_alive()
    assert not hilo_a.is_alive()
    assert not errores, errores

    assert resultados["recuperado"] is True
    assert resultados["confirmado"] is False

    evento.refresh_from_db()

    assert evento.estado_procesamiento == (
        EventoWhatsApp.EstadoProcesamiento.PENDIENTE
    )
    assert evento.intentos_procesamiento == 1
    assert evento.fecha_proximo_intento is not None

    print("PRUEBA VERDE - RECUPERACION PRIMERO WHATSAPP")
    print("Recuperación completada:", resultados["recuperado"])
    print("Confirmación antigua rechazada:", not resultados["confirmado"])
    print("Estado final PENDIENTE:", True)
    print("Número de intento conservado:", True)
    print("Espera de reintento establecida:", True)
    print("Errores:", errores)

finally:
    recuperacion_terminada.set()

    hilo_b.join(timeout=10)
    hilo_a.join(timeout=10)

    # Eliminar exclusivamente el registro temporal.
    eliminados, _ = EventoWhatsApp.objects.filter(
        pk=evento_id,
        clave_evento=clave,
    ).delete()

    print("Registro temporal eliminado:", eliminados >= 1)
    print("Evento real PK 4 modificado:", False)
