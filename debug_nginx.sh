#!/bin/bash

# nginxのデバッグスクリプト

echo "=========================================="
echo "1. nginx設定の構文チェック"
echo "=========================================="
sudo docker exec backend_nginx nginx -t 2>&1

echo ""
echo "=========================================="
echo "2. nginxプロセスの状態確認"
echo "=========================================="
sudo docker exec backend_nginx ps aux | grep nginx

echo ""
echo "=========================================="
echo "3. ポート80と443のリッスン状態確認"
echo "=========================================="
sudo docker exec backend_nginx netstat -tlnp 2>/dev/null | grep -E ":80|:443" || \
sudo docker exec backend_nginx ss -tlnp 2>/dev/null | grep -E ":80|:443" || \
echo "netstat/ssコマンドが利用できません"

echo ""
echo "=========================================="
echo "4. nginxのエラーログ（最新20行）"
echo "=========================================="
sudo docker exec backend_nginx tail -n 20 /var/log/nginx/backend_error.log 2>&1 || echo "エラーログが見つかりません"

echo ""
echo "=========================================="
echo "5. SSL証明書ファイルの確認"
echo "=========================================="
sudo docker exec backend_nginx ls -la /etc/nginx/ssl/ 2>&1

echo ""
echo "=========================================="
echo "6. nginx設定ファイルの確認（HTTPS部分）"
echo "=========================================="
sudo docker exec backend_nginx cat /etc/nginx/conf.d/default.conf | grep -A 5 "listen 443"

echo ""
echo "=========================================="
echo "確認完了"
echo "=========================================="

