import os
import requests
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "image.settings")
django.setup()

from django.conf import settings

PHONE_NUMBER_ID = "1423894944146179"

token = settings.WHATSAPP_ACCESS_TOKEN
pin = os.environ.get("IMAGE_ENGINE_REGISTRATION_PIN")

if not token:
    raise RuntimeError("Falta WHATSAPP_ACCESS_TOKEN.")

if not pin or len(pin) != 6 or not pin.isdigit():
    raise RuntimeError("Falta un PIN válido de seis dígitos.")

respuesta = requests.post(
    f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/register",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    json={
        "messaging_product": "whatsapp",
        "pin": pin,
    },
    timeout=30,
)

print("HTTP:", respuesta.status_code)
print("Respuesta:", respuesta.text)
