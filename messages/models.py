from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Message(models.Model):
    """モデレーター同士のメッセージモデル
    
    - sender: 送信者（モデレーター）
    - recipient: 受信者（モデレーター）
    - community: 関連するコミュニティ
    - subject: 件名
    - body: メッセージ本文
    - is_read: 既読フラグ
    - created_at: 作成日時
    """
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='sent_messages'
    )
    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='received_messages'
    )
    community = models.ForeignKey(
        'communities.Community', on_delete=models.CASCADE, related_name='messages'
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(fields=['community', '-created_at']),
            models.Index(fields=['sender', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self) -> str:  # pragma: no cover
        return f"Message {self.id}: {self.sender_id} -> {self.recipient_id} ({self.community_id})"


class GroupChatMessage(models.Model):
    """コミュニティのグループチャットメッセージモデル
    
    モデレーター以上の権限を持つユーザーが参加できるグループチャット
    - sender: 送信者（モデレーター以上）
    - community: 関連するコミュニティ
    - body: メッセージ本文
    - reply_to: 引用元のメッセージ（任意）
    - report: 引用元の報告（任意）
    - created_at: 作成日時
    """
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='group_chat_messages'
    )
    community = models.ForeignKey(
        'communities.Community', on_delete=models.CASCADE, related_name='group_chat_messages'
    )
    body = models.TextField()
    reply_to = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='replies', verbose_name='引用元メッセージ'
    )
    report = models.ForeignKey(
        'Report', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='group_chat_messages', verbose_name='引用元報告'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['community', '-created_at']),
            models.Index(fields=['sender', '-created_at']),
            models.Index(fields=['report', '-created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self) -> str:  # pragma: no cover
        return f"GroupChatMessage {self.id}: {self.sender_id} -> {self.community_id}"


class Report(models.Model):
    """投稿・コメントの報告モデル
    
    コミュニティの投稿やコメントに対する報告を管理
    - reporter: 報告者
    - community: 報告先のコミュニティ
    - content_type: 報告対象の型（'post' または 'comment'）
    - content_object_id: 報告対象のID（投稿またはコメントのID）
    - body: 報告内容
    - status: 対応状況
    - created_at: 作成日時
    - updated_at: 更新日時
    """
    class Status(models.TextChoices):
        PENDING = 'pending', '未対応'
        IN_PROGRESS = 'in_progress', '対応中'
        RESOLVED = 'resolved', '対応済み'
        REJECTED = 'rejected', '却下'

    class ContentType(models.TextChoices):
        POST = 'post', '投稿'
        COMMENT = 'comment', 'コメント'

    reporter = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='reports'
    )
    community = models.ForeignKey(
        'communities.Community', on_delete=models.CASCADE, related_name='reports'
    )
    content_type = models.CharField(
        max_length=10, choices=ContentType.choices,
        verbose_name='報告対象の型'
    )
    content_object_id = models.PositiveIntegerField(
        verbose_name='報告対象のID'
    )
    body = models.TextField(verbose_name='報告内容')
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
        verbose_name='対応状況'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['community', '-created_at']),
            models.Index(fields=['community', 'status', '-created_at']),
            models.Index(fields=['reporter', '-created_at']),
            models.Index(fields=['content_type', 'content_object_id', '-created_at']),
        ]
        ordering = ['-created_at']

    @property
    def post(self):
        """報告対象の投稿を取得"""
        if self.content_type == self.ContentType.POST:
            from posts.models import Post
            try:
                return Post.objects.get(pk=self.content_object_id)
            except Post.DoesNotExist:
                return None
        return None

    @property
    def comment(self):
        """報告対象のコメントを取得"""
        if self.content_type == self.ContentType.COMMENT:
            from posts.models import Comment
            try:
                return Comment.objects.get(pk=self.content_object_id)
            except Comment.DoesNotExist:
                return None
        return None

    def __str__(self) -> str:  # pragma: no cover
        return f"Report {self.id}: {self.reporter_id} -> {self.content_type} {self.content_object_id} ({self.community_id})"

