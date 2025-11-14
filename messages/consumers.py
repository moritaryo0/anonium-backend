import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from .models import Message
from communities.models import CommunityMembership as CM


class MessageConsumer(AsyncWebsocketConsumer):
    """メッセージ用のWebSocket Consumer
    
    リアルタイムでメッセージの送受信、既読状態の更新を処理します
    """
    
    async def connect(self):
        """WebSocket接続時の処理"""
        self.user = self.scope['user']
        
        # 認証チェック
        if self.user.is_anonymous:
            await self.close()
            return
        
        # ユーザーごとのチャンネルグループに参加
        self.user_group_name = f'user_{self.user.id}'
        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # 接続通知を送信
        await self.send(text_data=json.dumps({
            'type': 'connection',
            'message': 'connected',
            'user_id': self.user.id,
        }))
    
    async def disconnect(self, close_code):
        """WebSocket切断時の処理"""
        if hasattr(self, 'user_group_name'):
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )
    
    async def receive(self, text_data):
        """クライアントからメッセージを受信した時の処理"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'ping':
                # ハートビート（接続確認）
                await self.send(text_data=json.dumps({
                    'type': 'pong',
                }))
            
            elif message_type == 'mark_read':
                # メッセージを既読にする
                message_id = data.get('message_id')
                if message_id:
                    await self.mark_message_as_read(message_id)
            
            elif message_type == 'get_unread_count':
                # 未読メッセージ数を取得
                community_id = data.get('community_id')
                count = await self.get_unread_count(community_id)
                await self.send(text_data=json.dumps({
                    'type': 'unread_count',
                    'count': count,
                    'community_id': community_id,
                }))
        
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON',
            }))
    
    async def mark_message_as_read(self, message_id):
        """メッセージを既読にする"""
        message = await self.get_message(message_id)
        if message and message.recipient == self.user and not message.is_read:
            await database_sync_to_async(self._mark_read)(message)
            # 送信者にも既読通知を送信
            sender_group_name = f'user_{message.sender_id}'
            await self.channel_layer.group_send(
                sender_group_name,
                {
                    'type': 'message_read',
                    'message_id': message_id,
                    'read_at': message.read_at.isoformat() if message.read_at else None,
                }
            )
    
    def _mark_read(self, message):
        """メッセージを既読にする（同期関数）"""
        message.is_read = True
        message.read_at = timezone.now()
        message.save(update_fields=['is_read', 'read_at'])
    
    @database_sync_to_async
    def get_message(self, message_id):
        """メッセージを取得"""
        try:
            return Message.objects.get(pk=message_id)
        except Message.DoesNotExist:
            return None
    
    @database_sync_to_async
    def get_unread_count(self, community_id=None):
        """未読メッセージ数を取得"""
        queryset = Message.objects.filter(
            recipient=self.user,
            is_read=False
        )
        if community_id:
            queryset = queryset.filter(community_id=community_id)
        return queryset.count()
    
    # WebSocketメッセージハンドラー
    
    async def new_message(self, event):
        """新しいメッセージを受信した時の処理"""
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'message': event['message'],
        }))
    
    async def message_read(self, event):
        """メッセージが既読になった時の処理"""
        await self.send(text_data=json.dumps({
            'type': 'message_read',
            'message_id': event['message_id'],
            'read_at': event.get('read_at'),
        }))
    
    async def new_group_chat_message(self, event):
        """新しいグループチャットメッセージを受信した時の処理"""
        await self.send(text_data=json.dumps({
            'type': 'new_group_chat_message',
            'message': event['message'],
        }))


class CommunityMessageConsumer(AsyncWebsocketConsumer):
    """コミュニティ別のメッセージ用WebSocket Consumer
    
    特定のコミュニティのメッセージをリアルタイムで受信します
    """
    
    async def connect(self):
        """WebSocket接続時の処理"""
        self.user = self.scope['user']
        self.community_id = self.scope['url_route']['kwargs']['community_id']
        
        print(f"WebSocket connection attempt: user={self.user.id if not self.user.is_anonymous else 'Anonymous'}, community_id={self.community_id}")
        
        # 認証チェック
        if self.user.is_anonymous:
            print(f"WebSocket connection rejected: User is anonymous")
            await self.close(code=4001)  # 認証エラーを示すカスタムコード
            return
        
        # コミュニティのモデレーターかチェック
        is_moderator = await self.check_moderator_permission()
        if not is_moderator:
            print(f"WebSocket connection rejected: User {self.user.id} is not a moderator of community {self.community_id}")
            await self.close(code=4003)  # 権限エラーを示すカスタムコード
            return
        
        # コミュニティごとのチャンネルグループに参加
        self.community_group_name = f'community_{self.community_id}'
        await self.channel_layer.group_add(
            self.community_group_name,
            self.channel_name
        )
        
        # ユーザーごとのチャンネルグループにも参加（個人宛メッセージ用）
        self.user_group_name = f'user_{self.user.id}'
        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )
        
        await self.accept()
        print(f"WebSocket connection accepted: user={self.user.id}, community_id={self.community_id}")
        
        # 接続通知を送信
        await self.send(text_data=json.dumps({
            'type': 'connection',
            'message': 'connected',
            'community_id': self.community_id,
            'user_id': self.user.id,
        }))
    
    async def disconnect(self, close_code):
        """WebSocket切断時の処理"""
        print(f"WebSocket disconnecting: user={self.user.id if hasattr(self, 'user') and not self.user.is_anonymous else 'Anonymous'}, community_id={self.community_id if hasattr(self, 'community_id') else 'N/A'}, close_code={close_code}")
        if hasattr(self, 'community_group_name'):
            await self.channel_layer.group_discard(
                self.community_group_name,
                self.channel_name
            )
        if hasattr(self, 'user_group_name'):
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )
    
    async def receive(self, text_data):
        """クライアントからメッセージを受信した時の処理"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'ping':
                # ハートビート（接続確認）
                await self.send(text_data=json.dumps({
                    'type': 'pong',
                }))
            
            elif message_type == 'mark_read':
                # メッセージを既読にする
                message_id = data.get('message_id')
                if message_id:
                    await self.mark_message_as_read(message_id)
        
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON',
            }))
    
    @database_sync_to_async
    def check_moderator_permission(self):
        """コミュニティのモデレーターかチェック"""
        membership = CM.objects.filter(
            community_id=self.community_id,
            user=self.user,
            status=CM.Status.APPROVED,
            role__in=[CM.Role.OWNER, CM.Role.ADMIN_MODERATOR, CM.Role.MODERATOR]
        ).first()
        return membership is not None
    
    async def mark_message_as_read(self, message_id):
        """メッセージを既読にする"""
        message = await self.get_message(message_id)
        if message and message.recipient == self.user and not message.is_read:
            await database_sync_to_async(self._mark_read)(message)
            # 送信者にも既読通知を送信
            sender_group_name = f'user_{message.sender_id}'
            await self.channel_layer.group_send(
                sender_group_name,
                {
                    'type': 'message_read',
                    'message_id': message_id,
                    'read_at': message.read_at.isoformat() if message.read_at else None,
                }
            )
    
    def _mark_read(self, message):
        """メッセージを既読にする（同期関数）"""
        message.is_read = True
        message.read_at = timezone.now()
        message.save(update_fields=['is_read', 'read_at'])
    
    @database_sync_to_async
    def get_message(self, message_id):
        """メッセージを取得"""
        try:
            return Message.objects.get(pk=message_id, community_id=self.community_id)
        except Message.DoesNotExist:
            return None
    
    # WebSocketメッセージハンドラー
    
    async def new_message(self, event):
        """新しいメッセージを受信した時の処理"""
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'message': event['message'],
        }))
    
    async def message_read(self, event):
        """メッセージが既読になった時の処理"""
        await self.send(text_data=json.dumps({
            'type': 'message_read',
            'message_id': event['message_id'],
            'read_at': event.get('read_at'),
        }))
    
    async def new_group_chat_message(self, event):
        """新しいグループチャットメッセージを受信した時の処理"""
        await self.send(text_data=json.dumps({
            'type': 'new_group_chat_message',
            'message': event['message'],
        }))

