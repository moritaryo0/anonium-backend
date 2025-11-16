"""メール送信ユーティリティ関数"""

from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.template.loader import render_to_string
from typing import Optional, Tuple
import logging
from django.core import signing
from django.contrib.auth.models import User
from .models import UserProfile
import ipaddress
import uuid
from email.utils import formataddr

logger = logging.getLogger(__name__)


def get_client_ip(request) -> Optional[str]:
    """リクエストからクライアントのIPアドレスを取得
    
    Args:
        request: Djangoのリクエストオブジェクト
        
    Returns:
        Optional[str]: IPアドレス（取得できない/該当なしの場合はNone）
        グローバルIPのみを許可（ローカルネットワーク上のプライベートIPは除外）
    """
    from django.conf import settings
    
    # 候補IPを優先度順に収集
    candidates = []
    # Cloudflare等
    cf_ip = request.META.get('HTTP_CF_CONNECTING_IP')
    if cf_ip:
        candidates.append(cf_ip.strip())
    # プロキシやロードバランサー経由の場合を考慮（左からオリジナルクライアント）
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        for part in x_forwarded_for.split(','):
            ip = part.strip()
            if ip:
                candidates.append(ip)
    # X-Real-IP
    x_real_ip = request.META.get('HTTP_X_REAL_IP')
    if x_real_ip:
        candidates.append(x_real_ip.strip())
    # REMOTE_ADDR
    remote_addr = request.META.get('REMOTE_ADDR')
    if remote_addr:
        candidates.append(remote_addr.strip())

    # グローバルIPのみを許可（ローカルネットワーク上のプライベートIPは除外）
    for ip_str in candidates:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            # グローバルIPのみを許可
            if ip_obj.is_global:
                return ip_str
        except ValueError:
            # 不正なIPはスキップ
            continue
    return None


