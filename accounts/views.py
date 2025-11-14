from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.conf import settings
from django.utils import timezone
from PIL import Image
import os, time

from django.contrib.auth.models import User
from .serializers import LoginSerializer, SignupSerializer, UserSerializer, UserUpdateSerializer, NotificationSerializer
from django.core import signing
import secrets
from django.shortcuts import get_object_or_404
from .models import UserProfile, UserMute, Notification, EmailVerificationToken, EmailVerificationAttempt
from .utils import send_verification_email, get_client_ip, decode_guest_token, get_or_create_guest_user, get_guest_token_from_request, set_jwt_cookies, transfer_guest_user_data
from app.utils import delete_media_file_by_url


class SignupView(APIView):
    permission_classes = [AllowAny]

    def _get_guest_user(self, request):
        """ゲストユーザーを取得（既存のみ、新規作成はしない）"""
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def post(self, request):
        # ステップ1: 新規ユーザーを作成（email, passwordを設定）
        # ゲストユーザーは保持し、ステップ3でデータを統合する
        
        # フォームデータから通常のフィールドを取得
        email = request.data.get('email', '').strip()
        data = {
            'email': email,
            'display_name': request.data.get('display_name'),
            'password': request.data.get('password'),
        }
        
        # メールアドレスの重複チェック（認証中の場合は再送信を実行）
        if email:
            try:
                existing_user = User.objects.get(email=email)
                # 認証中のユーザーの場合（is_active=False）
                if not existing_user.is_active:
                    # 再送信を実行
                    import logging
                    logger = logging.getLogger(__name__)
                    try:
                        verification_token = EmailVerificationToken.create_token(existing_user)
                        send_verification_email(existing_user, verification_token.token)
                        logger.info(f"Verification email resent for existing unverified user: {existing_user.id}, email: {email}")
                    except Exception as e:
                        logger.error(f"Failed to resend verification email for user: {existing_user.id}, email: {email}: {e}", exc_info=True)
                    
                    # JWTトークンを発行してクッキーに設定
                    refresh = RefreshToken.for_user(existing_user)
                    resp = Response(
                        {
                            'user': UserSerializer(existing_user).data,
                            'message': '認証メールを再送信しました。メール内のリンクをクリックして認証を完了してください。',
                            'email_verification_required': True,
                        },
                        status=status.HTTP_200_OK,
                    )
                    set_jwt_cookies(resp, refresh)
                    return resp
                # 認証済みユーザーの場合（is_active=True）
                else:
                    # 使用済みエラーを返す
                    return Response(
                        {'detail': 'このメールアドレスは既に使用されています。'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except User.DoesNotExist:
                # 既存ユーザーが存在しない場合は通常通り新規作成
                pass
        
        # 常に新規ユーザーを作成（ゲストユーザーは保持）
        serializer = SignupSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # アイコンのアップロードはステップ3で処理するため、ステップ1ではスキップ
        
        # IPアドレスを取得して保存
        client_ip = get_client_ip(request)
        if client_ip:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if not profile.registration_ip:
                # 登録時のIPアドレスが未設定の場合のみ保存
                profile.registration_ip = client_ip
                profile.last_login_ip = client_ip  # 初回登録時は同じIP
                profile.save(update_fields=['registration_ip', 'last_login_ip', 'updated_at'])
            elif not profile.last_login_ip:
                # 登録時のIPは設定済みだが、ログインIPが未設定の場合
                profile.last_login_ip = client_ip
                profile.save(update_fields=['last_login_ip', 'updated_at'])

        # メール認証トークンを生成して送信
        try:
            verification_token = EmailVerificationToken.create_token(user)
            send_verification_email(user, verification_token.token)
        except Exception as e:
            # メール送信に失敗してもユーザー作成は成功として扱う
            # （後で再送信できるため）
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send verification email: {e}", exc_info=True)

        # JWTトークンを発行してクッキーに設定（メール認証前でもトークンを発行）
        refresh = RefreshToken.for_user(user)
        
        # レスポンスを作成
        resp = Response(
            {
                'user': UserSerializer(user).data,
                'message': 'アカウントを作成しました。メールアドレス認証のメールを送信しました。メール内のリンクをクリックして認証を完了してください。',
                'email_verification_required': True,
            },
            status=status.HTTP_201_CREATED,
        )
        
        # JWTトークンをCookieに保存
        set_jwt_cookies(resp, refresh)
        
        # ゲストトークンは削除しない（ステップ3で引き継ぎを行うため保持）
        
        return resp


class MeView(APIView):
    def get_permissions(self):
        if self.request.method == 'GET':
            # GET はゲストユーザーも許可
            return [AllowAny()]
        # PATCH はゲストユーザーも許可（AllowAnyでゲストユーザーも編集可能）
        return [AllowAny()]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（存在しない場合は作成）"""
        # JWTトークンがある場合（認証済みユーザーがいる場合）は、ゲストユーザーを作成しない
        # Cookieにaccess_tokenまたはrefresh_tokenがある場合、認証済みユーザーとして扱う
        access_token = request.COOKIES.get('access_token')
        refresh_token = request.COOKIES.get('refresh_token')
        if access_token or refresh_token:
            # JWTトークンがある場合は、ゲストユーザーを作成しない
            return None
        return get_or_create_guest_user(request, create_if_not_exists=True)

    def _get_user(self, request):
        """認証済みユーザーまたはゲストユーザーを取得"""
        user = request.user if (request.user and request.user.is_authenticated) else None
        if not user:
            user = self._resolve_guest_user(request)
        return user

    def get(self, request):
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        user = self._get_user(request)
        if not user:
            return Response({'detail': 'ユーザーが見つかりません。'}, status=status.HTTP_404_NOT_FOUND)
        return Response(UserSerializer(user).data)

    def patch(self, request):
        # ゲストユーザーも編集可能
        # ステップ3: 表示名とアイコンを更新し、ゲストユーザーのデータを統合
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        user = self._get_user(request)
        if not user:
            return Response({'detail': 'ユーザーが見つかりません。'}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserUpdateSerializer(data=request.data, context={'request': request, 'user': user})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # ゲストユーザーの場合は、ゲストトークンを削除しない（再生成を防ぐ）
        # 通常ユーザーの場合のみ、ゲストトークンを削除
        is_guest = user.username and user.username.startswith('Anonium-')
        if not is_guest:
            # ステップ3完了時：ゲストユーザーのデータを統合
            # メールアドレス入力ミスで作成された未認証ユーザーは、トークンが切れたら無効になるため統合不要
            guest_user = get_or_create_guest_user(request, create_if_not_exists=False)
            if guest_user and guest_user.id != user.id:
                transfer_guest_user_data(guest_user, user)
            
            # ゲストトークンを削除
            guest_token = get_guest_token_from_request(request)
            if guest_token:
                resp = Response(UserSerializer(user).data)
                resp.set_cookie('guest_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
            else:
                resp = Response(UserSerializer(user).data)
        else:
            # ゲストユーザーの場合は、ゲストトークンを保持
            resp = Response(UserSerializer(user).data)
        
        # キャッシュ削除
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/accounts/{user.username}/*')
        
        return resp


class UserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, username: str):
        # 本人以外は 404 を返して非公開化
        if not request.user or request.user.username != username:
            return Response({'detail': 'not found'}, status=status.HTTP_404_NOT_FOUND)
        return Response(UserSerializer(request.user).data)


class UploadUserIconView(APIView):
    permission_classes = [AllowAny]

    def _get_user(self, request):
        """認証済みユーザーまたはゲストユーザーを取得"""
        user = request.user if (request.user and request.user.is_authenticated) else None
        if not user:
            # ゲストユーザーを解決（存在しない場合は作成）
            from .utils import get_or_create_guest_user
            user = get_or_create_guest_user(request, create_if_not_exists=True)
        return user

    def post(self, request):
        # ゲストユーザーも編集可能
        user = self._get_user(request)
        if not user:
            return Response({'detail': 'ユーザーが見つかりません。'}, status=status.HTTP_404_NOT_FOUND)

        file = request.FILES.get('image')
        if not file:
            return Response({'detail': 'image file required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            image = Image.open(file)
        except Exception:
            return Response({'detail': 'invalid image'}, status=status.HTTP_400_BAD_REQUEST)

        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        W, H = image.size

        def _as_float(name: str):
            v = request.data.get(name)
            if v is None:
                return None
            try:
                return float(v)
            except Exception:
                return None

        cx = _as_float('crop_x')
        cy = _as_float('crop_y')
        cw = _as_float('crop_w')
        ch = _as_float('crop_h')

        if cw and ch:
            if cw <= 1 and ch <= 1:
                px = int((cx or 0) * W)
                py = int((cy or 0) * H)
                pw = int(cw * W)
                ph = int(ch * H)
            else:
                px = int(cx or 0)
                py = int(cy or 0)
                pw = int(cw)
                ph = int(ch)
            px = max(0, min(px, W - 1))
            py = max(0, min(py, H - 1))
            pw = max(1, min(pw, W - px))
            ph = max(1, min(ph, H - py))
            box = (px, py, px + pw, py + ph)
        else:
            s = min(W, H)
            x0 = (W - s) // 2
            y0 = (H - s) // 2
            box = (x0, y0, x0 + s, y0 + s)

        try:
            cropped = image.crop(box)
        except Exception:
            return Response({'detail': 'failed to crop'}, status=status.HTTP_400_BAD_REQUEST)

        # resize to 256x256 square
        cropped = cropped.resize((256, 256), Image.LANCZOS)

        folder = 'users/icons'
        ts = int(time.time())
        filename = f"u-{user.id}-{ts}.jpg"
        
        try:
            from app.utils import save_image_locally_or_gcs
            abs_url = save_image_locally_or_gcs(cropped, folder, filename, request)
        except Exception as e:
            return Response({'detail': 'failed to save'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        profile, _ = UserProfile.objects.get_or_create(user=user)
        previous_url = profile.icon_url
        profile.icon_url = abs_url
        profile.save(update_fields=['icon_url', 'updated_at'])

        if previous_url and previous_url != abs_url:
            delete_media_file_by_url(previous_url)

        # キャッシュ削除: ユーザー詳細、ユーザープロフィール
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/accounts/{user.username}/*')
        invalidate_cache(pattern='/api/accounts/me/*')
        
        return Response({'icon_url': abs_url})


class MuteListView(APIView):
    permission_classes = [AllowAny]

    def _resolve_guest_user(self, request):
        """ゲストユーザーを解決（既存のみ、新規作成はしない）"""
        return get_or_create_guest_user(request, create_if_not_exists=False)

    def get(self, request):
        # ユーザーを取得（認証済みユーザーまたはゲストユーザー）
        user = request.user if (request.user and request.user.is_authenticated) else None
        if not user:
            user = self._resolve_guest_user(request)
        if not user:
            return Response({'results': []})
        
        mutes = UserMute.objects.filter(user=user).select_related('target')
        data = [
            {
                'id': m.target.id,
                'username': m.target.username,
                'created_at': m.created_at,
            }
            for m in mutes
        ]
        return Response({'results': data})


class MuteCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        username = (request.data.get('target_username') or '').strip()
        target_id = request.data.get('target_id')
        target = None
        if username:
            target = get_object_or_404(User, username=username)
        elif target_id:
            target = get_object_or_404(User, pk=target_id)
        else:
            return Response({'detail': 'target_username か target_id が必要です。'}, status=status.HTTP_400_BAD_REQUEST)
        if target.id == request.user.id:
            return Response({'detail': '自分自身はミュートできません。'}, status=status.HTTP_400_BAD_REQUEST)
        UserMute.objects.get_or_create(user=request.user, target=target)
        
        # キャッシュ削除: ユーザーのミュート一覧、投稿一覧（ミュートされたユーザーの投稿が非表示になる）
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/accounts/{request.user.username}/*')
        invalidate_cache(pattern=f'/api/accounts/{request.user.username}/mutes/*')
        invalidate_cache(pattern='/api/posts/*')  # ミュートされたユーザーの投稿が非表示になる
        invalidate_cache(pattern='/api/posts/trending*')
        
        return Response({'detail': f'{getattr(target, "username", str(target.id))} をミュートしました。'}, status=status.HTTP_201_CREATED)


class MuteDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, username: str):
        target = get_object_or_404(User, username=username)
        UserMute.objects.filter(user=request.user, target=target).delete()
        
        # キャッシュ削除: ユーザーのミュート一覧、投稿一覧（ミュート解除されたユーザーの投稿が表示される）
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/accounts/{request.user.username}/*')
        invalidate_cache(pattern=f'/api/accounts/{request.user.username}/mutes/*')
        invalidate_cache(pattern='/api/posts/*')  # ミュート解除されたユーザーの投稿が表示される
        invalidate_cache(pattern='/api/posts/trending*')
        
        return Response(status=status.HTTP_204_NO_CONTENT)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']

        # IPアドレスを取得して保存
        client_ip = get_client_ip(request)
        if client_ip:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.last_login_ip = client_ip
            profile.save(update_fields=['last_login_ip', 'updated_at'])

        # メール認証が完了していない場合でもJWTトークンを発行
        # （サインアップと統一するため、認証コード入力ページに進めるようにする）
        refresh = RefreshToken.for_user(user)
        
        # セキュリティ対策: JWTトークンをCookieに保存（レスポンスボディから削除）
        resp = Response(
            {
                'user': UserSerializer(user).data,
                'email_verification_required': not user.is_active,
                'email': user.email if user.email else '',
            },
            status=status.HTTP_200_OK,
        )
        
        # ゲストトークンを削除（通常ユーザーでログインするため）
        # delete_cookieはsecureパラメータをサポートしていないため、空の値で上書きして削除
        resp.set_cookie('guest_token', '', max_age=0, path='/', samesite='Lax', secure=not settings.DEBUG, httponly=True)
        
        # Cookieにトークンを保存（メール認証が未完了でもトークンを発行）
        set_jwt_cookies(resp, refresh)
        
        # メール認証が未完了の場合、認証コードを再送信
        if not user.is_active:
            try:
                # 既存の未使用トークンを無効化して新しいトークンを生成
                verification_token = EmailVerificationToken.create_token(user)
                send_verification_email(user, verification_token.token)
            except Exception as e:
                # メール送信に失敗してもログイン処理は続行
                # （send_verification_email内でログが記録される）
                pass
        
        return resp


class GuestIssueView(APIView):
    permission_classes = [AllowAny]
    TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days (短縮: 30日→7日)
    TOKEN_ROTATE_SECONDS = 60 * 60 * 24 * 1  # Rotate signature every 1 day (短縮: 7日→1日)

    def post(self, request):
        # JWTトークンがある場合（ログイン済みユーザー）は、ゲストトークンを発行しない
        access_token = request.COOKIES.get('access_token')
        refresh_token = request.COOKIES.get('refresh_token')
        if access_token or refresh_token:
            # JWTトークンがある場合は、ゲストトークンを発行せず、エラーを返す
            return Response(
                {'detail': 'ログイン済みユーザーのため、ゲストトークンは発行できません。'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        token = get_guest_token_from_request(request)
        gid, issued_at = decode_guest_token(token)

        now_ts = int(time.time())
        token_updated = False
        if not gid:
            # ゲストトークンが存在しない場合、新規発行
            gid = secrets.token_urlsafe(6)  # ~16 chars URL-safe string (~96bit)
            issued_at = now_ts
            token_updated = True
        else:
            if issued_at is None:
                issued_at = now_ts
                token_updated = True
            age = now_ts - issued_at
            if age >= self.TOKEN_TTL_SECONDS:
                # 期限切れ扱い: 新しいgidで再発行
                gid = secrets.token_urlsafe(6)
                issued_at = now_ts
                token_updated = True
            elif age >= self.TOKEN_ROTATE_SECONDS:
                # 同じgidで署名だけ更新（発行時刻をリフレッシュ）
                issued_at = now_ts
                token_updated = True

        payload = {'gid': gid, 'iat': issued_at}
        token = signing.dumps(payload, salt='guest')

        # ゲストユーザーを取得または作成し、IPアドレスを保存
        uname = f"Anonium-{gid}"
        guest_user, created = User.objects.get_or_create(
            username=uname,
            defaults={'email': '', 'is_active': True}
        )
        
        # IPアドレスを取得して保存
        client_ip = get_client_ip(request)
        if client_ip:
            profile, profile_created = UserProfile.objects.get_or_create(user=guest_user)
            if created or profile_created:
                # 新規作成時は登録IPとして保存
                if not profile.registration_ip:
                    profile.registration_ip = client_ip
                if not profile.last_login_ip:
                    profile.last_login_ip = client_ip
                profile.save(update_fields=['registration_ip', 'last_login_ip', 'updated_at'])
            else:
                # 既存ユーザーの場合はログインIPとして更新
                profile.last_login_ip = client_ip
                profile.save(update_fields=['last_login_ip', 'updated_at'])

        # セキュリティ対策: トークンをレスポンスボディから削除（Cookieのみで送信）
        resp = Response({'gid': gid}, status=status.HTTP_200_OK)
        cookie_kwargs = {
            'httponly': True,
            'samesite': 'Lax',
            'secure': not settings.DEBUG,
            'path': '/',
            'max_age': self.TOKEN_TTL_SECONDS,
        }
        # 常にクッキーを設定（有効期限を延長するため、トークンが更新されていない場合でも設定）
        resp.set_cookie('guest_token', token, **cookie_kwargs)
        return resp


class NotificationListView(APIView):
    """通知一覧を取得するAPI"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        # 認証済みユーザーの通知を取得（最新順）
        queryset = Notification.objects.filter(
            recipient=request.user
        ).select_related(
            'actor', 'actor__profile', 'post', 'comment', 'community'
        ).order_by('-created_at')
        
        # 未読のみをフィルタする場合
        unread_only = request.query_params.get('unread_only', '').lower()
        if unread_only in ('true', '1'):
            queryset = queryset.filter(is_read=False)
        
        # 件数制限（デフォルト50件）
        limit = request.query_params.get('limit', '50')
        try:
            limit = int(limit)
            limit = max(1, min(limit, 100))  # 1-100件の範囲
            queryset = queryset[:limit]
        except (ValueError, TypeError):
            queryset = queryset[:50]
        
        # シリアライズして配列形式で返す
        serializer = NotificationSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class NotificationUnreadCountView(APIView):
    """未読通知数を取得するAPI"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # 認証済みユーザーの未読通知数を取得
        unread_count = Notification.objects.filter(
            recipient=request.user,
            is_read=False
        ).count()
        
        return Response({
            'unread_count': unread_count
        }, status=status.HTTP_200_OK)


class NotificationMarkAllReadView(APIView):
    """すべての通知を既読にするAPI"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # 認証済みユーザーの未読通知をすべて既読にする
        updated_count = Notification.objects.filter(
            recipient=request.user,
            is_read=False
        ).update(is_read=True)
        
        return Response({
            'detail': f'{updated_count}件の通知を既読にしました。',
            'updated_count': updated_count
        }, status=status.HTTP_200_OK)


class EmailVerificationView(APIView):
    """メールアドレス認証API（セキュリティ強化版）"""
    permission_classes = [AllowAny]
    
    # セキュリティ設定
    MAX_ATTEMPTS = 5  # 最大試行回数
    LOCK_DURATION_MINUTES = 15  # ロック期間（分）
    MAX_ATTEMPTS_PER_HOUR = 10  # 1時間あたりの最大試行回数

    def _verify_token(self, token: str, ip_address: str):
        """トークンを検証してユーザーを有効化（セキュリティチェック付き）"""
        import logging
        logger = logging.getLogger(__name__)
        
        if not token:
            logger.warning(f"Email verification attempt with empty token from IP: {ip_address}")
            return None, '認証コードを入力してください。'
        
        # トークンの形式をチェック（6桁の数字のみ）
        if not token.isdigit() or len(token) != 6:
            logger.warning(f"Email verification attempt with invalid token format from IP: {ip_address}")
            return None, '認証コードの形式が正しくありません。'
        
        # IPアドレスベースの試行回数制限をチェック
        attempt = EmailVerificationAttempt.get_or_create_attempt(ip_address=ip_address, user=None)
        if attempt.is_locked():
            remaining_minutes = int((attempt.locked_until - timezone.now()).total_seconds() / 60)
            logger.warning(f"Email verification attempt blocked (locked) from IP: {ip_address}, remaining: {remaining_minutes} minutes")
            return None, f'試行回数が上限に達しました。{remaining_minutes}分後に再度お試しください。'
        
        # トークンを検索（セキュリティ: 存在しない場合と無効な場合で同じメッセージを返す）
        try:
            verification_token = EmailVerificationToken.objects.get(token=token)
        except EmailVerificationToken.DoesNotExist:
            # トークンが存在しない場合、試行回数を増やす
            attempt.increment_attempt(max_attempts=self.MAX_ATTEMPTS, lock_duration_minutes=self.LOCK_DURATION_MINUTES)
            logger.warning(f"Email verification attempt with non-existent token from IP: {ip_address}, attempts: {attempt.attempt_count}")
            # セキュリティ: 情報漏洩を防ぐため、存在しないトークンも無効なトークンと同じメッセージを返す
            return None, '認証コードが正しくないか、有効期限が切れています。'
        
        # ユーザーを取得
        user = verification_token.user
        
        # ユーザー固有の試行回数制限をチェック
        user_attempt = EmailVerificationAttempt.get_or_create_attempt(ip_address=ip_address, user=user)
        if user_attempt.is_locked():
            remaining_minutes = int((user_attempt.locked_until - timezone.now()).total_seconds() / 60)
            logger.warning(f"Email verification attempt blocked (user locked) for user: {user.id}, IP: {ip_address}, remaining: {remaining_minutes} minutes")
            return None, f'試行回数が上限に達しました。{remaining_minutes}分後に再度お試しください。'
        
        # トークンの有効性をチェック
        if not verification_token.is_valid():
            # 無効なトークン（期限切れまたは使用済み）の場合、試行回数を増やす
            attempt.increment_attempt(max_attempts=self.MAX_ATTEMPTS, lock_duration_minutes=self.LOCK_DURATION_MINUTES)
            user_attempt.increment_attempt(max_attempts=self.MAX_ATTEMPTS, lock_duration_minutes=self.LOCK_DURATION_MINUTES)
            logger.warning(f"Email verification attempt with invalid/expired token for user: {user.id}, IP: {ip_address}")
            # セキュリティ: 情報漏洩を防ぐため、期限切れ/使用済みも同じメッセージを返す
            return None, '認証コードが正しくないか、有効期限が切れています。'
        
        # 認証成功
        # ユーザーを有効化
        user.is_active = True
        user.save(update_fields=['is_active'])
        
        # トークンを使用済みにマーク
        verification_token.is_used = True
        verification_token.save(update_fields=['is_used'])
        
        # 試行回数をリセット
        attempt.reset_attempts()
        user_attempt.reset_attempts()
        
        logger.info(f"Email verification successful for user: {user.id}, IP: {ip_address}")
        
        return user, None

    def post(self, request):
        """POSTリクエスト（フロントエンドからのAPI呼び出し用）"""
        token = request.data.get('token', '').strip()
        ip_address = get_client_ip(request) or '0.0.0.0'
        
        user, error = self._verify_token(token, ip_address)
        
        if error:
            return Response(
                {'detail': error},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # ログイン用のトークンをCookieに保存
        refresh = RefreshToken.for_user(user)
        resp = Response({
            'detail': 'メールアドレスの認証が完了しました。',
            'user': UserSerializer(user).data,
        }, status=status.HTTP_200_OK)
        
        # ゲストトークンは削除しない（ステップ3で引き継ぎを行うため保持）
        
        # Cookieにトークンを保存
        set_jwt_cookies(resp, refresh)
        
        return resp


class ResendVerificationEmailView(APIView):
    """メール認証メール再送信API（セキュリティ強化版）"""
    permission_classes = [AllowAny]
    
    # セキュリティ設定
    MAX_RESENDS_PER_HOUR = 3  # 1時間あたりの最大再送信回数
    MIN_RESEND_INTERVAL_MINUTES = 1  # 再送信の最小間隔（分）

    def post(self, request):
        import logging
        logger = logging.getLogger(__name__)
        
        email = request.data.get('email', '').strip()
        ip_address = get_client_ip(request) or '0.0.0.0'
        
        if not email:
            logger.warning(f"Resend verification email attempt with empty email from IP: {ip_address}")
            return Response(
                {'detail': 'メールアドレスが指定されていません。'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # メールアドレスの形式をチェック
        if '@' not in email:
            logger.warning(f"Resend verification email attempt with invalid email format from IP: {ip_address}")
            return Response(
                {'detail': 'メールアドレスの形式が正しくありません。'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # IPアドレスベースの再送信レート制限をチェック
        # 過去1時間以内の再送信回数を確認
        one_hour_ago = timezone.now() - timezone.timedelta(hours=1)
        recent_resends = EmailVerificationToken.objects.filter(
            user__email=email,
            created_at__gte=one_hour_ago
        ).count()
        
        if recent_resends >= self.MAX_RESENDS_PER_HOUR:
            logger.warning(f"Resend verification email rate limit exceeded for email: {email}, IP: {ip_address}")
            return Response(
                {'detail': '再送信の回数が上限に達しました。しばらくしてから再度お試しください。'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # 最新のトークンの作成時刻を確認（最小間隔チェック）
        latest_token = EmailVerificationToken.objects.filter(
            user__email=email
        ).order_by('-created_at').first()
        
        if latest_token:
            time_since_last_resend = (timezone.now() - latest_token.created_at).total_seconds() / 60
            if time_since_last_resend < self.MIN_RESEND_INTERVAL_MINUTES:
                remaining_seconds = int((self.MIN_RESEND_INTERVAL_MINUTES - time_since_last_resend) * 60)
                logger.warning(f"Resend verification email too soon for email: {email}, IP: {ip_address}, remaining: {remaining_seconds} seconds")
                return Response(
                    {'detail': f'再送信の間隔が短すぎます。{remaining_seconds}秒後に再度お試しください。'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )
        
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # セキュリティ上の理由で、ユーザーが存在しない場合も同じメッセージを返す
            # ただし、ログには記録する
            logger.info(f"Resend verification email request for non-existent email: {email}, IP: {ip_address}")
            return Response({
                'detail': 'メールアドレスが見つからない場合、認証メールを送信しました。',
            }, status=status.HTTP_200_OK)
        
        # 既に認証済みの場合はエラーを返す
        if user.is_active:
            logger.info(f"Resend verification email request for already verified user: {user.id}, IP: {ip_address}")
            return Response(
                {'detail': 'このメールアドレスは既に認証済みです。'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 新しいトークンを生成して送信
        try:
            verification_token = EmailVerificationToken.create_token(user)
            send_verification_email(user, verification_token.token)
            logger.info(f"Verification email resent for user: {user.id}, email: {email}, IP: {ip_address}")
            return Response({
                'detail': '認証メールを再送信しました。',
            }, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Failed to resend verification email for user: {user.id}, email: {email}, IP: {ip_address}: {e}", exc_info=True)
            return Response(
                {'detail': 'メール送信に失敗しました。しばらくしてから再度お試しください。'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
