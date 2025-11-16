#!/bin/bash

# SSL証明書ファイルの形式を確認・修正するスクリプト

CERT_FILE="nginx/ssl/origin.crt"
KEY_FILE="nginx/ssl/origin.key"

echo "=========================================="
echo "SSL証明書ファイルの確認"
echo "=========================================="

if [ ! -f "$CERT_FILE" ]; then
    echo "❌ 証明書ファイルが見つかりません: $CERT_FILE"
    exit 1
fi

if [ ! -f "$KEY_FILE" ]; then
    echo "❌ 秘密鍵ファイルが見つかりません: $KEY_FILE"
    exit 1
fi

echo ""
echo "=========================================="
echo "1. 証明書ファイルの内容確認（最初の5行）"
echo "=========================================="
head -5 "$CERT_FILE"

echo ""
echo "=========================================="
echo "2. 証明書ファイルの形式確認"
echo "=========================================="
file "$CERT_FILE"

echo ""
echo "=========================================="
echo "3. 証明書ファイルがPEM形式か確認"
echo "=========================================="
if grep -q "BEGIN CERTIFICATE" "$CERT_FILE"; then
    echo "✅ 証明書ファイルはPEM形式です"
else
    echo "❌ 証明書ファイルがPEM形式ではありません"
    echo ""
    echo "証明書ファイルの最初の行を確認してください:"
    head -1 "$CERT_FILE"
    echo ""
    echo "正しい形式は以下のようになっている必要があります:"
    echo "-----BEGIN CERTIFICATE-----"
fi

echo ""
echo "=========================================="
echo "4. 秘密鍵ファイルの内容確認（最初の5行）"
echo "=========================================="
head -5 "$KEY_FILE"

echo ""
echo "=========================================="
echo "5. 秘密鍵ファイルがPEM形式か確認"
echo "=========================================="
if grep -q "BEGIN.*PRIVATE KEY" "$KEY_FILE"; then
    echo "✅ 秘密鍵ファイルはPEM形式です"
else
    echo "❌ 秘密鍵ファイルがPEM形式ではありません"
    echo ""
    echo "秘密鍵ファイルの最初の行を確認してください:"
    head -1 "$KEY_FILE"
    echo ""
    echo "正しい形式は以下のいずれかである必要があります:"
    echo "-----BEGIN PRIVATE KEY-----"
    echo "または"
    echo "-----BEGIN RSA PRIVATE KEY-----"
fi

echo ""
echo "=========================================="
echo "6. ファイルの権限確認"
echo "=========================================="
ls -la "$CERT_FILE" "$KEY_FILE"

echo ""
echo "=========================================="
echo "確認完了"
echo "=========================================="
echo ""
echo "問題がある場合の対処法:"
echo "1. Cloudflareダッシュボードから証明書と秘密鍵を再ダウンロード"
echo "2. 証明書ファイルの最初の行が '-----BEGIN CERTIFICATE-----' であることを確認"
echo "3. 秘密鍵ファイルの最初の行が '-----BEGIN PRIVATE KEY-----' または '-----BEGIN RSA PRIVATE KEY-----' であることを確認"
echo "4. ファイルの末尾に改行があることを確認"
echo "5. 権限を設定: chmod 644 $CERT_FILE && chmod 600 $KEY_FILE"
echo ""

