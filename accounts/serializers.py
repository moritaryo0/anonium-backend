from typing import Any
import secrets
import string

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from .models import UserProfile, Notification
from django.db import DatabaseError


class UserSerializer(serializers.ModelSerializer):
    icon_url = serializers.SerializerMethodField()
    score = serializers.SerializerMethodField()
    is_guest = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    display_name_or_username = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'date_joined',
            'icon_url',
            'score',
            'is_guest',
            'display_name',
            'display_name_or_username',
        ]
        read_only_fields = ['id', 'date_joined']

    def get_icon_url(self, obj: User) -> str:
        try:
            profile = getattr(obj, 'profile', None)
            return getattr(profile, 'icon_url', '') if profile else ''
        except DatabaseError:
            # accounts_userprofile テーブル未作成でも落ちないようにガード
            return ''

    def get_score(self, obj: User) -> int:
        try:
            profile = getattr(obj, 'profile', None)
            return getattr(profile, 'score', 0) if profile else 0
        except DatabaseError:
            return 0

    def get_is_guest(self, obj: User) -> bool:
        """ユーザー名が Anonium- で始まる場合はゲストユーザーと判定"""
        return obj.username.startswith('Anonium-') if obj.username else False

    def get_display_name(self, obj: User) -> str:
        """表示名を取得"""
        try:
            profile = getattr(obj, 'profile', None)
            if profile and profile.display_name:
                return profile.display_name
        except DatabaseError:
            pass
        return ''

    def get_display_name_or_username(self, obj: User) -> str:
        """表示名があれば表示名、なければユーザー名を返す"""
        display_name = self.get_display_name(obj)
        return display_name if display_name else (obj.username or '')


class UserUpdateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, required=False)
    display_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    icon_url = serializers.URLField(required=False, allow_blank=True)

    def validate_username(self, value: str) -> str:
        # ゲストユーザーの場合は request.user が None の可能性があるため、
        # context から user を取得
        user = self.context.get('user')
        if not user:
            raise serializers.ValidationError('ユーザーが見つかりません。')
        # ゲストユーザーの場合はusername変更を不許可
        if user.username and user.username.startswith('Anonium-'):
            raise serializers.ValidationError('ゲストユーザーのユーザーIDは変更できません。')
        if User.objects.exclude(pk=user.pk).filter(username=value).exists():
            raise serializers.ValidationError('このユーザー名は既に使われています。')
        return value

    def save(self, **kwargs: Any):
        # ゲストユーザーの場合は request.user が None の可能性があるため、
        # context から user を取得
        user = self.context.get('user')
        if not user:
            raise ValueError('ユーザーが見つかりません。')
        username = self.validated_data.get('username')
        display_name = self.validated_data.get('display_name')
        icon_url = self.validated_data.get('icon_url')
        
        profile, _ = UserProfile.objects.get_or_create(user=user)
        update_fields = []
        user_update_fields = []
        
        # ステップ3: usernameを更新（通常ユーザーの場合、ゲストユーザーは変更不可）
        if username is not None and user.username != username:
            # ゲストユーザーの場合はusername変更を不許可
            if user.username and user.username.startswith('Anonium-'):
                raise ValueError('ゲストユーザーのユーザーIDは変更できません。')
            user.username = username
            user_update_fields.append('username')
        
        # ユーザーフィールドを保存
        if user_update_fields:
            user.save(update_fields=user_update_fields)
        
        if display_name is not None:
            profile.display_name = display_name
            update_fields.append('display_name')
        
        if icon_url is not None:
            profile.icon_url = icon_url
            update_fields.append('icon_url')
        
        if update_fields:
            update_fields.append('updated_at')
            profile.save(update_fields=update_fields)
        
        return user


