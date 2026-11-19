from django.test import TestCase
import tempfile
from pathlib import Path

from io import BytesIO
from unittest.mock import patch
from decimal import Decimal
from PIL import Image
from django.test import SimpleTestCase, override_settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.test import TransactionTestCase
from datetime import timedelta

from web.models import (
    Estado, Ciudad, Sector, Imagen, Pago,
    Fondo, UnidadVenta, PaqueteImagen,
)
from web.services.image_processor import (
    eliminar_fondo,
    aplicar_fondo,
    generar_variantes,
    ImageProcessingError,
)
from web.services.creditos import (
    CreditosInsuficientesError,
    validar_creditos_disponibles,
    obtener_creditos_disponibles,
    reservar_creditos,
    consumir_creditos_reservados,
    cerrar_operacion,
    validar_imagenes_seleccionadas,
)
from web.services.procesamiento import recibir_imagen
from web.services.creditos import cancelar_operacion
from web.services.limpieza import eliminar_archivos_operacion


class ImageProcessorTests(SimpleTestCase):

    @patch("web.services.image_processor.remove")
    def test_eliminar_fondo_devuelve_png_rgba(self, mock_remove):

        # Simular fotografía original
        imagen = Image.new(
            "RGB",
            (100, 100),
            color="red"
        )

        entrada = BytesIO()
        imagen.save(entrada, format="JPEG")

        # Simular resultado de rembg
        resultado = Image.new(
            "RGBA",
            (100, 100),
            color=(255, 0, 0, 0)
        )

        salida = BytesIO()
        resultado.save(salida, format="PNG")

        mock_remove.return_value = salida.getvalue()

        # Ejecutar servicio
        imagen_procesada = eliminar_fondo(
            entrada.getvalue()
        )

        # Verificar resultado
        with Image.open(BytesIO(imagen_procesada)) as final:
            self.assertEqual(final.format, "PNG")
            self.assertEqual(final.mode, "RGBA")
            self.assertEqual(final.size, (100, 100))

        mock_remove.assert_called_once()

    def test_eliminar_fondo_imagen_vacia(self):

        with self.assertRaises(ImageProcessingError):
            eliminar_fondo(b"")

    def test_aplicar_fondo_genera_imagen_combinada(self):

        # Imagen transparente con un píxel rojo visible
        sujeto = Image.new(
            "RGBA",
            (100, 100),
            (0, 0, 0, 0),
        )
        sujeto.putpixel((50, 50), (255, 0, 0, 255))

        buffer_sujeto = BytesIO()
        sujeto.save(buffer_sujeto, format="PNG")

        # Fondo azul
        fondo = Image.new(
            "RGB",
            (200, 200),
            (0, 0, 255),
        )

        buffer_fondo = BytesIO()
        fondo.save(buffer_fondo, format="PNG")

        resultado = aplicar_fondo(
            buffer_sujeto.getvalue(),
            buffer_fondo.getvalue(),
        )

        with Image.open(BytesIO(resultado)) as imagen:
            self.assertEqual(imagen.format, "PNG")
            self.assertEqual(imagen.size, (100, 100))

            # Zona transparente: debe verse el fondo azul
            self.assertEqual(
                imagen.getpixel((0, 0)),
                (0, 0, 255, 255),
            )

            # Zona del sujeto: debe conservarse el rojo
            self.assertEqual(
                imagen.getpixel((50, 50)),
                (255, 0, 0, 255),
            )


    @patch("web.services.image_processor.aplicar_fondo")
    @patch("web.services.image_processor.eliminar_fondo")
    def test_generar_tres_variantes(
        self,
        mock_eliminar,
        mock_aplicar,
    ):
        mock_eliminar.return_value = b"imagen_sin_fondo"
        mock_aplicar.side_effect = [
            b"imagen_1",
            b"imagen_2",
            b"imagen_3",
        ]

        fondos = [
            b"fondo_1",
            b"fondo_2",
            b"fondo_3",
        ]

        resultado = generar_variantes(
            b"foto_original",
            fondos,
        )

        self.assertEqual(len(resultado), 3)

        # La eliminación debe ejecutarse una sola vez
        mock_eliminar.assert_called_once_with(
            b"foto_original"
        )

        # Se aplica un fondo por cada variante
        self.assertEqual(mock_aplicar.call_count, 3)

        self.assertEqual(
            resultado,
            [b"imagen_1", b"imagen_2", b"imagen_3"],
        )

    @patch("web.services.image_processor.aplicar_fondo")
    @patch("web.services.image_processor.eliminar_fondo")
    def test_generar_seis_variantes(
        self,
        mock_eliminar,
        mock_aplicar,
    ):
        mock_eliminar.return_value = b"imagen_sin_fondo"

        mock_aplicar.side_effect = [
            f"imagen_{i}".encode()
            for i in range(1, 7)
        ]

        fondos = [
            f"fondo_{i}".encode()
            for i in range(1, 7)
        ]

        resultado = generar_variantes(
            b"foto_original",
            fondos,
        )

        self.assertEqual(len(resultado), 6)

        # La IA elimina el fondo una sola vez
        mock_eliminar.assert_called_once_with(
            b"foto_original"
        )

        # Se generan seis composiciones
        self.assertEqual(mock_aplicar.call_count, 6)

        self.assertEqual(
            resultado,
            [f"imagen_{i}".encode() for i in range(1, 7)],
        )

    def test_generar_variantes_rechaza_cantidad_incorrecta(self):
        cantidades_invalidas = [0, 1, 2, 4, 5, 7]

        for cantidad in cantidades_invalidas:
            with self.subTest(cantidad=cantidad):
                fondos = [
                    b"fondo"
                    for _ in range(cantidad)
                ]

                with self.assertRaises(ImageProcessingError):
                    generar_variantes(
                        b"foto_original",
                        fondos,
                    )