def decode_guest_token(token: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """署名付きguest_tokenからgidと発行時刻を取り出す"""
    if not token:
        return None, None
    try:
        try:
            data = signing.loads(token, salt='guest')
        except Exception:
            gid = signing.Signer(salt='guest').unsign(token)
            return str(gid), None

        if isinstance(data, dict):
            gid = data.get('gid')
            issued_at = data.get('iat')
        else:
            gid = data
            issued_at = None

        gid_str = str(gid) if gid else None
        issued_int = None
        if issued_at is not None:
            try:
                issued_int = int(issued_at)
            except (TypeError, ValueError):
                issued_int = None
        return gid_str, issued_int
    except Exception:
        return None, None


def get_guest_token_from_request(request) -> Optional[str]:
    """リクエストからゲストトークンを取得（Cookieまたはヘッダーから）
    
    Args:
        request: Djangoのリクエストオブジェクト
        
    Returns:
        Optional[str]: ゲストトークン（取得できない場合はNone）
    """
    # まずCookieから取得を試みる
    token = request.COOKIES.get('guest_token')
    if token:
        return token
    
    # Cookieにない場合はヘッダーから取得を試みる
    token = request.META.get('HTTP_X_GUEST_TOKEN')
    if token:
        return token
    
    return None


def get_or_create_guest_user(request, create_if_not_exists: bool = True) -> Optional[User]:
    """ゲストユーザーを取得または作成し、IPアドレスを保存
    
    Args:
        request: Djangoのリクエストオブジェクト
        create_if_not_exists: ユーザーが存在しない場合に作成するかどうか
        
    Returns:
        Optional[User]: ゲストユーザー（取得/作成できない場合はNone）
    """
    token = get_guest_token_from_request(request)
    gid, _ = decode_guest_token(token)
    if not gid:
        return None
    
    uname = f"Anonium-{gid}"
    user = User.objects.filter(username=uname).first()
    
    if not user and create_if_not_exists:
        # ゲストユーザーが存在しない場合は作成
        user = User.objects.create_user(username=uname, email='', is_active=True)
    
    if user:
        # IPアドレスを取得して保存
        client_ip = get_client_ip(request)
        if client_ip:
            # UserProfileを取得または作成（シグナルで既に作成されている可能性がある）
            profile, profile_created = UserProfile.objects.get_or_create(user=user)
            
            # IPアドレスの更新が必要かどうかを判定
            needs_update = False
            update_fields = []
            
            if profile_created:
                # 新規作成時は登録IPとして保存
                profile.registration_ip = client_ip
                profile.last_login_ip = client_ip
                needs_update = True
                update_fields = ['registration_ip', 'last_login_ip', 'updated_at']
            else:
                # 既存のプロフィールの場合、IPが未設定なら設定する
                # シグナルで作成された場合、registration_ipがNoneの可能性がある
                if not profile.registration_ip:
                    profile.registration_ip = client_ip
                    needs_update = True
                    update_fields.append('registration_ip')
                if not profile.last_login_ip:
                    profile.last_login_ip = client_ip
                    needs_update = True
                    update_fields.append('last_login_ip')
                if needs_update:
                    update_fields.append('updated_at')
            
            if needs_update:
                profile.save(update_fields=update_fields)
    
    return user


def set_jwt_cookies(response, refresh_token_obj):
    """JWTトークンをCookieに保存するヘルパー関数
    
    Args:
        response: Django Responseオブジェクト
        refresh_token_obj: RefreshTokenオブジェクト
    """
    from django.conf import settings
    import logging
    logger = logging.getLogger(__name__)
    
    access_token = str(refresh_token_obj.access_token)
    refresh_token = str(refresh_token_obj)
    
    # アクセストークンをCookieに保存
    access_cookie_kwargs = {
        'httponly': True,
        'samesite': 'Lax',
        'secure': not settings.DEBUG,
        'path': '/',
        'max_age': int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
    }
    response.set_cookie('access_token', access_token, **access_cookie_kwargs)
    logger.debug(f'Set access_token cookie: length={len(access_token)}, secure={access_cookie_kwargs["secure"]}, samesite={access_cookie_kwargs["samesite"]}')
    
    # リフレッシュトークンをCookieに保存
    refresh_cookie_kwargs = {
        'httponly': True,
        'samesite': 'Lax',
        'secure': not settings.DEBUG,
        'path': '/',
        'max_age': int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
    }
    response.set_cookie('refresh_token', refresh_token, **refresh_cookie_kwargs)
    logger.debug(f'Set refresh_token cookie: length={len(refresh_token)}, secure={refresh_cookie_kwargs["secure"]}, samesite={refresh_cookie_kwargs["samesite"]}')


def transfer_guest_user_data(guest_user: User, new_user: User) -> None:
    """ゲストユーザーのデータを新規ユーザーに引き継ぐ
    
    Args:
        guest_user: ゲストユーザー
        new_user: 引き継ぎ先の新規ユーザー
    """
    from django.db import transaction
    from posts.models import Post, Comment, PostVote, CommentVote, PollVote
    from communities.models import CommunityMembership, CommunityMute
    from .models import UserMute, Notification
    
    logger.info(f"Transferring guest user data from {guest_user.id} to {new_user.id}")
    
    with transaction.atomic():
        # 投稿を引き継ぐ
        Post.objects.filter(author=guest_user).update(author=new_user)
        
        # コメントを引き継ぐ
        Comment.objects.filter(author=guest_user).update(author=new_user)
        
        # 投票を引き継ぐ（重複チェック付き）
        # PostVote
        for vote in PostVote.objects.filter(user=guest_user):
            if not PostVote.objects.filter(post=vote.post, user=new_user).exists():
                vote.user = new_user
                vote.save()
            else:
                vote.delete()  # 重複する場合は削除
        
        # CommentVote
        for vote in CommentVote.objects.filter(user=guest_user):
            if not CommentVote.objects.filter(comment=vote.comment, user=new_user).exists():
                vote.user = new_user
                vote.save()
            else:
                vote.delete()  # 重複する場合は削除
        
        # PollVote
        for vote in PollVote.objects.filter(user=guest_user):
            if not PollVote.objects.filter(poll=vote.poll, user=new_user).exists():
                vote.user = new_user
                vote.save()
            else:
                vote.delete()  # 重複する場合は削除
        
        # ミュートを引き継ぐ（重複チェック付き）
        for mute in UserMute.objects.filter(user=guest_user):
            if not UserMute.objects.filter(user=new_user, target=mute.target).exists():
                mute.user = new_user
                mute.save()
            else:
                mute.delete()  # 重複する場合は削除
        
        # コミュニティメンバーシップを引き継ぐ（重複チェック付き）
        for membership in CommunityMembership.objects.filter(user=guest_user):
            if not CommunityMembership.objects.filter(user=new_user, community=membership.community).exists():
                membership.user = new_user
                membership.save()
            else:
                membership.delete()  # 重複する場合は削除
        
        # コミュニティミュートを引き継ぐ（重複チェック付き）
        for mute in CommunityMute.objects.filter(user=guest_user):
            if not CommunityMute.objects.filter(user=new_user, community=mute.community).exists():
                mute.user = new_user
                mute.save()
            else:
                mute.delete()  # 重複する場合は削除
        
        # 通知を引き継ぐ
        Notification.objects.filter(recipient=guest_user).update(recipient=new_user)
        Notification.objects.filter(actor=guest_user).update(actor=new_user)
        
        # ゲストユーザーを削除
        guest_user.delete()
        
        logger.info(f"Successfully transferred guest user data from {guest_user.id} to {new_user.id}")


def send_verification_email(user, token: str) -> bool:
    """メールアドレス認証メールを送信（6桁のワンタイムパスワード形式）
    
    HTMLとテキストの両方のメールを送信し、適切なヘッダーを設定して
    迷惑メールフォルダに入らないように最適化しています。
    
    Args:
        user: 認証対象のユーザー
        token: 認証トークン（6桁の数字コード）
        
    Returns:
        bool: 送信成功時True、失敗時False
    """
    try:
        # フロントエンドのURLを取得
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        verification_url = f"{frontend_url}/verify-email?email={user.email}"
        
        # 表示名を取得
        display_name = user.profile.display_name if hasattr(user, 'profile') and user.profile.display_name else user.username
        
        # メール送信元の設定
        from_email = settings.DEFAULT_FROM_EMAIL
        # Fromヘッダーに表示名を含める（迷惑メール判定を避けるため）
        from_email_formatted = formataddr(('Anonium', from_email))
        
        # メール件名
        subject = "【Anonium】メールアドレスの認証をお願いします"
        
        # テンプレートのコンテキスト
        context = {
            'display_name': display_name,
            'token': token,
            'verification_url': verification_url,
        }
        
        # HTMLメールの生成
        html_message = render_to_string('accounts/email_verification.html', context)
        
        # テキストメールの生成
        text_message = render_to_string('accounts/email_verification.txt', context)
        
        # EmailMultiAlternativesを使用してHTMLとテキストの両方を送信
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_message,
            from_email=from_email_formatted,
            to=[user.email],
        )
        
        # HTMLバージョンを添付
        email.attach_alternative(html_message, "text/html")
        
        # 迷惑メールに入らないようにするためのヘッダー設定
        # Message-ID: 一意のメッセージIDを生成（重要：重複しないように）
        message_id = f"<{uuid.uuid4()}@{settings.ALLOWED_HOSTS[0] if settings.ALLOWED_HOSTS else 'example.com'}>"
        email.extra_headers['Message-ID'] = message_id
        
        # Precedence: auto（自動送信メールであることを示す）
        email.extra_headers['Precedence'] = 'auto'
        
        # X-Auto-Response-Suppress: 自動応答を抑制
        email.extra_headers['X-Auto-Response-Suppress'] = 'All'
        
        # X-Mailer: メールクライアント情報（Djangoアプリケーションであることを示す）
        email.extra_headers['X-Mailer'] = 'Django/Anonium'
        
        # Return-Path: 返信先アドレス（bounce処理用、EmailMultiAlternativesが自動設定するため通常は不要）
        # email.extra_headers['Return-Path'] = from_email  # SESが自動で設定するためコメントアウト
        
        # Reply-To: 返信先アドレス（通常は設定しないが、必要に応じて）
        # email.reply_to = [from_email]  # 認証メールなので返信不要
        
        # メール送信
        try:
            email.send(fail_silently=False)
            logger.info(f"Verification email sent to {user.email} with code {token} (Message-ID: {message_id})")
            return True
        except Exception as send_error:
            # Amazon SESの検証エラーを識別
            error_str = str(send_error)
            if 'failed the check' in error_str or 'Email address not verified' in error_str:
                logger.error(
                    f"Failed to send verification email to {user.email}: "
                    f"Amazon SES email address verification error. "
                    f"The recipient email address may not be verified in SES sandbox mode. "
                    f"Error: {send_error}",
                    exc_info=True
                )
            elif 'MessageRejected' in error_str or 'InvalidParameterValue' in error_str:
                logger.error(
                    f"Failed to send verification email to {user.email}: "
                    f"Amazon SES message rejected. "
                    f"Error: {send_error}",
                    exc_info=True
                )
            else:
                logger.error(
                    f"Failed to send verification email to {user.email}: {send_error}",
                    exc_info=True
                )
            raise  # エラーを再発生させて外側のexceptで処理
    except Exception as e:
        # 外側のexcept: メール送信以外のエラー（テンプレート読み込みエラーなど）
        error_str = str(e)
        if 'failed the check' in error_str or 'Email address not verified' in error_str:
            logger.error(
                f"Failed to send verification email to {user.email}: "
                f"Amazon SES email address verification error. "
                f"The recipient email address ({user.email}) may not be verified in SES sandbox mode. "
                f"Please verify the email address in AWS SES console or move SES out of sandbox mode. "
                f"Error: {e}",
                exc_info=True
            )
        else:
            logger.error(
                f"Failed to send verification email to {user.email}: {e}",
                exc_info=True
            )
        return False
