from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.models import User
from django.conf import settings
from .models import UserProfile
from .serializers import UserSerializer
from .utils import set_jwt_cookies
import secrets
import string
import base64
import requests
import urllib.parse
import hashlib
from django.utils import timezone


def generate_random_username() -> str:
    """ランダムなユーザー名を生成"""
    chars = string.ascii_lowercase + string.digits + '_'
    while True:
        username = 'user_' + ''.join(secrets.choice(chars) for _ in range(12))
        if not User.objects.filter(username=username).exists():
            return username


class OAuthBaseView(APIView):
    permission_classes = [AllowAny]
    provider_name = ''

    def _get_or_create_user(self, oauth_id: str, email: str, display_name: str, provider: str) -> tuple[User, bool]:
        """OAuthユーザーを取得または作成
        Returns:
            tuple[User, bool]: (user, is_new_user) のタプル
        """
        # まず、プロバイダー固有のユーザー名で既存ユーザーを検索
        username = f"{provider}_{oauth_id}"
        user = User.objects.filter(username=username).first()
        
        # ユーザー名で見つからない場合、emailで検索（emailが提供されている場合）
        if not user and email:
            user = User.objects.filter(email=email).first()
        
        is_new_user = False
        if user:
            # 既存ユーザーが見つかった場合（再ログイン）
            # emailが更新されている場合は更新
            if email and user.email != email:
                user.email = email
                user.save(update_fields=['email'])
            # プロフィールを取得または作成（既存ユーザーの場合はdisplay_nameとicon_urlは更新しない）
            profile, _ = UserProfile.objects.get_or_create(user=user)
            # 再ログイン時はdisplay_nameとicon_urlを更新しない
        else:
            # 新規ユーザーを作成
            is_new_user = True
            # ユーザー名が既に存在する場合はランダムなユーザー名を生成
            if User.objects.filter(username=username).exists():
                username = generate_random_username()
            
            # emailがない場合は空文字列を設定（X (Twitter)の場合など）
            user_email = email if email else ''
            
            # OAuthユーザーはパスワードなしで作成
            user = User(username=username, email=user_email)
            user.set_unusable_password()  # パスワードを使用不可に設定
            user.save()  # この時点でシグナルが発火してUserProfileが自動作成される可能性がある
            
            # プロフィールを取得または作成（シグナルで既に作成されている可能性がある）
            default_display_name = display_name.strip() if display_name and display_name.strip() else (
                email.split('@')[0] if email else username
            )
            # シグナルで既に作成されている可能性があるため、get_or_createを使用
            profile, _ = UserProfile.objects.get_or_create(
                user=user,
                defaults={'display_name': default_display_name}
            )
            # 表示名が設定されていない、または更新が必要な場合は更新
            if not profile.display_name or (display_name and display_name.strip() and profile.display_name != display_name.strip()):
                profile.display_name = display_name.strip() if display_name and display_name.strip() else default_display_name
                profile.save(update_fields=['display_name', 'updated_at'])
        
        return (user, is_new_user)


