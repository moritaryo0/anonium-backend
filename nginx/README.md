# Nginx設定

このディレクトリには本番環境用のnginx設定ファイルが含まれています。

## 構成

- `nginx.conf`: nginxのメイン設定ファイル
- `conf.d/default.conf`: Djangoアプリケーション用のリバースプロキシ設定
- `logs/`: nginxログファイルの保存先

## 使用方法

### 起動

```bash
# 既存のcomposeファイルと組み合わせて起動
docker-compose -f docker-compose.prod.yml -f docker-compose.prod.nginx.yml up -d
```

### 設定の確認

```bash
# nginx設定ファイルの構文チェック
docker-compose -f docker-compose.prod.yml -f docker-compose.prod.nginx.yml exec nginx nginx -t

# nginx設定の再読み込み
docker-compose -f docker-compose.prod.yml -f docker-compose.prod.nginx.yml exec nginx nginx -s reload
```

## 機能

- **リバースプロキシ**: Djangoアプリケーション（gunicorn）へのリクエストを転送
- **静的ファイル配信**: `/static/` と `/media/` を直接配信（キャッシュ設定あり）
- **gzip圧縮**: レスポンスの圧縮による転送量削減
- **セキュリティヘッダー**: 基本的なセキュリティヘッダーの設定
- **ログ管理**: アクセスログとエラーログの分離

## SSL/TLS設定

`conf.d/default.conf` にSSL設定のコメントアウトされた例があります。
証明書を準備して、該当箇所のコメントを外して設定してください。

## 注意事項

- 静的ファイルとメディアファイルはボリュームマウントで配信されます
- `collectstatic` が実行されていることを確認してください
- 本番環境では適切な `ALLOWED_HOSTS` を設定してください

