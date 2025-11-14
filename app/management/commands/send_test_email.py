"""
テストメール送信用のDjango管理コマンド

使用方法:
    python manage.py send_test_email recipient@example.com
    python manage.py send_test_email recipient@example.com --token 123456
    python manage.py send_test_email recipient@example.com --subject "テスト件名"
"""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from accounts.utils import send_verification_email
from accounts.models import EmailVerificationToken
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.template.loader import render_to_string
from email.utils import formataddr
import uuid


class Command(BaseCommand):
    help = 'テストメールを送信します'

    def add_arguments(self, parser):
        parser.add_argument(
            'email',
            type=str,
            help='送信先のメールアドレス'
        )
        parser.add_argument(
            '--token',
            type=str,
            default=None,
            help='認証トークン（6桁の数字）。指定しない場合は自動生成されます'
        )
        parser.add_argument(
            '--subject',
            type=str,
            default=None,
            help='メール件名（デフォルト: 認証メールの件名）'
        )
        parser.add_argument(
            '--simple',
            action='store_true',
            help='シンプルなテキストメールを送信（認証メールテンプレートを使用しない）'
        )
        parser.add_argument(
            '--username',
            type=str,
            default=None,
            help='ユーザー名（既存ユーザーが見つからない場合に使用）'
        )

    def handle(self, *args, **options):
        email = options['email']
        token = options.get('token')
        subject = options.get('subject')
        simple = options.get('simple', False)
        username = options.get('username')

        # メールアドレスの形式チェック
        if '@' not in email:
            raise CommandError(f'無効なメールアドレス: {email}')

        # ユーザーを取得または作成
        user = User.objects.filter(email=email).first()
        if not user:
            if username:
                user = User.objects.filter(username=username).first()
                if user:
                    # 既存ユーザーのメールアドレスを更新
                    user.email = email
                    user.save()
                    self.stdout.write(
                        self.style.WARNING(f'既存ユーザー "{username}" のメールアドレスを更新しました')
                    )
                else:
                    # 新規ユーザーを作成
                    user = User.objects.create_user(
                        username=username or f'test_user_{email.split("@")[0]}',
                        email=email,
                        is_active=False
                    )
                    self.stdout.write(
                        self.style.SUCCESS(f'テスト用ユーザーを作成しました: {user.username}')
                    )
            else:
                # ユーザー名が指定されていない場合は一時的なユーザーを作成
                user = User.objects.create_user(
                    username=f'test_user_{email.split("@")[0]}',
                    email=email,
                    is_active=False
                )
                self.stdout.write(
                    self.style.SUCCESS(f'テスト用ユーザーを作成しました: {user.username}')
                )

        # トークンの生成
        if not token:
            if simple:
                token = '123456'  # シンプルモードでは固定値
            else:
                # EmailVerificationTokenを使用してトークンを生成
                verification_token = EmailVerificationToken.create_token(user)
                token = verification_token.token
                self.stdout.write(f'認証トークンを生成しました: {token}')

        # シンプルなメール送信
        if simple:
            try:
                from_email = formataddr(('Anonium', settings.DEFAULT_FROM_EMAIL))
                email_subject = subject or '【Anonium】テストメール'
                email_body = f"""
これはテストメールです。

メールアドレス: {email}
認証トークン: {token}

このメールはテスト送信用です。
"""
                send_mail(
                    subject=email_subject,
                    message=email_body,
                    from_email=from_email,
                    recipient_list=[email],
                    fail_silently=False,
                )
                self.stdout.write(
                    self.style.SUCCESS(f'シンプルなテストメールを送信しました: {email}')
                )
            except Exception as e:
                raise CommandError(f'メール送信に失敗しました: {e}')

        # 認証メールテンプレートを使用した送信
        else:
            try:
                result = send_verification_email(user, token)
                if result:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'認証メールを送信しました: {email} (トークン: {token})'
                        )
                    )
                else:
                    raise CommandError('メール送信に失敗しました（詳細はログを確認してください）')
            except Exception as e:
                raise CommandError(f'メール送信に失敗しました: {e}')

        # メール設定の確認
        self.stdout.write('\n--- メール設定 ---')
        self.stdout.write(f'EMAIL_HOST: {settings.EMAIL_HOST}')
        self.stdout.write(f'EMAIL_PORT: {settings.EMAIL_PORT}')
        self.stdout.write(f'EMAIL_USE_TLS: {settings.EMAIL_USE_TLS}')
        self.stdout.write(f'EMAIL_HOST_USER: {settings.EMAIL_HOST_USER[:3]}***' if settings.EMAIL_HOST_USER else 'EMAIL_HOST_USER: (未設定)')
        self.stdout.write(f'DEFAULT_FROM_EMAIL: {settings.DEFAULT_FROM_EMAIL}')
        self.stdout.write(f'FRONTEND_URL: {getattr(settings, "FRONTEND_URL", "未設定")}')