#*** fin clase
class CreditosServiceTests(TestCase):

    def test_sector_sin_creditos_no_puede_procesar(self):

        sector_id = 999999

        with self.assertRaises(CreditosInsuficientesError):
            validar_creditos_disponibles(sector_id)

    def test_calcular_creditos_disponibles(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Prueba",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        Pago.objects.create(
            codigo_pago="PAGO-TEST-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("1000.00"),
            paquetes_pagados=10,
            creditos_usados=3,
            creditos_reservados=2,
            precio_unitario=Decimal("100.00"),
        )

        disponibles = obtener_creditos_disponibles(
            sector.id
        )

        self.assertEqual(disponibles, 5)

    def test_creditos_disponibles_varios_pagos(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ002",
            nombre="Sector Dos Pagos",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        Pago.objects.create(
            codigo_pago="PAGO-TEST-002",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("1000.00"),
            paquetes_pagados=10,
            creditos_reservados=2,
            creditos_usados=3,
            precio_unitario=Decimal("100.00"),
        )

        Pago.objects.create(
            codigo_pago="PAGO-TEST-003",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("500.00"),
            paquetes_pagados=5,
            creditos_reservados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        disponibles = obtener_creditos_disponibles(
            sector.id
        )

        self.assertEqual(disponibles, 8)

    def test_reserva_dos_creditos_de_pagos_distintos(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ003",
            nombre="Sector Reserva",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        fecha = timezone.now()

        pago_antiguo = Pago.objects.create(
            codigo_pago="PAGO-FIFO-001",
            sector=sector,
            anio=2026,
            semana=43,
            fecha_pago=fecha - timedelta(days=7),
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        pago_nuevo = Pago.objects.create(
            codigo_pago="PAGO-FIFO-002",
            sector=sector,
            anio=2026,
            semana=44,
            fecha_pago=fecha,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        archivo = ContentFile(
            b"imagen_original",
            name="foto_original.jpg",
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-TEST-001",
            imagen_original=archivo,
        )

        self.assertEqual(len(paquetes), 2)

        self.assertEqual(paquetes[0].pago_id, pago_antiguo.id)
        self.assertEqual(paquetes[1].pago_id, pago_nuevo.id)

        self.assertEqual(paquetes[0].numero_paquete, 1)
        self.assertEqual(paquetes[1].numero_paquete, 2)

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.RESERVADO,
        )

        self.assertEqual(
            paquetes[1].estado_credito,
            PaqueteImagen.EstadoCredito.RESERVADO,
        )

        pago_antiguo.refresh_from_db()
        pago_nuevo.refresh_from_db()

        self.assertEqual(pago_antiguo.creditos_reservados, 1)
        self.assertEqual(pago_nuevo.creditos_reservados, 1)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_reserva_duplicada_no_descuenta_creditos(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ004",
            nombre="Sector Duplicados",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-DUP-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        archivo = ContentFile(
            b"imagen_original",
            name="foto_original.jpg",
        )

        # Primera reserva
        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-DUP-001",
            imagen_original=archivo,
        )

        self.assertEqual(len(paquetes), 2)

        # Intento de reserva duplicada
        with self.assertRaises(ValueError):
            reservar_creditos(
                unidad_venta_id=unidad.id,
                codigo_proceso="PROCESO-DUP-001",
                imagen_original=archivo,
            )

        pago.refresh_from_db()

        # Los créditos siguen reservados una sola vez
        self.assertEqual(pago.creditos_reservados, 2)
        self.assertEqual(pago.creditos_usados, 0)

        # No se crearon paquetes adicionales
        self.assertEqual(
            PaqueteImagen.objects.filter(
                codigo_proceso="PROCESO-DUP-001"
            ).count(),
            2,
        )

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_reserva_un_credito_disponible(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ005",
            nombre="Sector Un Credito",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-UNO-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("500.00"),
            paquetes_pagados=5,
            creditos_usados=3,
            creditos_reservados=1,
            precio_unitario=Decimal("100.00"),
        )

        archivo = ContentFile(
            b"imagen_original",
            name="foto_original.jpg",
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-UNO-001",
            imagen_original=archivo,
        )

        # Solamente debe reservar un crédito.
        self.assertEqual(len(paquetes), 1)

        # El paquete corresponde al Pago disponible.
        self.assertEqual(paquetes[0].pago_id, pago.id)

        # El crédito debe quedar reservado.
        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.RESERVADO,
        )

        # Verificar saldos después de la reserva.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 2)
        self.assertEqual(pago.creditos_usados, 3)

        # El Sector ya no tiene créditos disponibles.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

        # Un crédito permite generar tres imágenes.
        self.assertEqual(len(paquetes) * 3, 3)

    def test_reserva_sin_creditos_disponibles(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ006",
            nombre="Sector Sin Creditos",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-SIN-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("500.00"),
            paquetes_pagados=5,
            creditos_usados=3,
            creditos_reservados=2,
            precio_unitario=Decimal("100.00"),
        )

        archivo = ContentFile(
            b"imagen_original",
            name="foto_original.jpg",
        )

        with self.assertRaises(CreditosInsuficientesError):
            reservar_creditos(
                unidad_venta_id=unidad.id,
                codigo_proceso="PROCESO-SIN-001",
                imagen_original=archivo,
            )

        pago.refresh_from_db()

        # No debe modificar los saldos.
        self.assertEqual(pago.creditos_usados, 3)
        self.assertEqual(pago.creditos_reservados, 2)

        # No debe crear paquetes.
        self.assertFalse(
            PaqueteImagen.objects.filter(
                codigo_proceso="PROCESO-SIN-001"
            ).exists()
        )

        # El saldo disponible continúa en cero.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_reserva_revierte_si_falla_segundo_paquete(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ007",
            nombre="Sector Rollback",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-ROLLBACK-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        archivo = ContentFile(
            b"imagen_original",
            name="foto_original.jpg",
        )

        crear_original = PaqueteImagen.objects.create
        contador = 0

        def crear_con_error(*args, **kwargs):
            nonlocal contador
            contador += 1

            if contador == 2:
                raise RuntimeError(
                    "Error simulado al crear segundo paquete"
                )

            return crear_original(*args, **kwargs)

        with patch.object(
            PaqueteImagen.objects,
            "create",
            side_effect=crear_con_error,
        ):
            with self.assertRaises(RuntimeError):
                reservar_creditos(
                    unidad_venta_id=unidad.id,
                    codigo_proceso="PROCESO-ROLLBACK-001",
                    imagen_original=archivo,
                )

        pago.refresh_from_db()

        # La reserva del primer crédito debe revertirse.
        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(pago.creditos_usados, 0)

        # Ninguno de los dos paquetes debe existir.
        self.assertEqual(
            PaqueteImagen.objects.filter(
                codigo_proceso="PROCESO-ROLLBACK-001"
            ).count(),
            0,
        )

        # Los dos créditos siguen disponibles.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            2,
        )

    def test_calcular_creditos_a_consumir(self):
        from web.services.creditos import calcular_creditos_a_consumir

        casos = [
            (1, 1),
            (2, 1),
            (3, 1),
            (4, 2),
            (5, 2),
            (6, 2),
        ]

        for imagenes, creditos_esperados in casos:
            with self.subTest(imagenes=imagenes):
                resultado = calcular_creditos_a_consumir(imagenes)

                self.assertEqual(
                    resultado,
                    creditos_esperados,
                )

    def test_calcular_creditos_rechaza_cantidades_invalidas(self):
        from web.services.creditos import calcular_creditos_a_consumir

        cantidades_invalidas = [
            -5,
            -1,
            0,
            7,
            10,
            1.5,
            "3",
            None,
            True,
            False,
        ]

        for cantidad in cantidades_invalidas:
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(ValueError):
                    calcular_creditos_a_consumir(cantidad)

    def test_consumir_un_credito_y_liberar_otro(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ008",
            nombre="Sector Consumo",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago_antiguo = Pago.objects.create(
            codigo_pago="PAGO-CONSUMO-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
            fecha_pago=timezone.now() - timedelta(days=7),
        )

        pago_nuevo = Pago.objects.create(
            codigo_pago="PAGO-CONSUMO-002",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-CONSUMO-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)

        consumir_creditos_reservados(
            codigo_proceso="PROCESO-CONSUMO-001",
            numero_imagenes=3,
        )

        pago_antiguo.refresh_from_db()
        pago_nuevo.refresh_from_db()

        self.assertEqual(pago_antiguo.creditos_usados, 1)
        self.assertEqual(pago_antiguo.creditos_reservados, 0)
        self.assertTrue(pago_antiguo.pago_consumido)

        self.assertEqual(pago_nuevo.creditos_usados, 0)
        self.assertEqual(pago_nuevo.creditos_reservados, 0)
        self.assertFalse(pago_nuevo.pago_consumido)

        paquetes[0].refresh_from_db()
        paquetes[1].refresh_from_db()

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertEqual(
            paquetes[1].estado_credito,
            PaqueteImagen.EstadoCredito.LIBERADO,
        )

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            1,
        )

    def test_consumir_dos_creditos_por_seis_imagenes(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ009",
            nombre="Sector Seis Imagenes",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-SEIS-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-SEIS-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)

        consumir_creditos_reservados(
            codigo_proceso="PROCESO-SEIS-001",
            numero_imagenes=6,
        )

        pago.refresh_from_db()

        # Ambos créditos deben estar consumidos.
        self.assertEqual(pago.creditos_usados, 2)
        self.assertEqual(pago.creditos_reservados, 0)
        self.assertTrue(pago.pago_consumido)

        # Ambos paquetes deben quedar consumidos.
        for paquete in paquetes:
            paquete.refresh_from_db()

            self.assertEqual(
                paquete.estado_credito,
                PaqueteImagen.EstadoCredito.CONSUMIDO,
            )

        # No deben quedar créditos disponibles.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_consumo_duplicado_no_descuenta_creditos(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ010",
            nombre="Sector Consumo Duplicado",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-DUPLICADO-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-DUPLICADO-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        # Primera confirmación: consumir un crédito.
        consumir_creditos_reservados(
            codigo_proceso="PROCESO-DUPLICADO-001",
            numero_imagenes=3,
        )

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            1,
        )

        # Segunda confirmación del mismo proceso.
        with self.assertRaises(ValueError):
            consumir_creditos_reservados(
                codigo_proceso="PROCESO-DUPLICADO-001",
                numero_imagenes=3,
            )

        # Los saldos no deben modificarse.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            1,
        )

        paquetes[0].refresh_from_db()
        paquetes[1].refresh_from_db()

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertEqual(
            paquetes[1].estado_credito,
            PaqueteImagen.EstadoCredito.LIBERADO,
        )

    def test_no_consumir_dos_creditos_si_solo_hay_uno_reservado(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ011",
            nombre="Sector Reserva Insuficiente",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-INSUFICIENTE-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-INSUFICIENTE-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 1)

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 1)
        self.assertEqual(pago.creditos_usados, 0)

        # Se intenta consumir dos créditos, pero solo hay uno reservado.
        with self.assertRaises(ValueError):
            consumir_creditos_reservados(
                codigo_proceso="PROCESO-INSUFICIENTE-001",
                numero_imagenes=6,
            )

        # La operación rechazada no debe modificar los saldos.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 1)
        self.assertEqual(pago.creditos_usados, 0)

        paquetes[0].refresh_from_db()

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.RESERVADO,
        )

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_consumo_revierte_si_falla_segundo_paquete(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ012",
            nombre="Sector Rollback Consumo",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-ROLLBACK-CONSUMO-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-ROLLBACK-CONSUMO-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)

        pago.refresh_from_db()
        self.assertEqual(pago.creditos_reservados, 2)

        guardar_original = PaqueteImagen.save
        contador = 0

        def guardar_con_error(instancia, *args, **kwargs):
            nonlocal contador
            contador += 1

            if contador == 2:
                raise RuntimeError(
                    "Error simulado al guardar segundo paquete"
                )

            return guardar_original(instancia, *args, **kwargs)

        with patch.object(
            PaqueteImagen,
            "save",
            guardar_con_error,
        ):
            with self.assertRaises(RuntimeError):
                consumir_creditos_reservados(
                    codigo_proceso="PROCESO-ROLLBACK-CONSUMO-001",
                    numero_imagenes=6,
                )

        # Los movimientos contables deben revertirse.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 2)
        self.assertEqual(pago.creditos_usados, 0)
        self.assertFalse(pago.pago_consumido)

        # Ambos paquetes deben permanecer reservados.
        for paquete in paquetes:
            paquete.refresh_from_db()

            self.assertEqual(
                paquete.estado_credito,
                PaqueteImagen.EstadoCredito.RESERVADO,
            )

        # No deben aparecer créditos disponibles,
        # porque los dos siguen reservados.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_no_cerrar_operacion_sin_imagenes_procesadas(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ013",
            nombre="Sector Sin Imagenes",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-SIN-IMAGENES-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-SIN-IMAGENES-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 1)

        # No se han generado registros Imagen.
        self.assertEqual(
            Imagen.objects.filter(
                paquete__codigo_proceso="PROCESO-SIN-IMAGENES-001"
            ).count(),
            0,
        )

        with self.assertRaisesMessage(
            ValueError,
            "La operación no tiene imágenes procesadas",
        ):
            cerrar_operacion(
                codigo_proceso="PROCESO-SIN-IMAGENES-001",
                imagenes_ids=[],
            )            

        # La operación debe conservar su reserva.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 1)
        self.assertEqual(pago.creditos_usados, 0)

        paquetes[0].refresh_from_db()

        self.assertEqual(
            paquetes[0].estado,
            PaqueteImagen.EstadoPaquete.RECIBIDO,
        )

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.RESERVADO,
        )

        self.assertIsNone(paquetes[0].fecha_cierre)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_rechaza_imagen_de_otro_proceso(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ014",
            nombre="Sector Validacion Imagenes",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        Pago.objects.create(
            codigo_pago="PAGO-VALIDACION-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("400.00"),
            paquetes_pagados=4,
            precio_unitario=Decimal("100.00"),
        )

        paquetes_a = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-VALIDACION-A",
            imagen_original=ContentFile(
                b"foto_a",
                name="foto_a.jpg",
            ),
        )

        paquetes_b = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-VALIDACION-B",
            imagen_original=ContentFile(
                b"foto_b",
                name="foto_b.jpg",
            ),
        )

        fondo_a = Fondo.objects.create(
            codigo_fondo="FONDO-VALIDACION-A",
            imagen="fondos/fondo_a.jpg",
        )

        fondo_b = Fondo.objects.create(
            codigo_fondo="FONDO-VALIDACION-B",
            imagen="fondos/fondo_b.jpg",
        )

        imagen_a = Imagen.objects.create(
            paquete=paquetes_a[0],
            codigo_imagen="IMG-VALIDACION-A",
            fondo=fondo_a,
            imagen_procesada="imagenes/procesadas/a.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        imagen_b = Imagen.objects.create(
            paquete=paquetes_b[0],
            codigo_imagen="IMG-VALIDACION-B",
            fondo=fondo_b,
            imagen_procesada="imagenes/procesadas/b.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        # La imagen A pertenece al proceso A.
        self.assertEqual(
            validar_imagenes_seleccionadas(
                codigo_proceso="PROCESO-VALIDACION-A",
                imagenes_ids=[imagen_a.id],
            ),
            1,
        )

        # No debe permitirse seleccionar la imagen B
        # como parte del proceso A.
        with self.assertRaisesMessage(
            ValueError,
            "no pertenecen al proceso",
        ):
            validar_imagenes_seleccionadas(
                codigo_proceso="PROCESO-VALIDACION-A",
                imagenes_ids=[
                    imagen_a.id,
                    imagen_b.id,
                ],
            )

        # La validación no debe modificar los saldos.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

        self.assertEqual(
            Pago.objects.get(
                codigo_pago="PAGO-VALIDACION-001"
            ).creditos_reservados,
            4,
        )

        # Tampoco debe marcar imágenes como seleccionadas.
        imagen_a.refresh_from_db()
        imagen_b.refresh_from_db()

        self.assertFalse(imagen_a.seleccionada)
        self.assertFalse(imagen_b.seleccionada)

    def test_rechaza_imagen_no_procesada(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ015",
            nombre="Sector Imagen Pendiente",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-PENDIENTE-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-PENDIENTE-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        fondo_1 = Fondo.objects.create(
            codigo_fondo="FONDO-PENDIENTE-001",
            imagen="fondos/fondo_pendiente_1.jpg",
        )

        fondo_2 = Fondo.objects.create(
            codigo_fondo="FONDO-PENDIENTE-002",
            imagen="fondos/fondo_pendiente_2.jpg",
        )

        imagen_procesada = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-PROCESADA-001",
            fondo=fondo_1,
            imagen_procesada="imagenes/procesadas/imagen_1.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        imagen_pendiente = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-PENDIENTE-001",
            fondo=fondo_2,
            estado_proceso=Imagen.EstadoProceso.PROCESANDO,
        )

        # La imagen procesada puede validarse.
        self.assertEqual(
            validar_imagenes_seleccionadas(
                codigo_proceso="PROCESO-PENDIENTE-001",
                imagenes_ids=[imagen_procesada.id],
            ),
            1,
        )

        # Una selección que incluye una imagen pendiente
        # debe rechazarse completamente.
        with self.assertRaisesMessage(
            ValueError,
            "todavía no están procesadas",
        ):
            validar_imagenes_seleccionadas(
                codigo_proceso="PROCESO-PENDIENTE-001",
                imagenes_ids=[
                    imagen_procesada.id,
                    imagen_pendiente.id,
                ],
            )

        # Ninguna imagen debe quedar seleccionada.
        imagen_procesada.refresh_from_db()
        imagen_pendiente.refresh_from_db()

        self.assertFalse(imagen_procesada.seleccionada)
        self.assertFalse(imagen_pendiente.seleccionada)

        # La reserva y los saldos deben permanecer intactos.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 1)
        self.assertEqual(pago.creditos_usados, 0)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_cerrar_operacion_rechaza_seleccion_no_procesada(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ016",
            nombre="Sector Cierre Invalido",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-CIERRE-INVALIDO-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-CIERRE-INVALIDO-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        fondo_1 = Fondo.objects.create(
            codigo_fondo="FONDO-CIERRE-INVALIDO-001",
            imagen="fondos/cierre_1.jpg",
        )

        fondo_2 = Fondo.objects.create(
            codigo_fondo="FONDO-CIERRE-INVALIDO-002",
            imagen="fondos/cierre_2.jpg",
        )

        imagen_lista = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-CIERRE-LISTA-001",
            fondo=fondo_1,
            imagen_procesada="imagenes/procesadas/cierre_1.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        imagen_pendiente = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-CIERRE-PENDIENTE-001",
            fondo=fondo_2,
            estado_proceso=Imagen.EstadoProceso.PROCESANDO,
        )

        with self.assertRaisesMessage(
            ValueError,
            "todavía no están procesadas",
        ):
            cerrar_operacion(
                codigo_proceso="PROCESO-CIERRE-INVALIDO-001",
                imagenes_ids=[
                    imagen_lista.id,
                    imagen_pendiente.id,
                ],
            )

        pago.refresh_from_db()
        paquetes[0].refresh_from_db()
        imagen_lista.refresh_from_db()
        imagen_pendiente.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 1)
        self.assertEqual(pago.creditos_usados, 0)

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.RESERVADO,
        )

        self.assertEqual(
            paquetes[0].estado,
            PaqueteImagen.EstadoPaquete.RECIBIDO,
        )

        self.assertIsNone(paquetes[0].fecha_cierre)

        self.assertFalse(imagen_lista.seleccionada)
        self.assertFalse(imagen_pendiente.seleccionada)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_cerrar_operacion_registra_imagenes_seleccionadas(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ017",
            nombre="Sector Seleccion",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-SELECCION-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-SELECCION-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        fondo_1 = Fondo.objects.create(
            codigo_fondo="FONDO-SELECCION-001",
            imagen="fondos/seleccion_1.jpg",
        )

        fondo_2 = Fondo.objects.create(
            codigo_fondo="FONDO-SELECCION-002",
            imagen="fondos/seleccion_2.jpg",
        )

        imagen_1 = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-SELECCION-001",
            fondo=fondo_1,
            imagen_procesada="imagenes/procesadas/seleccion_1.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        imagen_2 = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-SELECCION-002",
            fondo=fondo_2,
            imagen_procesada="imagenes/procesadas/seleccion_2.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        cerrar_operacion(
            codigo_proceso="PROCESO-SELECCION-001",
            imagenes_ids=[imagen_1.id],
        )

        imagen_1.refresh_from_db()
        imagen_2.refresh_from_db()
        pago.refresh_from_db()
        paquetes[0].refresh_from_db()

        self.assertTrue(imagen_1.seleccionada)
        self.assertFalse(imagen_2.seleccionada)

        # El crédito reservado se consume.
        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(pago.creditos_usados, 1)
        self.assertTrue(pago.pago_consumido)

        # El paquete queda consumido y cerrado.
        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertEqual(
            paquetes[0].estado,
            PaqueteImagen.EstadoPaquete.CERRADO,
        )

        self.assertIsNotNone(paquetes[0].fecha_cierre)
    def test_cerrar_operacion_consume_un_credito(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ018",
            nombre="Sector Cierre Definitivo",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-CIERRE-DEFINITIVO-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso="PROCESO-CIERRE-DEFINITIVO-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        fondo = Fondo.objects.create(
            codigo_fondo="FONDO-CIERRE-DEFINITIVO-001",
            imagen="fondos/cierre_definitivo.jpg",
        )

        imagen = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-CIERRE-DEFINITIVO-001",
            fondo=fondo,
            imagen_procesada=(
                "imagenes/procesadas/cierre_definitivo.jpg"
            ),
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        cerrar_operacion(
            codigo_proceso="PROCESO-CIERRE-DEFINITIVO-001",
            imagenes_ids=[imagen.id],
        )

        pago.refresh_from_db()
        paquetes[0].refresh_from_db()
        imagen.refresh_from_db()

        # La imagen quedó seleccionada.
        self.assertTrue(imagen.seleccionada)

        # La reserva se convirtió en consumo.
        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(pago.creditos_usados, 1)
        self.assertTrue(pago.pago_consumido)

        # El paquete quedó consumido y cerrado.
        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertEqual(
            paquetes[0].estado,
            PaqueteImagen.EstadoPaquete.CERRADO,
        )

        self.assertIsNotNone(paquetes[0].fecha_cierre)

        # No quedan créditos disponibles.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

    def test_cerrar_operacion_consume_un_credito_y_libera_segundo(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ019",
            nombre="Sector Liberacion",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-LIBERACION-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-LIBERACION-001"

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso=codigo_proceso,
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)

        pago.refresh_from_db()
        self.assertEqual(pago.creditos_reservados, 2)

        fondo_1 = Fondo.objects.create(
            codigo_fondo="FONDO-LIBERACION-001",
            imagen="fondos/liberacion_1.jpg",
        )

        fondo_2 = Fondo.objects.create(
            codigo_fondo="FONDO-LIBERACION-002",
            imagen="fondos/liberacion_2.jpg",
        )

        imagen_1 = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-LIBERACION-001",
            fondo=fondo_1,
            imagen_procesada="imagenes/procesadas/liberacion_1.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        imagen_2 = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-LIBERACION-002",
            fondo=fondo_2,
            imagen_procesada="imagenes/procesadas/liberacion_2.jpg",
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        cerrar_operacion(
            codigo_proceso=codigo_proceso,
            imagenes_ids=[imagen_1.id, imagen_2.id],
        )

        pago.refresh_from_db()

        for paquete in paquetes:
            paquete.refresh_from_db()

        imagen_1.refresh_from_db()
        imagen_2.refresh_from_db()

        # Un crédito consumido, otro liberado.
        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(pago.creditos_usados, 1)
        self.assertFalse(pago.pago_consumido)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            1,
        )

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertEqual(
            paquetes[1].estado_credito,
            PaqueteImagen.EstadoCredito.LIBERADO,
        )

        # Ambos paquetes pertenecen a la misma
        # operación y deben quedar cerrados.
        for paquete in paquetes:
            self.assertEqual(
                paquete.estado,
                PaqueteImagen.EstadoPaquete.CERRADO,
            )
            self.assertIsNotNone(paquete.fecha_cierre)

        self.assertTrue(imagen_1.seleccionada)
        self.assertTrue(imagen_2.seleccionada)

    def test_cerrar_operacion_seis_imagenes_consume_dos_creditos(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ020",
            nombre="Sector Seis Imagenes",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-SEIS-IMAGENES-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-SEIS-IMAGENES-001"

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso=codigo_proceso,
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)

        imagenes_ids = []

        # Tres variantes por cada paquete reservado.
        for numero, paquete in enumerate(paquetes, start=1):
            for variante in range(1, 4):

                fondo = Fondo.objects.create(
                    codigo_fondo=(
                        f"FONDO-SEIS-{numero}-{variante}"
                    ),
                    imagen=(
                        f"fondos/seis_{numero}_{variante}.jpg"
                    ),
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    codigo_imagen=(
                        f"IMG-SEIS-{numero}-{variante}"
                    ),
                    fondo=fondo,
                    imagen_procesada=(
                        f"imagenes/procesadas/"
                        f"seis_{numero}_{variante}.jpg"
                    ),
                    estado_proceso=(
                        Imagen.EstadoProceso.PROCESADA
                    ),
                )

                imagenes_ids.append(imagen.id)

        self.assertEqual(len(imagenes_ids), 6)

        cerrar_operacion(
            codigo_proceso=codigo_proceso,
            imagenes_ids=imagenes_ids,
        )

        pago.refresh_from_db()

        for paquete in paquetes:
            paquete.refresh_from_db()

        # Se consumen los dos créditos.
        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(pago.creditos_usados, 2)
        self.assertTrue(pago.pago_consumido)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

        # Ambos paquetes quedan consumidos y cerrados.
        for paquete in paquetes:
            self.assertEqual(
                paquete.estado_credito,
                PaqueteImagen.EstadoCredito.CONSUMIDO,
            )

            self.assertEqual(
                paquete.estado,
                PaqueteImagen.EstadoPaquete.CERRADO,
            )

            self.assertIsNotNone(paquete.fecha_cierre)

        # Las seis imágenes quedaron seleccionadas.
        self.assertEqual(
            Imagen.objects.filter(
                pk__in=imagenes_ids,
                seleccionada=True,
            ).count(),
            6,
        )

    def test_cerrar_operacion_duplicada_no_consume_creditos(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ021",
            nombre="Sector Cierre Duplicado",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-CIERRE-DUPLICADO-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-CIERRE-DUPLICADO-001"

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso=codigo_proceso,
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        fondo = Fondo.objects.create(
            codigo_fondo="FONDO-CIERRE-DUPLICADO-001",
            imagen="fondos/cierre_duplicado.jpg",
        )

        imagen = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-CIERRE-DUPLICADO-001",
            fondo=fondo,
            imagen_procesada=(
                "imagenes/procesadas/cierre_duplicado.jpg"
            ),
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        # Primer cierre: consume un crédito y libera otro.
        cerrar_operacion(
            codigo_proceso=codigo_proceso,
            imagenes_ids=[imagen.id],
        )

        pago.refresh_from_db()
        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

        fecha_cierre_original = {}

        for paquete in paquetes:
            paquete.refresh_from_db()
            fecha_cierre_original[paquete.pk] = (
                paquete.fecha_cierre
            )

        # Segundo cierre: debe rechazarse.
        with self.assertRaisesMessage(
            ValueError,
            "La operación ya está cerrada.",
        ):
            cerrar_operacion(
                codigo_proceso=codigo_proceso,
                imagenes_ids=[imagen.id],
            )

        pago.refresh_from_db()
        imagen.refresh_from_db()

        for paquete in paquetes:
            paquete.refresh_from_db()

        # No existe un segundo consumo.
        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

        # El crédito liberado sigue disponible.
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            1,
        )

        # Los paquetes permanecen cerrados.
        for paquete in paquetes:
            self.assertEqual(
                paquete.estado,
                PaqueteImagen.EstadoPaquete.CERRADO,
            )

            self.assertEqual(
                paquete.fecha_cierre,
                fecha_cierre_original[paquete.pk],
            )

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertEqual(
            paquetes[1].estado_credito,
            PaqueteImagen.EstadoCredito.LIBERADO,
        )

        # La selección original se conserva.
        self.assertTrue(imagen.seleccionada)

    def test_cerrar_operacion_revierte_todo_si_falla_cierre(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ022",
            nombre="Sector Rollback Cierre",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-ROLLBACK-CIERRE-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-ROLLBACK-CIERRE-001"

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso=codigo_proceso,
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        fondo = Fondo.objects.create(
            codigo_fondo="FONDO-ROLLBACK-CIERRE-001",
            imagen="fondos/rollback_cierre.jpg",
        )

        imagen = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-ROLLBACK-CIERRE-001",
            fondo=fondo,
            imagen_procesada=(
                "imagenes/procesadas/rollback_cierre.jpg"
            ),
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        # Confirmar el estado inicial.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 2)
        self.assertEqual(pago.creditos_usados, 0)

        # Guardar el método original antes de aplicar patch.
        filtro_real = PaqueteImagen.objects.filter

        def filtro_con_error(*args, **kwargs):
            queryset = filtro_real(*args, **kwargs)

            if "pk__in" in kwargs:

                def fallar_update(**campos):
                    raise RuntimeError(
                        "Error simulado al cerrar paquetes"
                    )

                queryset.update = fallar_update

            return queryset

        with patch(
            "web.services.creditos.PaqueteImagen.objects.filter",
            side_effect=filtro_con_error,
        ):
            with self.assertRaisesMessage(
                RuntimeError,
                "Error simulado al cerrar paquetes",
            ):
                cerrar_operacion(
                    codigo_proceso=codigo_proceso,
                    imagenes_ids=[imagen.id],
                )

        # Verificar que toda la transacción se revirtió.
        pago.refresh_from_db()
        imagen.refresh_from_db()

        for paquete in paquetes:
            paquete.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 2)
        self.assertEqual(pago.creditos_usados, 0)
        self.assertFalse(pago.pago_consumido)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            0,
        )

        self.assertFalse(imagen.seleccionada)

        for paquete in paquetes:
            self.assertEqual(
                paquete.estado_credito,
                PaqueteImagen.EstadoCredito.RESERVADO,
            )

            self.assertEqual(
                paquete.estado,
                PaqueteImagen.EstadoPaquete.RECIBIDO,
            )

            self.assertIsNone(paquete.fecha_cierre)

    def test_cerrar_operacion_respeta_fifo_entre_pagos(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ023",
            nombre="Sector FIFO Cierre",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago_antiguo = Pago.objects.create(
            codigo_pago="PAGO-FIFO-CIERRE-001",
            sector=sector,
            anio=2026,
            semana=42,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        pago_reciente = Pago.objects.create(
            codigo_pago="PAGO-FIFO-CIERRE-002",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        # Establecer explícitamente el orden FIFO.
        fecha_base = timezone.now()

        Pago.objects.filter(
            pk=pago_antiguo.pk,
        ).update(
            fecha_pago=fecha_base - timedelta(days=7),
        )

        Pago.objects.filter(
            pk=pago_reciente.pk,
        ).update(
            fecha_pago=fecha_base,
        )

        codigo_proceso = "PROCESO-FIFO-CIERRE-001"

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso=codigo_proceso,
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)
        self.assertEqual(paquetes[0].pago_id, pago_antiguo.pk)
        self.assertEqual(paquetes[1].pago_id, pago_reciente.pk)

        fondo = Fondo.objects.create(
            codigo_fondo="FONDO-FIFO-CIERRE-001",
            imagen="fondos/fifo_cierre.jpg",
        )

        imagen = Imagen.objects.create(
            paquete=paquetes[0],
            codigo_imagen="IMG-FIFO-CIERRE-001",
            fondo=fondo,
            imagen_procesada=(
                "imagenes/procesadas/fifo_cierre.jpg"
            ),
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        # Una imagen seleccionada: consume un crédito.
        cerrar_operacion(
            codigo_proceso=codigo_proceso,
            imagenes_ids=[imagen.pk],
        )

        pago_antiguo.refresh_from_db()
        pago_reciente.refresh_from_db()

        for paquete in paquetes:
            paquete.refresh_from_db()

        imagen.refresh_from_db()

        # El pago más antiguo absorbe el consumo.
        self.assertEqual(pago_antiguo.creditos_reservados, 0)
        self.assertEqual(pago_antiguo.creditos_usados, 1)
        self.assertTrue(pago_antiguo.pago_consumido)

        # El pago más reciente recupera su crédito.
        self.assertEqual(pago_reciente.creditos_reservados, 0)
        self.assertEqual(pago_reciente.creditos_usados, 0)
        self.assertFalse(pago_reciente.pago_consumido)

        self.assertEqual(
            obtener_creditos_disponibles(sector.pk),
            1,
        )

        self.assertEqual(
            paquetes[0].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertEqual(
            paquetes[1].estado_credito,
            PaqueteImagen.EstadoCredito.LIBERADO,
        )

        for paquete in paquetes:
            self.assertEqual(
                paquete.estado,
                PaqueteImagen.EstadoPaquete.CERRADO,
            )
            self.assertIsNotNone(paquete.fecha_cierre)

        self.assertTrue(imagen.seleccionada)

    def test_rechaza_cuatro_imagenes_del_mismo_paquete(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ024",
            nombre="Sector Limite Paquete",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        Pago.objects.create(
            codigo_pago="PAGO-LIMITE-PAQUETE-001",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-LIMITE-PAQUETE-001"

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso=codigo_proceso,
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)

        imagenes_ids = []

        for numero in range(1, 5):
            fondo = Fondo.objects.create(
                codigo_fondo=f"FONDO-LIMITE-{numero}",
                imagen=f"fondos/limite_{numero}.jpg",
            )

            imagen = Imagen.objects.create(
                paquete=paquetes[0],
                codigo_imagen=f"IMG-LIMITE-{numero}",
                fondo=fondo,
                imagen_procesada=(
                    f"imagenes/procesadas/limite_{numero}.jpg"
                ),
                estado_proceso=Imagen.EstadoProceso.PROCESADA,
            )

            imagenes_ids.append(imagen.pk)

        with self.assertRaisesMessage(
            ValueError,
            "Un paquete no puede tener más de 3 imágenes seleccionadas.",
        ):
            cerrar_operacion(
                codigo_proceso=codigo_proceso,
                imagenes_ids=imagenes_ids,
            )

        pago = paquetes[0].pago
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 2)
        self.assertEqual(pago.creditos_usados, 0)

        for paquete in paquetes:
            paquete.refresh_from_db()

            self.assertEqual(
                paquete.estado_credito,
                PaqueteImagen.EstadoCredito.RESERVADO,
            )

            self.assertEqual(
                paquete.estado,
                PaqueteImagen.EstadoPaquete.RECIBIDO,
            )

            self.assertIsNone(paquete.fecha_cierre)

        self.assertEqual(
            Imagen.objects.filter(
                pk__in=imagenes_ids,
                seleccionada=True,
            ).count(),
            0,
        )

    def test_cierre_no_libera_paquete_con_imagen_seleccionada(self):

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ025",
            nombre="Sector Correspondencia",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        pago_antiguo = Pago.objects.create(
            codigo_pago="PAGO-CORRESP-001",
            sector=sector,
            anio=2026,
            semana=42,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        pago_reciente = Pago.objects.create(
            codigo_pago="PAGO-CORRESP-002",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        fecha_base = timezone.now()

        Pago.objects.filter(
            pk=pago_antiguo.pk,
        ).update(
            fecha_pago=fecha_base - timedelta(days=7),
        )

        Pago.objects.filter(
            pk=pago_reciente.pk,
        ).update(
            fecha_pago=fecha_base,
        )

        codigo_proceso = "PROCESO-CORRESP-001"

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.id,
            codigo_proceso=codigo_proceso,
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)
        self.assertEqual(paquetes[0].pago_id, pago_antiguo.pk)
        self.assertEqual(paquetes[1].pago_id, pago_reciente.pk)

        fondo = Fondo.objects.create(
            codigo_fondo="FONDO-CORRESP-001",
            imagen="fondos/correspondencia.jpg",
        )

        imagen = Imagen.objects.create(
            paquete=paquetes[1],
            codigo_imagen="IMG-CORRESP-001",
            fondo=fondo,
            imagen_procesada=(
                "imagenes/procesadas/correspondencia.jpg"
            ),
            estado_proceso=Imagen.EstadoProceso.PROCESADA,
        )

        cerrar_operacion(
            codigo_proceso=codigo_proceso,
            imagenes_ids=[imagen.pk],
        )

        for paquete in paquetes:
            paquete.refresh_from_db()

        imagen.refresh_from_db()

        # El paquete que contiene la imagen seleccionada
        # debe tener un crédito consumido.
        self.assertEqual(
            paquetes[1].estado_credito,
            PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        self.assertTrue(imagen.seleccionada)

        pago_antiguo.refresh_from_db()
        pago_reciente.refresh_from_db()

        # El consumo financiero se realiza sobre el pago antiguo.
        self.assertEqual(
            pago_antiguo.creditos_reservados,
            0,
        )
        self.assertEqual(
            pago_antiguo.creditos_usados,
            1,
        )

        # El pago reciente recupera su crédito.
        self.assertEqual(
            pago_reciente.creditos_reservados,
            0,
        )
        self.assertEqual(
            pago_reciente.creditos_usados,
            0,
        )

        # El paquete seleccionado queda asociado al pago
        # que financió efectivamente el crédito.
        # El paquete conserva el Pago original de reserva.
        self.assertEqual(
            paquetes[1].pago_id,
            pago_reciente.pk,
        )        

        # El Sector conserva un crédito disponible.
        self.assertEqual(
            obtener_creditos_disponibles(sector.pk),
            1,
        )

    def test_cierre_tres_imagenes_de_dos_paquetes_consume_un_credito(self):
        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )
        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )
        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ026",
            nombre="Sector Seleccion Distribuida",
            representante="Representante Prueba",
            telefono1="6560000000",
        )
        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad de Venta 1",
            whatsapp="6560000000",
        )

        fecha_base = timezone.now()

        pago_antiguo = Pago.objects.create(
            codigo_pago="PAGO-DIST-001",
            sector=sector,
            anio=2026,
            semana=42,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
            fecha_pago=fecha_base - timedelta(days=7),
        )
        pago_reciente = Pago.objects.create(
            codigo_pago="PAGO-DIST-002",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
            fecha_pago=fecha_base,
        )

        paquetes = reservar_creditos(
            unidad_venta_id=unidad.pk,
            codigo_proceso="PROCESO-DIST-001",
            imagen_original=ContentFile(
                b"imagen_original",
                name="foto_original.jpg",
            ),
        )

        self.assertEqual(len(paquetes), 2)

        pago_ids_originales = [
            paquete.pago_id for paquete in paquetes
        ]

        imagenes = []
        for indice in range(3):
            fondo = Fondo.objects.create(
                codigo_fondo=f"FONDO-DIST-{indice}",
                imagen=f"fondos/dist_{indice}.jpg",
            )
            paquete = paquetes[0] if indice < 2 else paquetes[1]

            imagenes.append(
                Imagen.objects.create(
                    paquete=paquete,
                    codigo_imagen=f"IMG-DIST-{indice}",
                    fondo=fondo,
                    imagen_procesada=f"imagenes/dist_{indice}.png",
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )
            )

        cerrar_operacion(
            codigo_proceso="PROCESO-DIST-001",
            imagenes_ids=[imagen.pk for imagen in imagenes],
        )

        pago_antiguo.refresh_from_db()
        pago_reciente.refresh_from_db()

        for paquete in paquetes:
            paquete.refresh_from_db()

        self.assertEqual(pago_antiguo.creditos_usados, 1)
        self.assertEqual(pago_reciente.creditos_usados, 0)
        self.assertEqual(pago_antiguo.creditos_reservados, 0)
        self.assertEqual(pago_reciente.creditos_reservados, 0)

        self.assertEqual(
            [paquete.pago_id for paquete in paquetes],
            pago_ids_originales,
        )

        self.assertEqual(
            sum(
                paquete.estado_credito
                == PaqueteImagen.EstadoCredito.CONSUMIDO
                for paquete in paquetes
            ),
            1,
        )
        self.assertEqual(
            sum(
                paquete.estado_credito
                == PaqueteImagen.EstadoCredito.LIBERADO
                for paquete in paquetes
            ),
            1,
        )

        self.assertTrue(
            all(
                paquete.estado == PaqueteImagen.EstadoPaquete.CERRADO
                for paquete in paquetes
            )
        )

        self.assertEqual(
            Imagen.objects.filter(
                pk__in=[imagen.pk for imagen in imagenes],
                seleccionada=True,
            ).count(),
            3,
        )

        self.assertEqual(
            obtener_creditos_disponibles(sector.pk),
            1,
        )

    def test_recibir_imagen_guarda_original_una_sola_vez(self):
        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ036",
            nombre="Sector Prueba 36",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 36",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-036",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        imagen = SimpleUploadedFile(
            "foto_original.jpg",
            b"contenido-de-prueba",
            content_type="image/jpeg",
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.id,
                    codigo_proceso="PROCESO-036",
                    imagen_original=imagen,
                )

                self.assertEqual(len(paquetes), 2)

                rutas = {
                    paquete.imagen_original.name
                    for paquete in paquetes
                }

                # Ambos paquetes comparten la misma ruta.
                self.assertEqual(len(rutas), 1)

                ruta = rutas.pop()

                # La fotografía existe físicamente.
                archivo = Path(directorio) / ruta
                self.assertTrue(archivo.is_file())

                # Solamente existe un archivo original.
                archivos = [
                    p for p in Path(directorio).rglob("*")
                    if p.is_file()
                ]
                self.assertEqual(len(archivos), 1)

                # Los créditos están reservados, no consumidos.
                pago.refresh_from_db()

                self.assertEqual(pago.creditos_reservados, 2)
                self.assertEqual(pago.creditos_usados, 0)

                for paquete in paquetes:
                    self.assertEqual(
                        paquete.estado_credito,
                        PaqueteImagen.EstadoCredito.RESERVADO,
                    )

    def test_seleccionar_fondos_activos(self):
        from web.services.procesamiento import seleccionar_fondos

        # Crear seis fondos activos.
        for numero in range(1, 7):
            Fondo.objects.create(
                codigo_fondo=f"FONDO-{numero:02d}",
                imagen=f"fondos/fondo_{numero:02d}.jpg",
                activo=True,
            )

        # Crear un fondo inactivo.
        Fondo.objects.create(
            codigo_fondo="FONDO-00",
            imagen="fondos/fondo_00.jpg",
            activo=False,
        )

        fondos = seleccionar_fondos(3)

        self.assertEqual(len(fondos), 3)

        self.assertEqual(
            len({fondo.pk for fondo in fondos}),
            3,
        )

        self.assertNotIn(
            "FONDO-00",
            [fondo.codigo_fondo for fondo in fondos],
        )
    
        self.assertTrue(
            all(fondo.activo for fondo in fondos)
        )

        # También debe permitir seleccionar seis fondos.
        fondos_seis = seleccionar_fondos(6)

        self.assertEqual(len(fondos_seis), 6)

        self.assertEqual(
            len({fondo.pk for fondo in fondos_seis}),
            6,
        )

        # No debe permitir solicitar una cantidad diferente.
        with self.assertRaises(ValueError):
            seleccionar_fondos(4)

        # No debe permitir más fondos de los disponibles.
        Fondo.objects.filter(
            codigo_fondo="FONDO-06"
        ).update(activo=False)

        with self.assertRaises(ValueError):
            seleccionar_fondos(6)

    def test_generar_tres_variantes_y_guardarlas(self):
        import tempfile
        from decimal import Decimal
        from pathlib import Path
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ038",
            nombre="Sector Prueba 38",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 38",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-038",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-38-{numero}",
                        activo=True,
                    )
                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-038",
                    imagen_original=original,
                )

                self.assertEqual(len(paquetes), 1)

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    return_value=[
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ],
                ) as generador:

                    procesar_variantes("PROCESO-038")

                    generador.assert_called_once()

                paquete = paquetes[0]
                paquete.refresh_from_db()
                pago.refresh_from_db()

                imagenes = list(
                    Imagen.objects.filter(
                        paquete=paquete
                    ).order_by("pk")
                )

                self.assertEqual(len(imagenes), 3)

                self.assertEqual(
                    len({imagen.fondo_id for imagen in imagenes}),
                    3,
                )

                for imagen in imagenes:
                    self.assertEqual(
                        imagen.estado_proceso,
                        Imagen.EstadoProceso.PROCESADA,
                    )

                    self.assertTrue(
                        imagen.imagen_procesada.name
                    )

                    self.assertTrue(
                        Path(
                            imagen.imagen_procesada.path
                        ).is_file()
                    )

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)

    def test_generar_seis_variantes_en_dos_paquetes(self):
        import tempfile
        from decimal import Decimal
        from pathlib import Path
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ039",
            nombre="Sector Prueba 39",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 39",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-039",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 7):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-39-{numero}",
                        activo=True,
                    )
                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-039",
                    imagen_original=original,
                )

                self.assertEqual(len(paquetes), 2)

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    return_value=[
                        f"variante-{numero}".encode()
                        for numero in range(1, 7)
                    ],
                ) as generador:

                    procesar_variantes("PROCESO-039")

                    generador.assert_called_once()

                paquetes = list(
                    PaqueteImagen.objects.filter(
                        codigo_proceso="PROCESO-039"
                    ).order_by("numero_paquete")
                )

                imagenes = list(
                    Imagen.objects.filter(
                        paquete__in=paquetes
                    ).order_by("pk")
                )

                self.assertEqual(len(imagenes), 6)

                self.assertEqual(
                    len({imagen.fondo_id for imagen in imagenes}),
                    6,
                )

                self.assertEqual(
                    Imagen.objects.filter(
                        paquete=paquetes[0]
                    ).count(),
                    3,
                )

                self.assertEqual(
                    Imagen.objects.filter(
                        paquete=paquetes[1]
                    ).count(),
                    3,
                )

                for paquete in paquetes:
                    self.assertEqual(
                        paquete.estado,
                        PaqueteImagen.EstadoPaquete.LISTO,
                    )

                    self.assertEqual(
                        paquete.estado_credito,
                        PaqueteImagen.EstadoCredito.RESERVADO,
                    )

                for imagen in imagenes:
                    self.assertEqual(
                        imagen.estado_proceso,
                        Imagen.EstadoProceso.PROCESADA,
                    )

                    self.assertTrue(
                        Path(
                            imagen.imagen_procesada.path
                        ).is_file()
                    )

                # Ambos paquetes comparten el original.
                self.assertEqual(
                    paquetes[0].imagen_original.name,
                    paquetes[1].imagen_original.name,
                )

                # Un original y seis variantes procesadas.
                originales = list(
                    (Path(directorio) / "imagenes" / "originales")
                    .rglob("*")
                )

                self.assertEqual(
                    len([p for p in originales if p.is_file()]),
                    1,
                )

                pago.refresh_from_db()

                self.assertEqual(pago.creditos_reservados, 2)
                self.assertEqual(pago.creditos_usados, 0)

    def test_procesar_variantes_es_idempotente(self):
        import tempfile
        from decimal import Decimal
        from pathlib import Path
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ040",
            nombre="Sector Prueba 40",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 40",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-040",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-40-{numero}",
                        activo=True,
                    )
                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-040",
                    imagen_original=original,
                )

                paquete = paquetes[0]

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    return_value=[
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ],
                ) as generador:

                    primera = procesar_variantes("PROCESO-040")
                    segunda = procesar_variantes("PROCESO-040")

                    self.assertEqual(
                        generador.call_count,
                        1,
                    )

                self.assertEqual(len(primera), 3)
                self.assertEqual(len(segunda), 3)

                self.assertEqual(
                    {imagen.pk for imagen in primera},
                    {imagen.pk for imagen in segunda},
                )

                self.assertEqual(
                    Imagen.objects.filter(
                        paquete=paquete
                    ).count(),
                    3,
                )

                archivos = [
                    archivo
                    for archivo in (
                        Path(directorio) / "imagenes" / "procesadas"
                    ).rglob("*")
                    if archivo.is_file()
                ]

                self.assertEqual(len(archivos), 3)

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)

    def test_error_motor_ia_no_genera_imagenes_ni_consume_creditos(self):
        import tempfile
        from decimal import Decimal
        from pathlib import Path
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ041",
            nombre="Sector Prueba 41",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 41",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-041",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-41-{numero}",
                        activo=True,
                    )
                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-041",
                    imagen_original=original,
                )

                paquete = paquetes[0]

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    side_effect=RuntimeError(
                        "Error simulado del motor de IA"
                    ),
                ) as generador:

                    with self.assertRaisesRegex(
                        RuntimeError,
                        "Error simulado del motor de IA",
                    ):
                        procesar_variantes("PROCESO-041")

                    generador.assert_called_once()

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    Imagen.objects.filter(
                        paquete=paquete,
                    ).count(),
                    0,
                )

                archivos_procesados = [
                    archivo
                    for archivo in (
                        Path(directorio) / "imagenes" / "procesadas"
                    ).rglob("*")
                    if archivo.is_file()
                ]

                self.assertEqual(len(archivos_procesados), 0)

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.RECIBIDO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)

    def test_error_guardando_variante_revierte_registros_y_archivos(self):
        import tempfile
        from decimal import Decimal
        from pathlib import Path
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ042",
            nombre="Sector Prueba 42",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 42",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-042",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-42-{numero}",
                        activo=True,
                    )

                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-042",
                    imagen_original=original,
                )

                paquete = paquetes[0]

                # Fallar al guardar el segundo registro Imagen.
                # El primer archivo ya debe haberse guardado.
                guardar_original = Imagen.save
                contador = {"llamadas": 0}

                def guardar_con_error(instancia, *args, **kwargs):
                    contador["llamadas"] += 1

                    if contador["llamadas"] == 2:
                        raise RuntimeError(
                            "Error simulado guardando segunda imagen"
                        )

                    return guardar_original(
                        instancia,
                        *args,
                        **kwargs,
                    )

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    return_value=[
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ],
                ):
                    with patch.object(
                        Imagen,
                        "save",
                        guardar_con_error,
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "Error simulado guardando segunda imagen",
                        ):
                            procesar_variantes("PROCESO-042")

                self.assertEqual(
                    contador["llamadas"],
                    2,
                )

                # La transacción debe revertir los registros.
                self.assertEqual(
                    Imagen.objects.filter(
                        paquete=paquete,
                    ).count(),
                    0,
                )

                # También deben eliminarse los archivos físicos.
                archivos_procesados = [
                    archivo
                    for archivo in (
                        Path(directorio) / "imagenes" / "procesadas"
                    ).rglob("*")
                    if archivo.is_file()
                ]

                self.assertEqual(
                    len(archivos_procesados),
                    0,
                )

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.RECIBIDO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertEqual(
                    pago.creditos_reservados,
                    1,
                )

                self.assertEqual(
                    pago.creditos_usados,
                    0,
                )

    def test_paquete_esta_procesando_durante_ejecucion_ia(self):
        import tempfile
        from decimal import Decimal
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ043",
            nombre="Sector Prueba 43",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 43",
            whatsapp="6560000000",
        )

        Pago.objects.create(
            codigo_pago="PAGO-TEST-043",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-43-{numero}",
                        activo=True,
                    )
                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-043",
                    imagen_original=original,
                )

                paquete = paquetes[0]
                estados_durante_ia = []

                def motor_simulado(original_bytes, fondos_bytes):
                    paquete.refresh_from_db()

                    estados_durante_ia.append(paquete.estado)

                    self.assertEqual(
                        paquete.estado,
                        PaqueteImagen.EstadoPaquete.PROCESANDO,
                    )

                    self.assertEqual(
                        paquete.estado_credito,
                        PaqueteImagen.EstadoCredito.RESERVADO,
                    )

                    self.assertEqual(len(fondos_bytes), 3)

                    return [
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ]

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    side_effect=motor_simulado,
                ) as generador:
                    procesar_variantes("PROCESO-043")

                    generador.assert_called_once()

                paquete.refresh_from_db()

                self.assertEqual(
                    estados_durante_ia,
                    [PaqueteImagen.EstadoPaquete.PROCESANDO],
                )

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertEqual(
                    Imagen.objects.filter(paquete=paquete).count(),
                    3,
                )
    
    def test_segunda_solicitud_no_procesa_operacion_en_curso(self):
        import tempfile
        from decimal import Decimal
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ044",
            nombre="Sector Prueba 44",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 44",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-044",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-44-{numero}",
                        activo=True,
                    )

                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-044",
                    imagen_original=original,
                )

                paquete = paquetes[0]
                intentos_motor = []

                def motor_simulado(original_bytes, fondos_bytes):
                    intentos_motor.append(1)

                    paquete.refresh_from_db()

                    self.assertEqual(
                        paquete.estado,
                        PaqueteImagen.EstadoPaquete.PROCESANDO,
                    )

                    # Simula otra solicitud mientras la primera
                    # todavía está ejecutando el motor de IA.
                    with self.assertRaisesRegex(
                        ValueError,
                        "La operación ya está en procesamiento",
                    ):
                        procesar_variantes("PROCESO-044")

                    return [
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ]

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    side_effect=motor_simulado,
                ) as generador:

                    imagenes = procesar_variantes("PROCESO-044")

                    generador.assert_called_once()

                self.assertEqual(len(intentos_motor), 1)
                self.assertEqual(len(imagenes), 3)

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertEqual(
                    Imagen.objects.filter(
                        paquete=paquete,
                    ).count(),
                    3,
                )

                self.assertEqual(
                    pago.creditos_reservados,
                    1,
                )

                self.assertEqual(
                    pago.creditos_usados,
                    0,
                )

    def test_procesar_variantes_registra_fechas_inicio_y_fin(self):
        import tempfile
        from decimal import Decimal
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ047",
            nombre="Sector Prueba 47",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 47",
            whatsapp="6560000000",
        )

        Pago.objects.create(
            codigo_pago="PAGO-TEST-047",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-47-{numero}",
                        activo=True,
                    )
                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-047",
                    imagen_original=original,
                )

                paquete = paquetes[0]

                self.assertIsNone(paquete.fecha_inicio_proceso)
                self.assertIsNone(paquete.fecha_fin_proceso)

                def motor_simulado(original_bytes, fondos_bytes):
                    paquete.refresh_from_db()

                    self.assertEqual(
                        paquete.estado,
                        PaqueteImagen.EstadoPaquete.PROCESANDO,
                    )
                    self.assertIsNotNone(
                        paquete.fecha_inicio_proceso,
                    )
                    self.assertIsNone(
                        paquete.fecha_fin_proceso,
                    )

                    return [
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ]

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    side_effect=motor_simulado,
                ):
                    imagenes = procesar_variantes("PROCESO-047")

                paquete.refresh_from_db()

                self.assertEqual(len(imagenes), 3)

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertIsNotNone(
                    paquete.fecha_inicio_proceso,
                )

                self.assertIsNotNone(
                    paquete.fecha_fin_proceso,
                )

                self.assertGreaterEqual(
                    paquete.fecha_fin_proceso,
                    paquete.fecha_inicio_proceso,
                )

                paquete.pago.refresh_from_db()

                self.assertEqual(
                    paquete.pago.creditos_reservados,
                    1,
                )
                self.assertEqual(
                    paquete.pago.creditos_usados,
                    0,
                )

    def test_error_procesamiento_conserva_fecha_inicio_sin_fecha_fin(self):
        import tempfile
        from decimal import Decimal
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ048",
            nombre="Sector Prueba 48",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 48",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-048",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-48-{numero}",
                        activo=True,
                    )

                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-048",
                    imagen_original=original,
                )

                paquete = paquetes[0]

                def motor_con_error(original_bytes, fondos_bytes):
                    paquete.refresh_from_db()

                    self.assertEqual(
                        paquete.estado,
                        PaqueteImagen.EstadoPaquete.PROCESANDO,
                    )

                    self.assertIsNotNone(
                        paquete.fecha_inicio_proceso,
                    )

                    self.assertIsNone(
                        paquete.fecha_fin_proceso,
                    )

                    raise RuntimeError("Error simulado del motor de IA")

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    side_effect=motor_con_error,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "Error simulado del motor de IA",
                    ):
                        procesar_variantes("PROCESO-048")

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.RECIBIDO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertIsNotNone(
                    paquete.fecha_inicio_proceso,
                )

                self.assertIsNone(
                    paquete.fecha_fin_proceso,
                )

                self.assertEqual(
                    Imagen.objects.filter(paquete=paquete).count(),
                    0,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)

    def test_reintento_procesamiento_actualiza_fechas_sin_reservar_mas_creditos(self):
        import tempfile
        from decimal import Decimal
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from django.utils import timezone

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ049",
            nombre="Sector Prueba 49",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 49",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-049",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-49-{numero}",
                        activo=True,
                    )
                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-049",
                    imagen_original=original,
                )

                paquete = paquetes[0]

                # Primer intento: falla el motor de IA.
                with patch(
                    "web.services.procesamiento.generar_variantes",
                    side_effect=RuntimeError("Error temporal de IA"),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "Error temporal de IA",
                    ):
                        procesar_variantes("PROCESO-049")

                paquete.refresh_from_db()
                pago.refresh_from_db()

                primera_fecha = paquete.fecha_inicio_proceso

                self.assertIsNotNone(primera_fecha)
                self.assertIsNone(paquete.fecha_fin_proceso)

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.RECIBIDO,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)

                # Simulamos que el primer intento ocurrió antes.
                fecha_anterior = timezone.now() - timezone.timedelta(
                    minutes=5,
                )

                PaqueteImagen.objects.filter(
                    pk=paquete.pk,
                ).update(
                    fecha_inicio_proceso=fecha_anterior,
                )

                # Segundo intento: procesamiento exitoso.
                with patch(
                    "web.services.procesamiento.generar_variantes",
                    return_value=[
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ],
                ) as generador:

                    imagenes = procesar_variantes("PROCESO-049")

                    generador.assert_called_once()

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(len(imagenes), 3)

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertGreater(
                    paquete.fecha_inicio_proceso,
                    fecha_anterior,
                )

                self.assertIsNotNone(
                    paquete.fecha_fin_proceso,
                )

                self.assertGreaterEqual(
                    paquete.fecha_fin_proceso,
                    paquete.fecha_inicio_proceso,
                )

                self.assertEqual(
                    Imagen.objects.filter(paquete=paquete).count(),
                    3,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)

    def test_cancelar_operacion_libera_creditos_sin_duplicar(self):
        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Cancelacion",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-CANCEL-001",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("500.00"),
            paquetes_pagados=5,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-CANCEL-001"

        for numero in (1, 2):
            PaqueteImagen.objects.create(
                numero_paquete=numero,
                unidad_venta=unidad,
                pago=pago,
                codigo_proceso=codigo_proceso,
                imagen_original="imagenes/originales/prueba.jpg",
                estado=PaqueteImagen.EstadoPaquete.LISTO,
                estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
            )

        pago.creditos_reservados = 2
        pago.save(update_fields=["creditos_reservados"])

        # Primera cancelación.
        cancelar_operacion(codigo_proceso)

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(pago.creditos_usados, 0)
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            5,
        )

        paquetes = PaqueteImagen.objects.filter(
            codigo_proceso=codigo_proceso
        )

        self.assertEqual(paquetes.count(), 2)

        for paquete in paquetes:
            self.assertEqual(
                paquete.estado,
                PaqueteImagen.EstadoPaquete.CANCELADO,
            )
            self.assertEqual(
                paquete.estado_credito,
                PaqueteImagen.EstadoCredito.LIBERADO,
            )

        # Segunda cancelación: no debe modificar los saldos.
        cancelar_operacion(codigo_proceso)

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_reservados, 0)
        self.assertEqual(pago.creditos_usados, 0)
        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            5,
        )

    def test_cancelar_operacion_libera_creditos_de_pagos_distintos(self):
        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Cancelacion",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pagos = []

        for numero in (1, 2):
            pago = Pago.objects.create(
                codigo_pago=f"PAGO-CANCEL-{numero}",
                sector=sector,
                anio=2026,
                semana=43 + numero,
                importe_pagado=Decimal("100.00"),
                paquetes_pagados=1,
                creditos_reservados=1,
                precio_unitario=Decimal("100.00"),
            )
            pagos.append(pago)

            PaqueteImagen.objects.create(
                numero_paquete=numero,
                unidad_venta=unidad,
                pago=pago,
                codigo_proceso="PROCESO-CANCEL-DOS-PAGOS",
                imagen_original="imagenes/originales/prueba.jpg",
                estado=PaqueteImagen.EstadoPaquete.LISTO,
                estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
            )

        cancelar_operacion("PROCESO-CANCEL-DOS-PAGOS")

        for pago in pagos:
            pago.refresh_from_db()
            self.assertEqual(pago.creditos_reservados, 0)
            self.assertEqual(pago.creditos_usados, 0)

        self.assertEqual(
            obtener_creditos_disponibles(sector.id),
            2,
        )

        paquetes = PaqueteImagen.objects.filter(
            codigo_proceso="PROCESO-CANCEL-DOS-PAGOS"
        )

        self.assertEqual(
            paquetes.filter(
                estado=PaqueteImagen.EstadoPaquete.CANCELADO,
                estado_credito=PaqueteImagen.EstadoCredito.LIBERADO,
            ).count(),
            2,
        )

        # Comprobar idempotencia.
        cancelar_operacion("PROCESO-CANCEL-DOS-PAGOS")

        for pago in pagos:
            pago.refresh_from_db()
            self.assertEqual(pago.creditos_reservados, 0)

    def test_identificar_operaciones_vencidas(self):
        from datetime import timedelta
        from django.utils import timezone

        ahora = timezone.now()

        fecha_vencida = ahora - timedelta(hours=13)
        fecha_vigente = ahora - timedelta(hours=5)

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Prueba",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-VENCIMIENTO-001",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("200.00"),
            paquetes_pagados=2,
            creditos_reservados=2,
            precio_unitario=Decimal("100.00"),
        )

        for codigo, fecha in (
            ("PROCESO-VENCIDO", fecha_vencida),
            ("PROCESO-VIGENTE", fecha_vigente),
        ):
            PaqueteImagen.objects.create(
                numero_paquete=1,
                unidad_venta=unidad,
                pago=pago,
                codigo_proceso=codigo,
                imagen_original="imagenes/originales/prueba.jpg",
                estado=PaqueteImagen.EstadoPaquete.LISTO,
                estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
                fecha_fin_proceso=fecha,
            )

        limite = ahora - timedelta(hours=12)

        operaciones_vencidas = list(
            PaqueteImagen.objects.filter(
                estado=PaqueteImagen.EstadoPaquete.LISTO,
                estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
                fecha_fin_proceso__lte=limite,
            )
            .values_list("codigo_proceso", flat=True)
            .distinct()
        )

        self.assertEqual(
            operaciones_vencidas,
            ["PROCESO-VENCIDO"],
        )

    def test_eliminar_archivos_operacion_conserva_registros(self):
        import tempfile
        from pathlib import Path
        from django.core.files.base import ContentFile
        from django.test import override_settings
        from web.services.limpieza import eliminar_archivos_operacion
        from datetime import timedelta
        from django.utils import timezone

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Limpieza",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-LIMPIEZA-001",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-LIMPIEZA-001",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    fecha_fin_proceso=timezone.now() - timedelta(hours=13),
                )

                paquete.imagen_original.save(
                    "original.jpg",
                    ContentFile(b"fotografia original"),
                    save=True,
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-LIMPIEZA-001",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    codigo_imagen="IMAGEN-LIMPIEZA-001",
                    fondo=fondo,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                    seleccionada=True,
                )

                imagen.imagen_procesada.save(
                    "procesada.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                ruta_original = Path(paquete.imagen_original.path)
                ruta_procesada = Path(imagen.imagen_procesada.path)

                self.assertTrue(ruta_original.exists())
                self.assertTrue(ruta_procesada.exists())

                eliminados = eliminar_archivos_operacion(
                    "PROCESO-LIMPIEZA-001"
                )

                self.assertEqual(eliminados, 2)
                self.assertFalse(ruta_original.exists())
                self.assertFalse(ruta_procesada.exists())

                paquete.refresh_from_db()
                imagen.refresh_from_db()
                pago.refresh_from_db()

                self.assertFalse(paquete.imagen_original)
                self.assertFalse(imagen.imagen_procesada)

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.CERRADO,
                )
                self.assertEqual(
                    imagen.seleccionada,
                    True,
                )
                self.assertEqual(pago.creditos_usados, 1)

                # Segunda ejecución: no debe eliminar nuevamente.
                self.assertEqual(
                    eliminar_archivos_operacion(
                        "PROCESO-LIMPIEZA-001"
                    ),
                    0,
                )

    def test_comando_limpieza_cancela_y_elimina_archivos(self):
        import tempfile
        from datetime import timedelta
        from pathlib import Path

        from django.core.files.base import ContentFile
        from django.core.management import call_command
        from django.test import override_settings
        from django.utils import timezone

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Limpieza",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-LIMPIEZA-COMANDO",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_reservados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-LIMPIEZA-COMANDO",
                    estado=PaqueteImagen.EstadoPaquete.LISTO,
                    estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
                    fecha_fin_proceso=timezone.now() - timedelta(hours=13),
                )

                paquete.imagen_original.save(
                    "original.jpg",
                    ContentFile(b"fotografia original"),
                    save=True,
                )

                ruta_original = Path(paquete.imagen_original.path)

                self.assertTrue(ruta_original.exists())

                call_command("limpiar_imagenes", verbosity=0)

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.CANCELADO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.LIBERADO,
                )

                self.assertEqual(pago.creditos_reservados, 0)
                self.assertEqual(pago.creditos_usados, 0)
                self.assertFalse(ruta_original.exists())
                self.assertFalse(paquete.imagen_original)

                # Segunda ejecución: no debe alterar los saldos.
                call_command("limpiar_imagenes", verbosity=0)

                pago.refresh_from_db()

                self.assertEqual(pago.creditos_reservados, 0)
                self.assertEqual(pago.creditos_usados, 0)

    def test_comando_limpieza_elimina_procesadas_sin_original(self):
        import tempfile
        from datetime import timedelta
        from pathlib import Path

        from django.core.files.base import ContentFile
        from django.core.management import call_command
        from django.test import override_settings
        from django.utils import timezone

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Limpieza",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-LIMPIEZA-PROCESADA",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-SIN-ORIGINAL",
                    imagen_original="",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    fecha_fin_proceso=timezone.now() - timedelta(hours=13),
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-LIMPIEZA-PROCESADA",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    codigo_imagen="IMAGEN-LIMPIEZA-PROCESADA",
                    fondo=fondo,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                    seleccionada=True,
                )

                imagen.imagen_procesada.save(
                    "procesada.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                ruta_procesada = Path(imagen.imagen_procesada.path)
                self.assertTrue(ruta_procesada.exists())

                call_command("limpiar_imagenes", verbosity=0)

                paquete.refresh_from_db()
                imagen.refresh_from_db()
                pago.refresh_from_db()

                self.assertFalse(ruta_procesada.exists())
                self.assertFalse(imagen.imagen_procesada)

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.CERRADO,
                )
                self.assertTrue(imagen.seleccionada)
                self.assertEqual(pago.creditos_usados, 1)

                # Una segunda ejecución no debe alterar nada.
                call_command("limpiar_imagenes", verbosity=0)

                self.assertEqual(
                    Imagen.objects.filter(pk=imagen.pk).count(),
                    1,
                )

    def test_eliminar_archivos_respeta_tiempo_conservacion(self):
        import tempfile
        from datetime import timedelta
        from pathlib import Path

        from django.core.files.base import ContentFile
        from django.test import override_settings
        from django.utils import timezone

        from web.services.limpieza import eliminar_archivos_operacion

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                HORAS_CONSERVACION_IMAGENES=24,
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Conservación",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-CONSERVACION-24H",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PRUEBA-CONSERVACION-24H",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    fecha_fin_proceso=timezone.now() - timedelta(hours=13),
                )

                paquete.imagen_original.save(
                    "original_conservacion.jpg",
                    ContentFile(b"fotografia de prueba"),
                    save=True,
                )

                ruta_original = Path(paquete.imagen_original.path)
                self.assertTrue(ruta_original.exists())

                # Caso 1: 13 horas transcurridas, mínimo 24.
                with self.assertRaises(ValueError):
                    eliminar_archivos_operacion(
                        "PRUEBA-CONSERVACION-24H"
                    )

                paquete.refresh_from_db()

                self.assertTrue(ruta_original.exists())
                self.assertTrue(paquete.imagen_original)

                # Caso 2: ya transcurrieron 25 horas.
                paquete.fecha_fin_proceso = (
                    timezone.now() - timedelta(hours=25)
                )
                paquete.save(update_fields=["fecha_fin_proceso"])

                eliminados = eliminar_archivos_operacion(
                    "PRUEBA-CONSERVACION-24H"
                )

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(eliminados, 1)
                self.assertFalse(ruta_original.exists())
                self.assertFalse(paquete.imagen_original)

                # La operación y sus créditos permanecen intactos.
                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.CERRADO,
                )
                self.assertEqual(pago.creditos_usados, 1)

    def test_comando_limpieza_dry_run_no_modifica_datos(self):
        import tempfile
        from datetime import timedelta
        from pathlib import Path

        from django.core.files.base import ContentFile
        from django.core.management import call_command
        from django.test import override_settings
        from django.utils import timezone

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                HORAS_CONSERVACION_IMAGENES=12,
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Simulación",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-DRY-RUN",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_reservados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-DRY-RUN",
                    estado=PaqueteImagen.EstadoPaquete.LISTO,
                    estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
                    fecha_fin_proceso=timezone.now() - timedelta(hours=13),
                )

                paquete.imagen_original.save(
                    "original_dry_run.jpg",
                    ContentFile(b"fotografia de prueba"),
                    save=True,
                )

                ruta_original = Path(paquete.imagen_original.path)

                call_command("limpiar_imagenes", "--dry-run")

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertEqual(
                    paquete.estado_credito,
                    PaqueteImagen.EstadoCredito.RESERVADO,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)
                self.assertTrue(ruta_original.exists())
                self.assertTrue(paquete.imagen_original)

    def test_obtener_imagenes_para_entrega_dos_paquetes(self):
        import tempfile

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.services.entrega import obtener_imagenes_para_entrega

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Entrega",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-ENTREGA-001",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("200.00"),
                    paquetes_pagados=2,
                    creditos_usados=2,
                    precio_unitario=Decimal("100.00"),
                )

                paquete1 = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-ENTREGA-001",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                )

                paquete2 = PaqueteImagen.objects.create(
                    numero_paquete=2,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-ENTREGA-001",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                )

                fondo1 = Fondo.objects.create(
                    codigo_fondo="FONDO-ENTREGA-001",
                )

                fondo2 = Fondo.objects.create(
                    codigo_fondo="FONDO-ENTREGA-002",
                )

                fondo3 = Fondo.objects.create(
                    codigo_fondo="FONDO-ENTREGA-003",
                )

                imagen1 = Imagen.objects.create(
                    paquete=paquete1,
                    fondo=fondo1,
                    codigo_imagen="ENTREGA-IMAGEN-001",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen2 = Imagen.objects.create(
                    paquete=paquete1,
                    fondo=fondo2,
                    codigo_imagen="ENTREGA-IMAGEN-002",
                    seleccionada=False,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen3 = Imagen.objects.create(
                    paquete=paquete2,
                    fondo=fondo3,
                    codigo_imagen="ENTREGA-IMAGEN-003",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                for imagen in (imagen1, imagen2, imagen3):
                    imagen.imagen_procesada.save(
                        f"{imagen.codigo_imagen}.jpg",
                        ContentFile(b"imagen procesada"),
                        save=True,
                    )

                imagenes = obtener_imagenes_para_entrega(
                    "PROCESO-ENTREGA-001"
                )

                self.assertEqual(len(imagenes), 2)

                self.assertEqual(
                    [imagen.codigo_imagen for imagen in imagenes],
                    [
                        "ENTREGA-IMAGEN-001",
                        "ENTREGA-IMAGEN-003",
                    ],
                )

                pago.refresh_from_db()

                # La consulta no debe consumir créditos adicionales.
                self.assertEqual(pago.creditos_usados, 2)
                self.assertEqual(pago.creditos_reservados, 0)

                # Tampoco debe marcar las imágenes como enviadas.
                imagen1.refresh_from_db()
                imagen3.refresh_from_db()

                self.assertEqual(
                    imagen1.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )
                self.assertEqual(
                    imagen3.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )

    def test_obtener_imagenes_para_entrega_rechaza_operacion_no_cerrada(self):
        import tempfile

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.services.entrega import obtener_imagenes_para_entrega

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Entrega",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-ENTREGA-002",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_reservados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-ENTREGA-002",
                    estado=PaqueteImagen.EstadoPaquete.LISTO,
                    estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-ENTREGA-004",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="ENTREGA-IMAGEN-004",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "imagen_entrega_004.jpg",
                    ContentFile(b"imagen procesada"),
                    save=True,
                )

                with self.assertRaisesMessage(
                    ValueError,
                    "La operación todavía no está cerrada.",
                ):
                    obtener_imagenes_para_entrega(
                        "PROCESO-ENTREGA-002"
                    )

                paquete.refresh_from_db()
                pago.refresh_from_db()
                imagen.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )
                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)
                self.assertTrue(imagen.seleccionada)
                self.assertTrue(imagen.imagen_procesada)

    def test_obtener_imagenes_para_entrega_rechaza_archivo_inexistente(self):
        import tempfile

        from django.test import override_settings

        from web.services.entrega import obtener_imagenes_para_entrega

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Entrega",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-ENTREGA-003",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-ENTREGA-003",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-ENTREGA-005",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="ENTREGA-IMAGEN-005",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                    imagen_procesada="imagenes/procesadas/archivo_inexistente.jpg",
                )

                with self.assertRaisesMessage(
                    ValueError,
                    "El archivo de la imagen ENTREGA-IMAGEN-005 no existe.",
                ):
                    obtener_imagenes_para_entrega(
                        "PROCESO-ENTREGA-003"
                    )

                # La validación no debe modificar registros ni créditos.
                imagen.refresh_from_db()
                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertTrue(imagen.seleccionada)
                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )
                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.CERRADO,
                )
                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(
                    imagen.imagen_procesada.name,
                    "imagenes/procesadas/archivo_inexistente.jpg",
                )

    def test_registrar_imagenes_enviadas_es_idempotente(self):
        import tempfile

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.services.entrega import registrar_imagenes_enviadas

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Entrega",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-ENVIO-IDEMPOTENTE",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-ENVIO-IDEMPOTENTE",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                )

                fondo1 = Fondo.objects.create(
                    codigo_fondo="FONDO-ENVIO-001",
                )

                fondo2 = Fondo.objects.create(
                    codigo_fondo="FONDO-ENVIO-002",
                )

                imagen_seleccionada = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo1,
                    codigo_imagen="ENVIO-IMAGEN-001",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen_no_seleccionada = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo2,
                    codigo_imagen="ENVIO-IMAGEN-002",
                    seleccionada=False,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen_seleccionada.imagen_procesada.save(
                    "imagen_seleccionada.jpg",
                    ContentFile(b"imagen seleccionada"),
                    save=True,
                )

                imagen_no_seleccionada.imagen_procesada.save(
                    "imagen_no_seleccionada.jpg",
                    ContentFile(b"imagen no seleccionada"),
                    save=True,
                )

                # Primera ejecución: registra el envío.
                cantidad = registrar_imagenes_enviadas(
                    "PROCESO-ENVIO-IDEMPOTENTE"
                )

                self.assertEqual(cantidad, 1)

                imagen_seleccionada.refresh_from_db()

                self.assertEqual(
                    imagen_seleccionada.estado_proceso,
                    Imagen.EstadoProceso.ENVIADA,
                )
                self.assertIsNotNone(imagen_seleccionada.fecha_envio)

                primera_fecha = imagen_seleccionada.fecha_envio

                # Segunda ejecución: no duplica el registro.
                cantidad = registrar_imagenes_enviadas(
                    "PROCESO-ENVIO-IDEMPOTENTE"
                )

                self.assertEqual(cantidad, 0)

                imagen_seleccionada.refresh_from_db()
                imagen_no_seleccionada.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    imagen_seleccionada.fecha_envio,
                    primera_fecha,
                )

                self.assertEqual(
                    imagen_no_seleccionada.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )
                self.assertIsNone(
                    imagen_no_seleccionada.fecha_envio
                )

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_enviar_imagenes_por_email(self):
        import tempfile

        from django.core.files.base import ContentFile
        from django.core import mail
        from django.test import override_settings

        from web.services.entrega import enviar_imagenes_por_email

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Entrega Email",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-EMAIL-001",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-EMAIL-001",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente@example.com",
                )

                fondo1 = Fondo.objects.create(
                    codigo_fondo="FONDO-EMAIL-001",
                )

                fondo2 = Fondo.objects.create(
                    codigo_fondo="FONDO-EMAIL-002",
                )

                imagen1 = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo1,
                    codigo_imagen="EMAIL-IMAGEN-001",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen2 = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo2,
                    codigo_imagen="EMAIL-IMAGEN-002",
                    seleccionada=False,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen1.imagen_procesada.save(
                    "seleccionada.jpg",
                    ContentFile(b"fotografia seleccionada"),
                    save=True,
                )

                imagen2.imagen_procesada.save(
                    "no_seleccionada.jpg",
                    ContentFile(b"fotografia no seleccionada"),
                    save=True,
                )

                cantidad = enviar_imagenes_por_email(
                    "PROCESO-EMAIL-001",
                    clave_solicitud="SOLICITUD-EMAIL-001",
                )

                self.assertEqual(cantidad, 1)
                self.assertEqual(len(mail.outbox), 1)

                mensaje = mail.outbox[0]

                self.assertEqual(
                    mensaje.to,
                    ["cliente@example.com"],
                )

                self.assertEqual(len(mensaje.attachments), 1)

                self.assertEqual(
                    mensaje.attachments[0][0],
                    "seleccionada.jpg",
                )

                imagen1.refresh_from_db()
                imagen2.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    imagen1.estado_proceso,
                    Imagen.EstadoProceso.ENVIADA,
                )
                self.assertIsNotNone(imagen1.fecha_envio)

                self.assertEqual(
                    imagen2.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )
                self.assertIsNone(imagen2.fecha_envio)

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

                # Repetir la misma solicitud no debe duplicar el correo.
                cantidad = enviar_imagenes_por_email(
                    "PROCESO-EMAIL-001",
                    clave_solicitud="SOLICITUD-EMAIL-001",
                )

                self.assertEqual(cantidad, 0)
                self.assertEqual(len(mail.outbox), 1)

    def test_enviar_imagenes_por_email_fallo_no_registra_envio(self):
        import tempfile
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.services.entrega import enviar_imagenes_por_email
        from web.models import IntentoEntrega

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Email Fallido",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-EMAIL-FALLO",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-EMAIL-FALLO",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-EMAIL-FALLO",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="EMAIL-IMAGEN-FALLO",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "imagen_fallo.jpg",
                    ContentFile(b"fotografia de prueba"),
                    save=True,
                )

                # Simulamos que el servidor de correo falla.
                with patch(
                    "web.services.entrega.EmailMessage.send",
                    side_effect=ConnectionError("Servidor SMTP no disponible"),
                ):
                    with self.assertRaises(ConnectionError):
                        enviar_imagenes_por_email(
                            "PROCESO-EMAIL-FALLO",
                            clave_solicitud="SOLICITUD-EMAIL-FALLO-001",
                        )

                imagen.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )
                self.assertIsNone(imagen.fecha_envio)

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

                intento = IntentoEntrega.objects.get(
                    codigo_proceso="PROCESO-EMAIL-FALLO",
                    clave_idempotencia="EMAIL-SOLICITUD-EMAIL-FALLO-001",
                )

                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ERROR,
                )

                self.assertIn(
                    "Servidor SMTP no disponible",
                    intento.mensaje_error,
                )

                # El archivo sigue disponible para reintentar.
                self.assertTrue(
                    imagen.imagen_procesada.storage.exists(
                        imagen.imagen_procesada.name
                    )
                )

    def test_enviar_imagenes_por_email_sin_destinatario(self):
        import tempfile
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.services.entrega import enviar_imagenes_por_email

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Sin Email",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-EMAIL-SIN-DESTINO",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-EMAIL-SIN-DESTINO",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-EMAIL-SIN-DESTINO",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="EMAIL-IMAGEN-SIN-DESTINO",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "imagen_sin_destino.jpg",
                    ContentFile(b"fotografia de prueba"),
                    save=True,
                )

                with patch(
                    "web.services.entrega.EmailMessage.send"
                ) as enviar:
                    with self.assertRaisesMessage(
                        ValueError,
                        "La operación no tiene correo electrónico de destino.",
                    ):
                        enviar_imagenes_por_email(
                            "PROCESO-EMAIL-SIN-DESTINO",
                            clave_solicitud="SOLICITUD-EMAIL-SIN-DESTINO-001",
                        )
                    enviar.assert_not_called()

                imagen.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )
                self.assertIsNone(imagen.fecha_envio)
                self.assertEqual(pago.creditos_usados, 1)

    def test_enviar_imagenes_por_email_destinatarios_diferentes(self):
        import tempfile
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.services.entrega import enviar_imagenes_por_email

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Email Diferente",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-EMAIL-DIFERENTE",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("200.00"),
                    paquetes_pagados=2,
                    creditos_usados=2,
                    precio_unitario=Decimal("100.00"),
                )

                paquete1 = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-EMAIL-DIFERENTE",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente1@example.com",
                )

                paquete2 = PaqueteImagen.objects.create(
                    numero_paquete=2,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-EMAIL-DIFERENTE",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente2@example.com",
                )

                fondo1 = Fondo.objects.create(
                    codigo_fondo="FONDO-EMAIL-DIF-001",
                )

                fondo2 = Fondo.objects.create(
                    codigo_fondo="FONDO-EMAIL-DIF-002",
                )

                for numero, paquete, fondo in (
                    (1, paquete1, fondo1),
                    (2, paquete2, fondo2),
                ):
                    imagen = Imagen.objects.create(
                        paquete=paquete,
                        fondo=fondo,
                        codigo_imagen=f"EMAIL-DIF-IMAGEN-{numero}",
                        seleccionada=True,
                        estado_proceso=Imagen.EstadoProceso.PROCESADA,
                    )

                    imagen.imagen_procesada.save(
                        f"imagen_{numero}.jpg",
                        ContentFile(b"fotografia procesada"),
                        save=True,
                    )

                with patch(
                    "web.services.entrega.EmailMessage.send"
                ) as enviar:
                    with self.assertRaisesMessage(
                        ValueError,
                        "La operación tiene diferentes correos de destino.",
                    ):
                        enviar_imagenes_por_email(
                            "PROCESO-EMAIL-DIFERENTE",
                            clave_solicitud="SOLICITUD-EMAIL-DIFERENTE-001",
                        )
                    enviar.assert_not_called()

                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 2)
                self.assertEqual(pago.creditos_reservados, 0)

                self.assertEqual(
                    Imagen.objects.filter(
                        paquete__codigo_proceso="PROCESO-EMAIL-DIFERENTE",
                        estado_proceso=Imagen.EstadoProceso.ENVIADA,
                    ).count(),
                    0,
                )

    def test_crear_intento_entrega_es_idempotente(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import crear_intento_entrega

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Intentos",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-INTENTO-001",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso="PROCESO-INTENTO-001",
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        # Primera llamada: crea el intento.
        intento1 = crear_intento_entrega(
            codigo_proceso="PROCESO-INTENTO-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="ENTREGA-INTENTO-001",
        )

        self.assertEqual(
            intento1.estado,
            IntentoEntrega.Estado.PENDIENTE,
        )

        # Segunda llamada: recupera el mismo registro.
        intento2 = crear_intento_entrega(
            codigo_proceso="PROCESO-INTENTO-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="ENTREGA-INTENTO-001",
        )

        self.assertEqual(intento1.pk, intento2.pk)

        self.assertEqual(
            IntentoEntrega.objects.filter(
                clave_idempotencia="ENTREGA-INTENTO-001"
            ).count(),
            1,
        )

        # La clave no puede reutilizarse con otro destinatario.
        with self.assertRaisesMessage(
            ValueError,
            "La clave de idempotencia pertenece a otra entrega.",
        ):
            crear_intento_entrega(
                codigo_proceso="PROCESO-INTENTO-001",
                canal=IntentoEntrega.Canal.EMAIL,
                destinatario="otro@example.com",
                clave_idempotencia="ENTREGA-INTENTO-001",
            )

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_iniciar_intento_entrega_es_idempotente(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_intento_entrega,
            iniciar_intento_entrega,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Intento Enviando",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-INICIAR-INTENTO",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso="PROCESO-INICIAR-INTENTO",
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        intento = crear_intento_entrega(
            codigo_proceso="PROCESO-INICIAR-INTENTO",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="CLAVE-INICIAR-INTENTO",
        )

        # Primera llamada: reclama el intento.
        resultado1 = iniciar_intento_entrega(
            "CLAVE-INICIAR-INTENTO"
        )

        self.assertTrue(resultado1)

        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        # Segunda llamada: no puede reclamarlo nuevamente.
        resultado2 = iniciar_intento_entrega(
            "CLAVE-INICIAR-INTENTO"
        )

        self.assertFalse(resultado2)

        # No debe crear nuevos intentos.
        self.assertEqual(
            IntentoEntrega.objects.filter(
                clave_idempotencia="CLAVE-INICIAR-INTENTO"
            ).count(),
            1,
        )

        # Una clave inexistente debe generar error.
        with self.assertRaisesMessage(
            ValueError,
            "El intento de entrega no existe.",
        ):
            iniciar_intento_entrega("CLAVE-INEXISTENTE")

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_confirmar_intento_entrega_es_idempotente(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_intento_entrega,
            iniciar_intento_entrega,
            confirmar_intento_entrega,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Confirmar Entrega",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-CONFIRMAR-ENTREGA",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso="PROCESO-CONFIRMAR-ENTREGA",
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        intento = crear_intento_entrega(
            codigo_proceso="PROCESO-CONFIRMAR-ENTREGA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="CLAVE-CONFIRMAR-ENTREGA",
        )

        self.assertTrue(
            iniciar_intento_entrega("CLAVE-CONFIRMAR-ENTREGA")
        )

        # Primera confirmación.
        resultado1 = confirmar_intento_entrega(
            "CLAVE-CONFIRMAR-ENTREGA"
        )

        self.assertTrue(resultado1)

        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIADO,
        )
        self.assertIsNotNone(intento.fecha_envio)

        fecha_original = intento.fecha_envio

        # Segunda confirmación: no modifica la fecha.
        resultado2 = confirmar_intento_entrega(
            "CLAVE-CONFIRMAR-ENTREGA"
        )

        self.assertFalse(resultado2)

        intento.refresh_from_db()

        self.assertEqual(intento.fecha_envio, fecha_original)

        # No permite confirmar un intento inexistente.
        with self.assertRaisesMessage(
            ValueError,
            "El intento de entrega no existe.",
        ):
            confirmar_intento_entrega("CLAVE-INEXISTENTE")

        # No permite confirmar un intento pendiente.
        crear_intento_entrega(
            codigo_proceso="PROCESO-CONFIRMAR-ENTREGA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="CLAVE-PENDIENTE-ENTREGA",
        )

        with self.assertRaisesMessage(
            ValueError,
            "El intento de entrega no está en estado ENVIANDO.",
        ):
            confirmar_intento_entrega(
                "CLAVE-PENDIENTE-ENTREGA"
            )

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_registrar_error_entrega_es_idempotente(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_intento_entrega,
            iniciar_intento_entrega,
            registrar_error_entrega,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Error Entrega",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-ERROR-ENTREGA",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso="PROCESO-ERROR-ENTREGA",
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        intento = crear_intento_entrega(
            codigo_proceso="PROCESO-ERROR-ENTREGA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="CLAVE-ERROR-ENTREGA",
        )

        self.assertTrue(
            iniciar_intento_entrega("CLAVE-ERROR-ENTREGA")
        )

        # Primer registro del error.
        resultado1 = registrar_error_entrega(
            "CLAVE-ERROR-ENTREGA",
            "Servidor SMTP no disponible",
        )

        self.assertTrue(resultado1)

        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ERROR,
        )
        self.assertEqual(
            intento.mensaje_error,
            "Servidor SMTP no disponible",
        )
        self.assertIsNone(intento.fecha_envio)

        # Segunda llamada: conserva el error original.
        resultado2 = registrar_error_entrega(
            "CLAVE-ERROR-ENTREGA",
            "Otro error diferente",
        )

        self.assertFalse(resultado2)

        intento.refresh_from_db()

        self.assertEqual(
            intento.mensaje_error,
            "Servidor SMTP no disponible",
        )

        # No permite registrar error en un intento inexistente.
        with self.assertRaisesMessage(
            ValueError,
            "El intento de entrega no existe.",
        ):
            registrar_error_entrega(
                "CLAVE-INEXISTENTE",
                "Error de prueba",
            )

        # No permite registrar error en un intento pendiente.
        crear_intento_entrega(
            codigo_proceso="PROCESO-ERROR-ENTREGA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="CLAVE-ERROR-PENDIENTE",
        )

        with self.assertRaisesMessage(
            ValueError,
            "El intento de entrega no está en estado ENVIANDO.",
        ):
            registrar_error_entrega(
                "CLAVE-ERROR-PENDIENTE",
                "Error de prueba",
            )

        # No permite un mensaje de error vacío.
        with self.assertRaisesMessage(
            ValueError,
            "El mensaje de error es obligatorio.",
        ):
            registrar_error_entrega(
                "CLAVE-ERROR-ENTREGA",
                "   ",
            )

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_enviar_email_registra_intento_exitoso(self):
        import tempfile

        from django.core import mail
        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.models import IntentoEntrega
        from web.services.entrega import enviar_imagenes_por_email

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND=(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Historial Email",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-HISTORIAL-EMAIL",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-HISTORIAL-EMAIL",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=(
                        PaqueteImagen.EstadoCredito.CONSUMIDO
                    ),
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-HISTORIAL-EMAIL",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-HISTORIAL-EMAIL",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                # Primer envío.
                resultado1 = enviar_imagenes_por_email(
                    "PROCESO-HISTORIAL-EMAIL",
                    clave_solicitud="SOLICITUD-HISTORIAL-EMAIL-001",
                )
                self.assertEqual(resultado1, 1)
                self.assertEqual(len(mail.outbox), 1)

                intento = IntentoEntrega.objects.get(
                    clave_idempotencia="EMAIL-SOLICITUD-HISTORIAL-EMAIL-001"
                )                    

                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ENVIADO,
                )
                self.assertEqual(
                    intento.canal,
                    IntentoEntrega.Canal.EMAIL,
                )
                self.assertEqual(
                    intento.destinatario,
                    "cliente@example.com",
                )
                self.assertIsNotNone(intento.fecha_envio)
                self.assertEqual(intento.mensaje_error, "")

                fecha_original = intento.fecha_envio

                imagen.refresh_from_db()

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.ENVIADA,
                )
                self.assertIsNotNone(imagen.fecha_envio)

                # Misma solicitud: no debe duplicar el envío.
                resultado2 = enviar_imagenes_por_email(
                    "PROCESO-HISTORIAL-EMAIL",
                    clave_solicitud="SOLICITUD-HISTORIAL-EMAIL-001",
                )
                self.assertEqual(resultado2, 0)
                self.assertEqual(len(mail.outbox), 1)

                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso="PROCESO-HISTORIAL-EMAIL",
                        canal=IntentoEntrega.Canal.EMAIL,
                    ).count(),
                    1,
                )

                intento.refresh_from_db()

                self.assertEqual(
                    intento.fecha_envio,
                    fecha_original,
                )

                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_enviar_email_registra_intento_fallido(self):
        import tempfile
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.models import IntentoEntrega
        from web.services.entrega import enviar_imagenes_por_email

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Fallo SMTP",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-FALLO-SMTP",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-FALLO-SMTP",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=(
                        PaqueteImagen.EstadoCredito.CONSUMIDO
                    ),
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-FALLO-SMTP",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-FALLO-SMTP",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                clave_solicitud = "SOLICITUD-FALLO-SMTP-001"
                clave = f"EMAIL-{clave_solicitud}"

                # Simular un fallo del servidor SMTP.
                with patch(
                    "web.services.entrega.EmailMessage.send",
                    side_effect=ConnectionError(
                        "Servidor SMTP no disponible"
                    ),
                ) as enviar:
                    with self.assertRaises(ConnectionError):
                        enviar_imagenes_por_email(
                            "PROCESO-FALLO-SMTP",
                            clave_solicitud=clave_solicitud,
                        )
                    enviar.assert_called_once()

                intento = IntentoEntrega.objects.get(
                    clave_idempotencia=clave
                )

                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ERROR,
                )
                self.assertEqual(
                    intento.mensaje_error,
                    "Servidor SMTP no disponible",
                )
                self.assertIsNone(intento.fecha_envio)

                imagen.refresh_from_db()

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )
                self.assertIsNone(imagen.fecha_envio)

                # No debe reenviar automáticamente.
                with patch(
                    "web.services.entrega.EmailMessage.send"
                ) as enviar:
                    resultado = enviar_imagenes_por_email(
                        "PROCESO-FALLO-SMTP",
                        clave_solicitud=clave_solicitud,
                    )
                    self.assertEqual(resultado, 0)
                    enviar.assert_not_called()

                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        clave_idempotencia=clave
                    ).count(),
                    1,
                )

                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_evaluar_reintento_entrega(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_intento_entrega,
            iniciar_intento_entrega,
            registrar_error_entrega,
            confirmar_intento_entrega,
            evaluar_reintento_entrega,
        )

        codigo_proceso = "PROCESO-EVALUAR-REINTENTO"

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Evaluar Reintento",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-EVALUAR-REINTENTO",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso=codigo_proceso,
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        canal = IntentoEntrega.Canal.EMAIL
        destinatario = "cliente@example.com"

        # 1. Sin intentos registrados.
        self.assertEqual(
            evaluar_reintento_entrega(codigo_proceso, canal),
            "SIN_INTENTOS",
        )

        # 2. Intento pendiente.
        crear_intento_entrega(
            codigo_proceso=codigo_proceso,
            canal=canal,
            destinatario=destinatario,
            clave_idempotencia="REINTENTO-EVALUAR-001",
        )

        self.assertEqual(
            evaluar_reintento_entrega(codigo_proceso, canal),
            "EN_PROCESO",
        )

        # También debe permanecer EN_PROCESO al iniciar.
        self.assertTrue(
            iniciar_intento_entrega("REINTENTO-EVALUAR-001")
        )

        self.assertEqual(
            evaluar_reintento_entrega(codigo_proceso, canal),
            "EN_PROCESO",
        )

        # 3. Fallo de comunicación: requiere revisión manual.
        self.assertTrue(
            registrar_error_entrega(
                "REINTENTO-EVALUAR-001",
                "Servidor SMTP no disponible",
            )
        )

        self.assertEqual(
            evaluar_reintento_entrega(codigo_proceso, canal),
            "REVISION_MANUAL",
        )

        # 4. Simular un segundo intento autorizado.
        crear_intento_entrega(
            codigo_proceso=codigo_proceso,
            canal=canal,
            destinatario=destinatario,
            clave_idempotencia="REINTENTO-EVALUAR-002",
        )

        self.assertTrue(
            iniciar_intento_entrega("REINTENTO-EVALUAR-002")
        )

        self.assertTrue(
            confirmar_intento_entrega("REINTENTO-EVALUAR-002")
        )

        self.assertEqual(
            evaluar_reintento_entrega(codigo_proceso, canal),
            "ENVIADO",
        )

        # El historial conserva ambos intentos.
        self.assertEqual(
            IntentoEntrega.objects.filter(
                codigo_proceso=codigo_proceso,
                canal=canal,
            ).count(),
            2,
        )

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_autorizar_reintento_entrega(self):
        from django.contrib.auth import get_user_model

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_intento_entrega,
            iniciar_intento_entrega,
            registrar_error_entrega,
            autorizar_reintento_entrega,
        )

        codigo_proceso = "PROCESO-AUTORIZAR-REINTENTO"

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Autorizar Reintento",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-AUTORIZAR-REINTENTO",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso=codigo_proceso,
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        usuario = get_user_model().objects.create_user(
            username="admin_reintentos",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        # Crear un intento inicial que termina en ERROR.
        intento_original = crear_intento_entrega(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REINTENTO-ORIGINAL",
        )

        self.assertTrue(
            iniciar_intento_entrega("EMAIL-REINTENTO-ORIGINAL")
        )

        self.assertTrue(
            registrar_error_entrega(
                "EMAIL-REINTENTO-ORIGINAL",
                "Servidor SMTP no disponible",
            )
        )

        # Autorizar manualmente un nuevo intento.
        nuevo_intento = autorizar_reintento_entrega(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            usuario=usuario,
            motivo="Se verificó el fallo y se autorizó reenviar.",
        )

        self.assertNotEqual(
            intento_original.pk,
            nuevo_intento.pk,
        )

        self.assertEqual(
            nuevo_intento.estado,
            IntentoEntrega.Estado.PENDIENTE,
        )

        self.assertEqual(
            nuevo_intento.autorizado_por,
            usuario,
        )

        self.assertIsNotNone(
            nuevo_intento.fecha_autorizacion,
        )

        self.assertEqual(
            nuevo_intento.motivo_autorizacion,
            "Se verificó el fallo y se autorizó reenviar.",
        )

        self.assertNotEqual(
            nuevo_intento.clave_idempotencia,
            intento_original.clave_idempotencia,
        )

        # El intento original debe conservarse sin cambios.
        intento_original.refresh_from_db()

        self.assertEqual(
            intento_original.estado,
            IntentoEntrega.Estado.ERROR,
        )

        self.assertEqual(
            intento_original.mensaje_error,
            "Servidor SMTP no disponible",
        )

        # El historial debe contener ambos intentos.
        self.assertEqual(
            IntentoEntrega.objects.filter(
                codigo_proceso=codigo_proceso,
                canal=IntentoEntrega.Canal.EMAIL,
            ).count(),
            2,
        )

        # No permitir otra autorización mientras exista
        # un intento PENDIENTE.
        with self.assertRaisesMessage(
            ValueError,
            "La operación ya tiene un intento en proceso.",
        ):
            autorizar_reintento_entrega(
                codigo_proceso=codigo_proceso,
                canal=IntentoEntrega.Canal.EMAIL,
                destinatario="cliente@example.com",
                usuario=usuario,
                motivo="Segunda autorización",
            )

        # No deben modificarse los créditos.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_enviar_email_reintento_despues_de_fallo(self):        
        import tempfile
        from unittest.mock import patch

        from django.core import mail
        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.models import IntentoEntrega
        from web.services.entrega import enviar_imagenes_por_email
    
        codigo_proceso = "PROCESO-EMAIL-REINTENTO"

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND=(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Reintento Email",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-EMAIL-REINTENTO",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso=codigo_proceso,
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=(
                        PaqueteImagen.EstadoCredito.CONSUMIDO
                    ),
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-EMAIL-REINTENTO",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-EMAIL-REINTENTO",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                # Primer envío: simular un fallo SMTP.
                with patch(
                    "web.services.entrega.EmailMessage.send",
                    side_effect=ConnectionError(
                        "Servidor SMTP no disponible"
                    ),
                ):
                    with self.assertRaises(ConnectionError):
                        enviar_imagenes_por_email(
                            codigo_proceso,
                            clave_solicitud="REINTENTO-FALLO-001",
                        )
                intento_original = IntentoEntrega.objects.get(
                    clave_idempotencia="EMAIL-REINTENTO-FALLO-001"
                )

                self.assertEqual(
                    intento_original.estado,
                    IntentoEntrega.Estado.ERROR,
                )

                # Nueva solicitud después del fallo SMTP.
                resultado = enviar_imagenes_por_email(
                    codigo_proceso,
                    clave_solicitud="REINTENTO-FALLO-002",
                )

                self.assertEqual(resultado, 1)
                self.assertEqual(len(mail.outbox), 1)

                reintento = IntentoEntrega.objects.get(
                    clave_idempotencia="EMAIL-REINTENTO-FALLO-002"
                )

                self.assertEqual(
                    reintento.estado,
                    IntentoEntrega.Estado.ENVIADO,
                )
                self.assertIsNotNone(reintento.fecha_envio)

                # El intento original conserva su error.
                intento_original.refresh_from_db()

                self.assertEqual(
                    intento_original.estado,
                    IntentoEntrega.Estado.ERROR,
                )
                self.assertEqual(
                    intento_original.mensaje_error,
                    "Servidor SMTP no disponible",
                )

                # Deben existir exactamente dos intentos.
                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso=codigo_proceso,
                        canal=IntentoEntrega.Canal.EMAIL,
                    ).count(),
                    2,
                )

                imagen.refresh_from_db()

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.ENVIADA,
                )
                self.assertIsNotNone(imagen.fecha_envio)

                # Una nueva llamada no debe duplicar el correo.
                self.assertEqual(
                    enviar_imagenes_por_email(
                        codigo_proceso,
                        clave_solicitud="REINTENTO-FALLO-002",
                    ),
                    0,
                )

                self.assertEqual(len(mail.outbox), 1)

                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_email_no_reenvia_si_reintento_esta_enviando(self):
        import tempfile
        from unittest.mock import patch

        from django.contrib.auth import get_user_model
        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.models import IntentoEntrega
        from web.services.entrega import enviar_imagenes_por_email
        from web.services.intentos_entrega import (
            crear_intento_entrega,
            iniciar_intento_entrega,
            registrar_error_entrega,
            autorizar_reintento_entrega,
        )

        codigo_proceso = "PROCESO-REINTENTO-ENVIANDO"

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Reintento Enviando",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-REINTENTO-ENVIANDO",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso=codigo_proceso,
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=(
                        PaqueteImagen.EstadoCredito.CONSUMIDO
                    ),
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-REINTENTO-ENVIANDO",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-REINTENTO-ENVIANDO",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                usuario = get_user_model().objects.create_user(
                    username="admin_reintento_enviando",
                    password="ClavePrueba123!",
                    is_staff=True,
                    is_active=True,
                )

                # Primer intento: simular un fallo.
                crear_intento_entrega(
                    codigo_proceso=codigo_proceso,
                    canal=IntentoEntrega.Canal.EMAIL,
                    destinatario="cliente@example.com",
                    clave_idempotencia=f"EMAIL-{codigo_proceso}",
                )

                self.assertTrue(
                    iniciar_intento_entrega(
                        f"EMAIL-{codigo_proceso}"
                    )
                )

                self.assertTrue(
                    registrar_error_entrega(
                        f"EMAIL-{codigo_proceso}",
                        "Fallo SMTP inicial",
                    )
                )

                # Autorizar el reintento.
                reintento = autorizar_reintento_entrega(
                    codigo_proceso=codigo_proceso,
                    canal=IntentoEntrega.Canal.EMAIL,
                    destinatario="cliente@example.com",
                    usuario=usuario,
                    motivo="Fallo revisado por administración.",
                )

                clave_solicitud = reintento.clave_idempotencia.removeprefix("EMAIL-")

                # Simular que otro proceso ya inició el reintento.
                self.assertTrue(
                    iniciar_intento_entrega(
                        reintento.clave_idempotencia
                    )
                )

                # Una nueva solicitud no debe enviar otro correo.
                with patch(
                    "web.services.entrega.EmailMessage.send"
                ) as enviar:
                    resultado = enviar_imagenes_por_email(
                        codigo_proceso,
                        clave_solicitud=clave_solicitud,
                    )
                    self.assertEqual(resultado, 0)
                    enviar.assert_not_called()

                reintento.refresh_from_db()
                imagen.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    reintento.estado,
                    IntentoEntrega.Estado.ENVIANDO,
                )

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )

                self.assertIsNone(imagen.fecha_envio)

                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso=codigo_proceso,
                        canal=IntentoEntrega.Canal.EMAIL,
                    ).count(),
                    2,
                )

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_no_autorizar_reintento_si_existe_envio_activo(self):
        from django.contrib.auth import get_user_model

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_intento_entrega,
            iniciar_intento_entrega,
            registrar_error_entrega,
            autorizar_reintento_entrega,
        )

        codigo_proceso = "PROCESO-REINTENTO-ACTIVO"

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Reintento Activo",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-REINTENTO-ACTIVO",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso=codigo_proceso,
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
        )

        usuario = get_user_model().objects.create_user(
            username="admin_reintento_activo",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        # Primer intento: ERROR.
        crear_intento_entrega(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REINTENTO-ACTIVO-ORIGINAL",
        )

        self.assertTrue(
            iniciar_intento_entrega(
                "EMAIL-REINTENTO-ACTIVO-ORIGINAL"
            )
        )

        self.assertTrue(
            registrar_error_entrega(
                "EMAIL-REINTENTO-ACTIVO-ORIGINAL",
                "Fallo SMTP inicial",
            )
        )

        # Primer reintento autorizado.
        reintento = autorizar_reintento_entrega(
            codigo_proceso=codigo_proceso,
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            usuario=usuario,
            motivo="Fallo revisado.",
        )

        self.assertTrue(
            iniciar_intento_entrega(
                reintento.clave_idempotencia
            )
        )

        # No debe autorizar otro mientras el anterior
        # permanezca ENVIANDO.
        with self.assertRaisesMessage(
            ValueError,
            "La operación ya tiene un intento en proceso.",
        ):
            autorizar_reintento_entrega(
                codigo_proceso=codigo_proceso,
                canal=IntentoEntrega.Canal.EMAIL,
                destinatario="cliente@example.com",
                usuario=usuario,
                motivo="Segundo reintento no permitido.",
            )

        self.assertEqual(
            IntentoEntrega.objects.filter(
                codigo_proceso=codigo_proceso,
                canal=IntentoEntrega.Canal.EMAIL,
            ).count(),
            2,
        )

        reintento.refresh_from_db()
        pago.refresh_from_db()

        self.assertEqual(
            reintento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )
        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_reclamar_intento_email_es_idempotente(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import reclamar_intento_email

        codigo_proceso = "PROCESO-RECLAMAR-EMAIL"

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Prueba",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-RECLAMAR-EMAIL",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            creditos_reservados=0,
            precio_unitario=Decimal("100.00"),
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso=codigo_proceso,
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
            email_destino="cliente@example.com",
        )

        # Primera llamada: debe reclamar el intento.
        primer_intento = reclamar_intento_email(
            codigo_proceso=codigo_proceso,
            destinatario="cliente@example.com",
        )

        self.assertIsNotNone(primer_intento)

        self.assertEqual(
            primer_intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            primer_intento.clave_idempotencia,
            f"EMAIL-{codigo_proceso}",
        )

        # Segunda llamada: no debe reclamarlo nuevamente.
        segundo_intento = reclamar_intento_email(
            codigo_proceso=codigo_proceso,
            destinatario="cliente@example.com",
        )

        self.assertIsNone(segundo_intento)

        # Debe existir exactamente un intento.
        self.assertEqual(
            IntentoEntrega.objects.filter(
                codigo_proceso=codigo_proceso,
                canal=IntentoEntrega.Canal.EMAIL,
            ).count(),
            1,
        )

        primer_intento.refresh_from_db()

        self.assertEqual(
            primer_intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        # La reclamación no debe consumir créditos.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_email_no_duplica_solicitud_fallida(self):
        import tempfile
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.models import IntentoEntrega
        from web.services.entrega import enviar_imagenes_por_email

        codigo_proceso = "PROCESO-ERROR-SIN-AUTORIZACION"
        clave_solicitud = "SOLICITUD-ERROR-SMTP-001"

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Error Email",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-ERROR-SIN-AUTORIZACION",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso=codigo_proceso,
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-ERROR-SIN-AUTORIZACION",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-ERROR-SIN-AUTORIZACION",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                # Primer envío: falla el servidor SMTP.
                with patch(
                    "web.services.entrega.EmailMessage.send",
                    side_effect=ConnectionError("Fallo SMTP"),
                ):
                    with self.assertRaises(ConnectionError):
                        enviar_imagenes_por_email(
                            codigo_proceso,
                            clave_solicitud=clave_solicitud,
                        )

                intento = IntentoEntrega.objects.get(
                    clave_idempotencia=f"EMAIL-{clave_solicitud}"
                )
                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ERROR,
                )

                # Repetir la misma solicitud fallida
                # no debe generar otro envío.
                with patch(
                    "web.services.entrega.EmailMessage.send"
                ) as enviar:
                    resultado = enviar_imagenes_por_email(
                        codigo_proceso,
                        clave_solicitud=clave_solicitud,
                    )

                    self.assertEqual(resultado, 0)
                    enviar.assert_not_called()

                # El historial debe permanecer intacto.
                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso=codigo_proceso,
                        canal=IntentoEntrega.Canal.EMAIL,
                    ).count(),
                    1,
                )

                intento.refresh_from_db()
                imagen.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ERROR,
                )

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )

                self.assertIsNone(imagen.fecha_envio)

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_email_no_duplica_si_falla_confirmacion(self):
        import tempfile
        from unittest.mock import patch

        from django.core import mail
        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.models import IntentoEntrega
        from web.services.entrega import enviar_imagenes_por_email

        codigo_proceso = "PROCESO-FALLO-CONFIRMACION"
        clave_solicitud = "SOLICITUD-FALLO-CONFIRMACION-001"

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND=(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Confirmacion",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-FALLO-CONFIRMACION",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso=codigo_proceso,
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-FALLO-CONFIRMACION",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-FALLO-CONFIRMACION",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                # SMTP acepta el mensaje, pero falla
                # la confirmación posterior en la BD.
                with patch(
                    "web.services.entrega.confirmar_intento_entrega",
                    side_effect=RuntimeError(
                        "Error al confirmar en base de datos"
                    ),
                ):
                    with self.assertRaisesMessage(
                        RuntimeError,
                        "Error al confirmar en base de datos",
                    ):
                        enviar_imagenes_por_email(
                            codigo_proceso,
                            clave_solicitud=clave_solicitud,
                        )

                # El correo sí salió del backend simulado.
                self.assertEqual(len(mail.outbox), 1)

                intento = IntentoEntrega.objects.get(
                    clave_idempotencia=f"EMAIL-{clave_solicitud}"
                )

                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ENVIANDO,
                )
                # Repetir la misma solicitud no debe duplicar el correo.
                resultado = enviar_imagenes_por_email(
                    codigo_proceso,
                    clave_solicitud=clave_solicitud,
                )

                self.assertEqual(resultado, 0)
                self.assertEqual(len(mail.outbox), 1)

                intento.refresh_from_db()
                imagen.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ENVIANDO,
                )

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.PROCESADA,
                )

                self.assertIsNone(imagen.fecha_envio)

                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso=codigo_proceso,
                        canal=IntentoEntrega.Canal.EMAIL,
                    ).count(),
                    1,
                )

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_detectar_intentos_entrega_pendientes_revision(self):
        from datetime import timedelta

        from django.utils import timezone

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            obtener_intentos_entrega_pendientes_revision,
        )

        ahora = timezone.now()

        intento_antiguo = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente1@example.com",
            clave_idempotencia="EMAIL-REVISION-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(minutes=30),
            fecha_inicio_envio=ahora - timedelta(minutes=30),
        )

        intento_reciente = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-002",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente2@example.com",
            clave_idempotencia="EMAIL-REVISION-002",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(minutes=5),
            fecha_inicio_envio=ahora - timedelta(minutes=5),
        )

        intento_error = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-003",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente3@example.com",
            clave_idempotencia="EMAIL-REVISION-003",
            estado=IntentoEntrega.Estado.ERROR,
            fecha_creacion=ahora - timedelta(minutes=40),
            fecha_inicio_envio=ahora - timedelta(minutes=40),
        )

        pendientes = list(
            obtener_intentos_entrega_pendientes_revision(
                minutos=15
            )
        )

        self.assertEqual(
            [intento.pk for intento in pendientes],
            [intento_antiguo.pk],
        )

        # La consulta no debe modificar registros.
        intento_antiguo.refresh_from_db()
        intento_reciente.refresh_from_db()
        intento_error.refresh_from_db()

        self.assertEqual(
            intento_antiguo.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            intento_reciente.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            intento_error.estado,
            IntentoEntrega.Estado.ERROR,
        )

    def test_fecha_inicio_envio_se_registra_una_sola_vez(self):
        from datetime import timedelta

        from django.utils import timezone

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import iniciar_intento_entrega

        ahora = timezone.now()

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-FECHA-INICIO",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-FECHA-INICIO",
            estado=IntentoEntrega.Estado.PENDIENTE,
            fecha_creacion=ahora - timedelta(minutes=30),
        )

        # Antes de iniciar el envío no debe existir fecha.
        self.assertIsNone(intento.fecha_inicio_envio)

        # Primera llamada: reclamar el intento.
        resultado = iniciar_intento_entrega(
            intento.clave_idempotencia
        )

        self.assertTrue(resultado)

        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNotNone(intento.fecha_inicio_envio)

        fecha_original = intento.fecha_inicio_envio

        # La fecha de inicio debe ser posterior
        # a la fecha de creación.
        self.assertGreater(
            fecha_original,
            intento.fecha_creacion,
        )

        # Segunda llamada: no debe reclamar nuevamente.
        resultado = iniciar_intento_entrega(
            intento.clave_idempotencia
        )

        self.assertFalse(resultado)

        intento.refresh_from_db()

        # El estado y la fecha deben permanecer iguales.
        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            intento.fecha_inicio_envio,
            fecha_original,
        )

    def test_detectar_intentos_por_fecha_inicio_envio(self):
        from datetime import timedelta

        from django.utils import timezone

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            obtener_intentos_entrega_pendientes_revision,
        )

        ahora = timezone.now()

        # Creado hace mucho tiempo, pero iniciado recientemente.
        intento_reciente = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-INICIO-RECIENTE",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente1@example.com",
            clave_idempotencia="EMAIL-INICIO-RECIENTE",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=2),
            fecha_inicio_envio=ahora - timedelta(minutes=5),
        )

        # Iniciado hace 30 minutos: requiere revisión.
        intento_antiguo = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-INICIO-ANTIGUO",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente2@example.com",
            clave_idempotencia="EMAIL-INICIO-ANTIGUO",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=2),
            fecha_inicio_envio=ahora - timedelta(minutes=30),
        )

        # Registro histórico sin fecha de inicio.
        intento_sin_fecha = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-SIN-FECHA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente3@example.com",
            clave_idempotencia="EMAIL-SIN-FECHA",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=3),
            fecha_inicio_envio=None,
        )

        pendientes = list(
            obtener_intentos_entrega_pendientes_revision(
                minutos=15
            )
        )

        # Únicamente el envío iniciado hace 30 minutos.
        self.assertEqual(
            [intento.pk for intento in pendientes],
            [intento_antiguo.pk],
        )

        # La consulta no modifica los registros.
        intento_reciente.refresh_from_db()
        intento_antiguo.refresh_from_db()
        intento_sin_fecha.refresh_from_db()

        self.assertEqual(
            intento_reciente.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            intento_antiguo.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            intento_sin_fecha.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(
            intento_sin_fecha.fecha_inicio_envio
        )

    def test_detectar_intentos_entrega_sin_fecha_inicio(self):
        from datetime import timedelta

        from django.utils import timezone

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            obtener_intentos_entrega_sin_fecha_inicio,
        )

        ahora = timezone.now()

        # Registro histórico sin fecha de inicio.
        intento_historico = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-HISTORICO-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente1@example.com",
            clave_idempotencia="EMAIL-HISTORICO-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=2),
            fecha_inicio_envio=None,
        )

        # Envío activo con fecha registrada.
        intento_activo = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-HISTORICO-002",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente2@example.com",
            clave_idempotencia="EMAIL-HISTORICO-002",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=1),
            fecha_inicio_envio=ahora - timedelta(minutes=5),
        )

        # Intento fallido, no debe aparecer.
        intento_error = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-HISTORICO-003",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente3@example.com",
            clave_idempotencia="EMAIL-HISTORICO-003",
            estado=IntentoEntrega.Estado.ERROR,
            fecha_creacion=ahora - timedelta(hours=3),
            fecha_inicio_envio=None,
        )

        pendientes = list(
            obtener_intentos_entrega_sin_fecha_inicio()
        )

        self.assertEqual(
            [intento.pk for intento in pendientes],
            [intento_historico.pk],
        )

        # Verificar que no se modificaron los registros.
        intento_historico.refresh_from_db()
        intento_activo.refresh_from_db()
        intento_error.refresh_from_db()

        self.assertEqual(
            intento_historico.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(
            intento_historico.fecha_inicio_envio
        )

        self.assertEqual(
            intento_activo.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            intento_error.estado,
            IntentoEntrega.Estado.ERROR,
        )

    def test_unificar_intentos_revision_administrativa(self):
        from datetime import timedelta

        from django.utils import timezone

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            obtener_intentos_para_revision_administrativa,
        )

        ahora = timezone.now()

        # Envío detenido: inició hace 30 minutos.
        detenido = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REV-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente1@example.com",
            clave_idempotencia="EMAIL-REV-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=2),
            fecha_inicio_envio=ahora - timedelta(minutes=30),
        )

        # Registro histórico sin fecha de inicio.
        historico = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REV-002",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente2@example.com",
            clave_idempotencia="EMAIL-REV-002",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=3),
            fecha_inicio_envio=None,
        )

        # Envío reciente: no requiere revisión.
        reciente = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REV-003",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente3@example.com",
            clave_idempotencia="EMAIL-REV-003",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=1),
            fecha_inicio_envio=ahora - timedelta(minutes=5),
        )

        # Intento fallido: pertenece a otro flujo de revisión.
        fallido = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REV-004",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente4@example.com",
            clave_idempotencia="EMAIL-REV-004",
            estado=IntentoEntrega.Estado.ERROR,
            fecha_creacion=ahora - timedelta(hours=4),
            fecha_inicio_envio=ahora - timedelta(hours=3),
        )

        pendientes = list(
            obtener_intentos_para_revision_administrativa(
                minutos=15
            )
        )

        self.assertEqual(
            {intento.pk for intento in pendientes},
            {detenido.pk, historico.pk},
        )

        # Verificar que la consulta no modifica estados.
        detenido.refresh_from_db()
        historico.refresh_from_db()
        reciente.refresh_from_db()
        fallido.refresh_from_db()

        self.assertEqual(
            detenido.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            historico.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            reciente.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            fallido.estado,
            IntentoEntrega.Estado.ERROR,
        )

    def test_listar_entregas_pendientes_revision(self):
        from datetime import timedelta

        from django.utils import timezone

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            listar_entregas_pendientes_revision,
        )

        ahora = timezone.now()

        detenido = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-LISTA-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente1@example.com",
            clave_idempotencia="EMAIL-LISTA-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=2),
            fecha_inicio_envio=ahora - timedelta(minutes=30),
        )

        historico = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-LISTA-002",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente2@example.com",
            clave_idempotencia="EMAIL-LISTA-002",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=3),
            fecha_inicio_envio=None,
        )

        reciente = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-LISTA-003",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente3@example.com",
            clave_idempotencia="EMAIL-LISTA-003",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(minutes=5),
            fecha_inicio_envio=ahora - timedelta(minutes=5),
        )

        resultados = listar_entregas_pendientes_revision(
            minutos=15
        )

        self.assertEqual(len(resultados), 2)

        por_codigo = {
            registro["codigo_proceso"]: registro
            for registro in resultados
        }

        self.assertEqual(
            por_codigo[detenido.codigo_proceso]["motivo_revision"],
            "TIEMPO_EXCEDIDO",
        )

        self.assertEqual(
            por_codigo[historico.codigo_proceso]["motivo_revision"],
            "SIN_FECHA_INICIO",
        )

        self.assertNotIn(
            reciente.codigo_proceso,
            por_codigo,
        )

        self.assertEqual(
            por_codigo[detenido.codigo_proceso]["destinatario"],
            "cliente1@example.com",
        )

        self.assertEqual(
            por_codigo[historico.codigo_proceso]["estado"],
            IntentoEntrega.Estado.ENVIANDO,
        )

        # La consulta no debe modificar registros.
        detenido.refresh_from_db()
        historico.refresh_from_db()
        reciente.refresh_from_db()

        self.assertEqual(
            detenido.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            historico.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            reciente.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

    def test_vista_entregas_revision_rechaza_usuario_no_staff(self):
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory

        from web.views import entregas_pendientes_revision

        usuario = get_user_model().objects.create_user(
            username="usuario_no_staff",
            password="ClavePrueba123!",
            is_staff=False,
            is_active=True,
        )

        request = RequestFactory().get(
            "/entregas/pendientes-revision/"
        )

        request.user = usuario

        respuesta = entregas_pendientes_revision(request)

        # El decorador debe impedir el acceso y
        # redirigir al inicio de sesión administrativo.
        self.assertEqual(
            respuesta.status_code,
            302,
        )

        self.assertIn(
            "/admin/login/",
            respuesta["Location"],
        )

    def test_vista_entregas_revision_permite_staff(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        usuario = get_user_model().objects.create_user(
            username="admin_entregas_revision",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse("web:entregas_pendientes_revision")
        )

        self.assertEqual(respuesta.status_code, 200)

        self.assertTemplateUsed(
            respuesta,
            "web/entregas_pendientes_revision.html",
        )

        self.assertContains(
            respuesta,
            "IMAGE ENGINE",
        )

        self.assertContains(
            respuesta,
            "No existen entregas pendientes de revisión.",
        )

    def test_vista_entregas_revision_muestra_registros(self):
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_revision_registros",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        ahora = timezone.now()

        # Entrega detenida que requiere revisión.
        IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-PENDIENTE-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente1@example.com",
            clave_idempotencia="EMAIL-PENDIENTE-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=1),
            fecha_inicio_envio=ahora - timedelta(minutes=30),
        )

        # Entrega reciente que no requiere revisión.
        IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-RECIENTE-002",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente2@example.com",
            clave_idempotencia="EMAIL-RECIENTE-002",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(minutes=5),
            fecha_inicio_envio=ahora - timedelta(minutes=5),
        )

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse("web:entregas_pendientes_revision")
        )

        self.assertEqual(respuesta.status_code, 200)

        # Debe mostrar la entrega detenida.
        self.assertContains(
            respuesta,
            "PROCESO-PENDIENTE-001",
        )

        self.assertContains(
            respuesta,
            "cliente1@example.com",
        )

        self.assertContains(
            respuesta,
            "Tiempo excedido",
        )

        # No debe mostrar la entrega reciente.
        self.assertNotContains(
            respuesta,
            "PROCESO-RECIENTE-002",
        )

        self.assertNotContains(
            respuesta,
            "cliente2@example.com",
        )

    def test_vista_entregas_revision_muestra_sin_fecha_inicio(self):
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_revision_historicos",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        ahora = timezone.now()

        # Intento histórico sin fecha de inicio.
        historico = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-HISTORICO-VISTA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="historico@example.com",
            clave_idempotencia="EMAIL-HISTORICO-VISTA",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(hours=3),
            fecha_inicio_envio=None,
        )

        # Intento reciente que no requiere revisión.
        IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-RECIENTE-VISTA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="reciente@example.com",
            clave_idempotencia="EMAIL-RECIENTE-VISTA",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_creacion=ahora - timedelta(minutes=5),
            fecha_inicio_envio=ahora - timedelta(minutes=5),
        )

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse("web:entregas_pendientes_revision")
        )

        self.assertEqual(respuesta.status_code, 200)

        # Debe mostrar el registro histórico.
        self.assertContains(
            respuesta,
            "PROCESO-HISTORICO-VISTA",
        )

        self.assertContains(
            respuesta,
            "historico@example.com",
        )

        self.assertContains(
            respuesta,
            "Sin fecha de inicio",
        )

        self.assertContains(
            respuesta,
            "Sin registro",
        )

        # No debe mostrar el envío reciente.
        self.assertNotContains(
            respuesta,
            "PROCESO-RECIENTE-VISTA",
        )

        # La consulta no debe modificar el intento.
        historico.refresh_from_db()

        self.assertEqual(
            historico.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(
            historico.fecha_inicio_envio
        )

    def test_vista_entregas_revision_rechaza_anonimo(self):
        from django.urls import reverse

        url = reverse("web:entregas_pendientes_revision")

        # Solicitud HTTP sin iniciar sesión.
        respuesta = self.client.get(url)

        # Debe redirigir al login administrativo.
        self.assertEqual(
            respuesta.status_code,
            302,
        )

        self.assertIn(
            "/admin/login/",
            respuesta["Location"],
        )

        # La URL original debe conservarse para
        # regresar después de iniciar sesión.
        self.assertIn(
            "next=",
            respuesta["Location"],
        )

    def test_vista_entregas_revision_http_rechaza_no_staff(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        usuario = get_user_model().objects.create_user(
            username="usuario_revision_no_staff",
            password="ClavePrueba123!",
            is_staff=False,
            is_active=True,
        )

        self.client.force_login(usuario)

        url = reverse("web:entregas_pendientes_revision")

        respuesta = self.client.get(url)

        # Un usuario autenticado sin permisos staff
        # debe ser redirigido al login administrativo.
        self.assertEqual(
            respuesta.status_code,
            302,
        )

        self.assertIn(
            "/admin/login/",
            respuesta["Location"],
        )

        self.assertIn(
            "next=",
            respuesta["Location"],
        )

        # No debe entregar el contenido administrativo.
        self.assertNotIn(
            b"PROCESO-",
            respuesta.content,
        )

    def test_vista_entregas_revision_es_solo_lectura(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_revision_solo_lectura",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Solo Lectura",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-SOLO-LECTURA",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("500.00"),
            paquetes_pagados=5,
            creditos_usados=2,
            creditos_reservados=1,
            precio_unitario=Decimal("100.00"),
        )

        fecha_inicio = timezone.now() - timedelta(minutes=30)

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-SOLO-LECTURA",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-SOLO-LECTURA",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_inicio_envio=fecha_inicio,
        )

        fecha_creacion_original = intento.fecha_creacion
        total_intentos = IntentoEntrega.objects.count()

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse("web:entregas_pendientes_revision")
        )

        self.assertEqual(respuesta.status_code, 200)

        self.assertContains(
            respuesta,
            "PROCESO-SOLO-LECTURA",
        )

        # Consultar la pantalla no modifica el intento.
        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertEqual(
            intento.fecha_inicio_envio,
            fecha_inicio,
        )

        self.assertEqual(
            intento.fecha_creacion,
            fecha_creacion_original,
        )

        self.assertIsNone(intento.fecha_envio)

        # No deben crearse ni eliminarse intentos.
        self.assertEqual(
            IntentoEntrega.objects.count(),
            total_intentos,
        )

        # Los créditos del sector permanecen intactos.
        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 2)
        self.assertEqual(pago.creditos_reservados, 1)
        self.assertEqual(
            pago.importe_pagado,
            Decimal("500.00"),
        )

    def test_vista_entregas_revision_muestra_resumen(self):
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_resumen_entregas",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        ahora = timezone.now()

        for numero in range(2):
            IntentoEntrega.objects.create(
                codigo_proceso=f"PROCESO-RESUMEN-{numero}",
                canal=IntentoEntrega.Canal.EMAIL,
                destinatario="cliente@example.com",
                clave_idempotencia=f"EMAIL-RESUMEN-{numero}",
                estado=IntentoEntrega.Estado.ENVIANDO,
                fecha_inicio_envio=(
                    ahora - timedelta(minutes=30)
                ),
            )

        IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-RESUMEN-HISTORICO",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="historico@example.com",
            clave_idempotencia="EMAIL-RESUMEN-HISTORICO",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_inicio_envio=None,
        )

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse("web:entregas_pendientes_revision")
        )

        self.assertEqual(respuesta.status_code, 200)

        self.assertEqual(
            respuesta.context["resumen"],
            {
                "total": 3,
                "tiempo_excedido": 2,
                "sin_fecha_inicio": 1,
            },
        )

        self.assertContains(respuesta, "Total pendientes")
        self.assertContains(respuesta, "Tiempo excedido")
        self.assertContains(respuesta, "Sin fecha de inicio")

    def test_detalle_intento_entrega_permite_staff(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_detalle_entrega",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-DETALLE-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-DETALLE-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse(
                "web:detalle_intento_entrega",
                args=[intento.pk],
            )
        )

        self.assertEqual(respuesta.status_code, 200)

        self.assertTemplateUsed(
            respuesta,
            "web/detalle_intento_entrega.html",
        )

        self.assertContains(
            respuesta,
            "PROCESO-DETALLE-001",
        )

        self.assertContains(
            respuesta,
            "cliente@example.com",
        )

        self.assertContains(
            respuesta,
            "EMAIL-DETALLE-001",
        )

        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

    def test_vista_entregas_revision_incluye_enlace_detalle(self):
        from datetime import timedelta

        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from django.utils import timezone

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_enlace_detalle",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-ENLACE-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-ENLACE-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
            fecha_inicio_envio=(
                timezone.now() - timedelta(minutes=30)
            ),
        )

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse("web:entregas_pendientes_revision")
        )

        self.assertEqual(respuesta.status_code, 200)

        url_detalle = reverse(
            "web:detalle_intento_entrega",
            args=[intento.pk],
        )

        self.assertContains(
            respuesta,
            url_detalle,
        )

        self.assertContains(
            respuesta,
            "Ver detalle",
        )

        # El enlace no debe modificar el intento.
        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

    def test_detalle_intento_entrega_rechaza_no_staff(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="usuario_detalle_no_staff",
            password="ClavePrueba123!",
            is_staff=False,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-PRIVADO-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="privado@example.com",
            clave_idempotencia="EMAIL-PRIVADO-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        self.client.force_login(usuario)

        url = reverse(
            "web:detalle_intento_entrega",
            args=[intento.pk],
        )

        respuesta = self.client.get(url)

        # El usuario sin permisos no puede acceder.
        self.assertEqual(respuesta.status_code, 302)

        self.assertIn(
            "/admin/login/",
            respuesta["Location"],
        )

        # No debe exponerse información confidencial.
        self.assertNotIn(
            b"privado@example.com",
            respuesta.content,
        )

        self.assertNotIn(
            b"EMAIL-PRIVADO-001",
            respuesta.content,
        )

        # El intento permanece sin modificaciones.
        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

    def test_detalle_intento_entrega_rechaza_anonimo(self):
        from django.urls import reverse

        from web.models import IntentoEntrega

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-ANONIMO-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente-privado@example.com",
            clave_idempotencia="EMAIL-ANONIMO-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        url = reverse(
            "web:detalle_intento_entrega",
            args=[intento.pk],
        )

        # No iniciamos sesión.
        respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 302)

        self.assertIn(
            "/admin/login/",
            respuesta["Location"],
        )

        self.assertIn(
            "next=",
            respuesta["Location"],
        )

        # No se debe exponer información del intento.
        self.assertNotIn(
            b"cliente-privado@example.com",
            respuesta.content,
        )

        self.assertNotIn(
            b"EMAIL-ANONIMO-001",
            respuesta.content,
        )

        # La consulta no modifica el registro.
        intento.refresh_from_db()

        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

    def test_registrar_revision_entrega_confirmada(self):
        from django.contrib.auth import get_user_model

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            registrar_revision_entrega,
        )

        usuario = get_user_model().objects.create_user(
            username="admin_revision_confirmada",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REVISION-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        registrar_revision_entrega(
            intento_id=intento.pk,
            usuario=usuario,
            resultado=IntentoEntrega.ResultadoRevision.CONFIRMADA,
            observaciones="Se verificó la entrega con el proveedor.",
        )

        intento.refresh_from_db()

        self.assertEqual(
            intento.resultado_revision,
            IntentoEntrega.ResultadoRevision.CONFIRMADA,
        )

        self.assertEqual(intento.revisado_por, usuario)

        self.assertIsNotNone(intento.fecha_revision)

        self.assertEqual(
            intento.observaciones_revision,
            "Se verificó la entrega con el proveedor.",
        )

        # La revisión no cambia el estado técnico.
        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

    def test_registrar_revision_entrega_no_permite_sobrescribir(self):
        from django.contrib.auth import get_user_model

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            registrar_revision_entrega,
        )

        usuario = get_user_model().objects.create_user(
            username="admin_revision_original",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        otro_usuario = get_user_model().objects.create_user(
            username="admin_revision_segunda",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-002",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REVISION-002",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        registrar_revision_entrega(
            intento_id=intento.pk,
            usuario=usuario,
            resultado=IntentoEntrega.ResultadoRevision.CONFIRMADA,
            observaciones="Entrega verificada con el proveedor.",
        )

        intento.refresh_from_db()

        fecha_revision_original = intento.fecha_revision

        # Intentamos registrar una segunda revisión.
        with self.assertRaisesMessage(
            ValueError,
            "Este intento ya tiene una revisión administrativa.",
        ):
            registrar_revision_entrega(
                intento_id=intento.pk,
                usuario=otro_usuario,
                resultado=IntentoEntrega.ResultadoRevision.NO_REALIZADA,
                observaciones="Intento de modificar la conclusión.",
            )

        intento.refresh_from_db()

        # Debe conservarse la primera revisión.
        self.assertEqual(
            intento.resultado_revision,
            IntentoEntrega.ResultadoRevision.CONFIRMADA,
        )

        self.assertEqual(intento.revisado_por, usuario)

        self.assertEqual(
            intento.fecha_revision,
            fecha_revision_original,
        )

        self.assertEqual(
            intento.observaciones_revision,
            "Entrega verificada con el proveedor.",
        )

        # El estado técnico tampoco cambia.
        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

    def test_registrar_revision_entrega_rechaza_no_staff(self):
        from django.contrib.auth import get_user_model

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            registrar_revision_entrega,
        )

        usuario = get_user_model().objects.create_user(
            username="usuario_revision_no_staff",
            password="ClavePrueba123!",
            is_staff=False,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-003",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REVISION-003",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Se requiere un administrador activo.",
        ):
            registrar_revision_entrega(
                intento_id=intento.pk,
                usuario=usuario,
                resultado=IntentoEntrega.ResultadoRevision.CONFIRMADA,
                observaciones="Intento sin permisos administrativos.",
            )

        intento.refresh_from_db()

        # No debe registrarse ninguna revisión.
        self.assertIsNone(intento.revisado_por)
        self.assertIsNone(intento.fecha_revision)
        self.assertEqual(intento.resultado_revision, "")
        self.assertEqual(intento.observaciones_revision, "")

        # El estado técnico permanece intacto.
        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

    def test_detalle_intento_entrega_muestra_revision(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            registrar_revision_entrega,
        )

        usuario = get_user_model().objects.create_user(
            username="admin_detalle_revision",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-004",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REVISION-004",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        registrar_revision_entrega(
            intento_id=intento.pk,
            usuario=usuario,
            resultado=IntentoEntrega.ResultadoRevision.CONFIRMADA,
            observaciones="Entrega verificada con el proveedor SMTP.",
        )

        self.client.force_login(usuario)

        respuesta = self.client.get(
            reverse(
                "web:detalle_intento_entrega",
                args=[intento.pk],
            )
        )

        self.assertEqual(respuesta.status_code, 200)

        self.assertContains(
            respuesta,
            "Entrega confirmada",
        )

        self.assertContains(
            respuesta,
            "admin_detalle_revision",
        )

        self.assertContains(
            respuesta,
            "Entrega verificada con el proveedor SMTP.",
        )

        self.assertContains(
            respuesta,
            "Fecha de revisión",
        )

    def test_formulario_revision_entrega_valida_datos(self):
        from web.forms_entrega import RevisionEntregaForm
        from web.models import IntentoEntrega

        formulario = RevisionEntregaForm(
            data={
                "resultado": (
                    IntentoEntrega.ResultadoRevision.CONFIRMADA
                ),
                "observaciones": (
                    "  Entrega verificada con el proveedor SMTP.  "
                ),
            }
        )

        self.assertTrue(
            formulario.is_valid(),
            formulario.errors,
        )

        self.assertEqual(
            formulario.cleaned_data["resultado"],
            IntentoEntrega.ResultadoRevision.CONFIRMADA,
        )

        self.assertEqual(
            formulario.cleaned_data["observaciones"],
            "Entrega verificada con el proveedor SMTP.",
        )

    def test_detalle_intento_entrega_registra_revision_post(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_revision_post",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-POST-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REVISION-POST-001",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        self.client.force_login(usuario)

        url = reverse(
            "web:detalle_intento_entrega",
            args=[intento.pk],
        )

        respuesta = self.client.post(
            url,
            data={
                "resultado": (
                    IntentoEntrega.ResultadoRevision.CONFIRMADA
                ),
                "observaciones": (
                    "Entrega confirmada con el proveedor SMTP."
                ),
            },
        )

        # Después de registrar, redirige al detalle.
        self.assertRedirects(respuesta, url)

        intento.refresh_from_db()

        self.assertEqual(
            intento.resultado_revision,
            IntentoEntrega.ResultadoRevision.CONFIRMADA,
        )

        self.assertEqual(intento.revisado_por, usuario)

        self.assertIsNotNone(intento.fecha_revision)

        self.assertEqual(
            intento.observaciones_revision,
            "Entrega confirmada con el proveedor SMTP.",
        )

        # No modifica el estado técnico del envío.
        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

    def test_detalle_intento_entrega_post_no_sobrescribe_revision(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from web.models import IntentoEntrega

        usuario = get_user_model().objects.create_user(
            username="admin_revision_post_duplicado",
            password="ClavePrueba123!",
            is_staff=True,
            is_active=True,
        )

        intento = IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-REVISION-POST-002",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-REVISION-POST-002",
            estado=IntentoEntrega.Estado.ENVIANDO,
        )

        self.client.force_login(usuario)

        url = reverse(
            "web:detalle_intento_entrega",
            args=[intento.pk],
        )

        # Primera revisión: válida.
        respuesta = self.client.post(
            url,
            data={
                "resultado": IntentoEntrega.ResultadoRevision.CONFIRMADA,
                "observaciones": "Entrega confirmada con el proveedor.",
            },
        )

        self.assertRedirects(respuesta, url)

        intento.refresh_from_db()
        fecha_original = intento.fecha_revision

        # Segundo POST: intenta cambiar la conclusión.
        respuesta = self.client.post(
            url,
            data={
                "resultado": IntentoEntrega.ResultadoRevision.NO_REALIZADA,
                "observaciones": "Intento de sobrescribir la revisión.",
            },
        )

        self.assertRedirects(respuesta, url)

        intento.refresh_from_db()

        # Se conserva la revisión original.
        self.assertEqual(
            intento.resultado_revision,
            IntentoEntrega.ResultadoRevision.CONFIRMADA,
        )

        self.assertEqual(
            intento.observaciones_revision,
            "Entrega confirmada con el proveedor.",
        )

        self.assertEqual(intento.fecha_revision, fecha_original)
        self.assertEqual(intento.revisado_por, usuario)

        # No se modifica el estado técnico.
        self.assertEqual(
            intento.estado,
            IntentoEntrega.Estado.ENVIANDO,
        )

        self.assertIsNone(intento.fecha_envio)

        # El formulario ya no debe mostrarse.
        respuesta = self.client.get(url)

        self.assertEqual(respuesta.status_code, 200)

        self.assertNotContains(
            respuesta,
            "Registrar revisión administrativa",
        )

    def test_obtener_resumen_envios(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import obtener_resumen_envios

        for numero in range(3):
            IntentoEntrega.objects.create(
                codigo_proceso="PROCESO-CONTROL-001",
                canal=IntentoEntrega.Canal.EMAIL,
                destinatario="cliente@example.com",
                clave_idempotencia=f"EMAIL-CONTROL-{numero}",
                estado=IntentoEntrega.Estado.ENVIADO,
            )

        IntentoEntrega.objects.create(
            codigo_proceso="PROCESO-CONTROL-001",
            canal=IntentoEntrega.Canal.EMAIL,
            destinatario="cliente@example.com",
            clave_idempotencia="EMAIL-CONTROL-ERROR",
            estado=IntentoEntrega.Estado.ERROR,
        )

        resumen = obtener_resumen_envios(
            "PROCESO-CONTROL-001"
        )

        self.assertEqual(resumen["total_intentos"], 4)
        self.assertEqual(resumen["envios_exitosos"], 3)
        self.assertEqual(resumen["intentos_fallidos"], 1)
        self.assertEqual(resumen["reenvios"], 2)

    def test_crear_solicitud_envio_email_permite_reenvios(self):
        from web.models import IntentoEntrega
        from web.services.intentos_entrega import (
            crear_solicitud_envio_email,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Prueba",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba",
            whatsapp="6560000000",
        )

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            codigo_proceso="PROCESO-REENVIO-001",
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
        )

        primer_envio = crear_solicitud_envio_email(
            codigo_proceso="PROCESO-REENVIO-001",
            destinatario="cliente@example.com",
            clave_solicitud="SOLICITUD-001",
        )

        # Repetir la misma solicitud no crea otro intento.
        repetido = crear_solicitud_envio_email(
            codigo_proceso="PROCESO-REENVIO-001",
            destinatario="cliente@example.com",
            clave_solicitud="SOLICITUD-001",
        )

        self.assertEqual(primer_envio.pk, repetido.pk)

        # Una nueva solicitud permite reenviar.
        segundo_envio = crear_solicitud_envio_email(
            codigo_proceso="PROCESO-REENVIO-001",
            destinatario="cliente@example.com",
            clave_solicitud="SOLICITUD-002",
        )

        self.assertNotEqual(
            primer_envio.pk,
            segundo_envio.pk,
        )

        self.assertEqual(
            IntentoEntrega.objects.filter(
                codigo_proceso="PROCESO-REENVIO-001",
                canal=IntentoEntrega.Canal.EMAIL,
            ).count(),
            2,
        )

        self.assertEqual(
            primer_envio.estado,
            IntentoEntrega.Estado.PENDIENTE,
        )

        self.assertEqual(
            segundo_envio.estado,
            IntentoEntrega.Estado.PENDIENTE,
        )

    def test_enviar_email_permite_reenvio_y_registra_intentos(self):
        import tempfile

        from decimal import Decimal

        from django.core import mail
        from django.core.files.base import ContentFile
        from django.test import override_settings

        from web.models import IntentoEntrega
        from web.services.entrega import enviar_imagenes_por_email

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND=(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ):
                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Prueba",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    nombre="Unidad Prueba",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-REENVIO-REAL",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso="PROCESO-REENVIO-REAL",
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=(
                        PaqueteImagen.EstadoCredito.CONSUMIDO
                    ),
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-REENVIO-REAL",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-REENVIO-REAL",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "foto.jpg",
                    ContentFile(b"fotografia"),
                    save=True,
                )

                # Primer envío.
                resultado_1 = enviar_imagenes_por_email(
                    "PROCESO-REENVIO-REAL",
                    clave_solicitud="SOLICITUD-REAL-001",
                )

                # Reenvío voluntario.
                resultado_2 = enviar_imagenes_por_email(
                    "PROCESO-REENVIO-REAL",
                    clave_solicitud="SOLICITUD-REAL-002",
                )

                self.assertEqual(resultado_1, 1)
                self.assertEqual(resultado_2, 1)

                self.assertEqual(len(mail.outbox), 2)

                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso="PROCESO-REENVIO-REAL",
                        estado=IntentoEntrega.Estado.ENVIADO,
                    ).count(),
                    2,
                )

                # Repetir la segunda solicitud no envía otro correo.
                resultado_repetido = enviar_imagenes_por_email(
                    "PROCESO-REENVIO-REAL",
                    clave_solicitud="SOLICITUD-REAL-002",
                )

                self.assertEqual(resultado_repetido, 0)
                self.assertEqual(len(mail.outbox), 2)

                # Los créditos no se modifican.
                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

                imagen.refresh_from_db()

                self.assertEqual(
                    imagen.estado_proceso,
                    Imagen.EstadoProceso.ENVIADA,
                )

    def test_ver_imagenes_envia_email_sin_consumir_creditos(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        User = get_user_model()

        usuario = User.objects.create_user(
            username="unidad_email_test",
            password="Test12345!",
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Prueba Email",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            usuario=usuario,
            nombre="Unidad Email",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-VISTA-EMAIL-001",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-VISTA-EMAIL-001"

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso=codigo_proceso,
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
            email_destino="cliente@example.com",
        )

        self.client.force_login(usuario)

        url = reverse(
            "web:ver_imagenes",
            args=[codigo_proceso],
        )

        # Abrir la página para generar la clave en la sesión.
        respuesta_get = self.client.get(url)

        self.assertEqual(respuesta_get.status_code, 200)

        clave_solicitud = respuesta_get.context[
            "clave_solicitud_email"
        ]

        with patch(
            "web.views.enviar_imagenes_por_email",
            return_value=2,
        ) as enviar:
            respuesta = self.client.post(
                reverse(
                    "web:ver_imagenes",
                    args=[codigo_proceso],
                ),
                {
                    "accion": "enviar_email",
                    "clave_solicitud": clave_solicitud,
                },
            )            

        self.assertEqual(respuesta.status_code, 302)

        enviar.assert_called_once()

        argumentos = enviar.call_args.kwargs

        self.assertEqual(
            argumentos["codigo_proceso"],
            codigo_proceso,
        )

        self.assertEqual(
            argumentos["clave_solicitud"],
            clave_solicitud,
        )

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_ver_imagenes_no_permite_enviar_operacion_ajena(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        User = get_user_model()

        usuario_propietario = User.objects.create_user(
            username="propietario_email",
            password="Test12345!",
        )

        usuario_ajeno = User.objects.create_user(
            username="usuario_ajeno_email",
            password="Test12345!",
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ001",
            nombre="Sector Seguridad Email",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad_propietaria = UnidadVenta.objects.create(
            sector=sector,
            usuario=usuario_propietario,
            nombre="Unidad Propietaria",
            whatsapp="6560000000",
        )

        UnidadVenta.objects.create(
            sector=sector,
            usuario=usuario_ajeno,
            nombre="Unidad Ajena",
            whatsapp="6560000001",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-SEGURIDAD-EMAIL-001",
            sector=sector,
            anio=2026,
            semana=44,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            creditos_usados=1,
            precio_unitario=Decimal("100.00"),
        )

        codigo_proceso = "PROCESO-SEGURIDAD-EMAIL-001"

        PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad_propietaria,
            pago=pago,
            codigo_proceso=codigo_proceso,
            estado=PaqueteImagen.EstadoPaquete.CERRADO,
            estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
            email_destino="cliente@example.com",
        )

        self.client.force_login(usuario_ajeno)

        with patch(
            "web.views.enviar_imagenes_por_email"
        ) as enviar:
            respuesta = self.client.post(
                reverse(
                    "web:ver_imagenes",
                    args=[codigo_proceso],
                ),
                {"accion": "enviar_email"},
            )

        self.assertEqual(respuesta.status_code, 302)

        enviar.assert_not_called()

        pago.refresh_from_db()

        self.assertEqual(pago.creditos_usados, 1)
        self.assertEqual(pago.creditos_reservados, 0)

    def test_ver_imagenes_no_duplica_email_misma_solicitud(self):
        from django.contrib.auth import get_user_model
        from django.core import mail
        from django.urls import reverse

        from web.models import IntentoEntrega

        User = get_user_model()

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND=(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ):
                usuario = User.objects.create_user(
                    username="unidad_idempotencia",
                    password="Test12345!",
                )

                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Idempotencia",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    usuario=usuario,
                    nombre="Unidad Idempotencia",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-VISTA-IDEMPOTENCIA",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                codigo_proceso = "PROCESO-VISTA-IDEMPOTENCIA"

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso=codigo_proceso,
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-VISTA-IDEMPOTENCIA",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-VISTA-IDEMPOTENCIA",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                self.client.force_login(usuario)

                url = reverse(
                    "web:ver_imagenes",
                    args=[codigo_proceso],
                )

                respuesta_get = self.client.get(url)
                self.assertEqual(respuesta_get.status_code, 200)

                clave_solicitud = respuesta_get.context[
                    "clave_solicitud_email"
                ]

                datos = {
                    "accion": "enviar_email",
                    "clave_solicitud": clave_solicitud,
                }

                # Primera solicitud.
                respuesta_1 = self.client.post(url, datos)

                # El navegador repite exactamente la misma solicitud.
                respuesta_2 = self.client.post(url, datos)

                self.assertEqual(respuesta_1.status_code, 302)
                self.assertEqual(respuesta_2.status_code, 302)

                # Solo debe existir un correo.
                self.assertEqual(len(mail.outbox), 1)

                self.assertEqual(
                    mail.outbox[0].to,
                    ["cliente@example.com"],
                )

                # Solo un intento de entrega.
                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso=codigo_proceso,
                        canal=IntentoEntrega.Canal.EMAIL,
                    ).count(),
                    1,
                )

                intento = IntentoEntrega.objects.get(
                    clave_idempotencia=f"EMAIL-{clave_solicitud}"
                )

                self.assertEqual(
                    intento.estado,
                    IntentoEntrega.Estado.ENVIADO,
                )

                # No se consumen créditos adicionales.
                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

                # Nuevo reenvío voluntario.
                respuesta_get = self.client.get(url)

                self.assertEqual(respuesta_get.status_code, 200)

                nueva_clave = respuesta_get.context[
                    "clave_solicitud_email"
                ]

                self.assertNotEqual(
                    nueva_clave,
                    clave_solicitud,
                )

                respuesta_reenvio = self.client.post(
                    url,
                    {
                        "accion": "enviar_email",
                        "clave_solicitud": nueva_clave,
                    },
                )

                self.assertEqual(
                    respuesta_reenvio.status_code,
                    302,
                )

                # Dos solicitudes diferentes: dos correos.
                self.assertEqual(len(mail.outbox), 2)

                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso=codigo_proceso,
                        canal=IntentoEntrega.Canal.EMAIL,
                        estado=IntentoEntrega.Estado.ENVIADO,
                    ).count(),
                    2,
                )

                # El reenvío no consume créditos.
                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)

    def test_ver_imagenes_reintenta_email_despues_de_error(self):
        from unittest.mock import patch

        from django.contrib.auth import get_user_model
        from django.core import mail
        from django.urls import reverse

        from web.models import IntentoEntrega

        User = get_user_model()

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(
                MEDIA_ROOT=directorio,
                EMAIL_BACKEND=(
                    "django.core.mail.backends.locmem.EmailBackend"
                ),
            ):
                usuario = User.objects.create_user(
                    username="unidad_reintento_email",
                    password="Test12345!",
                )

                estado = Estado.objects.create(
                    nombre="Chihuahua",
                    abreviacion="CHIH",
                )

                ciudad = Ciudad.objects.create(
                    codigo_ciudad="JUAREZ",
                    estado=estado,
                    nombre="Ciudad Juárez",
                )

                sector = Sector.objects.create(
                    ciudad=ciudad,
                    codigo_sector="JUAREZ001",
                    nombre="Sector Reintento",
                    representante="Representante Prueba",
                    telefono1="6560000000",
                )

                unidad = UnidadVenta.objects.create(
                    sector=sector,
                    usuario=usuario,
                    nombre="Unidad Reintento",
                    whatsapp="6560000000",
                )

                pago = Pago.objects.create(
                    codigo_pago="PAGO-VISTA-REINTENTO",
                    sector=sector,
                    anio=2026,
                    semana=44,
                    importe_pagado=Decimal("100.00"),
                    paquetes_pagados=1,
                    creditos_usados=1,
                    precio_unitario=Decimal("100.00"),
                )

                codigo_proceso = "PROCESO-VISTA-REINTENTO"

                paquete = PaqueteImagen.objects.create(
                    numero_paquete=1,
                    unidad_venta=unidad,
                    pago=pago,
                    codigo_proceso=codigo_proceso,
                    estado=PaqueteImagen.EstadoPaquete.CERRADO,
                    estado_credito=PaqueteImagen.EstadoCredito.CONSUMIDO,
                    email_destino="cliente@example.com",
                )

                fondo = Fondo.objects.create(
                    codigo_fondo="FONDO-VISTA-REINTENTO",
                )

                imagen = Imagen.objects.create(
                    paquete=paquete,
                    fondo=fondo,
                    codigo_imagen="IMAGEN-VISTA-REINTENTO",
                    seleccionada=True,
                    estado_proceso=Imagen.EstadoProceso.PROCESADA,
                )

                imagen.imagen_procesada.save(
                    "fotografia.jpg",
                    ContentFile(b"fotografia procesada"),
                    save=True,
                )

                self.client.force_login(usuario)

                url = reverse(
                    "web:ver_imagenes",
                    args=[codigo_proceso],
                )

                respuesta_get = self.client.get(url)
                self.assertEqual(respuesta_get.status_code, 200)

                clave_original = respuesta_get.context[
                    "clave_solicitud_email"
                ]

                # Simular falla del servidor de correo.
                with patch(
                    "django.core.mail.EmailMessage.send",
                    side_effect=RuntimeError("Servidor SMTP no disponible"),
                ):
                    respuesta_error = self.client.post(
                        url,
                        {
                            "accion": "enviar_email",
                            "clave_solicitud": clave_original,
                        },
                    )

                self.assertEqual(respuesta_error.status_code, 302)

                intento_error = IntentoEntrega.objects.get(
                    clave_idempotencia=f"EMAIL-{clave_original}"
                )

                self.assertEqual(
                    intento_error.estado,
                    IntentoEntrega.Estado.ERROR,
                )

                # La vista debe generar una nueva clave.
                respuesta_get = self.client.get(url)
                self.assertEqual(respuesta_get.status_code, 200)

                nueva_clave = respuesta_get.context[
                    "clave_solicitud_email"
                ]

                self.assertNotEqual(nueva_clave, clave_original)

                # Segundo intento: servidor de correo disponible.
                respuesta_reintento = self.client.post(
                    url,
                    {
                        "accion": "enviar_email",
                        "clave_solicitud": nueva_clave,
                    },
                )

                self.assertEqual(respuesta_reintento.status_code, 302)

                intento_exitoso = IntentoEntrega.objects.get(
                    clave_idempotencia=f"EMAIL-{nueva_clave}"
                )

                self.assertEqual(
                    intento_exitoso.estado,
                    IntentoEntrega.Estado.ENVIADO,
                )

                self.assertEqual(len(mail.outbox), 1)

                # Se conservan ambos intentos.
                self.assertEqual(
                    IntentoEntrega.objects.filter(
                        codigo_proceso=codigo_proceso,
                        canal=IntentoEntrega.Canal.EMAIL,
                    ).count(),
                    2,
                )

                # El reintento no consume créditos.
                pago.refresh_from_db()

                self.assertEqual(pago.creditos_usados, 1)
                self.assertEqual(pago.creditos_reservados, 0)
            
