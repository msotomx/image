from unittest.mock import MagicMock, patch

from web.services.whatsapp_estados import procesar_estado_whatsapp


# Simulamos que el trabajador A reclamó el intento 1,
# pero el evento ya no pertenece a ese intento.
with patch(
    "web.services.whatsapp_estados.reclamar_evento_whatsapp",
    return_value=1,
) as reclamar:

    with patch(
        "web.services.whatsapp_estados.EventoWhatsApp.objects"
    ) as objetos_evento:

        # La consulta protegida no encuentra el intento 1.
        objetos_evento.select_for_update.return_value.filter.return_value.first.return_value = None

        with patch(
            "web.services.whatsapp_estados.confirmar_evento_whatsapp"
        ) as confirmar:

            with patch(
                "web.services.whatsapp_estados.registrar_error_evento_whatsapp"
            ) as registrar_error:

                with patch(
                    "web.services.whatsapp_estados.registrar_imagen_enviada_whatsapp"
                ) as enviar:

                    with patch(
                        "web.services.whatsapp_estados.registrar_imagen_entregada_whatsapp"
                    ) as entregar:

                        with patch(
                            "web.services.whatsapp_estados.registrar_imagen_leida_whatsapp"
                        ) as leer:

                            with patch(
                                "web.services.whatsapp_estados.registrar_imagen_fallida_whatsapp"
                            ) as fallar:

                                resultado = procesar_estado_whatsapp(900001)

                                assert resultado is False

                                reclamar.assert_called_once_with(900001)

                                confirmar.assert_not_called()
                                registrar_error.assert_not_called()

                                enviar.assert_not_called()
                                entregar.assert_not_called()
                                leer.assert_not_called()
                                fallar.assert_not_called()


print("PRUEBA VERDE - INTENTO OBSOLETO WHATSAPP")
print("Intento antiguo rechazado:", True)
print("Fotografía sin modificar:", True)
print("Evento sin confirmar:", True)
print("Error no registrado sobre intento nuevo:", True)
print("Base de datos modificada:", False)
