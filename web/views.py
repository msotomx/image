import uuid
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Q, Count, Max

from web.models import Sector, UnidadVenta, PaqueteImagen, Imagen
from web.models import IntentoEntrega
from web.forms import RecepcionImagenForm
from web.services.procesamiento import recibir_imagen, procesar_variantes
from web.services.creditos import cerrar_operacion
from web.services.creditos import obtener_creditos_disponibles
from django.contrib.admin.views.decorators import staff_member_required
from web.services.intentos_entrega import (
    listar_entregas_pendientes_revision,
    registrar_revision_entrega,
)
from web.forms_entrega import RevisionEntregaForm
from web.services.entrega import enviar_imagenes_por_email
from web.services.whatsapp_qr import generar_qr_whatsapp
from web.services.whatsapp_clientes import (
    registrar_whatsapp_cliente,
)


@login_required
def inicio(request):
    sector = Sector.objects.filter(
        usuario=request.user
    ).first()

    unidad_venta = UnidadVenta.objects.filter(
        usuario=request.user
    ).select_related("sector").first()

    sector_saldo = sector or (
        unidad_venta.sector if unidad_venta else None
    )

    creditos_disponibles = (
        obtener_creditos_disponibles(sector_saldo.id)
        if sector_saldo else None
    )

    context = {
        "sector": sector,
        "unidad_venta": unidad_venta,
        "creditos_disponibles": creditos_disponibles,
    }

    return render(request, "web/inicio.html", context)

@login_required
def recibir_fotografia(request):
    unidad_venta = UnidadVenta.objects.filter(
        usuario=request.user
    ).first()

    if unidad_venta is None:
        messages.error(
            request,
            "Solo las Unidades de Venta pueden enviar fotografías."
        )
        return redirect("web:inicio")

    if request.method == "POST":
        form = RecepcionImagenForm(request.POST, request.FILES)

        if form.is_valid():
            codigo_proceso = uuid.uuid4().hex.upper()

            try:
                paquetes = recibir_imagen(
                    unidad_venta_id=unidad_venta.pk,
                    codigo_proceso=codigo_proceso,
                    imagen_original=form.cleaned_data["imagen_original"],
                )

            except ValueError as exc:
                messages.error(request, str(exc))

            else:
                messages.success(
                    request,
                    f"Fotografía recibida correctamente. "
                    f"Se reservaron {len(paquetes)} crédito(s) "
                    f"para generar {len(paquetes) * 3} imágenes. "
                    f"Operación: {codigo_proceso}"
                )
#                return redirect("web:inicio")
                return redirect(
                    "web:ver_imagenes",
                    codigo_proceso=codigo_proceso,
                )
    else:
        form = RecepcionImagenForm()

    return render(
        request,
        "web/recibir_fotografia.html",
        {"form": form},
    )

