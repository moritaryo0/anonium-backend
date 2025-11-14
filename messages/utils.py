"""
メッセージ用のユーティリティ関数
"""
from django.conf import settings

# WebSocketが有効な場合のみインポート
if settings.ENABLE_WEBSOCKET:
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync


def send_message_via_websocket(message):
    """メッセージをWebSocket経由で送信する"""
    if not settings.ENABLE_WEBSOCKET:
        return  # WebSocketが無効な場合は何もしない
    
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    
    # メッセージをシリアライズ（requestコンテキストなしでシリアライズ）
    # WebSocket経由ではrequestオブジェクトがないため、手動でデータを構築
    def get_user_data(user):
        """ユーザーデータを取得"""
        try:
            profile = getattr(user, 'profile', None)
            icon_url = profile.icon_url if profile else ''
            score = profile.score if profile else 0
        except Exception:
            icon_url = ''
            score = 0
        
        return {
            'id': user.id,
            'username': user.username,
            'email': user.email if hasattr(user, 'email') else '',
            'icon_url': icon_url,
            'score': score,
        }
    
    message_data = {
        'id': message.id,
        'sender': get_user_data(message.sender),
        'recipient': get_user_data(message.recipient),
        'community': {
            'id': message.community.id,
            'name': message.community.name,
            'slug': message.community.slug,
        },
        'subject': message.subject,
        'body': message.body,
        'is_read': message.is_read,
        'read_at': message.read_at.isoformat() if message.read_at else None,
        'created_at': message.created_at.isoformat(),
        'updated_at': message.updated_at.isoformat(),
    }
    
    # 受信者にメッセージを送信
    recipient_group_name = f'user_{message.recipient_id}'
    async_to_sync(channel_layer.group_send)(
        recipient_group_name,
        {
            'type': 'new_message',
            'message': message_data,
        }
    )
    
    # コミュニティグループにも送信（コミュニティ別のWebSocket接続がある場合）
    community_group_name = f'community_{message.community_id}'
    async_to_sync(channel_layer.group_send)(
        community_group_name,
        {
            'type': 'new_message',
            'message': message_data,
        }
    )


def notify_message_read(message):
    """メッセージが既読になったことをWebSocketで通知する"""
    if not settings.ENABLE_WEBSOCKET:
        return  # WebSocketが無効な場合は何もしない
    
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    
    # 送信者に既読通知を送信
    sender_group_name = f'user_{message.sender_id}'
    async_to_sync(channel_layer.group_send)(
        sender_group_name,
        {
            'type': 'message_read',
            'message_id': message.id,
            'read_at': message.read_at.isoformat() if message.read_at else None,
        }
    )
    
    # コミュニティグループにも送信
    community_group_name = f'community_{message.community_id}'
    async_to_sync(channel_layer.group_send)(
        community_group_name,
        {
            'type': 'message_read',
            'message_id': message.id,
            'read_at': message.read_at.isoformat() if message.read_at else None,
        }
    )


def send_group_chat_message_via_websocket(message):
    """グループチャットメッセージをWebSocket経由で送信する"""
    if not settings.ENABLE_WEBSOCKET:
        return  # WebSocketが無効な場合は何もしない
    
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    
    # メッセージをシリアライズ（requestコンテキストなしでシリアライズ）
    def get_user_data(user):
        """ユーザーデータを取得"""
        try:
            profile = getattr(user, 'profile', None)
            icon_url = profile.icon_url if profile else ''
            score = profile.score if profile else 0
        except Exception:
            icon_url = ''
            score = 0
        
        return {
            'id': user.id,
            'username': user.username,
            'email': user.email if hasattr(user, 'email') else '',
            'icon_url': icon_url,
            'score': score,
        }
    
    message_data = {
        'id': message.id,
        'sender': get_user_data(message.sender),
        'community': {
            'id': message.community.id,
            'name': message.community.name,
            'slug': message.community.slug,
        },
        'body': message.body,
        'created_at': message.created_at.isoformat(),
        'updated_at': message.updated_at.isoformat(),
    }
    
    # コミュニティグループにメッセージを送信（すべての参加モデレーターに配信）
    community_group_name = f'community_{message.community_id}'
    async_to_sync(channel_layer.group_send)(
        community_group_name,
        {
            'type': 'new_group_chat_message',
            'message': message_data,
        }
    )

