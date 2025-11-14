"""トークンリフレッシュとログアウトのView"""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from .utils import set_jwt_cookies


class TokenRefreshView(APIView):
    """トークンリフレッシュView - Cookieからリフレッシュトークンを読み取り、新しいアクセストークンを発行"""
    permission_classes = [AllowAny]

    def post(self, request):
        # Cookieからリフレッシュトークンを取得
        refresh_token = request.COOKIES.get('refresh_token')
        
        # ヘッダーからも取得を試みる（後方互換性のため）
        if not refresh_token:
            auth_header = request.META.get('HTTP_AUTHORIZATION', '')
            if auth_header.startswith('Bearer '):
                # これは通常アクセストークンのはずだが、リフレッシュトークンとして試す
                refresh_token = auth_header[7:]
            else:
                # リクエストボディからも取得を試みる（後方互換性のため）
                refresh_token = request.data.get('refresh')
        
        if not refresh_token:
            return Response(
                {'detail': 'リフレッシュトークンが見つかりません。'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        try:
            # リフレッシュトークンを検証してユーザーを取得
            refresh = RefreshToken(refresh_token)
            user_id = refresh.access_token.get('user_id')
            
            # ユーザーオブジェクトを取得
            from django.contrib.auth.models import User
            user_obj = User.objects.get(id=user_id)
            
            # 古いリフレッシュトークンを無効化（ブラックリストに追加）
            # 注意: ブラックリスト機能が有効な場合のみ
            try:
                refresh.blacklist()
            except Exception:
                # ブラックリスト機能が無効な場合は無視
                pass
            
            # 新しいアクセストークンとリフレッシュトークンを生成
            new_refresh = RefreshToken.for_user(user_obj)
            
            # レスポンスを作成
            resp = Response({
                'detail': 'トークンが更新されました。',
            }, status=status.HTTP_200_OK)
            
            # 新しいトークンをCookieに保存
            set_jwt_cookies(resp, new_refresh)
            
            return resp
            
        except (TokenError, InvalidToken) as e:
            return Response(
                {'detail': '無効なリフレッシュトークンです。'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        except User.DoesNotExist:
            return Response(
                {'detail': 'ユーザーが見つかりません。'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        except Exception as e:
            return Response(
                {'detail': f'トークンの更新に失敗しました: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class LogoutView(APIView):
    """ログアウトView - Cookieからトークンを削除"""
    permission_classes = [AllowAny]

    def post(self, request):
        from django.conf import settings
        # Cookieを削除
        resp = Response({
            'detail': 'ログアウトしました。',
        }, status=status.HTTP_200_OK)
        
        # クッキー削除（delete_cookieはsecureパラメータをサポートしていないため、空の値で上書きして削除）
        # アクセストークンのCookieを削除
        resp.set_cookie('access_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
        # リフレッシュトークンのCookieを削除
        resp.set_cookie('refresh_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
        
        return resp