@login_required
def ver_imagenes(request, codigo_proceso):
    unidad_venta = UnidadVenta.objects.filter(
        usuario=request.user
    ).first()

    if unidad_venta is None:
        messages.error(
            request,
            "Solo las Unidades de Venta pueden consultar sus imágenes."
        )
        return redirect("web:inicio")

    paquetes = PaqueteImagen.objects.filter(
        codigo_proceso=codigo_proceso,
        unidad_venta=unidad_venta,
    )

    if not paquetes.exists():
        messages.error(
            request,
            "La operación no existe o no pertenece a tu Unidad de Venta."
        )
        return redirect("web:inicio")

    if request.method == "POST":

        accion = request.POST.get("accion", "cerrar")

        if accion == "enviar_email":

            # Solo se permite enviar operaciones cerradas.
            if paquetes.exclude(
                estado=PaqueteImagen.EstadoPaquete.CERRADO
            ).exists():
                messages.error(
                    request,
                    "La operación debe estar cerrada antes de enviar."
                )

            else:
                try:
                    clave_solicitud = request.POST.get(
                        "clave_solicitud", ""
                    ).strip()

                    if not clave_solicitud:
                        raise ValueError(
                            "La solicitud de envío no tiene una clave válida."
                        )

                    claves_email = request.session.get(
                        "claves_solicitud_email", {}
                    )

                    clave_esperada = claves_email.get(
                        codigo_proceso
                    )

                    claves_utilizadas = request.session.get(
                        "claves_email_utilizadas", {}
                    )

                    claves_anteriores = claves_utilizadas.get(
                        codigo_proceso, []
                    )

                    if (
                        clave_solicitud != clave_esperada
                        and clave_solicitud not in claves_anteriores
                    ):
                        raise ValueError(
                            "La solicitud de envío no es válida "
                            "para esta operación."
                        )

                    cantidad = enviar_imagenes_por_email(
                        codigo_proceso=codigo_proceso,
                        clave_solicitud=clave_solicitud,
                    )

                    if cantidad > 0:
                        claves_email = request.session.get(
                            "claves_solicitud_email", {}
                        )

                        # Conservar la clave utilizada para reconocer
                        # una repetición de la misma solicitud.
                        claves_utilizadas = request.session.get(
                            "claves_email_utilizadas", {}
                        )

                        claves_operacion = claves_utilizadas.get(
                            codigo_proceso, []
                        )

                        if clave_solicitud not in claves_operacion:
                            claves_operacion.append(clave_solicitud)

                        claves_utilizadas[codigo_proceso] = claves_operacion

                        # Preparar una nueva solicitud voluntaria.
                        claves_email[codigo_proceso] = uuid4().hex

                        request.session["claves_email_utilizadas"] = (
                            claves_utilizadas
                        )
                        request.session["claves_solicitud_email"] = (
                            claves_email
                        )
                        request.session.modified = True

                except (ValueError, OSError, RuntimeError) as exc:

                    # Si el servicio registró un intento fallido,
                    # preparar una clave nueva para el reintento.
                    if clave_solicitud:
                        intento_fallido = IntentoEntrega.objects.filter(
                            clave_idempotencia=f"EMAIL-{clave_solicitud}",
                            codigo_proceso=codigo_proceso,
                            estado=IntentoEntrega.Estado.ERROR,
                        ).exists()

                        if intento_fallido:
                            claves_email = request.session.get(
                                "claves_solicitud_email", {}
                            )

                            if claves_email.get(codigo_proceso) == clave_solicitud:
                                claves_email[codigo_proceso] = uuid4().hex

                                request.session["claves_solicitud_email"] = (
                                    claves_email
                                )
                                request.session.modified = True

                    messages.error(
                        request,
                        f"No fue posible enviar las fotografías: {exc}"
                    )

                else:
                    if cantidad:
                        messages.success(
                            request,
                            f"Se enviaron {cantidad} fotografías por email."
                        )
                    else:
                        messages.info(
                            request,
                            "La solicitud ya había sido procesada."
                        )


        elif accion == "registrar_whatsapp_cliente":

            if paquetes.exclude(
                estado=PaqueteImagen.EstadoPaquete.CERRADO
            ).exists():
                messages.error(
                    request,
                    "La operación debe estar cerrada antes "
                    "de registrar el WhatsApp del cliente."
                )

            else:
                try:
                    telefono = request.POST.get(
                        "whatsapp_cliente", ""
                    ).strip()

                    telefono_normalizado = registrar_whatsapp_cliente(
                        codigo_proceso=codigo_proceso,
                        telefono=telefono,
                    )

                except ValueError as exc:
                    messages.error(request, str(exc))

                else:
                    messages.success(
                        request,
                        "WhatsApp del cliente registrado correctamente: "
                        f"{telefono_normalizado}"
                    )

        elif accion == "cerrar":

            try:
                imagenes_ids = [
                    int(valor)
                    for valor in request.POST.getlist("imagenes_ids")
                ]

                cerrar_operacion(
                    codigo_proceso=codigo_proceso,
                    imagenes_ids=imagenes_ids,
                )

            except (ValueError, TypeError) as exc:
                messages.error(request, str(exc))

            else:
                messages.success(
                    request,
                    "Operación cerrada correctamente. "
                    "Las imágenes seleccionadas quedaron registradas "
                    "y los créditos correspondientes fueron consumidos."
                )

        else:
            messages.error(request, "Acción no válida.")

        return redirect(
            "web:ver_imagenes",
            codigo_proceso=codigo_proceso,
        )

    imagenes = (
        Imagen.objects.filter(
            paquete__in=paquetes,
            estado_proceso__in=[
                Imagen.EstadoProceso.PROCESADA,
                Imagen.EstadoProceso.ENVIADA,
            ],
        )
        .select_related("fondo", "paquete")
        .order_by("codigo_imagen")
    )

    operacion_cerrada = not paquetes.exclude(
        estado=PaqueteImagen.EstadoPaquete.CERRADO
    ).exists()

    operacion_procesando = paquetes.filter(
        estado=PaqueteImagen.EstadoPaquete.PROCESANDO
    ).exists()

    operacion_lista = (
        not operacion_cerrada
        and not operacion_procesando
        and not paquetes.exclude(
            estado=PaqueteImagen.EstadoPaquete.LISTO
        ).exists()
    )

    operacion_esperando = (
        not operacion_cerrada
        and not operacion_procesando
        and not operacion_lista
    )

    # carrusel de imagenes
    codigos_operaciones = list(
        PaqueteImagen.objects.filter(
            unidad_venta=unidad_venta,
        )
        .exclude(
            estado=PaqueteImagen.EstadoPaquete.CERRADO
        )
        .order_by("fecha_creacion", "id")
        .values_list("codigo_proceso", flat=True)
        .distinct()
    )

    # Evitar duplicados cuando una operación tiene dos paquetes.
    codigos_operaciones = list(dict.fromkeys(codigos_operaciones))

    operacion_anterior = None
    operacion_siguiente = None

    total_operaciones = len(codigos_operaciones)
    numero_operacion = None

    if codigo_proceso in codigos_operaciones:
        posicion = codigos_operaciones.index(codigo_proceso)

        if posicion > 0:
            operacion_anterior = codigos_operaciones[posicion - 1]

        if posicion < len(codigos_operaciones) - 1:
            operacion_siguiente = codigos_operaciones[posicion + 1]

        numero_operacion = posicion + 1

    claves_email = request.session.get(
        "claves_solicitud_email", {}
    )

    clave_solicitud_email = claves_email.get(codigo_proceso)

    if not clave_solicitud_email:
        clave_solicitud_email = uuid4().hex

        claves_email[codigo_proceso] = clave_solicitud_email

        request.session["claves_solicitud_email"] = claves_email
        request.session.modified = True    

    # Comprobar que todos los paquetes tengan
    # el mismo WhatsApp del cliente registrado.
    telefonos_registrados = {
        (paquete.whatsapp_destino or "").strip()
        for paquete in paquetes
    }

    whatsapp_cliente_registrado = (
        len(telefonos_registrados) == 1
        and bool(next(iter(telefonos_registrados), ""))
    )

    # Generar el QR solamente cuando la operación
    # esté cerrada y el cliente tenga WhatsApp registrado.
    qr_whatsapp = None

    if operacion_cerrada and whatsapp_cliente_registrado:
        qr_whatsapp = generar_qr_whatsapp(codigo_proceso)

    return render(
        request,
        "web/ver_imagenes.html",
        {
            "codigo_proceso": codigo_proceso,
            "imagenes": imagenes,
            "operacion_cerrada": operacion_cerrada,
            "operacion_procesando": operacion_procesando,
            "operacion_lista": operacion_lista,
            "operacion_esperando": operacion_esperando,
            "operacion_anterior": operacion_anterior,
            "operacion_siguiente": operacion_siguiente,
            "total_operaciones": total_operaciones,
            "numero_operacion": numero_operacion,
            "clave_solicitud_email": clave_solicitud_email,
            "qr_whatsapp": qr_whatsapp,
        },
    )

