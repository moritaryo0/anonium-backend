from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
import secrets

User = get_user_model()


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    icon_url = models.URLField(blank=True)
    bio = models.TextField(blank=True)
    display_name = models.CharField(max_length=150, blank=True, help_text='表示名（ニックネーム）')
    score = models.IntegerField(default=0)  # 投稿・コメントへの投票で得られるスコア
    registration_ip = models.GenericIPAddressField(null=True, blank=True, help_text='登録時のIPアドレス')
    last_login_ip = models.GenericIPAddressField(null=True, blank=True, help_text='最後にログインした時のIPアドレス')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"profile:{self.user_id}"

# Create your models here.


class UserMute(models.Model):
    """ユーザーが他ユーザーをミュートする関係を表すモデル。

    - user: ミュートを設定したユーザー
    - target: ミュートされたユーザー
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mutes')
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name='muted_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'target'], name='unique_user_mute')
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"mute:{self.user_id}->{self.target_id}"


class Notification(models.Model):
    """通知モデル
    
    ユーザーに送られる通知を表すモデル。
    - recipient: 通知を受け取るユーザー
    - notification_type: 通知のタイプ
    - actor: 通知を発生させたユーザー（運営通知の場合はnull）
    - post: 関連するポスト（該当する場合）
    - comment: 関連するコメント（該当する場合）
    - community: 関連するコミュニティ（該当する場合）
    - is_read: 既読フラグ
    - created_at: 作成日時
    """
    
    class NotificationType(models.TextChoices):
        POST_COMMENT = 'post_comment', 'ポストへのコメント'
        FOLLOWED_POST_COMMENT = 'followed_post_comment', 'フォローしたスレッドへのコメント'
        COMMENT_REPLY = 'comment_reply', 'コメントへの返信'
        COMMENT_DELETED = 'comment_deleted', 'コメントの削除'
        ADMIN_NOTIFICATION = 'admin_notification', '運営通知'
        REPORT_CREATED = 'report_created', '報告の作成'
        # 将来的に追加可能な通知タイプ
        # POST_LIKE = 'post_like', 'ポストへのいいね'
        # COMMENT_LIKE = 'comment_like', 'コメントへのいいね'
        # COMMUNITY_INVITE = 'community_invite', 'コミュニティへの招待'
    
    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='notifications'
    )
    notification_type = models.CharField(
        max_length=32, choices=NotificationType.choices
    )
    actor = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='sent_notifications',
        null=True, blank=True, help_text='通知を発生させたユーザー（運営通知の場合はnull）'
    )
    # 関連するオブジェクト（該当する場合のみ設定）
    post = models.ForeignKey(
        'posts.Post', on_delete=models.CASCADE, related_name='notifications',
        null=True, blank=True
    )
    comment = models.ForeignKey(
        'posts.Comment', on_delete=models.CASCADE, related_name='notifications',
        null=True, blank=True
    )
    community = models.ForeignKey(
        'communities.Community', on_delete=models.CASCADE, related_name='notifications',
        null=True, blank=True
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(fields=['notification_type', '-created_at']),
        ]
        ordering = ['-created_at']
    
    def __str__(self) -> str:  # pragma: no cover
        return f"notification:{self.recipient_id}:{self.notification_type}:{self.id}"


class EmailVerificationToken(models.Model):
    """メールアドレス認証トークンモデル
    
    ユーザーのメールアドレス認証に使用するトークンを管理するモデル。
    - user: 認証対象のユーザー
    - token: 認証トークン（6桁の数字コード）
    - created_at: トークン作成日時
    - expires_at: トークンの有効期限
    - is_used: トークンが使用済みかどうか
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_verification_tokens')
    token = models.CharField(max_length=6, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    class Meta:
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['expires_at']),
        ]
        ordering = ['-created_at']
    
    def __str__(self) -> str:
        return f"email_verification:{self.user_id}:{self.token}"
    
    @classmethod
    def generate_token(cls) -> str:
        """認証トークンを生成（6桁の数字コード）
        
        セキュリティ上の考慮事項：
        - 6桁の数字コードは100万通りの組み合わせ（000000-999999）
        - 衝突の可能性は低いが、既存の有効なトークンと衝突しないようにチェック
        - 最大20回試行して衝突を避ける
        """
        max_attempts = 20
        for attempt in range(max_attempts):
            token = f"{secrets.randbelow(1000000):06d}"
            # 既存の有効なトークン（未使用かつ期限切れでない）と衝突しないことを確認
            # ユニーク制約があるため、データベースレベルでもチェックされる
            if not cls.objects.filter(
                token=token,
                is_used=False,
                expires_at__gt=timezone.now()
            ).exists():
                return token
        
        # 最大試行回数に達した場合（非常に稀）
        # タイムスタンプの一部を使用してさらにランダム性を高める
        import time as time_module
        timestamp_part = int(time_module.time() * 1000) % 10000  # 下4桁
        random_part = secrets.randbelow(100)  # 2桁
        # 6桁のコードを生成（タイムスタンプ4桁 + ランダム2桁）
        fallback_token = f"{(timestamp_part * 100 + random_part) % 1000000:06d}"
        
        # フォールバックトークンも衝突チェック
        if not cls.objects.filter(
            token=fallback_token,
            is_used=False,
            expires_at__gt=timezone.now()
        ).exists():
            return fallback_token
        
        # 最終フォールバック: ランダムに追加試行（最大100回）
        # シーケンシャル試行はパフォーマンスの問題があるため、ランダム試行に変更
        for _ in range(100):
            final_token = f"{secrets.randbelow(1000000):06d}"
            if not cls.objects.filter(
                token=final_token,
                is_used=False,
                expires_at__gt=timezone.now()
            ).exists():
                return final_token
        
        # これでも衝突する場合は例外を発生（理論上は発生しない）
        # 実際には、有効なトークンが100万個を超えることはないため、この例外は発生しない
        raise ValueError("Unable to generate unique verification token after all attempts")
    
    @classmethod
    def create_token(cls, user: User, expiration_hours: int = 24) -> 'EmailVerificationToken':
        """新しい認証トークンを作成
        
        Args:
            user: 認証対象のユーザー
            expiration_hours: トークンの有効期限（時間）
            
        Returns:
            EmailVerificationToken: 作成されたトークン
            
        Raises:
            ValueError: トークンの生成に失敗した場合
        """
        # 既存の未使用トークンを無効化（ユーザーごとに1つの有効なトークンのみ）
        cls.objects.filter(user=user, is_used=False).update(is_used=True)
        
        # トークンを生成（衝突チェック付き）
        max_create_attempts = 5
        for _ in range(max_create_attempts):
            try:
                token = cls.generate_token()
                expires_at = timezone.now() + timedelta(hours=expiration_hours)
                return cls.objects.create(
                    user=user,
                    token=token,
                    expires_at=expires_at,
                )
            except Exception as e:
                # ユニーク制約違反などの場合、再試行
                # ログに記録して次の試行に進む
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Token creation attempt failed for user {user.id}: {e}, retrying...")
                continue
        
        # 全ての試行が失敗した場合
        raise ValueError(f"Failed to create verification token for user {user.id} after {max_create_attempts} attempts")
    
    def is_valid(self) -> bool:
        """トークンが有効かどうかをチェック"""
        if self.is_used:
            return False
        if timezone.now() > self.expires_at:
            return False
        return True


