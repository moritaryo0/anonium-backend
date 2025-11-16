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
    from pathlib import Path
    
    BASE_DIR = Path(__file__).resolve().parent.parent
    env_prod_exists = (BASE_DIR / '.env.prod').exists()
    env_dev_exists = (BASE_DIR / '.env.dev').exists()
    
    return JsonResponse({
        "ENVIRONMENT": os.getenv('ENVIRONMENT', 'not set'),
        "ENVIRONMENT_setting": getattr(settings, 'ENVIRONMENT', 'not found'),
        "ALLOWED_HOSTS": settings.ALLOWED_HOSTS,
        "ALLOWED_HOSTS_env": os.getenv('ALLOWED_HOSTS', 'not set'),
        "CORS_ALLOWED_ORIGINS": list(settings.CORS_ALLOWED_ORIGINS),
        "CORS_ALLOWED_ORIGINS_env": os.getenv('CORS_ALLOWED_ORIGINS', 'not set'),
        "CORS_ALLOW_CREDENTIALS": settings.CORS_ALLOW_CREDENTIALS,
        "DEBUG": settings.DEBUG,
        "env_prod_exists": env_prod_exists,
        "env_dev_exists": env_dev_exists,
        "request_host": request.get_host(),
        "request_meta_host": request.META.get('HTTP_HOST', 'not set'),
    }, status=200)

