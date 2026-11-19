from types import SimpleNamespace  
from django.test import TestCase
from unittest.mock import MagicMock, patch
from django.test import override_settings

from web.services.whatsapp_entrega import enviar_fotografia_whatsapp
from web.services.whatsapp_meta import (
    WhatsAppMetaError,
    WhatsAppMetaResultadoIncierto,
)
from web.services.whatsapp_solicitudes import normalizar_whatsapp_mexico
from web.services.whatsapp_solicitudes import validar_remitente_whatsapp
from web.models import EventoWhatsApp, IntentoEntregaImagen
from web.services.whatsapp_mensajes import procesar_mensaje_whatsapp


class WhatsAppEntregaTests(TestCase):
    @override_settings(WHATSAPP_ENVIO_HABILITADO=True)
    @patch("web.services.whatsapp_entrega.confirmar_entrega_whatsapp_completa")
    @patch("web.services.whatsapp_entrega.registrar_envio_imagen_incierto_whatsapp")
    @patch("web.services.whatsapp_entrega.registrar_error_envio_imagen_whatsapp")
    @patch("web.services.whatsapp_entrega.confirmar_aceptacion_imagen_whatsapp")
    @patch("web.services.whatsapp_entrega.enviar_imagen_whatsapp_meta")
    @patch("web.services.whatsapp_entrega.subir_imagen_whatsapp_meta")
    @patch("web.services.whatsapp_entrega.IntentoEntregaImagen")
    @patch("web.services.whatsapp_entrega.reclamar_imagen_whatsapp")
    def test_flujo_whatsapp(
        self,
        reclamar,
        modelo,
        subir,
        enviar,
        confirmar,
        registrar_error,
        registrar_incierto,
        cerrar_entrega,
    ):
        reclamar.return_value = True

        archivo = MagicMock()
        archivo.__enter__.return_value = archivo

        imagen = SimpleNamespace(
            imagen_procesada=MagicMock(),
            seleccionada=True,
            estado_proceso="PROCESADA",
            EstadoProceso=SimpleNamespace(
                PROCESADA="PROCESADA",
                ENVIADA="ENVIADA",
            ),
        )
        imagen.imagen_procesada.open.return_value = archivo

        entrega = SimpleNamespace(
            imagen=imagen,
            intento_id=100,
            intento=SimpleNamespace(
                id=100,
                destinatario="5216561234567",
            ),
        )

        modelo.objects.select_related.return_value.get.return_value = entrega

        subir.return_value = "MEDIA_PRUEBA"
        enviar.return_value = "wamid.PRUEBA"
        confirmar.return_value = True

        argumentos = {
            "entrega_imagen_id": 999,
            "access_token": "TOKEN_SIMULADO",
            "phone_number_id": "PHONE_SIMULADO",
        }

        # Caso 1: Meta acepta el mensaje.
        resultado = enviar_fotografia_whatsapp(**argumentos)

        self.assertEqual(resultado, "wamid.PRUEBA")
        confirmar.assert_called_once_with(999, "wamid.PRUEBA")
        registrar_error.assert_not_called()
        registrar_incierto.assert_not_called()

        # Caso 2: Meta rechaza explícitamente el envío.
        confirmar.reset_mock()
        enviar.side_effect = WhatsAppMetaError("Rechazo HTTP 400")

        resultado = enviar_fotografia_whatsapp(**argumentos)

        self.assertIsNone(resultado)
        registrar_error.assert_called_once()
        registrar_incierto.assert_not_called()

        # Caso 3: resultado incierto del envío.
        registrar_error.reset_mock()
        enviar.side_effect = WhatsAppMetaResultadoIncierto(
            "Tiempo de espera agotado"
        )

        resultado = enviar_fotografia_whatsapp(**argumentos)

        self.assertIsNone(resultado)
        registrar_incierto.assert_called_once()
        registrar_error.assert_not_called()

    @patch("web.services.whatsapp_envios.IntentoEntregaImagen.objects")
    def test_confirmacion_tardia(self, objetos):
        from web.services.whatsapp_envios import (
            confirmar_aceptacion_imagen_whatsapp,
        )

        objetos.filter.return_value.update.return_value = 1

        resultado = confirmar_aceptacion_imagen_whatsapp(
            entrega_imagen_id=999,
            meta_message_id="wamid.TARDIO",
        )

        self.assertTrue(resultado)

        filtros = objetos.filter.call_args.kwargs

        self.assertEqual(
            filtros["estado__in"],
            ["ENVIANDO", "INCIERTA"],
        )

    @patch("web.services.whatsapp_envios.IntentoEntregaImagen.objects")
    @patch("web.services.whatsapp_envios.IntentoEntrega.objects")
    def test_cierre_entrega_whatsapp(self, intentos, imagenes):
        from types import SimpleNamespace
        from web.services.whatsapp_envios import (
            confirmar_entrega_whatsapp_completa,
        )

        intento = MagicMock()
        intento.canal = "WHATSAPP"
        intento.estado = "ENVIANDO"

        intentos.select_for_update.return_value.get.return_value = intento

        # El servicio utiliza filter(pk=...), no get().
        intentos.select_for_update.return_value.filter.return_value.get.return_value = intento

        consulta = (
            imagenes.select_for_update.return_value
            .filter.return_value
            .order_by.return_value
        )

        # Caso 1: todas las fotografías aceptadas.
        consulta.__iter__.return_value = iter([
            SimpleNamespace(
                estado="ACEPTADA",
                meta_message_id="wamid.1",
            ),
            SimpleNamespace(
                estado="ENTREGADA",
                meta_message_id="wamid.2",
            ),
        ])

        resultado = confirmar_entrega_whatsapp_completa(999)

        self.assertTrue(resultado)
        self.assertEqual(intento.estado, "ENVIADO")
        intento.save.assert_called_once()

        # Caso 2: una fotografía con resultado incierto.
        intento.reset_mock()
        intento.estado = "ENVIANDO"

        consulta.__iter__.return_value = iter([
            SimpleNamespace(
                estado="ACEPTADA",
                meta_message_id="wamid.1",
            ),
            SimpleNamespace(
                estado="INCIERTA",
                meta_message_id=None,
            ),
        ])

        resultado = confirmar_entrega_whatsapp_completa(999)

        self.assertFalse(resultado)
        self.assertEqual(intento.estado, "ENVIANDO")
        intento.save.assert_not_called()

    @override_settings(WHATSAPP_ENVIO_HABILITADO=True)
    @patch(
        "web.services.whatsapp_entrega.confirmar_entrega_whatsapp_completa"
    )
    @patch(
        "web.services.whatsapp_entrega.confirmar_aceptacion_imagen_whatsapp"
    )
    @patch(
        "web.services.whatsapp_entrega.enviar_imagen_whatsapp_meta"
    )
    @patch(
        "web.services.whatsapp_entrega.subir_imagen_whatsapp_meta"
    )
    @patch(
        "web.services.whatsapp_entrega.IntentoEntregaImagen.objects"
    )
    @patch(
        "web.services.whatsapp_entrega.reclamar_imagen_whatsapp"
    )
    def test_cierre_automatico_despues_de_aceptacion(
        self,
        reclamar,
        objetos,
        subir,
        enviar,
        confirmar,
        cerrar,
    ):
        from unittest.mock import MagicMock

        from web.services.whatsapp_entrega import (
            enviar_fotografia_whatsapp,
        )

        reclamar.return_value = True

        entrega = MagicMock()
        entrega.intento_id = 123
        entrega.intento.destinatario = "5216560000000"
        entrega.imagen.seleccionada = True
        entrega.imagen.estado_proceso = "PROCESADA"
        entrega.imagen.EstadoProceso.PROCESADA = "PROCESADA"
        entrega.imagen.EstadoProceso.ENVIADA = "ENVIADA"

        objetos.select_related.return_value.get.return_value = entrega

        subir.return_value = "media-123"
        enviar.return_value = "wamid.123"
        confirmar.return_value = True

        resultado = enviar_fotografia_whatsapp(
            entrega_imagen_id=456,
            access_token="token-prueba",
            phone_number_id="telefono-prueba",
        )

        self.assertEqual(resultado, "wamid.123")

        confirmar.assert_called_once_with(
            456,
            "wamid.123",
        )

        cerrar.assert_called_once_with(123)

    @override_settings(WHATSAPP_ENVIO_HABILITADO=True)
    @patch(
        "web.services.whatsapp_entrega.registrar_envio_imagen_incierto_whatsapp"
    )
    @patch(
        "web.services.whatsapp_entrega.confirmar_aceptacion_imagen_whatsapp"
    )
    @patch(
        "web.services.whatsapp_entrega.enviar_imagen_whatsapp_meta"
    )
    @patch(
        "web.services.whatsapp_entrega.subir_imagen_whatsapp_meta"
    )
    @patch(
        "web.services.whatsapp_entrega.IntentoEntregaImagen.objects"
    )
    @patch(
        "web.services.whatsapp_entrega.reclamar_imagen_whatsapp"
    )
    def test_conservar_meta_message_id_en_envio_incierto(
        self,
        reclamar,
        objetos,
        subir,
        enviar,
        confirmar,
        registrar_incierto,
    ):
        from unittest.mock import MagicMock
        from web.services.whatsapp_entrega import (
            enviar_fotografia_whatsapp,
        )

        reclamar.return_value = True

        entrega = MagicMock()
        entrega.imagen.seleccionada = True
        entrega.imagen.estado_proceso = "PROCESADA"
        entrega.imagen.EstadoProceso.PROCESADA = "PROCESADA"
        entrega.imagen.EstadoProceso.ENVIADA = "ENVIADA"

        objetos.select_related.return_value.get.return_value = entrega

        subir.return_value = "media-123"
        enviar.return_value = "wamid.123"
        confirmar.return_value = False

        resultado = enviar_fotografia_whatsapp(
            entrega_imagen_id=456,
            access_token="token-prueba",
            phone_number_id="telefono-prueba",
        )

        self.assertIsNone(resultado)

        registrar_incierto.assert_called_once()

        args, kwargs = registrar_incierto.call_args

        self.assertEqual(args[0], 456)
        self.assertIn("wamid.123", args[1])
        self.assertEqual(
            kwargs["meta_message_id"],
            "wamid.123",
        )


    def test_persistencia_meta_message_id_incierto(self):
        from uuid import uuid4

        from django.contrib.auth import get_user_model

        from web.models import (
            Estado,
            Ciudad,
            Sector,
            UnidadVenta,
            PaqueteImagen,
            Fondo,
            Imagen,
            IntentoEntrega,
            IntentoEntregaImagen,
        )

        from web.services.whatsapp_envios import (
            registrar_envio_imagen_incierto_whatsapp,
        )

        # Identificadores exclusivos para esta ejecución.
        sufijo = uuid4().hex
        codigo_sector = f"W{sufijo[:5]}"
        codigo_proceso = f"PROCESO-WA-{sufijo[:12]}"
        codigo_fondo = f"FONDO-WA-{sufijo[:12]}"
        codigo_imagen = f"IMAGEN-WA-{sufijo[:12]}"
        clave_idempotencia = f"WA-INCIERTO-{sufijo[:12]}"

        meta_id_original = f"wamid.prueba.{sufijo}"
        meta_id_segundo = f"wamid.segundo.{sufijo}"

        # 1. Usuario exclusivo de la prueba.
        usuario = get_user_model().objects.create_user(
            username=f"wa_test_{sufijo[:12]}",
            password="Test12345!",
        )

        # 2. Catálogos compartidos.
        estado, _ = Estado.objects.get_or_create(
            nombre="Chihuahua",
            defaults={
                "abreviacion": "CHIH",
            },
        )

        ciudad, _ = Ciudad.objects.get_or_create(
            estado=estado,
            nombre="Ciudad Juárez",
            defaults={
                "codigo_ciudad": "JUAREZ",
            },
        )

        # 3. Sector y unidad exclusivos.
        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector=codigo_sector,
            nombre=f"Sector WA {sufijo}",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            usuario=usuario,
            nombre=f"Unidad WA {sufijo[:12]}",
            whatsapp="6560000000",
        )

        # 4. Paquete y fotografía de prueba.
        paquete = PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            codigo_proceso=codigo_proceso,
        )

        fondo = Fondo.objects.create(
            codigo_fondo=codigo_fondo,
        )

        imagen = Imagen.objects.create(
            paquete=paquete,
            fondo=fondo,
            codigo_imagen=codigo_imagen,
            seleccionada=True,
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        # 5. Intento general WhatsApp.
        intento = IntentoEntrega.objects.create(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.WHATSAPP,
            destinatario="5216560000000",
            clave_idempotencia=clave_idempotencia,
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        # 6. Fotografía inicialmente ENVIANDO.
        entrega = IntentoEntregaImagen.objects.create(
            intento=intento,
            imagen=imagen,
            estado=IntentoEntregaImagen.Estado.ENVIANDO,
        )

        # 7. Primera actualización:
        # ENVIANDO -> INCIERTA, guardando el ID de Meta.
        resultado = registrar_envio_imagen_incierto_whatsapp(
            entrega.pk,
            "Meta aceptó el mensaje, pero falló la confirmación.",
            meta_message_id=meta_id_original,
        )

        self.assertTrue(resultado)

        entrega.refresh_from_db()

        self.assertEqual(
            entrega.estado,
            IntentoEntregaImagen.Estado.INCIERTA,
        )

        self.assertEqual(
            entrega.meta_message_id,
            meta_id_original,
        )

        # 8. Segunda actualización:
        # No debe sobrescribir el ID ni cambiar el estado.
        resultado_segundo = registrar_envio_imagen_incierto_whatsapp(
            entrega.pk,
            "Segundo intento de actualización.",
            meta_message_id=meta_id_segundo,
        )

        self.assertFalse(resultado_segundo)

        entrega.refresh_from_db()

        self.assertEqual(
            entrega.meta_message_id,
            meta_id_original,
        )

        self.assertEqual(
            entrega.estado,
            IntentoEntregaImagen.Estado.INCIERTA,
        )

        # 9. Verificar que el intento general
        # permanece en ENVIANDO.
        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

    @override_settings(WHATSAPP_ENVIO_HABILITADO=True)
    @patch("web.services.whatsapp_entrega.logger")
    @patch("web.services.whatsapp_entrega.confirmar_entrega_whatsapp_completa")
    @patch("web.services.whatsapp_entrega.enviar_imagen_whatsapp_meta")
    @patch("web.services.whatsapp_entrega.confirmar_aceptacion_imagen_whatsapp")
    def test_error_cierre_automatico_registra_log(
        self,
        confirmar,
        enviar,
        cerrar,
        logger,
    ):
        confirmar.return_value = True
        enviar.return_value = "wamid.PRUEBA"

        cerrar.side_effect = RuntimeError("Error simulado de cierre")

        with patch(
            "web.services.whatsapp_entrega.reclamar_imagen_whatsapp",
            return_value=True,
        ), patch(
            "web.services.whatsapp_entrega.IntentoEntregaImagen"
        ) as modelo, patch(
            "web.services.whatsapp_entrega.subir_imagen_whatsapp_meta",
            return_value="MEDIA_PRUEBA",
        ):
            imagen = SimpleNamespace(
                imagen_procesada=MagicMock(),
                seleccionada=True,
                estado_proceso="PROCESADA",
                EstadoProceso=SimpleNamespace(
                    PROCESADA="PROCESADA",
                    ENVIADA="ENVIADA",
                ),
            )

            imagen.imagen_procesada.open.return_value = MagicMock()

            entrega = SimpleNamespace(
                imagen=imagen,
                intento_id=100,
                intento=SimpleNamespace(destinatario="5216561234567"),
            )

            modelo.objects.select_related.return_value.get.return_value = entrega

            resultado = enviar_fotografia_whatsapp(
                entrega_imagen_id=999,
                access_token="TOKEN_SIMULADO",
                phone_number_id="PHONE_SIMULADO",
            )

        self.assertEqual(resultado, "wamid.PRUEBA")
        enviar.assert_called_once()
        confirmar.assert_called_once_with(999, "wamid.PRUEBA")
        cerrar.assert_called_once_with(100)
        logger.exception.assert_called_once()

    @patch("web.services.intentos_entrega.crear_intento_entrega")
    def test_crear_solicitud_envio_whatsapp(self, crear_intento):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_solicitud_envio_whatsapp,
        )

        intento_simulado = SimpleNamespace(id=100)
        crear_intento.return_value = intento_simulado

        argumentos = {
            "codigo_proceso": "PROCESO-001",
            "destinatario": "5216561234567",
            "clave_solicitud": "SOLICITUD-001",
        }

        resultado = crear_solicitud_envio_whatsapp(**argumentos)

        self.assertIs(resultado, intento_simulado)

        crear_intento.assert_called_once_with(
            codigo_proceso="PROCESO-001",
            canal=IntentoEntrega.Canal.WHATSAPP,
            destinatario="5216561234567",
            clave_idempotencia="WHATSAPP-SOLICITUD-001",
        )

        crear_intento.reset_mock()

        crear_solicitud_envio_whatsapp(
            **{**argumentos, "clave_solicitud": "SOLICITUD-002"}
        )

        crear_intento.assert_called_once_with(
            codigo_proceso="PROCESO-001",
            canal=IntentoEntrega.Canal.WHATSAPP,
            destinatario="5216561234567",
            clave_idempotencia="WHATSAPP-SOLICITUD-002",
        )

    @patch("web.services.whatsapp_imagenes.preparar_imagenes_whatsapp")
    @patch("web.services.whatsapp_imagenes.crear_solicitud_envio_whatsapp")
    def test_preparar_solicitud_envio_whatsapp(
        self,
        crear_solicitud,
        preparar_imagenes,
    ):
        from web.models import IntentoEntrega
        from web.services.whatsapp_imagenes import (
            preparar_solicitud_envio_whatsapp,
        )

        crear_solicitud.return_value = SimpleNamespace(
            pk=100,
            estado=IntentoEntrega.Estado.PENDIENTE,
        )
        preparar_imagenes.return_value = 3

        resultado = preparar_solicitud_envio_whatsapp(
            codigo_proceso="PROCESO-001",
            destinatario="5216561234567",
            clave_solicitud="SOLICITUD-001",
        )

        self.assertEqual(resultado, (100, 3))
        preparar_imagenes.assert_called_once_with(100)

    @override_settings(WHATSAPP_ENVIO_HABILITADO=True)
    @patch(
        "web.services.whatsapp_ejecutor."
        "cerrar_intento_whatsapp_con_errores"
    )
    @patch("web.services.whatsapp_ejecutor.enviar_fotografia_whatsapp")
    @patch("web.services.whatsapp_ejecutor.iniciar_intento_entrega")
    @patch("web.services.whatsapp_ejecutor.IntentoEntregaImagen")
    @patch("web.services.whatsapp_ejecutor.IntentoEntrega")
    def test_ejecutar_solicitud_whatsapp(
        self,
        modelo_intento,
        modelo_imagen,
        iniciar,
        enviar,
        cerrar_intento,
    ):
        from web.models import IntentoEntrega
        from web.services.whatsapp_ejecutor import (
            ejecutar_solicitud_whatsapp,
        )

        intento = SimpleNamespace(
            pk=100,
            canal=IntentoEntrega.Canal.WHATSAPP,
            clave_idempotencia="WHATSAPP-SOLICITUD-001",
        )

        modelo_intento.objects.get.return_value = intento
        modelo_intento.Canal.WHATSAPP = IntentoEntrega.Canal.WHATSAPP

        consulta = modelo_imagen.objects.filter.return_value
        consulta.count.return_value = 2
        consulta.values_list.return_value = [201, 202]

        iniciar.return_value = True
        enviar.side_effect = ["META-001", None]

        resultado = ejecutar_solicitud_whatsapp(
            intento_id=100,
            access_token="TOKEN-PRUEBA",
            phone_number_id="PHONE-PRUEBA",
        )

        self.assertEqual(
            resultado,
            {
                "iniciado": True,
                "fotografias": 2,
                "aceptadas": 1,
                "fallidas": 1,
            },
        )

        iniciar.assert_called_once_with(
            "WHATSAPP-SOLICITUD-001"
        )

        self.assertEqual(enviar.call_count, 2)
        cerrar_intento.assert_called_once_with(100)

    @patch(
        "web.services.whatsapp_recuperacion."
        "reintento_whatsapp_listo",
        return_value=True,
    )
    @patch(
        "web.services.whatsapp_recuperacion."
        "IntentoEntregaImagen"
    )
    @patch(
        "web.services.whatsapp_recuperacion."
        "crear_intento_entrega"
    )
    @patch(
        "web.services.whatsapp_recuperacion."
        "siguiente_reintento_whatsapp"
    )
    def test_preparar_reintento_automatico_whatsapp(
        self,
        siguiente,
        crear_intento,
        modelo_imagen,
        listo,
    ):
        from web.models import IntentoEntrega
        from web.services.whatsapp_recuperacion import (
            preparar_reintento_automatico_whatsapp,
        )

        original = SimpleNamespace(
            pk=125,
            imagen=SimpleNamespace(pk=50),
            intento=SimpleNamespace(
                codigo_proceso="PROCESO-001",
                destinatario="5216561234567",
            ),
        )

        modelo_imagen.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = original

        modelo_imagen.objects.get_or_create.return_value = (
            SimpleNamespace(pk=300),
            True,
        )

        modelo_imagen.objects.get_or_create.reset_mock()

        siguiente.return_value = 1
        crear_intento.return_value = SimpleNamespace(pk=200)

        # El canal del modelo simulado debe coincidir
        # con el valor del modelo real.
        resultado = preparar_reintento_automatico_whatsapp(125)

        self.assertEqual(resultado, (200, 1))
        listo.assert_called_once_with(125)
        
        crear_intento.assert_called_once_with(
            codigo_proceso="PROCESO-001",
            canal=IntentoEntrega.Canal.WHATSAPP,
            destinatario="5216561234567",
            clave_idempotencia="WA-RETRY-125-1",
        )

        modelo_imagen.objects.get_or_create.assert_called_once_with(
            intento=crear_intento.return_value,
            imagen=original.imagen,
        )

    @patch(
        "web.services.whatsapp_recuperacion."
        "crear_intento_entrega"
    )
    @patch(
        "web.services.whatsapp_recuperacion."
        "reintento_whatsapp_listo",
        return_value=False,
    )
    @patch(
        "web.services.whatsapp_recuperacion."
        "IntentoEntregaImagen"
    )
    def test_reintento_whatsapp_antes_de_cinco_minutos(
        self,
        modelo_imagen,
        listo,
        crear_intento,
    ):
        from web.services.whatsapp_recuperacion import (
            preparar_reintento_automatico_whatsapp,
        )

        entrega = SimpleNamespace(pk=125)

        modelo_imagen.objects.select_for_update.return_value \
            .select_related.return_value.get.return_value = entrega

        resultado = preparar_reintento_automatico_whatsapp(125)

        self.assertIsNone(resultado)
        listo.assert_called_once_with(125)
        crear_intento.assert_not_called()
        modelo_imagen.objects.get_or_create.assert_not_called()

    @patch(
        "web.services.whatsapp_recuperacion."
        "preparar_reintento_automatico_whatsapp"
    )
    @patch(
        "web.services.whatsapp_recuperacion."
        "obtener_candidatas_recuperacion_whatsapp"
    )
    def test_preparar_lote_recuperacion_whatsapp(
        self,
        obtener_candidatas,
        preparar_reintento,
    ):
        from web.services.whatsapp_recuperacion import (
            preparar_lote_recuperacion_whatsapp,
        )

        obtener_candidatas.return_value = [
            SimpleNamespace(pk=101),
            SimpleNamespace(pk=102),
            SimpleNamespace(pk=103),
        ]

        preparar_reintento.side_effect = [
            (201, 1),
            None,
            ValueError("Error simulado"),
        ]

        resultado = preparar_lote_recuperacion_whatsapp(
            limite=3
        )

        self.assertEqual(
            resultado,
            {
                "revisadas": 3,
                "preparadas": 1,
                "omitidas": 1,
                "errores": 1,
            },
        )

        self.assertEqual(preparar_reintento.call_count, 3)

    @patch("web.services.whatsapp_ejecutor.registrar_error_entrega")
    @patch("web.services.whatsapp_ejecutor.IntentoEntregaImagen")
    @patch("web.services.whatsapp_ejecutor.IntentoEntrega")
    def test_cerrar_intento_whatsapp_con_errores(
        self,
        modelo_intento,
        modelo_imagen,
        registrar_error,
    ):
        from web.models import IntentoEntrega
        from web.services.whatsapp_ejecutor import (
            cerrar_intento_whatsapp_con_errores,
        )

        intento = SimpleNamespace(
            pk=100,
            canal=IntentoEntrega.Canal.WHATSAPP,
            estado=IntentoEntrega.Estado.ENVIANDO,
            clave_idempotencia="WHATSAPP-SOLICITUD-001",
        )

        modelo_intento.Canal.WHATSAPP = IntentoEntrega.Canal.WHATSAPP
        modelo_intento.Estado.ENVIANDO = IntentoEntrega.Estado.ENVIANDO

        modelo_intento.objects.select_for_update.return_value \
            .get.return_value = intento

        consulta = modelo_imagen.objects.filter.return_value

        consulta.exists.return_value = True

        consulta.filter.return_value.exists.side_effect = [
            False,  # No hay fotografías pendientes o enviándose
            True,   # Sí hay fotografías en ERROR o INCIERTA
        ]

        registrar_error.return_value = True

        resultado = cerrar_intento_whatsapp_con_errores(100)

        self.assertTrue(resultado)

        registrar_error.assert_called_once_with(
            "WHATSAPP-SOLICITUD-001",
            "Entrega WhatsApp finalizada con fotografías "
            "en ERROR o INCIERTA.",
        )

    @patch("web.services.whatsapp_envios.IntentoEntregaImagen")
    def test_confirmacion_tardia_con_intento_en_error(
        self,
        modelo_imagen,
    ):
        from web.models import IntentoEntrega
        from web.services.whatsapp_envios import (
            confirmar_aceptacion_imagen_whatsapp,
        )

        modelo_imagen.objects.filter.return_value.update.return_value = 1

        resultado = confirmar_aceptacion_imagen_whatsapp(
            entrega_imagen_id=125,
            meta_message_id="META-TARDIO-001",
        )

        self.assertTrue(resultado)

        filtros = modelo_imagen.objects.filter.call_args.kwargs

        self.assertIn(
            IntentoEntrega.Estado.ERROR,
            filtros["intento__estado__in"],
        )

        self.assertIn(
            IntentoEntrega.Estado.ENVIANDO,
            filtros["intento__estado__in"],
        )


    @patch("web.services.whatsapp_envios.IntentoEntregaImagen")
    def test_confirmacion_tardia_respeta_meta_message_id(
        self,
        modelo_imagen,
    ):
        from django.db.models import Q
        from web.models import IntentoEntrega
        from web.services.whatsapp_envios import (
            confirmar_aceptacion_imagen_whatsapp,
        )

        modelo_imagen.objects.filter.return_value.update.return_value = 1

        resultado = confirmar_aceptacion_imagen_whatsapp(
            entrega_imagen_id=125,
            meta_message_id="META-TARDIO-001",
        )

        self.assertTrue(resultado)

        llamada = modelo_imagen.objects.filter.call_args
        condiciones = llamada.args
        filtros = llamada.kwargs

        self.assertEqual(len(condiciones), 1)

        self.assertEqual(
            condiciones[0],
            Q(meta_message_id__isnull=True)
            | Q(meta_message_id="META-TARDIO-001"),
        )

        self.assertIn(
            IntentoEntrega.Estado.ERROR,
            filtros["intento__estado__in"],
        )

        self.assertEqual(
            filtros["pk"],
            125,
        )

    @patch("web.services.whatsapp_envios.IntentoEntregaImagen")
    def test_estados_meta_resuelven_imagen_incierta(
        self,
        modelo_imagen,
    ):
        from web.models import IntentoEntregaImagen
        from web.services.whatsapp_envios import (
            registrar_imagen_enviada_whatsapp,
            registrar_imagen_entregada_whatsapp,
            registrar_imagen_leida_whatsapp,
            registrar_imagen_fallida_whatsapp,
        )

        modelo_imagen.Estado = IntentoEntregaImagen.Estado
        modelo_imagen.objects.filter.return_value.update.return_value = 1

        casos = [
            (registrar_imagen_enviada_whatsapp, False),
            (registrar_imagen_entregada_whatsapp, False),
            (registrar_imagen_leida_whatsapp, False),
            (registrar_imagen_fallida_whatsapp, True),
        ]

        for funcion, requiere_error in casos:
            with self.subTest(funcion=funcion.__name__):
                modelo_imagen.objects.filter.reset_mock()

                if requiere_error:
                    resultado = funcion(
                        "META-TARDIO-001",
                        "Error confirmado por Meta",
                    )
                else:
                    resultado = funcion("META-TARDIO-001")

                self.assertTrue(resultado)

                filtros = modelo_imagen.objects.filter.call_args.kwargs

                self.assertIn(
                    IntentoEntregaImagen.Estado.INCIERTA,
                    filtros["estado__in"],
                )

    @patch(
        "web.services.whatsapp_reintentos_trabajador."
        "ejecutar_solicitud_whatsapp"
    )
    @patch(
        "web.services.whatsapp_reintentos_trabajador."
        "IntentoEntrega"
    )
    def test_ejecutar_lote_reintentos_whatsapp(
        self,
        modelo_intento,
        ejecutar,
    ):
        from web.models import IntentoEntrega
        from web.services.whatsapp_reintentos_trabajador import (
            ejecutar_lote_reintentos_whatsapp,
        )

        modelo_intento.Canal.WHATSAPP = IntentoEntrega.Canal.WHATSAPP
        modelo_intento.Estado.PENDIENTE = IntentoEntrega.Estado.PENDIENTE

        consulta = modelo_intento.objects.filter.return_value
        consulta.order_by.return_value.values_list.return_value.__getitem__.return_value = [
            101, 102
        ]

        ejecutar.return_value = {"iniciado": True}

        resultado = ejecutar_lote_reintentos_whatsapp(
            access_token="TOKEN_SIMULADO",
            phone_number_id="PHONE_SIMULADO",
            limite=2,
        )

        self.assertEqual(resultado["seleccionados"], 2)
        self.assertEqual(resultado["iniciados"], 2)
        self.assertEqual(resultado["errores"], 0)
        self.assertEqual(ejecutar.call_count, 2)

        filtros = modelo_intento.objects.filter.call_args.kwargs

        self.assertEqual(
            filtros["clave_idempotencia__startswith"],
            "WA-RETRY-",
        )
        self.assertEqual(
            filtros["estado"],
            IntentoEntrega.Estado.PENDIENTE,
        )

    @patch(
        "web.services.whatsapp_reintentos_trabajador."
        "ejecutar_lote_reintentos_whatsapp"
    )
    @patch(
        "web.services.whatsapp_reintentos_trabajador."
        "preparar_lote_recuperacion_whatsapp"
    )
    def test_procesar_ciclo_recuperacion_whatsapp(
        self,
        preparar,
        ejecutar,
    ):
        from web.services.whatsapp_reintentos_trabajador import (
            procesar_ciclo_recuperacion_whatsapp,
        )

        preparar.return_value = {
            "revisadas": 2,
            "preparadas": 2,
            "omitidas": 0,
            "errores": 0,
        }

        ejecutar.return_value = {
            "seleccionados": 2,
            "iniciados": 2,
            "omitidos": 0,
            "errores": 0,
        }

        resultado = procesar_ciclo_recuperacion_whatsapp(
            access_token="TOKEN_SIMULADO",
            phone_number_id="PHONE_SIMULADO",
            limite=2,
        )

        preparar.assert_called_once_with(limite=2)

        ejecutar.assert_called_once_with(
            access_token="TOKEN_SIMULADO",
            phone_number_id="PHONE_SIMULADO",
            limite=2,
        )

        self.assertEqual(
            resultado["preparacion"]["preparadas"],
            2,
        )
        self.assertEqual(
            resultado["ejecucion"]["iniciados"],
            2,
        )

    @patch(
        "web.services.whatsapp_imagenes."
        "preparar_imagenes_whatsapp"
    )
    @patch(
        "web.services.whatsapp_imagenes."
        "crear_solicitud_envio_whatsapp"
    )
    def test_idempotencia_solicitud_whatsapp(
        self,
        crear_solicitud,
        preparar,
    ):
        from types import SimpleNamespace
        from web.models import IntentoEntrega
        from web.services.whatsapp_imagenes import (
            preparar_solicitud_envio_whatsapp,
        )

        preparar.return_value = 3

        def crear_intento(codigo_proceso, destinatario, clave_solicitud):
            identificador = {
                "CLAVE-001": 101,
                "CLAVE-002": 102,
            }[clave_solicitud]

            return SimpleNamespace(
                pk=identificador,
                estado=IntentoEntrega.Estado.PENDIENTE,
            )

        crear_solicitud.side_effect = crear_intento

        argumentos = {
            "codigo_proceso": "OPERACION-001",
            "destinatario": "526561234567",
        }

        primero = preparar_solicitud_envio_whatsapp(
            **argumentos,
            clave_solicitud="CLAVE-001",
        )

        repetido = preparar_solicitud_envio_whatsapp(
            **argumentos,
            clave_solicitud="CLAVE-001",
        )

        nuevo = preparar_solicitud_envio_whatsapp(
            **argumentos,
            clave_solicitud="CLAVE-002",
        )

        self.assertEqual(primero, repetido)
        self.assertNotEqual(primero[0], nuevo[0])
        self.assertEqual(primero[1], 3)
        self.assertEqual(preparar.call_count, 3)

    @override_settings(WHATSAPP_ENVIO_HABILITADO=False)
    @patch(
        "web.services.whatsapp_ejecutor."
        "IntentoEntrega.objects.get"
    )
    def test_whatsapp_bloqueado_por_configuracion(
        self,
        obtener_intento,
    ):
        from web.services.whatsapp_ejecutor import (
            ejecutar_solicitud_whatsapp,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "El envío real de WhatsApp está deshabilitado",
        ):
            ejecutar_solicitud_whatsapp(
                intento_id=123,
                access_token="token-prueba",
                phone_number_id="numero-prueba",
            )

        obtener_intento.assert_not_called()

    @override_settings(WHATSAPP_ENVIO_HABILITADO=False)
    @patch(
        "web.services.whatsapp_entrega."
        "subir_imagen_whatsapp_meta"
    )
    @patch(
        "web.services.whatsapp_entrega."
        "reclamar_imagen_whatsapp"
    )
    def test_envio_directo_whatsapp_bloqueado(
        self,
        reclamar,
        subir_imagen,
    ):
        from web.services.whatsapp_entrega import (
            enviar_fotografia_whatsapp,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "El envío real de WhatsApp está deshabilitado",
        ):
            enviar_fotografia_whatsapp(
                entrega_imagen_id=123,
                access_token="token-prueba",
                phone_number_id="numero-prueba",
            )

        reclamar.assert_not_called()
        subir_imagen.assert_not_called()

    @override_settings(WHATSAPP_ENVIO_HABILITADO=False)
    @patch("web.services.whatsapp_meta.requests.post")
    def test_cliente_meta_bloqueado_por_configuracion(
        self,
        post,
    ):
        from io import BytesIO

        from web.services.whatsapp_meta import (
            enviar_imagen_whatsapp_meta,
            subir_imagen_whatsapp_meta,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "El envío real de WhatsApp está deshabilitado",
        ):
            enviar_imagen_whatsapp_meta(
                access_token="token-prueba",
                phone_number_id="numero-prueba",
                telefono_destino="526561234567",
                media_id="media-prueba",
            )

        archivo = BytesIO(b"imagen-de-prueba")
        archivo.name = "foto.jpg"

        with self.assertRaisesRegex(
            RuntimeError,
            "El envío real de WhatsApp está deshabilitado",
        ):
            subir_imagen_whatsapp_meta(
                access_token="token-prueba",
                phone_number_id="numero-prueba",
                archivo=archivo,
            )

        post.assert_not_called()

    @override_settings(WHATSAPP_ENVIO_HABILITADO=True)
    @patch(
        "web.services.whatsapp_solicitudes_trabajador."
        "ejecutar_solicitud_whatsapp"
    )
    @patch(
        "web.services.whatsapp_solicitudes_trabajador."
        "IntentoEntrega.objects.filter"
    )
    def test_ejecutar_lote_solicitudes_whatsapp(
        self,
        filtrar,
        ejecutar,
    ):
        from unittest.mock import MagicMock

        from web.models import IntentoEntrega
        from web.services.whatsapp_solicitudes_trabajador import (
            ejecutar_lote_solicitudes_whatsapp,
        )

        consulta = MagicMock()
        filtrar.return_value = consulta
        consulta.order_by.return_value = consulta
        consulta.values_list.return_value.__getitem__.return_value = [
            101, 102
        ]

        ejecutar.return_value = {"iniciado": True}

        resultado = ejecutar_lote_solicitudes_whatsapp(
            access_token="token-prueba",
            phone_number_id="numero-prueba",
            limite=10,
        )

        filtrar.assert_called_once_with(
            canal=IntentoEntrega.Canal.WHATSAPP,
            estado=IntentoEntrega.Estado.PENDIENTE,
            clave_idempotencia__startswith="WHATSAPP-",
        )

        self.assertEqual(resultado["seleccionados"], 2)
        self.assertEqual(resultado["iniciados"], 2)
        self.assertEqual(resultado["errores"], 0)
        self.assertEqual(ejecutar.call_count, 2)

    @override_settings(WHATSAPP_ENVIO_HABILITADO=False)
    @patch(
        "web.services.whatsapp_ciclo_envios."
        "procesar_ciclo_recuperacion_whatsapp"
    )
    @patch(
        "web.services.whatsapp_ciclo_envios."
        "ejecutar_lote_solicitudes_whatsapp"
    )
    def test_ciclo_envios_whatsapp_deshabilitado(
        self,
        solicitudes,
        recuperacion,
    ):
        from web.services.whatsapp_ciclo_envios import (
            procesar_ciclo_envios_whatsapp,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "El envío real de WhatsApp está deshabilitado",
        ):
            procesar_ciclo_envios_whatsapp(limite=10)

        solicitudes.assert_not_called()
        recuperacion.assert_not_called()

    @patch(
        "web.management.commands.procesar_estados_whatsapp."
        "procesar_lote_estados_whatsapp"
    )
    def test_comando_procesar_estados_whatsapp(
        self,
        procesar,
    ):
        from io import StringIO
        from django.core.management import call_command

        procesar.return_value = {
            "seleccionados": 2,
            "procesados": 2,
            "no_procesados": 0,
        }

        salida = StringIO()

        call_command(
            "procesar_estados_whatsapp",
            limite=10,
            stdout=salida,
        )

        procesar.assert_called_once_with(limite=10)

        self.assertIn(
            "Estados WhatsApp procesados",
            salida.getvalue(),
        )

    @patch(
        "web.services.whatsapp_procesamiento."
        "EventoWhatsApp.objects.filter"
    )
    def test_evento_whatsapp_pk4_excluido(
        self,
        filtrar,
    ):
        from unittest.mock import MagicMock

        from web.services.whatsapp_procesamiento import (
            obtener_eventos_whatsapp_pendientes,
        )

        consulta = MagicMock()
        filtrar.return_value = consulta
        consulta.filter.return_value = consulta
        consulta.exclude.return_value = consulta
        consulta.order_by.return_value = consulta
        consulta.__getitem__.return_value = []

        resultado = obtener_eventos_whatsapp_pendientes(
            limite=10,
        )

        consulta.exclude.assert_called_once_with(pk__in=(4,))
        self.assertEqual(resultado, [])

    @patch(
        "web.services.whatsapp_estados."
        "reclamar_evento_whatsapp"
    )
    def test_procesar_estado_whatsapp_protege_pk4(
        self,
        reclamar,
    ):
        from web.services.whatsapp_estados import (
            procesar_estado_whatsapp,
        )

        resultado = procesar_estado_whatsapp(4)

        self.assertFalse(resultado)
        reclamar.assert_not_called()

    @patch(
        "web.services.whatsapp_procesamiento."
        "EventoWhatsApp.objects.filter"
    )
    def test_recuperacion_evento_whatsapp_protege_pk4(
        self,
        filtrar,
    ):
        from web.services.whatsapp_procesamiento import (
            recuperar_evento_whatsapp_atascado,
        )

        resultado = recuperar_evento_whatsapp_atascado(
            evento_id=4,
            minutos=10,
        )

        self.assertFalse(resultado)
        filtrar.assert_not_called()

        
# ***
class WhatsAppValidacionClienteTests(TestCase):

    def test_normalizar_telefonos_mexicanos(self):
        numeros = [
            "6561234567",
            "526561234567",
            "5216561234567",
            "+52 656 123 4567",
        ]

        for numero in numeros:
            with self.subTest(numero=numero):
                self.assertEqual(
                    normalizar_whatsapp_mexico(numero),
                    "526561234567",
                )

    def test_rechazar_telefono_invalido(self):
        numeros = [
            "",
            "12345",
            "346561234567",
        ]

        for numero in numeros:
            with self.subTest(numero=numero):
                with self.assertRaises(ValueError):
                    normalizar_whatsapp_mexico(numero)

    @patch("web.services.whatsapp_solicitudes.PaqueteImagen.objects")
    def test_remitente_autorizado(self, objetos):
        paquete = SimpleNamespace(
            unidad_venta_id=5,
            whatsapp_destino="6561234567",
        )

        objetos.filter.return_value = [paquete]

        resultado = validar_remitente_whatsapp(
            codigo_proceso="PROCESO-001",
            telefono_remitente="5216561234567",
        )

        self.assertEqual(resultado, "526561234567")

    @patch("web.services.whatsapp_solicitudes.PaqueteImagen.objects")
    def test_remitente_no_autorizado(self, objetos):
        paquete = SimpleNamespace(
            unidad_venta_id=5,
            whatsapp_destino="6561234567",
        )

        objetos.filter.return_value = [paquete]

        with self.assertRaisesRegex(
            ValueError,
            "El número remitente no coincide",
        ):
            validar_remitente_whatsapp(
                codigo_proceso="PROCESO-001",
                telefono_remitente="5216569998888",
            )

    @patch("web.services.whatsapp_solicitudes.PaqueteImagen.objects")
    def test_destinatarios_diferentes(self, objetos):
        objetos.filter.return_value = [
            SimpleNamespace(
                unidad_venta_id=5,
                whatsapp_destino="6561234567",
            ),
            SimpleNamespace(
                unidad_venta_id=5,
                whatsapp_destino="6569998888",
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "diferentes destinatarios",
        ):
            validar_remitente_whatsapp(
                codigo_proceso="PROCESO-001",
                telefono_remitente="5216561234567",
            )

    @patch("web.services.whatsapp_solicitudes.PaqueteImagen.objects")
    def test_operacion_inexistente(self, objetos):
        objetos.filter.return_value = []

        with self.assertRaisesRegex(
            ValueError,
            "La operación no existe",
        ):
            validar_remitente_whatsapp(
                codigo_proceso="INEXISTENTE",
                telefono_remitente="5216561234567",
            )

class WhatsAppMensajesTests(TestCase):

    @override_settings(WHATSAPP_EVENTOS_PROTEGIDOS=(4,))
    @patch("web.services.whatsapp_mensajes.reclamar_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.EventoWhatsApp.objects")
    def test_evento_protegido_no_se_procesa(
        self,
        objetos,
        reclamar,
    ):
        evento = SimpleNamespace(
            pk=4,
            tipo=EventoWhatsApp.Tipo.MENSAJE,
        )

        objetos.filter.return_value.first.return_value = evento
        reclamar.return_value = None

        resultado = procesar_mensaje_whatsapp(4)

        self.assertFalse(resultado)
        reclamar.assert_called_once_with(4)
        objetos.filter.assert_called_once_with(
            pk=4,
            tipo=EventoWhatsApp.Tipo.MENSAJE,
        )

    @patch("web.services.whatsapp_mensajes.confirmar_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.preparar_solicitud_envio_whatsapp")
    @patch("web.services.whatsapp_mensajes.validar_solicitud_whatsapp")
    @patch("web.services.whatsapp_mensajes.reclamar_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.EventoWhatsApp.objects")
    def test_solicitud_valida_prepara_entrega(
        self,
        objetos,
        reclamar,
        validar,
        preparar,
        confirmar,
    ):
        evento = SimpleNamespace(
            pk=10,
            tipo=EventoWhatsApp.Tipo.MENSAJE,
            mensaje_id="wamid.PRUEBA123",
            telefono="5216561234567",
            contenido={
                "type": "text",
                "text": {
                    "body": "Referencia: TOKEN_PRUEBA",
                },
            },
        )

        objetos.filter.return_value.first.return_value = evento
        reclamar.return_value = 1

        imagenes = [
            SimpleNamespace(pk=101),
            SimpleNamespace(pk=102),
        ]

        validar.return_value = (
            "PROCESO-001",
            "526561234567",
            imagenes,
        )

        preparar.return_value = (20, 2)
        confirmar.return_value = True

        resultado = procesar_mensaje_whatsapp(10)

        self.assertTrue(resultado)

        validar.assert_called_once_with(
            texto="Referencia: TOKEN_PRUEBA",
            telefono_remitente="5216561234567",
        )

        preparar.assert_called_once_with(
            codigo_proceso="PROCESO-001",
            destinatario="526561234567",
            clave_solicitud="wamid.PRUEBA123",
        )

        confirmar.assert_called_once_with(10, 1)

    @patch("web.services.whatsapp_mensajes.registrar_error_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.preparar_solicitud_envio_whatsapp")
    @patch("web.services.whatsapp_mensajes.validar_solicitud_whatsapp")
    @patch("web.services.whatsapp_mensajes.reclamar_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.EventoWhatsApp.objects")
    def test_remitente_no_autorizado_no_prepara_entrega(
        self,
        objetos,
        reclamar,
        validar,
        preparar,
        registrar_error,
    ):
        evento = SimpleNamespace(
            pk=11,
            tipo=EventoWhatsApp.Tipo.MENSAJE,
            mensaje_id="wamid.NO_AUTORIZADO",
            telefono="5216569998888",
            contenido={
                "type": "text",
                "text": {
                    "body": "Referencia: TOKEN_PRUEBA",
                },
            },
        )

        objetos.filter.return_value.first.return_value = evento
        reclamar.return_value = 1

        validar.side_effect = ValueError(
            "El número remitente no coincide con el registrado por el cliente."
        )

        resultado = procesar_mensaje_whatsapp(11)

        self.assertFalse(resultado)
        preparar.assert_not_called()

        registrar_error.assert_called_once_with(
            11,
            "El número remitente no coincide con el registrado por el cliente.",
            1,
        )

    @patch("web.services.whatsapp_mensajes.liberar_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.confirmar_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.preparar_solicitud_envio_whatsapp")
    @patch("web.services.whatsapp_mensajes.validar_solicitud_whatsapp")
    @patch("web.services.whatsapp_mensajes.reclamar_evento_whatsapp")
    @patch("web.services.whatsapp_mensajes.EventoWhatsApp.objects")
    def test_error_temporal_libera_evento(
        self,
        objetos,
        reclamar,
        validar,
        preparar,
        confirmar,
        liberar,
    ):
        evento = SimpleNamespace(
            pk=12,
            tipo=EventoWhatsApp.Tipo.MENSAJE,
            mensaje_id="wamid.ERROR_TEMPORAL",
            telefono="5216561234567",
            contenido={
                "type": "text",
                "text": {
                    "body": "Referencia: TOKEN_PRUEBA",
                },
            },
        )

        objetos.filter.return_value.first.return_value = evento
        reclamar.return_value = 1

        validar.return_value = (
            "PROCESO-001",
            "526561234567",
            [SimpleNamespace(pk=101)],
        )

        preparar.side_effect = RuntimeError(
            "Error temporal de base de datos"
        )

        liberar.return_value = True

        resultado = procesar_mensaje_whatsapp(12)

        self.assertFalse(resultado)

        liberar.assert_called_once_with(12, 1)
        confirmar.assert_not_called()

# ***

class WhatsAppMensajesTrabajadorTests(TestCase):

    @patch(
        "web.services.whatsapp_mensajes_trabajador.procesar_mensaje_whatsapp"
    )
    @patch(
        "web.services.whatsapp_mensajes_trabajador.obtener_eventos_whatsapp_pendientes"
    )
    def test_procesar_lote_mensajes(
        self,
        obtener_eventos,
        procesar,
    ):
        from web.services.whatsapp_mensajes_trabajador import (
            procesar_lote_mensajes_whatsapp,
        )

        obtener_eventos.return_value = [
            SimpleNamespace(
                pk=10,
                tipo=EventoWhatsApp.Tipo.MENSAJE,
            ),
            SimpleNamespace(
                pk=11,
                tipo=EventoWhatsApp.Tipo.MENSAJE,
            ),
        ]

        procesar.side_effect = [True, False]

        resultado = procesar_lote_mensajes_whatsapp(limite=10)

        self.assertEqual(
            resultado,
            {
                "seleccionados": 2,
                "procesados": 1,
                "no_procesados": 1,
            },
        )

        obtener_eventos.assert_called_once_with(
            limite=10,
            tipo=EventoWhatsApp.Tipo.MENSAJE,
        )

        self.assertEqual(procesar.call_count, 2)

    @patch(
        "web.services.whatsapp_mensajes_trabajador.procesar_mensaje_whatsapp"
    )
    @patch(
        "web.services.whatsapp_mensajes_trabajador.obtener_eventos_whatsapp_pendientes"
    )
    def test_no_procesar_eventos_estado(
        self,
        obtener_eventos,
        procesar,
    ):
        from web.services.whatsapp_mensajes_trabajador import (
            procesar_lote_mensajes_whatsapp,
        )

        obtener_eventos.return_value = [
            SimpleNamespace(
                pk=20,
                tipo=EventoWhatsApp.Tipo.ESTADO,
            ),
        ]

        resultado = procesar_lote_mensajes_whatsapp()

        self.assertEqual(resultado["seleccionados"], 0)
        procesar.assert_not_called()

    def test_rechazar_limite_invalido(self):
        from web.services.whatsapp_mensajes_trabajador import (
            procesar_lote_mensajes_whatsapp,
        )

        with self.assertRaises(ValueError):
            procesar_lote_mensajes_whatsapp(limite=0)

class WhatsAppMensajesProteccionTests(TestCase):

    @override_settings(WHATSAPP_EVENTOS_PROTEGIDOS=(4,))
    @patch(
        "web.services.whatsapp_procesamiento.EventoWhatsApp.objects"
    )
    def test_consulta_excluye_evento_protegido(self, objetos):
        from web.services.whatsapp_procesamiento import (
            obtener_eventos_whatsapp_pendientes,
        )

        consulta = MagicMock()

        objetos.filter.return_value = consulta
        consulta.filter.return_value = consulta
        consulta.exclude.return_value = consulta
        consulta.order_by.return_value = consulta

        obtener_eventos_whatsapp_pendientes(
            limite=10,
            tipo=EventoWhatsApp.Tipo.MENSAJE,
        )

        # La consulta debe excluir el evento protegido.
        self.assertTrue(
            consulta.exclude.called,
            "La consulta no excluyó los eventos protegidos.",
        )

        argumentos = consulta.exclude.call_args
        self.assertIn(
            4,
            argumentos.kwargs.get("pk__in", ()),
        )

from io import StringIO
from django.core.management import call_command


class WhatsAppMensajesComandoTests(TestCase):

    @patch(
        "web.management.commands.procesar_mensajes_whatsapp."
        "procesar_lote_mensajes_whatsapp"
    )
    def test_comando_ejecuta_trabajador(self, procesar):
        procesar.return_value = {
            "seleccionados": 3,
            "procesados": 2,
            "no_procesados": 1,
        }

        salida = StringIO()

        call_command(
            "procesar_mensajes_whatsapp",
            limite=10,
            stdout=salida,
        )

        procesar.assert_called_once_with(limite=10)

        self.assertIn(
            "seleccionados=3, procesados=2, no_procesados=1",
            salida.getvalue(),
        )

    @patch(
        "web.management.commands.procesar_mensajes_whatsapp."
        "procesar_lote_mensajes_whatsapp"
    )
    def test_comando_rechaza_limite_invalido(self, procesar):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command(
                "procesar_mensajes_whatsapp",
                limite=0,
            )

        procesar.assert_not_called()

class WhatsAppRecuperacionInciertaTests(TestCase):

    @patch(
        "web.services.whatsapp_recuperacion."
        "IntentoEntregaImagen.objects"
    )
    def test_incierta_no_genera_reintento(self, objetos):
        from web.services.whatsapp_recuperacion import (
            siguiente_reintento_whatsapp,
            reintento_whatsapp_listo,
        )

        entrega = SimpleNamespace(
            pk=100,
            estado=IntentoEntregaImagen.Estado.INCIERTA,
        )

        objetos.select_related.return_value.get.return_value = entrega
        objetos.get.return_value = entrega

        self.assertIsNone(
            siguiente_reintento_whatsapp(100)
        )

        self.assertFalse(
            reintento_whatsapp_listo(100)
        )

class WhatsAppRegistroClienteTests(TestCase):

    @patch(
        "web.services.whatsapp_clientes.PaqueteImagen.objects"
    )
    def test_registra_telefono_cliente_en_todos_los_paquetes(
        self,
        objetos,
    ):
        from unittest.mock import MagicMock

        from web.models import PaqueteImagen
        from web.services.whatsapp_clientes import (
            registrar_whatsapp_cliente,
        )

        paquete1 = MagicMock()
        paquete1.pk = 10
        paquete1.estado = PaqueteImagen.EstadoPaquete.CERRADO
        paquete1.unidad_venta_id = 5
        paquete1.whatsapp_destino = ""

        paquete2 = MagicMock()
        paquete2.pk = 11
        paquete2.estado = PaqueteImagen.EstadoPaquete.CERRADO
        paquete2.unidad_venta_id = 5
        paquete2.whatsapp_destino = ""

        consulta = MagicMock()
        objetos.select_for_update.return_value = consulta
        consulta.filter.return_value = consulta
        consulta.order_by.return_value = [
            paquete1,
            paquete2,
        ]

        with patch(
            "web.services.whatsapp_clientes.transaction.atomic"
        ):
            telefono = registrar_whatsapp_cliente(
                codigo_proceso="PROCESO-001",
                telefono="656 123 4567",
            )

        self.assertEqual(
            telefono,
            "526561234567",
        )

        objetos.filter.return_value.update.assert_called_once_with(
            whatsapp_destino="526561234567"
        )

    @patch(
        "web.services.whatsapp_clientes.IntentoEntrega.objects"
    )
    @patch(
        "web.services.whatsapp_clientes.PaqueteImagen.objects"
    )
    def test_no_permite_cambiar_telefono_con_solicitud_existente(
        self,
        objetos_paquetes,
        objetos_intentos,
    ):
        from unittest.mock import MagicMock

        from web.models import PaqueteImagen
        from web.services.whatsapp_clientes import (
            registrar_whatsapp_cliente,
        )

        paquete = MagicMock()
        paquete.pk = 10
        paquete.estado = PaqueteImagen.EstadoPaquete.CERRADO
        paquete.unidad_venta_id = 5
        paquete.whatsapp_destino = "526561234567"

        consulta = MagicMock()
        objetos_paquetes.select_for_update.return_value = consulta
        consulta.filter.return_value = consulta
        consulta.order_by.return_value = [paquete]

        objetos_intentos.filter.return_value.exists.return_value = True

        with self.assertRaisesRegex(
            ValueError,
            "No se puede cambiar el WhatsApp del cliente",
        ):
            registrar_whatsapp_cliente(
                codigo_proceso="PROCESO-001",
                telefono="656 987 6543",
            )

        objetos_paquetes.filter.assert_not_called()

    @patch(
        "web.services.whatsapp_clientes.PaqueteImagen.objects"
    )
    def test_no_permite_registrar_telefono_antes_del_cierre(
        self,
        objetos,
    ):
        from unittest.mock import MagicMock

        from web.models import PaqueteImagen
        from web.services.whatsapp_clientes import (
            registrar_whatsapp_cliente,
        )

        paquete = MagicMock()
        paquete.pk = 10
        paquete.estado = PaqueteImagen.EstadoPaquete.LISTO
        paquete.unidad_venta_id = 5
        paquete.whatsapp_destino = ""

        consulta = MagicMock()
        objetos.select_for_update.return_value = consulta
        consulta.filter.return_value = consulta
        consulta.order_by.return_value = [paquete]

        with self.assertRaisesRegex(
            ValueError,
            "La operación debe estar cerrada",
        ):
            registrar_whatsapp_cliente(
                codigo_proceso="PROCESO-001",
                telefono="656 123 4567",
            )

        objetos.filter.assert_not_called()

# ***