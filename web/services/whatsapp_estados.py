from django.db import transaction

from web.models import EventoWhatsApp, IntentoEntregaImagen

from web.services.whatsapp_procesamiento import (
    reclamar_evento_whatsapp,
    confirmar_evento_whatsapp,
    registrar_error_evento_whatsapp,
    liberar_evento_whatsapp,
    evento_whatsapp_protegido,
)

from web.services.whatsapp_envios import (
    registrar_imagen_enviada_whatsapp,
    registrar_imagen_entregada_whatsapp,
    registrar_imagen_leida_whatsapp,
    registrar_imagen_fallida_whatsapp,
)


def es_estado_whatsapp_ya_aplicado(
    estado_meta: str,
    estado_imagen: str,
) -> bool:
    """
    Determina si una notificación de Meta ya está
    reflejada en el estado actual de la fotografía.

    No modifica registros.
    """

    estados_ya_aplicados = {
        "sent": {
            IntentoEntregaImagen.Estado.ENVIADA,
            IntentoEntregaImagen.Estado.ENTREGADA,
            IntentoEntregaImagen.Estado.LEIDA,
        },
        "delivered": {
            IntentoEntregaImagen.Estado.ENTREGADA,
            IntentoEntregaImagen.Estado.LEIDA,
        },
        "read": {
            IntentoEntregaImagen.Estado.LEIDA,
        },
        "failed": {
            IntentoEntregaImagen.Estado.ERROR,
        },
    }

    return estado_imagen in estados_ya_aplicados.get(
        (estado_meta or "").strip().lower(),
        set(),
    )

def procesar_estado_whatsapp(evento_id: int) -> bool:
    """
    Procesa un evento de estado de Meta.

    La actualización de la fotografía y la confirmación
    del evento se realizan en una misma transacción.

    No envía mensajes ni modifica créditos.
    """
    # No procesar eventos configurados como protegidos.
    if evento_whatsapp_protegido(evento_id):
        return False

    numero_intento = reclamar_evento_whatsapp(evento_id)

    if numero_intento is None:
        return False

    try:
        with transaction.atomic():

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
            
            if evento.tipo != EventoWhatsApp.Tipo.ESTADO:
                raise ValueError(
                    "El evento no corresponde a un estado de Meta."
                )

            meta_id = (evento.mensaje_id or "").strip()
            estado = (evento.estado_mensaje or "").strip().lower()

            if not meta_id:
                raise ValueError(
                    "El evento no contiene identificador de Meta."
                )

            if not IntentoEntregaImagen.objects.filter(
                meta_message_id=meta_id
            ).exists():

                # El webhook pudo llegar antes de que se guardara
                # el identificador de Meta en la fotografía.
                if not liberar_evento_whatsapp(
                    evento_id,
                    numero_intento,
                ):                
                    raise ValueError(
                        "No fue posible liberar el evento pendiente."
                    )

                return False
   
            funciones = {
                "sent": registrar_imagen_enviada_whatsapp,
                "delivered": registrar_imagen_entregada_whatsapp,
                "read": registrar_imagen_leida_whatsapp,
            }

            if estado == "failed":

                contenido = evento.contenido or {}
                errores = contenido.get("errors") or []

                if errores and isinstance(errores[0], dict):
                    error_meta = errores[0]

                    codigo = error_meta.get("code", "SIN_CODIGO")
                    titulo = error_meta.get("title", "Error de Meta")

                    detalle = (
                        error_meta.get("error_data") or {}
                    ).get("details", "")

                    mensaje_error = f"Meta {codigo}: {titulo}"

                    if detalle:
                        mensaje_error += f" - {detalle}"

                else:
                    mensaje_error = (
                        "Meta notificó un fallo sin proporcionar detalles."
                    )

                actualizado = registrar_imagen_fallida_whatsapp(
                    meta_id,
                    mensaje_error,
                )

            elif estado in funciones:

                actualizado = funciones[estado](meta_id)

            else:

                raise ValueError(
                    f"Estado de Meta no soportado: {estado}"
                )


            # Si no hubo actualización, verificar si el evento
            # ya estaba aplicado o fue superado por otro estado.
            if not actualizado:

                estado_actual = (
                    IntentoEntregaImagen.objects.filter(
                        meta_message_id=meta_id
                    )
                    .values_list("estado", flat=True)
                    .first()
                )

                if not es_estado_whatsapp_ya_aplicado(
                    estado,
                    estado_actual,
                ):
                    raise ValueError(
                        "Transición WhatsApp incompatible: "
                        f"Meta={estado}, "
                        f"Fotografía={estado_actual}, "
                        f"Mensaje={meta_id}"
                    )

            if not confirmar_evento_whatsapp(
                evento_id,
                numero_intento,
            ):
                raise ValueError(
                    "No fue posible confirmar el procesamiento."
                )

        return True

    except Exception as exc:
        registrar_error_evento_whatsapp(
            evento_id,
            str(exc),
            numero_intento,
        )
        return False

