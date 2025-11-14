"""
ASGI config for app project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')

# DjangoのASGIアプリケーションを初期化
django_asgi_app = get_asgi_application()

# WebSocketルーティングをインポート
from messages.routing import websocket_urlpatterns
from app.middleware import JWTAuthMiddlewareStack

application = ProtocolTypeRouter({
    # HTTPリクエストは通常のDjangoアプリケーションにルーティング
    "http": django_asgi_app,
    
    # WebSocketリクエストはWebSocketルーティングにルーティング
    "websocket": JWTAuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
