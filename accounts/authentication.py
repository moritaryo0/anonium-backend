"""JWT認証クラス - Cookieからトークンを読み取る"""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed
from django.conf import settings


class CookieJWTAuthentication(JWTAuthentication):
    """
    JWT認証クラス - Cookieからトークンを読み取る
    
    AuthorizationヘッダーとCookieの両方からトークンを取得できるようにする
    Cookieが優先される
    """
    
    def authenticate(self, request):
        # まずCookieからトークンを取得
        access_token = request.COOKIES.get('access_token')
        refresh_token = request.COOKIES.get('refresh_token')
        
        if access_token:
            try:
                # Cookieから取得したトークンで認証
                validated_token = self.get_validated_token(access_token)
                user = self.get_user(validated_token)
                return (user, validated_token)
            except (InvalidToken, AuthenticationFailed):
                # アクセストークンが無効な場合、リフレッシュトークンから新しいアクセストークンを生成
                if refresh_token:
                    try:
                        from rest_framework_simplejwt.tokens import RefreshToken
                        refresh = RefreshToken(refresh_token)
                        # リフレッシュトークンから新しいアクセストークンを生成
                        new_access_token = refresh.access_token
                        user = self.get_user(self.get_validated_token(str(new_access_token)))
                        # 注意: ここでは新しいトークンを返すが、レスポンスでクッキーを更新する必要がある
                        # そのため、この認証クラスだけでは不十分で、Viewで処理する必要がある
                        return (user, self.get_validated_token(str(new_access_token)))
                    except (InvalidToken, AuthenticationFailed):
                        # リフレッシュトークンも無効な場合は、認証失敗
                        pass
                # Cookieのトークンが無効な場合は、Authorizationヘッダーを試す
                pass
        
        # Cookieにトークンがない場合、Authorizationヘッダーから取得（後方互換性のため）
        header = self.get_header(request)
        if header is None:
            return None
        
        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None
        
        validated_token = self.get_validated_token(raw_token)
        user = self.get_user(validated_token)
        return (user, validated_token)