def generate_random_username() -> str:
    """ランダムなユーザー名を生成"""
    # 英数字とアンダースコアを使用（Djangoのusername要件に準拠）
    chars = string.ascii_lowercase + string.digits + '_'
    while True:
        # 12文字のランダムな文字列を生成
        username = 'user_' + ''.join(secrets.choice(chars) for _ in range(12))
        if not User.objects.filter(username=username).exists():
            return username


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    display_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8, required=False)

    def validate_password(self, value: str) -> str:
        # パスワードは必須
        if not value:
            raise serializers.ValidationError('パスワードは必須です。')
        validate_password(value)
        return value

    def validate_email(self, value: str) -> str:
        # メールアドレスの重複チェック（認証済みユーザーのみ）
        # 認証中のユーザー（is_active=False）はSignupViewで再送信処理されるため、ここではチェックしない
        existing_user = User.objects.filter(email=value).first()
        if existing_user and existing_user.is_active:
            raise serializers.ValidationError('このメールアドレスは既に使用されています。')
        return value

    def validate_display_name(self, value: str) -> str:
        # display_nameが空文字列またはNoneの場合は、メールアドレスのローカル部分を使用
        # バリデーションでは許可する（空文字列も許可）
        if value:
            return value.strip()
        return ''

    def create(self, validated_data: dict[str, Any]) -> User:
        # ステップ1: 常に新規ユーザーを作成（email, passwordを設定）
        # ゲストユーザーは保持し、ステップ3でデータを統合する
        password: str = validated_data['password']
        email: str = validated_data['email']
        display_name: str = validated_data.get('display_name', '')
        
        # display_nameが空の場合は、メールアドレスのローカル部分を一時的な表示名として使用
        if not display_name or not display_name.strip():
            display_name = email.split('@')[0] if '@' in email else 'User'
        
        # 新規ユーザーを作成（メール認証が必要なため、is_active=False）
        username = generate_random_username()
        user = User(username=username, email=email, is_active=False)
        user.set_password(password)
        user.save()
        # プロフィールを作成/更新（シグナルで既に作成されている可能性がある）
        # post_saveシグナルで自動的にUserProfileが作成されるため、get_or_createを使用
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.display_name = display_name
        profile.save(update_fields=['display_name', 'updated_at'])
        
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        username_or_email = attrs.get('username')
        password = attrs.get('password')
        user_obj: User | None = None

        # Try username directly
        user = authenticate(
            request=self.context.get('request'),
            username=username_or_email,
            password=password,
        )
        if user is None:
            # Try resolving as email
            try:
                user_obj = User.objects.get(email=username_or_email)
                user = authenticate(
                    request=self.context.get('request'),
                    username=user_obj.username,
                    password=password,
                )
            except User.DoesNotExist:
                user = None

        if user is None:
            raise serializers.ValidationError('ユーザー名またはパスワードが正しくありません。')

        attrs['user'] = user
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    """通知シリアライザー"""
    actor_username = serializers.SerializerMethodField()
    actor_icon_url = serializers.SerializerMethodField()
    post_id = serializers.SerializerMethodField()
    post_title = serializers.SerializerMethodField()
    comment_id = serializers.SerializerMethodField()
    comment_body = serializers.SerializerMethodField()
    community_slug = serializers.SerializerMethodField()
    community_name = serializers.SerializerMethodField()
    notification_type_display = serializers.CharField(source='get_notification_type_display', read_only=True)
    link = serializers.SerializerMethodField()
    
    class Meta:
        model = Notification
        fields = [
            'id',
            'notification_type',
            'notification_type_display',
            'actor_username',
            'actor_icon_url',
            'post_id',
            'post_title',
            'comment_id',
            'comment_body',
            'community_slug',
            'community_name',
            'link',
            'is_read',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'is_read']
    
    def get_actor_username(self, obj: Notification) -> str:
        if not obj.actor:
            return ''
        try:
            profile = getattr(obj.actor, 'profile', None)
            if profile and profile.display_name:
                return profile.display_name
        except DatabaseError:
            pass
        return obj.actor.username if obj.actor else ''
    
    def get_actor_icon_url(self, obj: Notification) -> str:
        if not obj.actor:
            return ''
        try:
            profile = getattr(obj.actor, 'profile', None)
            return getattr(profile, 'icon_url', '') if profile else ''
        except DatabaseError:
            return ''
    
    def get_post_id(self, obj: Notification) -> int | None:
        return obj.post.id if obj.post else None
    
    def get_post_title(self, obj: Notification) -> str:
        return obj.post.title if obj.post else ''
    
    def get_comment_id(self, obj: Notification) -> int | None:
        return obj.comment.id if obj.comment else None
    
    def get_comment_body(self, obj: Notification) -> str:
        if not obj.comment:
            return ''
        # 削除されたコメントの場合は空文字を返す
        if obj.comment.is_deleted:
            return '[削除されました]'
        return obj.comment.body[:100] if obj.comment.body else ''  # 最大100文字
    
    def get_community_slug(self, obj: Notification) -> str | None:
        return obj.community.slug if obj.community else None
    
    def get_community_name(self, obj: Notification) -> str:
        return obj.community.name if obj.community else ''
    
    def get_link(self, obj: Notification) -> str:
        """通知タイプに応じてリンクを生成"""
        from .models import Notification as NotificationModel
        
        # 報告作成通知の場合は、コミュニティのチャットページにリンク
        if obj.notification_type == NotificationModel.NotificationType.REPORT_CREATED:
            if obj.community and obj.community.slug:
                return f"/v/{obj.community.slug}/chat"
        
        # その他の通知タイプは、既存のリンクロジックに従う
        # ポスト関連の通知
        if obj.post:
            return f"/p/{obj.post.id}"
        
        # コミュニティ関連の通知
        if obj.community and obj.community.slug:
            return f"/community/{obj.community.slug}"
        
        # デフォルトは空文字
        return ''

