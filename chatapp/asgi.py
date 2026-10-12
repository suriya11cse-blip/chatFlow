# chatapp/asgi.py
import os

from django.core.asgi import get_asgi_application

# ⚠️ Set Django settings BEFORE importing any Django-related modules
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatapp.settings')

# ⚠️ Initialize Django ASGI app FIRST — this loads apps, models, etc.
django_asgi_app = get_asgi_application()

# THEN import Channels-related modules (safe now)
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import chat.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            chat.routing.websocket_urlpatterns
        )
    ),
})