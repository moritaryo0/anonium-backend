from django.db import models
from django.contrib.auth import get_user_model

from communities.models import Community, CommunityTag


User = get_user_model()


class Post(models.Model):
    class PostType(models.TextChoices):
        TEXT = 'text', 'テキスト'
        POLL = 'poll', '投票'
        IMAGE = 'image', '画像'
        VIDEO = 'video', '動画'

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='posts')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    post_type = models.CharField(max_length=10, choices=PostType.choices, default=PostType.TEXT)
    score = models.IntegerField(default=0)
    votes_total = models.PositiveIntegerField(default=0)
    # タグ（コミュニティのタグから選択, 単一）
    tag = models.ForeignKey(CommunityTag, null=True, blank=True, on_delete=models.SET_NULL, related_name='posts')
    # soft delete flags
    is_deleted = models.BooleanField(default=False)
    # 編集済みフラグ（本文/タイトルが編集されたら True）
    is_edited = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='deleted_posts')
    created_ip = models.GenericIPAddressField(null=True, blank=True, help_text='投稿作成時のIPアドレス')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['community', '-created_at']),
            models.Index(fields=['is_deleted', '-created_at']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.community_id}:{self.title[:50]}"


class PostVote(models.Model):
    class Value(models.IntegerChoices):
        DOWN = -1
        UP = 1

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_votes')
    value = models.SmallIntegerField(choices=Value.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('post', 'user')
        indexes = [
            models.Index(fields=['post', 'user']),
        ]


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comments')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    body = models.TextField(blank=True)
    score = models.IntegerField(default=0)
    votes_total = models.PositiveIntegerField(default=0)
    # soft delete flags
    is_deleted = models.BooleanField(default=False)
    # 編集済みフラグ（本文が編集されたら True）
    is_edited = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='deleted_comments')
    created_ip = models.GenericIPAddressField(null=True, blank=True, help_text='コメント作成時のIPアドレス')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['post', 'parent', 'created_at']),
            models.Index(fields=['community', 'parent', 'created_at']),
            models.Index(fields=['post', 'is_deleted', 'created_at']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.post_id}:{self.author_id}:{self.body[:30]}"


class CommentVote(models.Model):
    class Value(models.IntegerChoices):
        DOWN = -1
        UP = 1

    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comment_votes')
    value = models.SmallIntegerField(choices=Value.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('comment', 'user')
        indexes = [
            models.Index(fields=['comment', 'user']),
        ]

class Poll(models.Model):
    post = models.OneToOneField(Post, on_delete=models.CASCADE, related_name='poll')
    title = models.CharField(max_length=200)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"Poll {self.post_id}:{self.title[:50]}"


class PollOption(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=500)
    vote_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['poll', 'created_at']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Option {self.poll_id}:{self.text[:30]}"


class PollVote(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='votes')
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_votes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('poll', 'user')
        indexes = [
            models.Index(fields=['poll', 'user']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"PollVote {self.poll_id}:{self.user_id} -> {self.option_id}"


class PostFollow(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='follows')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_follows')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('post', 'user')
        indexes = [
            models.Index(fields=['post', 'user']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"PostFollow {self.post_id}:{self.user_id}"


class PostMedia(models.Model):
    """ポストに添付された画像または動画のメタデータ"""
    class MediaType(models.TextChoices):
        IMAGE = 'image', '画像'
        VIDEO = 'video', '動画'

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='media')
    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    url = models.CharField(max_length=512)  # メディアのURL
    thumbnail_url = models.CharField(max_length=512, blank=True, default="")  # 動画のサムネイルURL（オプション）
    width = models.PositiveIntegerField(null=True, blank=True)  # 幅（ピクセル）
    height = models.PositiveIntegerField(null=True, blank=True)  # 高さ（ピクセル）
    duration = models.FloatField(null=True, blank=True)  # 動画の長さ（秒）
    file_size = models.PositiveIntegerField(null=True, blank=True)  # ファイルサイズ（バイト）
    order = models.PositiveIntegerField(default=0)  # 表示順序
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['post', 'order']),
        ]
        ordering = ['order', 'created_at']

    def __str__(self) -> str:  # pragma: no cover
        return f"PostMedia {self.post_id}:{self.media_type}:{self.url[:50]}"


class CommentMedia(models.Model):
    """コメントに添付された画像または動画のメタデータ"""
    class MediaType(models.TextChoices):
        IMAGE = 'image', '画像'
        VIDEO = 'video', '動画'

    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='media')
    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    url = models.CharField(max_length=512)  # メディアのURL
    thumbnail_url = models.CharField(max_length=512, blank=True, default="")  # 動画のサムネイルURL（オプション）
    width = models.PositiveIntegerField(null=True, blank=True)  # 幅（ピクセル）
    height = models.PositiveIntegerField(null=True, blank=True)  # 高さ（ピクセル）
    duration = models.FloatField(null=True, blank=True)  # 動画の長さ（秒）
    file_size = models.PositiveIntegerField(null=True, blank=True)  # ファイルサイズ（バイト）
    order = models.PositiveIntegerField(default=0)  # 表示順序
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['comment', 'order']),
        ]
        ordering = ['order', 'created_at']

    def __str__(self) -> str:  # pragma: no cover
        return f"CommentMedia {self.comment_id}:{self.media_type}:{self.url[:50]}"


class OGPCache(models.Model):
    url = models.CharField(max_length=512, unique=True)
    canonical_url = models.CharField(max_length=512, blank=True, default="")
    title = models.CharField(max_length=300, blank=True, default="")
    description = models.TextField(blank=True, default="")
    image = models.CharField(max_length=512, blank=True, default="")
    site_name = models.CharField(max_length=120, blank=True, default="")
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["url"]),
        ]

    def to_response_dict(self) -> dict:
        return {
            "url": self.url,
            "canonical_url": self.canonical_url or self.url,
            "title": self.title,
            "description": self.description,
            "image": self.image,
            "site_name": self.site_name,
        }
