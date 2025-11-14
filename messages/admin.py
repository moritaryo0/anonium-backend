from django.contrib import admin
from .models import Message, GroupChatMessage, Report


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'sender', 'recipient', 'community', 'subject', 'is_read', 'created_at')
    list_filter = ('is_read', 'created_at', 'community')
    search_fields = ('subject', 'body', 'sender__username', 'recipient__username')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(GroupChatMessage)
class GroupChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'sender', 'community', 'body', 'created_at')
    list_filter = ('created_at', 'community')
    search_fields = ('body', 'sender__username')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'reporter', 'community', 'content_type', 'content_object_id', 'status', 'created_at')
    list_filter = ('status', 'content_type', 'created_at', 'community')
    search_fields = ('body', 'reporter__username')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('報告情報', {
            'fields': ('reporter', 'community', 'content_type', 'content_object_id', 'body', 'status')
        }),
        ('タイムスタンプ', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

