from django.db.models import Q, OuterRef, Subquery
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from .models import Message, GroupChatMessage, Report
from .serializers import (
    MessageSerializer, MessageCreateSerializer,
    GroupChatMessageSerializer, GroupChatMessageCreateSerializer,
    ReportSerializer, ReportCreateSerializer
)
from .utils import send_message_via_websocket, send_group_chat_message_via_websocket
from communities.models import CommunityMembership as CM, Community
from communities.serializers import CommunitySerializer
from accounts.models import Notification


class MessageListView(generics.ListCreateAPIView):
    """メッセージ一覧取得・作成ビュー"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return MessageCreateSerializer
        return MessageSerializer

    def get_queryset(self):
        """ログインユーザーが受信者または送信者のメッセージを取得"""
        user = self.request.user
        
        # クエリパラメータでフィルタリング
        community_id = self.request.query_params.get('community_id')
        is_sent = self.request.query_params.get('is_sent', '').lower() == 'true'
        is_read = self.request.query_params.get('is_read')
        
        queryset = Message.objects.select_related('sender', 'recipient', 'community')
        
        if is_sent:
            # 送信メッセージ
            queryset = queryset.filter(sender=user)
        else:
            # 受信メッセージ（デフォルト）
            queryset = queryset.filter(recipient=user)
        
        if community_id:
            try:
                queryset = queryset.filter(community_id=int(community_id))
            except (ValueError, TypeError):
                pass
        
        if is_read is not None:
            is_read_bool = is_read.lower() == 'true'
            queryset = queryset.filter(is_read=is_read_bool)
        
        return queryset.order_by('-created_at')

    def perform_create(self, serializer):
        """メッセージ作成時の権限チェックはシリアライザーで行う"""
        message = serializer.save()
        # WebSocketでメッセージを送信
        send_message_via_websocket(message)


class MessageDetailView(generics.RetrieveUpdateDestroyAPIView):
    """メッセージ詳細ビュー"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MessageSerializer

    def get_queryset(self):
        """ログインユーザーが送信者または受信者のメッセージのみ取得"""
        user = self.request.user
        return Message.objects.filter(
            Q(sender=user) | Q(recipient=user)
        ).select_related('sender', 'recipient', 'community')

    def retrieve(self, request, *args, **kwargs):
        """メッセージ取得時に既読にする"""
        instance = self.get_object()
        
        # 受信者かつ未読の場合、既読にする
        if instance.recipient == request.user and not instance.is_read:
            instance.is_read = True
            instance.read_at = timezone.now()
            instance.save(update_fields=['is_read', 'read_at'])
            # WebSocketで既読通知を送信
            from .utils import notify_message_read
            notify_message_read(instance)
        
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def delete(self, request, *args, **kwargs):
        """メッセージ削除（送信者または受信者のみ可能）"""
        instance = self.get_object()
        
        # 送信者または受信者のみ削除可能
        if instance.sender != request.user and instance.recipient != request.user:
            raise PermissionDenied('このメッセージを削除する権限がありません。')
        
        return super().delete(request, *args, **kwargs)


class MessageMarkReadView(APIView):
    """メッセージを既読にするビュー"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        message = get_object_or_404(
            Message,
            pk=pk,
            recipient=request.user
        )
        
        if not message.is_read:
            message.is_read = True
            message.read_at = timezone.now()
            message.save(update_fields=['is_read', 'read_at'])
            # WebSocketで既読通知を送信
            from .utils import notify_message_read
            notify_message_read(message)
        
        return Response(MessageSerializer(message).data)


class MessageUnreadCountView(APIView):
    """未読メッセージ数を取得するビュー"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        community_id = request.query_params.get('community_id')
        
        queryset = Message.objects.filter(
            recipient=request.user,
            is_read=False
        )
        
        if community_id:
            try:
                queryset = queryset.filter(community_id=int(community_id))
            except (ValueError, TypeError):
                pass
        
        count = queryset.count()
        return Response({'unread_count': count})


