import mimetypes
from django.db import transaction
from django.utils import timezone
from pathlib import Path
from django.conf import settings
from django.core.mail import EmailMessage

from web.models import Imagen, PaqueteImagen, IntentoEntrega
from web.services.intentos_entrega import (
    crear_intento_entrega,
    iniciar_intento_entrega,
    confirmar_intento_entrega,
    registrar_error_entrega,
    reclamar_intento_email,
    crear_solicitud_envio_email,
)


def obtener_imagenes_para_entrega(codigo_proceso: str):
    """
    Obtiene las imágenes seleccionadas de una operación cerrada.
    No modifica créditos, estados ni fechas de envío.
    """
    paquetes = list(
        PaqueteImagen.objects.filter(
            codigo_proceso=codigo_proceso
        )
    )

    if not paquetes:
        raise ValueError("La operación no existe.")

    if any(
        paquete.estado != PaqueteImagen.EstadoPaquete.CERRADO
        for paquete in paquetes
    ):
        raise ValueError("La operación todavía no está cerrada.")

    imagenes = list(
        Imagen.objects.filter(
            paquete__in=paquetes,
            seleccionada=True,
        )
        .select_related("paquete", "fondo")
        .order_by("paquete__numero_paquete", "id")
    )

    if not imagenes:
        raise ValueError("La operación no tiene imágenes seleccionadas.")

    for imagen in imagenes:
        if not imagen.imagen_procesada:
            raise ValueError(
                f"La imagen {imagen.codigo_imagen} no tiene archivo procesado."
            )

        campo = imagen.imagen_procesada
        if not campo.storage.exists(campo.name):
            raise ValueError(
                f"El archivo de la imagen {imagen.codigo_imagen} no existe."
            )

    return imagenes


@transaction.atomic
def registrar_imagenes_enviadas(codigo_proceso: str) -> int:
    """
    Registra como enviadas las imágenes seleccionadas.

    No consume créditos.
    No modifica el cierre de la operación.
    No duplica fechas de envío en ejecuciones posteriores.
    """

    imagenes = obtener_imagenes_para_entrega(codigo_proceso)

    ids = [imagen.pk for imagen in imagenes]

    pendientes = Imagen.objects.select_for_update().filter(
        pk__in=ids,
    ).exclude(
        estado_proceso=Imagen.EstadoProceso.ENVIADA,
    )

    cantidad = pendientes.update(
        estado_proceso=Imagen.EstadoProceso.ENVIADA,
        fecha_envio=timezone.now(),
    )

    return cantidad

def enviar_imagenes_por_email(
    codigo_proceso: str,
    clave_solicitud: str,
) -> int:
    """
    Envía las fotografías seleccionadas de una operación.

    Cada clave_solicitud identifica una solicitud independiente.
    Una nueva clave permite reenviar las mismas fotografías.
    """

    imagenes = obtener_imagenes_para_entrega(codigo_proceso)

    paquetes = list(
        PaqueteImagen.objects.filter(
            codigo_proceso=codigo_proceso,
        ).order_by("numero_paquete")
    )

    destinatarios = {
        p.email_destino.strip()
        for p in paquetes
        if p.email_destino and p.email_destino.strip()
    }

    if not destinatarios:
        raise ValueError(
            "La operación no tiene correo electrónico de destino."
        )

    if len(destinatarios) > 1:
        raise ValueError(
            "La operación tiene diferentes correos de destino."
        )

    destinatario = destinatarios.pop()

    intento = crear_solicitud_envio_email(
        codigo_proceso=codigo_proceso,
        destinatario=destinatario,
        clave_solicitud=clave_solicitud,
    )

    # La misma solicitud solo puede iniciar el envío una vez.
    if not iniciar_intento_entrega(intento.clave_idempotencia):
        return 0

    try:
        mensaje = EmailMessage(
            subject="IMAGE ENGINE - Tus fotografías procesadas",
            body=(
                "Hola,\n\n"
                "Adjuntamos las fotografías que seleccionaste.\n\n"
                "Gracias por utilizar IMAGE ENGINE."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[destinatario],
        )

        for imagen in imagenes:
            archivo = imagen.imagen_procesada

            tipo_mime = (
                mimetypes.guess_type(archivo.name)[0]
                or "application/octet-stream"
            )

            with archivo.open("rb") as contenido:
                mensaje.attach(
                    Path(archivo.name).name,
                    contenido.read(),
                    tipo_mime,
                )

        enviados = mensaje.send(fail_silently=False)

        if enviados != 1:
            raise RuntimeError(
                "El servidor de correo no confirmó el envío."
            )

    except Exception as error:
        registrar_error_entrega(
            intento.clave_idempotencia,
            str(error) or type(error).__name__,
        )
        raise

    confirmar_intento_entrega(intento.clave_idempotencia)

    # Conservamos el estado ENVIADA en las imágenes.
    # En un reenvío ya estarán marcadas y no se modificarán.
    registrar_imagenes_enviadas(codigo_proceso)

    return len(imagenes)
