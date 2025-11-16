"""
Django views for health check and other utility endpoints
"""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings


@csrf_exempt
@require_http_methods(["GET", "HEAD"])
def health_check(request):
    """
    ヘルスチェック用エンドポイント
    コンテナのヘルスチェックで使用
    """
    return JsonResponse({"status": "ok"}, status=200)


@csrf_exempt
@require_http_methods(["GET"])
def debug_config(request):
    """
    デバッグ用エンドポイント（本番環境では削除推奨）
    設定値を確認するために使用
    """
    import os
    return JsonResponse({
        "ENVIRONMENT": os.getenv('ENVIRONMENT', 'not set'),
        "ALLOWED_HOSTS": settings.ALLOWED_HOSTS,
        "CORS_ALLOWED_ORIGINS": list(settings.CORS_ALLOWED_ORIGINS),
        "CORS_ALLOW_CREDENTIALS": settings.CORS_ALLOW_CREDENTIALS,
        "DEBUG": settings.DEBUG,
    }, status=200)