class GroupChatMessageListView(generics.ListCreateAPIView):
    """グループチャットメッセージ一覧取得・作成ビュー"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = GroupChatMessageSerializer

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return GroupChatMessageCreateSerializer
        return GroupChatMessageSerializer

    def get_queryset(self):
        """コミュニティのグループチャットメッセージを取得"""
        community_id = self.kwargs.get('community_id')
        user = self.request.user
        
        # コミュニティのモデレーター以上かチェック
        membership = CM.objects.filter(
            community_id=community_id,
            user=user,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        
        if not membership:
            raise PermissionDenied('このコミュニティのモデレーター以上の権限が必要です。')
        
        queryset = GroupChatMessage.objects.filter(
            community_id=community_id
        ).select_related('sender', 'community', 'report', 'report__reporter', 'reply_to', 'reply_to__sender')
        
        return queryset.order_by('-created_at')

    def perform_create(self, serializer):
        """グループチャットメッセージ作成時の権限チェックはシリアライザーで行う"""
        message = serializer.save()
        # WebSocketでメッセージを送信
        send_group_chat_message_via_websocket(message)


class GroupChatMessageDetailView(generics.RetrieveUpdateDestroyAPIView):
    """グループチャットメッセージ詳細・更新・削除ビュー"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = GroupChatMessageSerializer

    def get_queryset(self):
        """コミュニティのグループチャットメッセージを取得"""
        community_id = self.kwargs.get('community_id')
        user = self.request.user
        
        # コミュニティのモデレーター以上かチェック
        membership = CM.objects.filter(
            community_id=community_id,
            user=user,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        
        if not membership:
            raise PermissionDenied('このコミュニティのモデレーター以上の権限が必要です。')
        
        return GroupChatMessage.objects.filter(
            community_id=community_id
        ).select_related('sender', 'community', 'report', 'report__reporter', 'reply_to', 'reply_to__sender')

    def delete(self, request, *args, **kwargs):
        """メッセージ削除（送信者のみ可能）"""
        instance = self.get_object()
        
        # 送信者のみ削除可能
        if instance.sender != request.user:
            raise PermissionDenied('自分のメッセージのみ削除できます。')
        
        return super().delete(request, *args, **kwargs)


class ChatRoomListView(APIView):
    """チャットルーム一覧取得ビュー
    
    ユーザーがモデレーター以上の権限を持つコミュニティのグループチャット一覧を返す
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """モデレーター以上のコミュニティ一覧を取得"""
        user = request.user
        
        # モデレーター以上の権限を持つコミュニティを取得
        moderatorships = CM.objects.filter(
            user=user,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).select_related('community')
        
        community_ids = [m.community_id for m in moderatorships]
        
        if not community_ids:
            return Response([])
        
        # 各コミュニティの最後のメッセージを取得（効率的に一括取得）
        # 各コミュニティの最新メッセージIDを取得
        latest_message_ids = {}
        for community_id in community_ids:
            latest_msg = GroupChatMessage.objects.filter(
                community_id=community_id
            ).order_by('-created_at').values_list('id', flat=True).first()
            if latest_msg:
                latest_message_ids[community_id] = latest_msg
        
        # 最新メッセージを一括取得
        if latest_message_ids:
            latest_messages_qs = GroupChatMessage.objects.filter(
                id__in=latest_message_ids.values()
            ).select_related('sender')
            latest_messages = {msg.community_id: msg for msg in latest_messages_qs}
        else:
            latest_messages = {}
        
        communities = Community.objects.filter(id__in=community_ids)
        
        # シリアライズ
        result = []
        for community in communities:
            latest_message = None
            if community.id in latest_messages:
                latest_msg = latest_messages[community.id]
                latest_message = {
                    'id': latest_msg.id,
                    'body': latest_msg.body,
                    'sender': {
                        'id': latest_msg.sender_id,
                        'username': latest_msg.sender.username,
                    },
                    'created_at': latest_msg.created_at.isoformat(),
                }
            
            community_data = CommunitySerializer(community, context={'request': request}).data
            result.append({
                'community': community_data,
                'latest_message': latest_message,
            })
        
        # 最後のメッセージがあるコミュニティを優先してソート
        result.sort(key=lambda x: (
            x['latest_message']['created_at'] if x['latest_message'] else '1970-01-01T00:00:00Z'
        ), reverse=True)
        
        return Response(result)


class ReportListView(generics.ListAPIView):
    """報告一覧取得ビュー"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReportSerializer

    def get_serializer_context(self):
        """シリアライザーのcontextにrequestを追加"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def get_queryset(self):
        """コミュニティの報告を取得（モデレーター以上の権限が必要）"""
        community_id = self.kwargs.get('community_id')
        user = self.request.user
        
        # コミュニティのモデレーター以上かチェック
        membership = CM.objects.filter(
            community_id=community_id,
            user=user,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        
        if not membership:
            raise PermissionDenied('このコミュニティのモデレーター以上の権限が必要です。')
        
        queryset = Report.objects.filter(
            community_id=community_id
        ).select_related('reporter', 'community').order_by('-created_at')
        
        # ステータスでフィルタリング
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset


class ReportCreateView(generics.CreateAPIView):
    """報告作成ビュー"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReportCreateSerializer

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ReportCreateSerializer
        return ReportSerializer

    def create(self, request, *args, **kwargs):
        """報告を作成"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = serializer.save()
        
        # コミュニティのモデレーター以上のユーザーに通知を送信
        moderators = CM.objects.filter(
            community=report.community,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).exclude(user=report.reporter).select_related('user')
        
        notifications_to_create = []
        for membership in moderators:
            notifications_to_create.append(
                Notification(
                    recipient=membership.user,
                    notification_type=Notification.NotificationType.REPORT_CREATED,
                    actor=report.reporter,
                    community=report.community,
                )
            )
        
        # バルクインサートで通知を作成
        if notifications_to_create:
            Notification.objects.bulk_create(notifications_to_create)
        
        # キャッシュ削除: 報告一覧、投稿詳細（報告された投稿がある場合）
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/messages/reports/community/{report.community.id}/*')
        if report.post:
            invalidate_cache(key=f'/api/posts/{report.post.id}/')
        if report.comment:
            invalidate_cache(key=f'/api/comments/{report.comment.id}/')
            invalidate_cache(key=f'/api/posts/{report.comment.post_id}/')
            invalidate_cache(key=f'/api/posts/{report.comment.post_id}/comments/')
        
        # 作成された報告を返す（contextにrequestを渡す）
        response_serializer = ReportSerializer(report, context={'request': request})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class ReportUpdateView(generics.UpdateAPIView):
    """報告ステータス更新ビュー"""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReportSerializer
    lookup_field = 'pk'

    def get_queryset(self):
        """コミュニティの報告を取得（モデレーター以上の権限が必要）"""
        community_id = self.kwargs.get('community_id')
        user = self.request.user
        
        # コミュニティのモデレーター以上かチェック
        membership = CM.objects.filter(
            community_id=community_id,
            user=user,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        
        if not membership:
            raise PermissionDenied('このコミュニティのモデレーター以上の権限が必要です。')
        
        return Report.objects.filter(community_id=community_id)

    def get_serializer_context(self):
        """シリアライザーのcontextにrequestを追加"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def update(self, request, *args, **kwargs):
        """報告のステータスを更新"""
        instance = self.get_object()
        
        # ステータスのみ更新可能
        new_status = request.data.get('status')
        if new_status not in ['pending', 'in_progress', 'resolved', 'rejected']:
            return Response(
                {'detail': '無効なステータスです。'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        instance.status = new_status
        instance.save(update_fields=['status', 'updated_at'])
        
        # キャッシュ削除: 報告一覧、報告詳細
        from app.utils import invalidate_cache
        invalidate_cache(pattern=f'/api/messages/reports/community/{instance.community.id}/*')
        if instance.post:
            invalidate_cache(key=f'/api/posts/{instance.post.id}/')
        if instance.comment:
            invalidate_cache(key=f'/api/comments/{instance.comment.id}/')
            invalidate_cache(key=f'/api/posts/{instance.comment.post_id}/')
            invalidate_cache(key=f'/api/posts/{instance.comment.post_id}/comments/')
        
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

