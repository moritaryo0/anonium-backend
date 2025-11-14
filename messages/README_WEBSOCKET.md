# WebSocket実装について

## 概要

メッセージアプリにWebSocketを実装し、リアルタイムでのメッセージ送受信と既読通知を可能にしました。

## 実装内容

### 1. バックエンド

- **Django Channels**: WebSocket通信のためのライブラリ
- **JWT認証**: WebSocket接続をJWTトークンで認証
- **Channel Layer**: 開発環境ではIn-Memory、本番環境ではRedis推奨

### 2. WebSocketエンドポイント

#### すべてのメッセージを受信
```
ws://localhost:8000/ws/messages/?token=<JWT_TOKEN>
```

#### コミュニティ別のメッセージを受信
```
ws://localhost:8000/ws/messages/community/<community_id>/?token=<JWT_TOKEN>
```

### 3. WebSocketメッセージタイプ

#### クライアントからサーバーへ

**ping（接続確認）**
```json
{
  "type": "ping"
}
```

**mark_read（既読にする）**
```json
{
  "type": "mark_read",
  "message_id": 1
}
```

**get_unread_count（未読数を取得）**
```json
{
  "type": "get_unread_count",
  "community_id": 1  // オプション
}
```

#### サーバーからクライアントへ

**connection（接続完了）**
```json
{
  "type": "connection",
  "message": "connected",
  "user_id": 1,
  "community_id": 1  // コミュニティ別接続の場合
}
```

**new_message（新しいメッセージ）**
```json
{
  "type": "new_message",
  "message": {
    "id": 1,
    "sender": {...},
    "recipient": {...},
    "community": {...},
    "subject": "件名",
    "body": "本文",
    "is_read": false,
    "read_at": null,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:00:00Z"
  }
}
```

**message_read（メッセージが既読になった）**
```json
{
  "type": "message_read",
  "message_id": 1,
  "read_at": "2024-01-01T00:00:00Z"
}
```

**pong（pingへの応答）**
```json
{
  "type": "pong"
}
```

**unread_count（未読数）**
```json
{
  "type": "unread_count",
  "count": 5,
  "community_id": 1  // オプション
}
```

## フロントエンドでの使用方法（Next.js）

### 1. WebSocket接続

```typescript
const token = 'your_jwt_token'; // 認証トークン
const ws = new WebSocket(`ws://localhost:8000/ws/messages/?token=${token}`);

ws.onopen = () => {
  console.log('WebSocket接続が開きました');
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  switch (data.type) {
    case 'connection':
      console.log('接続完了:', data);
      break;
    case 'new_message':
      console.log('新しいメッセージ:', data.message);
      // UIにメッセージを追加
      break;
    case 'message_read':
      console.log('メッセージが既読になりました:', data.message_id);
      // 既読状態を更新
      break;
    case 'unread_count':
      console.log('未読数:', data.count);
      // 未読数を更新
      break;
  }
};

ws.onerror = (error) => {
  console.error('WebSocketエラー:', error);
};

ws.onclose = () => {
  console.log('WebSocket接続が閉じられました');
};
```

### 2. メッセージを送信

```typescript
// pingを送信（接続確認）
ws.send(JSON.stringify({ type: 'ping' }));

// メッセージを既読にする
ws.send(JSON.stringify({
  type: 'mark_read',
  message_id: 1
}));

// 未読数を取得
ws.send(JSON.stringify({
  type: 'get_unread_count',
  community_id: 1  // オプション
}));
```

### 3. React Hookの例

```typescript
import { useEffect, useRef, useState } from 'react';

function useMessageWebSocket(token: string) {
  const [messages, setMessages] = useState<any[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!token) return;

    const ws = new WebSocket(`ws://localhost:8000/ws/messages/?token=${token}`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.type === 'new_message') {
        setMessages(prev => [data.message, ...prev]);
      } else if (data.type === 'message_read') {
        setMessages(prev => prev.map(msg => 
          msg.id === data.message_id 
            ? { ...msg, is_read: true, read_at: data.read_at }
            : msg
        ));
      } else if (data.type === 'unread_count') {
        setUnreadCount(data.count);
      }
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    // 接続確認のpingを定期的に送信
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000); // 30秒ごと

    return () => {
      clearInterval(pingInterval);
      ws.close();
    };
  }, [token]);

  const markAsRead = (messageId: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'mark_read',
        message_id: messageId
      }));
    }
  };

  const getUnreadCount = (communityId?: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'get_unread_count',
        community_id: communityId
      }));
    }
  };

  return { messages, unreadCount, markAsRead, getUnreadCount };
}
```

## サーバーの起動方法

### 開発環境

```bash
# Django Channelsをインストール
pip install -r requirements.txt

# マイグレーションを実行
python manage.py migrate

# ASGIサーバーで起動（Daphneまたはuvicorn）
pip install daphne
daphne -b 0.0.0.0 -p 8000 app.asgi:application

# またはuvicorn
pip install uvicorn
uvicorn app.asgi:application --host 0.0.0.0 --port 8000
```

### 本番環境

本番環境ではRedisを使用することを推奨します。

1. Redisをインストール・起動
2. `settings.py`の`CHANNEL_LAYERS`をRedisに変更
3. ASGIサーバー（Daphne、uvicorn等）で起動

## 注意事項

1. **認証**: WebSocket接続にはJWTトークンが必要です
2. **CORS**: WebSocketのCORS設定は通常のHTTPリクエストとは異なります
3. **接続管理**: 接続が切断された場合の再接続処理を実装してください
4. **エラーハンドリング**: ネットワークエラーや認証エラーに対する適切な処理を実装してください
5. **パフォーマンス**: 大量の接続がある場合は、Redisを使用することを強く推奨します

