from django.urls import path
from .views import (
    MessageListView,
    MessageDetailView,
    MessageMarkReadView,
    MessageUnreadCountView,
    GroupChatMessageListView,
    GroupChatMessageDetailView,
    ChatRoomListView,
    ReportListView,
    ReportCreateView,
    ReportUpdateView,
)

urlpatterns = [
    path('', MessageListView.as_view(), name='message-list'),
    path('<int:pk>/', MessageDetailView.as_view(), name='message-detail'),
    path('<int:pk>/mark-read/', MessageMarkReadView.as_view(), name='message-mark-read'),
    path('unread-count/', MessageUnreadCountView.as_view(), name='message-unread-count'),
    path('group-chat/community/<int:community_id>/', GroupChatMessageListView.as_view(), name='group-chat-message-list'),
    path('group-chat/community/<int:community_id>/<int:pk>/', GroupChatMessageDetailView.as_view(), name='group-chat-message-detail'),
    path('chat-rooms/', ChatRoomListView.as_view(), name='chat-room-list'),
    path('reports/', ReportCreateView.as_view(), name='report-create'),
    path('reports/community/<int:community_id>/', ReportListView.as_view(), name='report-list'),
    path('reports/community/<int:community_id>/<int:pk>/', ReportUpdateView.as_view(), name='report-update'),
]

