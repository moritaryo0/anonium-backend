"""
WebSocket認証用のMiddleware
JWTトークンを使用してWebSocket接続を認証します
"""
import json
from urllib.parse import parse_qs
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

User = get_user_model()


@database_sync_to_async
def get_user_from_token(token_string):
    """JWTトークンからユーザーを取得"""
    try:
        if not token_string:
            return AnonymousUser()
        access_token = AccessToken(token_string)
        user_id = access_token['user_id']
        user = User.objects.get(pk=user_id)
        return user
    except (TokenError, InvalidToken) as e:
        print(f"Token validation error: {e}")
        return AnonymousUser()
    except User.DoesNotExist:
        print(f"User not found for token")
        return AnonymousUser()
    except Exception as e:
        print(f"Unexpected error in token validation: {e}")
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """
    WebSocket接続をJWTトークンで認証するMiddleware
    
    クエリパラメータまたはヘッダーからトークンを取得します
    例: ws://localhost:8000/ws/messages/?token=xxx
    """
    
    async def __call__(self, scope, receive, send):
        # WebSocket接続の場合のみ認証
        if scope['type'] != 'websocket':
            return await super().__call__(scope, receive, send)
        
        try:
            # クエリパラメータからトークンを取得
            query_string = scope.get('query_string', b'').decode()
            query_params = parse_qs(query_string)
            token = query_params.get('token', [None])[0]
            
            # ヘッダーからも取得を試みる（通常のWebSocketではあまり使われないが）
            if not token:
                headers = dict(scope.get('headers', []))
                auth_header = headers.get(b'authorization', b'').decode()
                if auth_header.startswith('Bearer '):
                    token = auth_header[7:]
            
            # トークンからユーザーを取得
            if token:
                scope['user'] = await get_user_from_token(token)
                print(f"WebSocket authentication: User {scope['user'].id if not scope['user'].is_anonymous else 'Anonymous'} authenticated")
            else:
                scope['user'] = AnonymousUser()
                print("WebSocket authentication: No token provided, using AnonymousUser")
        except Exception as e:
            print(f"WebSocket authentication error: {e}")
            scope['user'] = AnonymousUser()
        
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    """JWT認証Middlewareを適用するヘルパー関数"""
    return JWTAuthMiddleware(inner)

