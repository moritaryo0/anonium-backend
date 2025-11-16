#!/bin/bash

# nginx経由でのアクセステスト

echo "=========================================="
echo "1. nginx設定の確認"
echo "=========================================="
sudo docker exec backend_nginx nginx -t

echo ""
echo "=========================================="
echo "2. /userpost/ 設定の確認"
echo "=========================================="
sudo docker exec backend_nginx cat /etc/nginx/conf.d/default.conf | grep -A 10 "location /userpost/"

echo ""
echo "=========================================="
echo "3. nginx経由で /userpost/api/accounts/me/ にアクセス"
echo "=========================================="
sudo docker exec backend_nginx curl -v -H "Host: api.anonium.net" http://localhost/userpost/api/accounts/me/ 2>&1 | head -30

echo ""
echo "=========================================="
echo "4. nginx経由で /api/accounts/me/ にアクセス（直接）"
echo "=========================================="
sudo docker exec backend_nginx curl -v -H "Host: api.anonium.net" http://localhost/api/accounts/me/ 2>&1 | head -30

echo ""
echo "=========================================="
echo "5. バックエンドに直接アクセス（Hostヘッダー付き）"
echo "=========================================="
sudo docker exec backend_nginx curl -v -H "Host: api.anonium.net" http://backend:8080/api/accounts/me/ 2>&1 | head -30

echo ""
echo "=========================================="
echo "テスト完了"
echo "=========================================="

