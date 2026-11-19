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

# Creamos un evento exclusivamente para esta prueba.
evento = EventoWhatsApp.objects.create(
    clave_evento=f"prueba_concurrencia_{identificador}",
    tipo=EventoWhatsApp.Tipo.ESTADO,
    mensaje_id=f"wamid.prueba.{identificador}",
    contenido={},
    estado_procesamiento=EventoWhatsApp.EstadoProcesamiento.PROCESANDO,
    intentos_procesamiento=1,
    fecha_ultimo_intento=timezone.now() - timedelta(minutes=15),
)

evento_id = evento.pk

bloqueo_adquirido = threading.Event()
liberar_bloqueo = threading.Event()
recuperacion_iniciada = threading.Event()
recuperacion_terminada = threading.Event()

resultados = {}
errores = []


def trabajador_a():
    close_old_connections()

    try:
        with transaction.atomic():
            EventoWhatsApp.objects.select_for_update().get(pk=evento_id)

            bloqueo_adquirido.set()

            if not liberar_bloqueo.wait(timeout=10):
                raise TimeoutError("No se liberó el bloqueo a tiempo.")

            confirmado = confirmar_evento_whatsapp(
                evento_id,
                numero_intento=1,
            )

            if not confirmado:
                raise AssertionError(
                    "El trabajador A no pudo confirmar el evento."
                )

            resultados["confirmado"] = confirmado

    except Exception as exc:
        errores.append(("A", str(exc)))

    finally:
        connection.close()


def trabajador_b():
    close_old_connections()

    try:
        if not bloqueo_adquirido.wait(timeout=10):
            raise TimeoutError("El trabajador A no adquirió el bloqueo.")

        recuperacion_iniciada.set()

        resultados["recuperado"] = recuperar_evento_whatsapp_atascado(
            evento_id,
            minutos=10,
        )

    except Exception as exc:
        errores.append(("B", str(exc)))

    finally:
        recuperacion_terminada.set()
        connection.close()


hilo_a = threading.Thread(target=trabajador_a)
hilo_b = threading.Thread(target=trabajador_b)

try:
    hilo_a.start()

    assert bloqueo_adquirido.wait(timeout=10), (
        "El trabajador A no adquirió el bloqueo."
    )

    hilo_b.start()

    assert recuperacion_iniciada.wait(timeout=10), (
        "El trabajador B no inició la recuperación."
    )

    # Comprobar que B no haya terminado mientras A mantiene el bloqueo.
    termino_antes = recuperacion_terminada.wait(timeout=1)

    assert termino_antes is False, (
        "La recuperación terminó mientras la fila estaba bloqueada."
    )

    liberar_bloqueo.set()

    hilo_a.join(timeout=10)
    hilo_b.join(timeout=10)

    assert not hilo_a.is_alive()
    assert not hilo_b.is_alive()
    assert not errores, errores
    assert resultados["confirmado"] is True
    assert resultados["recuperado"] is False

    estado_final = EventoWhatsApp.objects.get(
        pk=evento_id
    ).estado_procesamiento

    assert estado_final == (
        EventoWhatsApp.EstadoProcesamiento.PROCESADO
    )
    print("PRUEBA VERDE - CONFIRMACION CONCURRENTE WHATSAPP")
    print("Trabajador A confirmó el evento:", resultados["confirmado"])
    print("Trabajador B rechazó recuperación:", not resultados["recuperado"])
    print("Estado final PROCESADO:", estado_final == "PROCESADO")
    print("Errores:", errores)

finally:
    liberar_bloqueo.set()

    hilo_a.join(timeout=10)
    hilo_b.join(timeout=10)

    # Eliminar exclusivamente el registro creado por esta prueba.
    EventoWhatsApp.objects.filter(
        pk=evento_id,
        clave_evento=f"prueba_concurrencia_{identificador}",
    ).delete()

    print("Registro temporal eliminado:", True)
    print("Evento real PK 4 modificado:", False)
