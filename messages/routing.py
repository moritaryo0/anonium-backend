from django.urls import path
from . import consumers

websocket_urlpatterns = [
    # すべてのメッセージを受信するWebSocket
    path('ws/messages/', consumers.MessageConsumer.as_asgi()),
    
    # コミュニティ別のメッセージを受信するWebSocket
    path('ws/messages/community/<int:community_id>/', consumers.CommunityMessageConsumer.as_asgi()),
]