@login_required
def historial_operaciones(request):
    unidad_venta = (
        UnidadVenta.objects
        .filter(usuario=request.user)
        .first()
    )

    if unidad_venta is None:
        messages.error(
            request,
            "Solo las Unidades de Venta pueden consultar su historial."
        )
        return redirect("web:inicio")

    operaciones = (
        PaqueteImagen.objects
        .filter(unidad_venta=unidad_venta)
        .values("codigo_proceso")
        .annotate(
            fecha_creacion=Max("fecha_creacion"),
            total_paquetes=Count("id"),
            fecha_cierre=Max("fecha_cierre"),
            paquetes_cerrados=Count(
                "id",
                filter=Q(estado=PaqueteImagen.EstadoPaquete.CERRADO),
            ),
            paquetes_listos=Count(
                "id",
                filter=Q(estado=PaqueteImagen.EstadoPaquete.LISTO),
            ),
            paquetes_procesando=Count(
                "id",
                filter=Q(estado=PaqueteImagen.EstadoPaquete.PROCESANDO),
            ),
        )
        .order_by("-fecha_creacion")
    )

    operaciones = list(operaciones)

    for operacion in operaciones:

        total = operacion["total_paquetes"]

        if operacion["paquetes_cerrados"] == total:
            estado = "CERRADO"

        elif operacion["paquetes_procesando"] > 0:
            estado = "PROCESANDO"

        elif operacion["paquetes_listos"] == total:
            estado = "LISTO"

        else:
            estado = "RECIBIDO"

        operacion["estado_operacion"] = estado

    return render(
        request,
        "web/historial_operaciones.html",
        {
            "unidad_venta": unidad_venta,
            "operaciones": operaciones,
        },
    )