class EmailVerificationAttempt(models.Model):
    """メール認証の試行回数を追跡するモデル（セキュリティ対策）
    
    ブルートフォース攻撃を防ぐために、IPアドレスとユーザーの組み合わせで
    試行回数を追跡します。
    - ip_address: 試行元のIPアドレス
    - user: 試行対象のユーザー（Noneの場合はIPアドレスのみで追跡）
    - attempt_count: 試行回数
    - last_attempt_at: 最後の試行日時
    - locked_until: ロック解除時刻（Noneの場合はロックされていない）
    """
    ip_address = models.GenericIPAddressField(db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, db_index=True)
    attempt_count = models.IntegerField(default=0)
    last_attempt_at = models.DateTimeField(auto_now=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['ip_address', 'user']),
            models.Index(fields=['ip_address', 'locked_until']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['ip_address', 'user'], name='unique_verification_attempt')
        ]
    
    def __str__(self) -> str:
        return f"verification_attempt:{self.ip_address}:{self.user_id if self.user else 'anonymous'}:{self.attempt_count}"
    
    def is_locked(self) -> bool:
        """ロックされているかどうかをチェック"""
        if not self.locked_until:
            return False
        if timezone.now() > self.locked_until:
            # ロック期限が過ぎた場合はリセット
            self.attempt_count = 0
            self.locked_until = None
            self.save(update_fields=['attempt_count', 'locked_until'])
            return False
        return True
    
    def increment_attempt(self, max_attempts: int = 5, lock_duration_minutes: int = 15):
        """試行回数を増やし、必要に応じてロックする"""
        self.attempt_count += 1
        self.last_attempt_at = timezone.now()
        
        if self.attempt_count >= max_attempts:
            # 最大試行回数を超えた場合、ロックする
            self.locked_until = timezone.now() + timedelta(minutes=lock_duration_minutes)
        
        self.save(update_fields=['attempt_count', 'last_attempt_at', 'locked_until'])
    
    def reset_attempts(self):
        """試行回数をリセット（認証成功時など）"""
        self.attempt_count = 0
        self.locked_until = None
        self.save(update_fields=['attempt_count', 'locked_until'])
    
    @classmethod
    def get_or_create_attempt(cls, ip_address: str, user: User = None):
        """試行記録を取得または作成"""
        attempt, created = cls.objects.get_or_create(
            ip_address=ip_address,
            user=user,
            defaults={
                'attempt_count': 0,
                'last_attempt_at': timezone.now(),
            }
        )
        return attempt