#*** fin de pruebas CreditosServiceTests

class ProcesamientoConcurrenciaTests(TransactionTestCase):

    def test_select_for_update_espera_y_observa_estado_actualizado(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connections, transaction

        # Usamos un paquete existente creado específicamente
        # para esta prueba.
        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ045",
            nombre="Sector Prueba 45",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 45",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-045",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        paquete = PaqueteImagen.objects.create(
            numero_paquete=1,
            unidad_venta=unidad,
            pago=pago,
            codigo_proceso="PROCESO-045",
            imagen_original="imagenes/originales/prueba45.jpg",
            estado=PaqueteImagen.EstadoPaquete.RECIBIDO,
            estado_credito=PaqueteImagen.EstadoCredito.RESERVADO,
        )

        bloqueo_obtenido = threading.Event()
        intento_b_iniciado = threading.Event()
        liberar_bloqueo = threading.Event()

        def conexion_a():
            connections.close_all()

            try:
                with transaction.atomic():
                    registro = (
                        PaqueteImagen.objects
                        .select_for_update()
                        .get(pk=paquete.pk)
                    )

                    registro.estado = (
                        PaqueteImagen.EstadoPaquete.PROCESANDO
                    )
                    registro.save(update_fields=["estado"])

                    bloqueo_obtenido.set()

                    if not liberar_bloqueo.wait(timeout=10):
                        raise TimeoutError(
                            "No se recibió la señal para liberar el bloqueo."
                        )
            finally:
                connections.close_all()

        def conexion_b():
            connections.close_all()

            try:
                if not bloqueo_obtenido.wait(timeout=10):
                    raise TimeoutError(
                        "La conexión A no obtuvo el bloqueo."
                    )

                with transaction.atomic():
                    intento_b_iniciado.set()

                    registro = (
                        PaqueteImagen.objects
                        .select_for_update()
                        .get(pk=paquete.pk)
                    )

                    return registro.estado
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuro_a = executor.submit(conexion_a)
            futuro_b = executor.submit(conexion_b)

            try:
                self.assertTrue(
                    bloqueo_obtenido.wait(timeout=10),
                    "La conexión A no obtuvo el bloqueo.",
                )

                self.assertTrue(
                    intento_b_iniciado.wait(timeout=10),
                    "La conexión B no inició su consulta.",
                )

                # Mientras A mantiene el bloqueo, B no debe
                # haber terminado de obtener el registro.
                self.assertFalse(
                    futuro_b.done(),
                    "La conexión B terminó antes de liberar el bloqueo.",
                )

            finally:
                liberar_bloqueo.set()

            futuro_a.result(timeout=10)
            estado_observado = futuro_b.result(timeout=10)

        self.assertEqual(
            estado_observado,
            PaqueteImagen.EstadoPaquete.PROCESANDO,
        )

    def test_dos_conexiones_no_procesan_misma_operacion(self):
        import tempfile
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from decimal import Decimal
        from unittest.mock import patch

        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.db import connections
        from django.test import override_settings

        from web.services.procesamiento import (
            recibir_imagen,
            procesar_variantes,
        )

        estado = Estado.objects.create(
            nombre="Chihuahua",
            abreviacion="CHIH",
        )

        ciudad = Ciudad.objects.create(
            codigo_ciudad="JUAREZ",
            estado=estado,
            nombre="Ciudad Juárez",
        )

        sector = Sector.objects.create(
            ciudad=ciudad,
            codigo_sector="JUAREZ046",
            nombre="Sector Prueba 46",
            representante="Representante Prueba",
            telefono1="6560000000",
        )

        unidad = UnidadVenta.objects.create(
            sector=sector,
            nombre="Unidad Prueba 46",
            whatsapp="6560000000",
        )

        pago = Pago.objects.create(
            codigo_pago="PAGO-TEST-046",
            sector=sector,
            anio=2026,
            semana=43,
            importe_pagado=Decimal("100.00"),
            paquetes_pagados=1,
            precio_unitario=Decimal("100.00"),
        )

        with tempfile.TemporaryDirectory() as directorio:
            with override_settings(MEDIA_ROOT=directorio):

                for numero in range(1, 4):
                    fondo = Fondo(
                        codigo_fondo=f"FONDO-46-{numero}",
                        activo=True,
                    )

                    fondo.imagen.save(
                        f"fondo_{numero}.jpg",
                        ContentFile(b"contenido-fondo"),
                        save=True,
                    )

                original = SimpleUploadedFile(
                    "foto_original.jpg",
                    b"contenido-original",
                    content_type="image/jpeg",
                )

                paquetes = recibir_imagen(
                    unidad_venta_id=unidad.pk,
                    codigo_proceso="PROCESO-046",
                    imagen_original=original,
                )

                paquete = paquetes[0]

                motor_iniciado = threading.Event()
                liberar_motor = threading.Event()
                llamadas_motor = []
                errores = []

                def motor_simulado(original_bytes, fondos_bytes):
                    llamadas_motor.append(1)
                    motor_iniciado.set()

                    if not liberar_motor.wait(timeout=10):
                        raise TimeoutError(
                            "No se liberó el motor de IA."
                        )

                    return [
                        b"variante-1",
                        b"variante-2",
                        b"variante-3",
                    ]

                def solicitud_a():
                    connections.close_all()
                    try:
                        return procesar_variantes("PROCESO-046")
                    finally:
                        connections.close_all()

                def solicitud_b():
                    connections.close_all()
                    try:
                        return procesar_variantes("PROCESO-046")
                    except ValueError as exc:
                        errores.append(str(exc))
                        return None
                    finally:
                        connections.close_all()

                with patch(
                    "web.services.procesamiento.generar_variantes",
                    side_effect=motor_simulado,
                ) as generador:

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        futuro_a = executor.submit(solicitud_a)

                        try:
                            self.assertTrue(
                                motor_iniciado.wait(timeout=10),
                                "La solicitud A no inició el motor.",
                            )

                            paquete.refresh_from_db()

                            self.assertEqual(
                                paquete.estado,
                                PaqueteImagen.EstadoPaquete.PROCESANDO,
                            )

                            futuro_b = executor.submit(solicitud_b)

                            resultado_b = futuro_b.result(timeout=10)

                            self.assertIsNone(resultado_b)

                        finally:
                            liberar_motor.set()

                        imagenes = futuro_a.result(timeout=10)

                    generador.assert_called_once()

                self.assertEqual(len(llamadas_motor), 1)
                self.assertEqual(len(imagenes), 3)

                self.assertEqual(len(errores), 1)

                self.assertIn(
                    "La operación ya está en procesamiento",
                    errores[0],
                )

                paquete.refresh_from_db()
                pago.refresh_from_db()

                self.assertEqual(
                    paquete.estado,
                    PaqueteImagen.EstadoPaquete.LISTO,
                )

                self.assertEqual(
                    Imagen.objects.filter(
                        paquete=paquete,
                    ).count(),
                    3,
                )

                self.assertEqual(pago.creditos_reservados, 1)
                self.assertEqual(pago.creditos_usados, 0)
