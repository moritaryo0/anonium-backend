# Nginx トラブルシューティング

## ポート80が既に使用されているエラー

### 問題
```
Error response from daemon: Bind for 0.0.0.0:80 failed: port is already allocated
```

### 解決方法

#### 方法1: 既存のコンテナを確認して停止

```bash
# ポート80を使用しているコンテナを確認
docker ps | grep :80

# または、すべてのコンテナを確認
docker ps -a

# 既存のnginxコンテナがあれば停止・削除
docker stop <container_name>
docker rm <container_name>
```

#### 方法2: ポートを変更して起動

```bash
# 環境変数でポートを指定（例: 8080）
NGINX_HTTP_PORT=8080 docker-compose -f docker-compose.prod.yml -f docker-compose.prod.nginx.yml up -d

# または、.env.prodファイルに追加
echo "NGINX_HTTP_PORT=8080" >> .env.prod
```

#### 方法3: システムのWebサーバーを停止

```bash
# macOS (Apache/httpd)
sudo launchctl unload -w /System/Library/LaunchDaemons/org.apache.httpd.plist

# Linux (Apache)
sudo systemctl stop apache2

# Linux (Nginx)
sudo systemctl stop nginx
```

## 静的ファイルが表示されない

### 確認事項

1. `collectstatic`が実行されているか確認
```bash
docker-compose -f docker-compose.prod.yml exec backend python manage.py collectstatic --noinput
```

2. ボリュームマウントのパスを確認
- `./staticfiles` ディレクトリが存在するか
- `./media` ディレクトリが存在するか

3. nginx設定ファイルのパスを確認
- `/var/www/static/` が正しくマウントされているか

## ログの確認

```bash
# nginxのアクセスログ
docker-compose -f docker-compose.prod.yml -f docker-compose.prod.nginx.yml exec nginx tail -f /var/log/nginx/access.log

# nginxのエラーログ
docker-compose -f docker-compose.prod.yml -f docker-compose.prod.nginx.yml exec nginx tail -f /var/log/nginx/error.log

# backendのログ
docker-compose -f docker-compose.prod.yml logs -f backend
```