class GoogleOAuthAuthorizeView(APIView):
    """Google OAuth認証URLを生成"""
    permission_classes = [AllowAny]

    def get(self, request):
        """Google OAuth認証URLを生成"""
        # 環境変数から設定を取得
        client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
        authorize_url = settings.GOOGLE_OAUTH_AUTHORIZE_URL
        
        # クライアントIDが設定されていない場合はエラー
        if not client_id:
            return Response(
                {'detail': 'Google OAuth is not configured. Please set GOOGLE_OAUTH_CLIENT_ID in environment variables.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # stateパラメータを生成（CSRF対策）
        state = secrets.token_urlsafe(32)
        
        # Google OAuth 2.0の認証URLを構築
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'access_type': 'offline',  # リフレッシュトークンを取得
            'prompt': 'consent',  # 常に同意画面を表示
        }
        
        auth_url = f"{authorize_url}?{urllib.parse.urlencode(params)}"
        
        return Response({
            'authorize_url': auth_url,
            'state': state,
        }, status=status.HTTP_200_OK)


class GoogleOAuthCallbackView(OAuthBaseView):
    """Google OAuth認証コールバック"""
    provider_name = 'google'

    def post(self, request):
        """Google OAuth認証コードを処理"""
        code = request.data.get('code')
        state = request.data.get('state')
        
        if not code:
            return Response(
                {'detail': 'Authorization code is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 環境変数から設定を取得
        client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET
        redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
        token_url = settings.GOOGLE_OAUTH_TOKEN_URL
        user_info_url = settings.GOOGLE_OAUTH_USER_INFO_URL
        
        # クライアントIDとシークレットが設定されていない場合はエラー
        if not client_id or not client_secret:
            return Response(
                {'detail': 'Google OAuth is not configured. Please set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in environment variables.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        try:
            # 認証コードをアクセストークンに交換
            token_data = {
                'code': code,
                'client_id': client_id,
                'client_secret': client_secret,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            }
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
            }
            
            token_response = requests.post(
                token_url,
                data=token_data,
                headers=headers,
                timeout=10
            )
            
            if token_response.status_code != 200:
                error_detail = token_response.json() if token_response.headers.get('content-type', '').startswith('application/json') else token_response.text
                return Response(
                    {'detail': f'Failed to exchange authorization code: {error_detail}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            token_data_response = token_response.json()
            access_token = token_data_response.get('access_token')
            
            if not access_token:
                return Response(
                    {'detail': 'Access token not found in response'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # アクセストークンでユーザー情報を取得
            user_info_headers = {
                'Authorization': f'Bearer {access_token}',
            }
            
            user_info_response = requests.get(
                user_info_url,
                headers=user_info_headers,
                timeout=10
            )
            
            if user_info_response.status_code != 200:
                error_detail = user_info_response.json() if user_info_response.headers.get('content-type', '').startswith('application/json') else user_info_response.text
                return Response(
                    {'detail': f'Failed to get user info: {error_detail}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            user_data = user_info_response.json()
            
            # ユーザー情報を取得
            oauth_id = user_data.get('id', '')
            email = user_data.get('email', '')
            display_name = user_data.get('name', '')
            picture = user_data.get('picture', '')  # プロフィール画像URL
            
            if not oauth_id:
                return Response(
                    {'detail': 'User ID not found in response'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not email:
                return Response(
                    {'detail': 'Email is required for Google OAuth'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # 表示名が提供されていない場合はemailのローカル部分を使用
            if not display_name:
                display_name = email.split('@')[0] if email else f'google_user_{oauth_id}'
            
            # ユーザーを取得または作成
            user, is_new_user = self._get_or_create_user(oauth_id, email, display_name, 'google')
            
            # プロフィール画像がある場合は新規ユーザーの場合のみ更新
            if picture and is_new_user:
                profile, _ = UserProfile.objects.get_or_create(user=user)
                if not profile.icon_url or profile.icon_url != picture:
                    profile.icon_url = picture
                    profile.save(update_fields=['icon_url', 'updated_at'])
            
            # JWTトークンを生成
            refresh = RefreshToken.for_user(user)
            
            # ユーザーデータを取得
            user_data = UserSerializer(user).data
            
            # 新規ユーザーの場合、ディスプレイネームが未設定かデフォルト値かをチェック
            needs_display_name_setup = False
            if is_new_user:
                profile = getattr(user, 'profile', None)
                if profile:
                    # デフォルト値（emailのローカル部分やusername）と一致する場合は設定が必要
                    default_display_name = email.split('@')[0] if email else user.username
                    if not profile.display_name or profile.display_name == default_display_name:
                        needs_display_name_setup = True
            
            # セキュリティ対策: JWTトークンをCookieに保存（レスポンスボディから削除）
            resp = Response(
                {
                    'user': user_data,
                    'is_new_user': is_new_user,
                    'needs_display_name_setup': needs_display_name_setup,
                },
                status=status.HTTP_200_OK,
            )
            
            # ゲストトークンを削除（通常ユーザーでログインするため）
            resp.set_cookie('guest_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
            
            # Cookieにトークンを保存
            set_jwt_cookies(resp, refresh)
            
            return resp
            
        except requests.exceptions.RequestException as e:
            return Response(
                {'detail': f'Failed to communicate with Google API: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            return Response(
                {'detail': f'An error occurred: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GoogleOAuthView(OAuthBaseView):
    """Google OAuth認証（モック・後方互換性のため残す）"""
    provider_name = 'google'

    def post(self, request):
        """Google OAuth認証（モック）"""
        # モック実装：リクエストボディからOAuthトークンとユーザー情報を受け取る
        # 実際の実装では、GoogleOAuthCallbackViewを使用することを推奨
        oauth_token = request.data.get('oauth_token') or request.data.get('id_token')
        email = request.data.get('email')
        display_name = request.data.get('name') or request.data.get('display_name')
        oauth_id = request.data.get('sub') or request.data.get('id') or secrets.token_hex(8)
        
        if not email:
            return Response({'detail': 'email is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # ユーザーを取得または作成
        user, is_new_user = self._get_or_create_user(oauth_id, email, display_name, 'google')
        
        # JWTトークンを生成
        refresh = RefreshToken.for_user(user)
        
        # ユーザーデータを取得
        user_data = UserSerializer(user).data
        
        # 新規ユーザーの場合、ディスプレイネームが未設定かデフォルト値かをチェック
        needs_display_name_setup = False
        if is_new_user:
            profile = getattr(user, 'profile', None)
            if profile:
                # デフォルト値（emailのローカル部分やusername）と一致する場合は設定が必要
                default_display_name = email.split('@')[0] if email else user.username
                if not profile.display_name or profile.display_name == default_display_name:
                    needs_display_name_setup = True
        
        # セキュリティ対策: JWTトークンをCookieに保存（レスポンスボディから削除）
        resp = Response(
            {
                'user': user_data,
                'is_new_user': is_new_user,
                'needs_display_name_setup': needs_display_name_setup,
            },
            status=status.HTTP_200_OK,
        )
        
        # ゲストトークンを削除（通常ユーザーでログインするため）
        # delete_cookieはsecureパラメータをサポートしていないため、空の値で上書きして削除
        resp.set_cookie('guest_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
        
        # Cookieにトークンを保存
        set_jwt_cookies(resp, refresh)
        
        return resp


class AppleOAuthView(OAuthBaseView):
    provider_name = 'apple'

    def post(self, request):
        """Apple OAuth認証（モック）"""
        # モック実装：リクエストボディからOAuthトークンとユーザー情報を受け取る
        oauth_token = request.data.get('oauth_token') or request.data.get('id_token')
        email = request.data.get('email')
        display_name = request.data.get('name') or request.data.get('display_name')
        oauth_id = request.data.get('sub') or request.data.get('user') or secrets.token_hex(8)
        
        if not email:
            return Response({'detail': 'email is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # ユーザーを取得または作成
        user, is_new_user = self._get_or_create_user(oauth_id, email, display_name, 'apple')
        
        # JWTトークンを生成
        refresh = RefreshToken.for_user(user)
        
        # ユーザーデータを取得
        user_data = UserSerializer(user).data
        
        # 新規ユーザーの場合、ディスプレイネームが未設定かデフォルト値かをチェック
        needs_display_name_setup = False
        if is_new_user:
            profile = getattr(user, 'profile', None)
            if profile:
                # デフォルト値（emailのローカル部分やusername）と一致する場合は設定が必要
                default_display_name = email.split('@')[0] if email else user.username
                if not profile.display_name or profile.display_name == default_display_name:
                    needs_display_name_setup = True
        
        # セキュリティ対策: JWTトークンをCookieに保存（レスポンスボディから削除）
        resp = Response(
            {
                'user': user_data,
                'is_new_user': is_new_user,
                'needs_display_name_setup': needs_display_name_setup,
            },
            status=status.HTTP_200_OK,
        )
        
        # ゲストトークンを削除（通常ユーザーでログインするため）
        # delete_cookieはsecureパラメータをサポートしていないため、空の値で上書きして削除
        resp.set_cookie('guest_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
        
        # Cookieにトークンを保存
        set_jwt_cookies(resp, refresh)
        
        return resp


class XOAuthAuthorizeView(APIView):
    """X (Twitter) OAuth認証URLを生成"""
    permission_classes = [AllowAny]

    def get(self, request):
        """X (Twitter) OAuth認証URLを生成"""
        # 環境変数から設定を取得
        client_id = settings.X_OAUTH_CLIENT_ID
        redirect_uri = settings.X_OAUTH_REDIRECT_URI
        authorize_url = settings.X_OAUTH_AUTHORIZE_URL
        
        # クライアントIDが設定されていない場合はエラー
        if not client_id:
            return Response(
                {'detail': 'X OAuth is not configured. Please set X_OAUTH_CLIENT_ID in environment variables.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # stateパラメータを生成（CSRF対策）
        state = secrets.token_urlsafe(32)
        
        # code_challengeとcode_verifierを生成（PKCE）
        code_verifier = secrets.token_urlsafe(32)
        # code_challengeはcode_verifierからSHA256ハッシュを計算
        code_challenge_bytes = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(code_challenge_bytes).decode('utf-8').rstrip('=')
        code_challenge_method = 'S256'  # X (Twitter) は 'S256' を推奨
        
        # 認証URLを構築
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'scope': 'tweet.read users.read offline.access',
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': code_challenge_method,
        }
        
        auth_url = f"{authorize_url}?{urllib.parse.urlencode(params)}"
        
        # セッションにstateとcode_verifierを保存（実際の実装ではセッションまたはRedisを使用）
        # ここでは簡易的にレスポンスに含める（実際の実装ではセッションを使用）
        return Response({
            'authorize_url': auth_url,
            'state': state,
            'code_verifier': code_verifier,  # 実際の実装ではセッションに保存
        }, status=status.HTTP_200_OK)


class XOAuthCallbackView(OAuthBaseView):
    """X (Twitter) OAuth認証コールバック"""
    provider_name = 'x'

    def post(self, request):
        """X (Twitter) OAuth認証コードを処理"""
        code = request.data.get('code')
        state = request.data.get('state')
        code_verifier = request.data.get('code_verifier')  # 実際の実装ではセッションから取得
        
        if not code:
            return Response(
                {'detail': 'Authorization code is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 環境変数から設定を取得
        client_id = settings.X_OAUTH_CLIENT_ID
        client_secret = settings.X_OAUTH_CLIENT_SECRET
        redirect_uri = settings.X_OAUTH_REDIRECT_URI
        token_url = settings.X_OAUTH_TOKEN_URL
        user_info_url = settings.X_OAUTH_USER_INFO_URL
        
        # クライアントIDとシークレットが設定されていない場合はエラー
        if not client_id or not client_secret:
            return Response(
                {'detail': 'X OAuth is not configured. Please set X_OAUTH_CLIENT_ID and X_OAUTH_CLIENT_SECRET in environment variables.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        try:
            # 認証コードをアクセストークンに交換
            token_data = {
                'code': code,
                'grant_type': 'authorization_code',
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'code_verifier': code_verifier,
            }
            
            # Basic認証ヘッダーを生成
            credentials = f"{client_id}:{client_secret}"
            encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': f'Basic {encoded_credentials}',
            }
            
            token_response = requests.post(
                token_url,
                data=token_data,
                headers=headers,
                timeout=10
            )
            
            if token_response.status_code != 200:
                error_detail = token_response.json() if token_response.headers.get('content-type', '').startswith('application/json') else token_response.text
                return Response(
                    {'detail': f'Failed to exchange authorization code: {error_detail}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            token_data_response = token_response.json()
            access_token = token_data_response.get('access_token')
            
            if not access_token:
                return Response(
                    {'detail': 'Access token not found in response'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # アクセストークンでユーザー情報を取得
            user_info_headers = {
                'Authorization': f'Bearer {access_token}',
            }
            
            user_info_params = {
                'user.fields': 'id,name,username,profile_image_url,description',
            }
            
            user_info_response = requests.get(
                user_info_url,
                headers=user_info_headers,
                params=user_info_params,
                timeout=10
            )
            
            if user_info_response.status_code != 200:
                error_detail = user_info_response.json() if user_info_response.headers.get('content-type', '').startswith('application/json') else user_info_response.text
                return Response(
                    {'detail': f'Failed to get user info: {error_detail}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            user_info_data = user_info_response.json()
            user_data = user_info_data.get('data', {})
            
            # ユーザー情報を取得
            oauth_id = user_data.get('id', '')
            display_name = user_data.get('name', '')
            screen_name = user_data.get('username', '')
            email = ''  # X (Twitter) API v2ではemailは取得できない（追加の申請が必要）
            
            if not oauth_id:
                return Response(
                    {'detail': 'User ID not found in response'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # 表示名が提供されていない場合はscreen_nameを使用
            if not display_name:
                display_name = screen_name or f'x_user_{oauth_id}'
            
            # ユーザーを取得または作成（emailは空でもOK）
            user, is_new_user = self._get_or_create_user(oauth_id, email, display_name, 'x')
            
            # JWTトークンを生成
            refresh = RefreshToken.for_user(user)
            
            # ユーザーデータを取得
            user_data = UserSerializer(user).data
            
            # 新規ユーザーの場合、ディスプレイネームが未設定かデフォルト値かをチェック
            needs_display_name_setup = False
            if is_new_user:
                profile = getattr(user, 'profile', None)
                if profile:
                    # デフォルト値（screen_nameやusername）と一致する場合は設定が必要
                    default_display_name = display_name if display_name else screen_name if screen_name else user.username
                    if not profile.display_name or profile.display_name == default_display_name:
                        needs_display_name_setup = True
            
            # セキュリティ対策: JWTトークンをCookieに保存（レスポンスボディから削除）
            resp = Response(
                {
                    'user': user_data,
                    'is_new_user': is_new_user,
                    'needs_display_name_setup': needs_display_name_setup,
                },
                status=status.HTTP_200_OK,
            )
            
            # ゲストトークンを削除（通常ユーザーでログインするため）
            resp.set_cookie('guest_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
            
            # Cookieにトークンを保存
            set_jwt_cookies(resp, refresh)
            
            return resp
            
        except requests.exceptions.RequestException as e:
            return Response(
                {'detail': f'Failed to communicate with X API: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            return Response(
                {'detail': f'An error occurred: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class XOAuthView(OAuthBaseView):
    """X (Twitter) OAuth認証（モック・後方互換性のため残す）"""
    provider_name = 'x'

    def post(self, request):
        """X (Twitter) OAuth認証（モック）"""
        # モック実装：リクエストボディからOAuthトークンとユーザー情報を受け取る
        # 実際の実装では、XOAuthCallbackViewを使用することを推奨
        oauth_token = request.data.get('oauth_token') or request.data.get('access_token')
        oauth_token_secret = request.data.get('oauth_token_secret')
        email = request.data.get('email') or ''  # emailは任意
        display_name = request.data.get('name') or request.data.get('screen_name')
        oauth_id = request.data.get('user_id') or request.data.get('id_str') or secrets.token_hex(8)
        screen_name = request.data.get('screen_name') or f'x_user_{oauth_id}'
        
        # 表示名が提供されていない場合はscreen_nameを使用
        if not display_name:
            display_name = screen_name
        
        # ユーザーを取得または作成（emailは空でもOK）
        user, is_new_user = self._get_or_create_user(oauth_id, email, display_name, 'x')
        
        # JWTトークンを生成
        refresh = RefreshToken.for_user(user)
        
        # ユーザーデータを取得
        user_data = UserSerializer(user).data
        
        # 新規ユーザーの場合、ディスプレイネームが未設定かデフォルト値かをチェック
        needs_display_name_setup = False
        if is_new_user:
            profile = getattr(user, 'profile', None)
            if profile:
                # デフォルト値（screen_nameやusername）と一致する場合は設定が必要
                default_display_name = display_name if display_name else screen_name if screen_name else user.username
                if not profile.display_name or profile.display_name == default_display_name:
                    needs_display_name_setup = True
        
        # セキュリティ対策: JWTトークンをCookieに保存（レスポンスボディから削除）
        resp = Response(
            {
                'user': user_data,
                'is_new_user': is_new_user,
                'needs_display_name_setup': needs_display_name_setup,
            },
            status=status.HTTP_200_OK,
        )
        
        # ゲストトークンを削除（通常ユーザーでログインするため）
        # delete_cookieはsecureパラメータをサポートしていないため、空の値で上書きして削除
        resp.set_cookie('guest_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
        
        # Cookieにトークンを保存
        set_jwt_cookies(resp, refresh)
        
        return resp

