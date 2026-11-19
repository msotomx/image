import hmac
import hashlib
import json
import logging
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from web.services.whatsapp_eventos import registrar_eventos_whatsapp

logger = logging.getLogger(__name__)
@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook_whatsapp(request):
    if request.method == "GET":
        modo = request.GET.get("hub.mode", "")
        token = request.GET.get("hub.verify_token", "")
        challenge = request.GET.get("hub.challenge", "")

        token_esperado = settings.IMAGE_ENGINE_WHATSAPP_VERIFY_TOKEN

        if (
            modo == "subscribe"
            and token_esperado
            and hmac.compare_digest(token, token_esperado)
            and challenge.isdecimal()
        ):
            return HttpResponse(challenge, content_type="text/plain")

        return HttpResponseForbidden("Verificación inválida.")

    # la firma X-Hub-Signature-256 de Meta.
    # Verificar que el POST proviene de Meta.
    app_secret = settings.IMAGE_ENGINE_META_APP_SECRET

    if not app_secret:
        return HttpResponse(
            "App Secret no configurado.",
            status=503,
        )

    firma_recibida = request.headers.get(
        "X-Hub-Signature-256", ""
    )

    firma_esperada = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        request.body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        firma_recibida,
        firma_esperada,
    ):
        return HttpResponseForbidden(
            "Firma de Meta inválida."
        )

    # La firma de Meta ya fue validada.
    try:
        evento = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(
            "JSON inválido.",
            status=400,
        )

    if not isinstance(evento, dict):
        return HttpResponse(
            "Formato de evento inválido.",
            status=400,
        )

    if evento.get("object") != "whatsapp_business_account":
        return HttpResponse(
            "Evento no reconocido.",
            status=400,
        )

    try:
        registrados = registrar_eventos_whatsapp(evento)
    except Exception:
        logger.exception(
            "Error al guardar eventos de WhatsApp."
        )
        return HttpResponse(
            "Error al registrar eventos.",
            status=500,
        )

    logger.info(
        "WhatsApp: %s eventos nuevos registrados.",
        registrados,
    )

    return HttpResponse(
        "Evento reconocido.",
        status=200,
    )
