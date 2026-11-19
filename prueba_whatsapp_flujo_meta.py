from io import BytesIO
from unittest.mock import Mock, patch

from web.services.whatsapp_meta import (
    subir_imagen_whatsapp_meta,
    enviar_imagen_whatsapp_meta,
)

archivo = BytesIO(b"IMAGEN_FICTICIA")
archivo.name = "prueba.jpg"

respuesta_carga = Mock()
respuesta_carga.ok = True
respuesta_carga.json.return_value = {
    "id": "MEDIA_PRUEBA_123"
}

respuesta_envio = Mock()
respuesta_envio.ok = True
respuesta_envio.json.return_value = {
    "messages": [{"id": "wamid.PRUEBA123"}]
}

with patch(
    "web.services.whatsapp_meta.requests.post",
    side_effect=[respuesta_carga, respuesta_envio],
) as post:

    media_id = subir_imagen_whatsapp_meta(
        access_token="TOKEN_FICTICIO",
        phone_number_id="123456789",
        archivo=archivo,
    )

    mensaje_id = enviar_imagen_whatsapp_meta(
        access_token="TOKEN_FICTICIO",
        phone_number_id="123456789",
        telefono_destino="5216560000000",
        media_id=media_id,
    )

    assert post.call_count == 2
    assert media_id == "MEDIA_PRUEBA_123"
    assert mensaje_id == "wamid.PRUEBA123"

    llamada_carga = post.call_args_list[0]
    llamada_envio = post.call_args_list[1]

    assert llamada_carga.args[0].endswith("/media")
    assert llamada_envio.args[0].endswith("/messages")
    assert llamada_envio.kwargs["json"]["image"]["id"] == media_id
    assert llamada_envio.kwargs["json"]["to"] == "5216560000000"

print("PRUEBA VERDE - FLUJO HTTP WHATSAPP")
print("Carga de imagen:", True)
print("Envío con media_id:", True)
print("Meta message ID:", mensaje_id)
print("Solicitudes HTTP simuladas:", 2)
print("Envíos reales:", 0)
