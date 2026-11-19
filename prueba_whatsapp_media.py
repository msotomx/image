from io import BytesIO
from unittest.mock import Mock, patch

from web.services.whatsapp_meta import subir_imagen_whatsapp_meta


archivo = BytesIO(b"IMAGEN_FICTICIA")
archivo.name = "prueba.jpg"

respuesta = Mock()
respuesta.ok = True
respuesta.json.return_value = {"id": "MEDIA_PRUEBA_123"}

with patch(
    "web.services.whatsapp_meta.requests.post",
    return_value=respuesta,
) as post:

    media_id = subir_imagen_whatsapp_meta(
        access_token="TOKEN_FICTICIO",
        phone_number_id="123456789",
        archivo=archivo,
    )

    assert media_id == "MEDIA_PRUEBA_123"
    assert post.call_count == 1

    args, kwargs = post.call_args

    assert args[0].endswith("/123456789/media")
    assert kwargs["data"]["messaging_product"] == "whatsapp"
    assert kwargs["files"]["file"][0] == "prueba.jpg"
    assert kwargs["files"]["file"][1] is archivo
    assert kwargs["files"]["file"][2] == "image/jpeg"
    assert kwargs["timeout"] == (5, 60)

print("PRUEBA VERDE - CARGA MEDIA WHATSAPP")
print("Media ID recibido:", media_id)
print("Solicitud HTTP simulada:", True)
print("Fotografías reales utilizadas:", 0)
print("Envíos reales realizados:", 0)
