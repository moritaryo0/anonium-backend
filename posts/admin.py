from django.contrib import admin

from .models import Post, PostVote, Comment, CommentVote, OGPCache, Poll, PollOption, PollVote as PollVoteModel, PostMedia, CommentMedia


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'author', 'community', 'post_type', 'score', 'is_deleted', 'created_at')
    list_filter = ('post_type', 'is_deleted', 'community')
    search_fields = ('title', 'body')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('id', 'post', 'author', 'score', 'is_deleted', 'created_at')
    list_filter = ('is_deleted',)
    search_fields = ('body',)


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ('id', 'post', 'title', 'created_at')
    search_fields = ('title',)


@admin.register(PollOption)
class PollOptionAdmin(admin.ModelAdmin):
    list_display = ('id', 'poll', 'text', 'vote_count')
    list_filter = ('poll',)


@admin.register(PollVoteModel)
class PollVoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'poll', 'option', 'user', 'created_at')


@admin.register(PostVote)
class PostVoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'post', 'user', 'value', 'created_at')


@admin.register(CommentVote)
class CommentVoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'comment', 'user', 'value', 'created_at')


@admin.register(PostMedia)
class PostMediaAdmin(admin.ModelAdmin):
    list_display = ('id', 'post', 'media_type', 'url', 'order', 'created_at')
    list_filter = ('media_type',)
    search_fields = ('url',)
    ordering = ('post', 'order', 'created_at')


@admin.register(CommentMedia)
class CommentMediaAdmin(admin.ModelAdmin):
    list_display = ('id', 'comment', 'media_type', 'url', 'order', 'created_at')
    list_filter = ('media_type',)
    search_fields = ('url',)
    ordering = ('comment', 'order', 'created_at')


@admin.register(OGPCache)
class OGPCacheAdmin(admin.ModelAdmin):
    list_display = ('url', 'title', 'fetched_at')
    search_fields = ('url', 'title')