@staff_member_required
def entregas_pendientes_revision(request):
    """
    Consulta administrativa de entregas pendientes.

    Solo lectura. No modifica intentos ni créditos.
    """

    entregas = listar_entregas_pendientes_revision(
        minutos=15
    )

    resumen = {
        "total": len(entregas),
        "tiempo_excedido": sum(
            1 for entrega in entregas
            if entrega["motivo_revision"] == "TIEMPO_EXCEDIDO"
        ),
        "sin_fecha_inicio": sum(
            1 for entrega in entregas
            if entrega["motivo_revision"] == "SIN_FECHA_INICIO"
        ),
    }

    return render(
        request,
        "web/entregas_pendientes_revision.html",
        {
            "entregas": entregas,
            "resumen": resumen,
        },
    )

@staff_member_required
def detalle_intento_entrega(request, intento_id):
    """
    Consulta administrativa del detalle de un intento.

    Solo lectura.
    No modifica estados ni créditos.
    """

    intento = get_object_or_404(
        IntentoEntrega,
        pk=intento_id,
    )

    return render(
        request,
        "web/detalle_intento_entrega.html",
        {
            "intento": intento,
        },
    )

@staff_member_required
def detalle_intento_entrega(request, intento_id):
    """
    Consulta y registro de revisión administrativa.

    No realiza envíos ni modifica créditos.
    """

    intento = get_object_or_404(
        IntentoEntrega,
        pk=intento_id,
    )

    puede_revisar = (
        intento.estado == IntentoEntrega.Estado.ENVIANDO
        and intento.fecha_revision is None
    )

    if request.method == "POST":
        if not puede_revisar:
            messages.error(
                request,
                "Este intento no admite una nueva revisión.",
            )
            return redirect(
                "web:detalle_intento_entrega",
                intento_id=intento.pk,
            )

        formulario = RevisionEntregaForm(request.POST)

        if formulario.is_valid():
            try:
                registrar_revision_entrega(
                    intento_id=intento.pk,
                    usuario=request.user,
                    resultado=formulario.cleaned_data["resultado"],
                    observaciones=formulario.cleaned_data["observaciones"],
                )
            except ValueError as error:
                formulario.add_error(None, str(error))
            else:
                messages.success(
                    request,
                    "Revisión administrativa registrada correctamente.",
                )
                return redirect(
                    "web:detalle_intento_entrega",
                    intento_id=intento.pk,
                )
    else:
        formulario = RevisionEntregaForm() if puede_revisar else None

    return render(
        request,
        "web/detalle_intento_entrega.html",
        {
            "intento": intento,
            "formulario": formulario,
            "puede_revisar": puede_revisar,
        },
    )
