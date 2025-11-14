from django.db import models
from django.contrib.auth import get_user_model
import json


User = get_user_model()


class Community(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = 'public', 'Public'
        RESTRICTED = 'restricted', 'Restricted'
        PRIVATE = 'private', 'Private'

    class JoinPolicy(models.TextChoices):
        OPEN = 'open', 'Open'
        APPROVAL = 'approval', 'Approval Required'
        INVITE = 'invite', 'Invite Only'
        LOGIN = 'login', 'Login Users Only'

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    rules = models.JSONField(default=list, blank=True)  # [{"title": "タイトル", "description": "説明"}, ...]
    icon_url = models.URLField(blank=True)
    banner_url = models.URLField(blank=True)

    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    join_policy = models.CharField(
        max_length=16, choices=JoinPolicy.choices, default=JoinPolicy.OPEN
    )
    is_nsfw = models.BooleanField(default=False)
    allow_repost = models.BooleanField(default=False)  # 転載許可フラグ
    karma = models.IntegerField(default=0, help_text='ゲストユーザーが投票するために必要な最小スコア')
    clip_post = models.ForeignKey(
        'posts.Post', on_delete=models.SET_NULL, related_name='clipped_in_communities',
        null=True, blank=True, help_text='コミュニティに固定されたポスト'
    )

    creator = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='created_communities'
    )
    members_count = models.PositiveIntegerField(default=0)
    # タグ付け許可範囲（コミュニティ単位）
    class TagPermissionScope(models.TextChoices):
        ALL = 'all', 'All Participants'
        MODERATOR = 'moderator', 'Moderators'
        OWNER = 'owner', 'Owner'
    tag_permission_scope = models.CharField(
        max_length=16, choices=TagPermissionScope.choices, default=TagPermissionScope.ALL
    )
    is_deleted = models.BooleanField(default=False, help_text='コミュニティが削除されたかどうか')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['visibility', '-created_at']),
            models.Index(fields=['is_deleted']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return self.slug


class CommunityMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        ADMIN_MODERATOR = 'admin_moderator', 'AdminModerator'
        MODERATOR = 'moderator', 'Moderator'
        MEMBER = 'member', 'Member'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'

    community = models.ForeignKey(
        Community, on_delete=models.CASCADE, related_name='memberships'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='community_memberships')
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPROVED)
    is_favorite = models.BooleanField(default=False)
    # 標準モデレーターを任命した管理モデレーター（管理モデレーターが降格された場合の連鎖解除に使用）
    appointed_by_admin = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='appointed_standard_moderators'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('community', 'user')
        indexes = [
            models.Index(fields=['user', 'community']),
            models.Index(fields=['community', 'role']),
            models.Index(fields=['community', 'appointed_by_admin']),
        ]
        constraints = [
            # appointed_by_admin は 標準モデレーター(role=moderator) 以外では NULL でなければならない
            models.CheckConstraint(
                name='appointed_by_admin_requires_standard_moderator',
                check=(
                    models.Q(appointed_by_admin__isnull=True) |
                    models.Q(role='moderator')
                ),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.user_id}:{self.community_id}:{self.role}"

    def save(self, *args, **kwargs):
        """ロール変更時の連鎖処理。

        - 管理モデレーター(admin_moderator) から他ロールへ降格された場合、
          その管理モデレーターが任命した標準モデレーター(modERATOR)を一括でメンバーへ戻す。
        - 標準モデレーター以外では appointed_by_admin は常に NULL に正規化する。
        """
        old_role = None
        if self.pk:
            try:
                old_role = CommunityMembership.objects.only('role').get(pk=self.pk).role
            except CommunityMembership.DoesNotExist:
                old_role = None

        # 標準モデレーター以外では appointed_by_admin をクリア
        if self.role != CommunityMembership.Role.MODERATOR and self.appointed_by_admin_id is not None:
            self.appointed_by_admin = None

        super().save(*args, **kwargs)

        # 管理モデレーター降格時の連鎖解除
        if old_role == CommunityMembership.Role.ADMIN_MODERATOR and self.role != CommunityMembership.Role.ADMIN_MODERATOR:
            CommunityMembership.objects.filter(
                community_id=self.community_id,
                role=CommunityMembership.Role.MODERATOR,
                appointed_by_admin_id=self.user_id,
            ).update(role=CommunityMembership.Role.MEMBER, appointed_by_admin=None)


class CommunityBlock(models.Model):
    community = models.ForeignKey(
        Community, on_delete=models.CASCADE, related_name='blocks'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='community_blocks')
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('community', 'user')
        indexes = [
            models.Index(fields=['community', 'user']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"block:{self.community_id}:{self.user_id}"


class CommunityTag(models.Model):
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='tags')
    name = models.CharField(max_length=15)
    color = models.CharField(max_length=16, default='#1e3a8a')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('community', 'name')
        indexes = [
            models.Index(fields=['community', 'name']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"tag:{self.community_id}:{self.name}"


class CommunityMute(models.Model):
    """ユーザーがコミュニティをミュートする関係を表すモデル。

    - user: ミュートを設定したユーザー
    - community: ミュートされたコミュニティ
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='community_mutes')
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='muted_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'community')
        indexes = [
            models.Index(fields=['user', 'community']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"mute:{self.user_id}->{self.community_id}"
