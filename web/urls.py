from django.urls import path
from django.contrib.auth import views as auth_views
from web import views
from web.views_whatsapp import webhook_whatsapp

app_name = "web"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("login/", auth_views.LoginView.as_view(template_name="web/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("recibir-fotografia/", views.recibir_fotografia, name="recibir_fotografia"),
    path("imagenes/<str:codigo_proceso>/", views.ver_imagenes, name="ver_imagenes"),
    path("historial/", views.historial_operaciones, name="historial_operaciones"),
    path(
        "entregas/pendientes-revision/",
        views.entregas_pendientes_revision,
        name="entregas_pendientes_revision",
    ),
    path(
        "entregas/intentos/<int:intento_id>/",
        views.detalle_intento_entrega,
        name="detalle_intento_entrega",
    ),
    path(
        "webhooks/whatsapp/",
        webhook_whatsapp,
        name="webhook_whatsapp",
    ),
]
