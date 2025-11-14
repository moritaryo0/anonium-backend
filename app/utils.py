from __future__ import annotations

"""アプリケーション共通のユーティリティ関数。"""

from urllib.parse import urlparse
import io
import os
import logging

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


def invalidate_cache(pattern: str | None = None, key: str | None = None) -> None:
    """Workersのキャッシュを削除する（無効化済み）
    
    この関数は何も行いません。workersのキャッシュ機能は削除されました。
    
    Args:
        pattern: パターンマッチ（無視される）
        key: 特定のキャッシュキー（無視される）
    
    注意:
        - この関数は互換性のために残されていますが、何も行いません
        - バックエンドコードからの呼び出しはエラーを発生させません
    """
    # キャッシュ削除機能は無効化されました
    # ログ出力のみ行う（デバッグ用）
    if pattern or key:
        logger.debug(
            f"Cache invalidation called but disabled: pattern={pattern}, key={key}. "
            "Workers cache feature has been removed."
        )


def delete_media_file_by_url(url: str | None) -> None:
    """MEDIA_URL配下のURLからローカルに保存されたファイルを削除する。

    - `url` が空、あるいは MEDIA_URL 配下でない場合は何もしない。
    - 該当ファイルが存在しない場合も例外を投げずに終了する。
    """

    if not url:
        return

    parsed = urlparse(url)
    path = parsed.path
    if not path:
        return

    media_url = settings.MEDIA_URL or ""
    media_path = urlparse(media_url).path if media_url else ""
    if not media_path:
        media_path = media_url

    if media_path and not media_path.startswith("/"):
        media_path = f"/{media_path}"

    if not media_path or not path.startswith(media_path):
        return

    relative_path = path[len(media_path) :].lstrip("/")
    if not relative_path:
        return

    if default_storage.exists(relative_path):
        default_storage.delete(relative_path)


def upload_image_to_gcs(image, folder: str, filename: str) -> str:
    """PIL ImageをGoogle Cloud Storageにアップロードする。
    
    Args:
        image: PIL Imageオブジェクト
        folder: GCS内のフォルダパス（例: 'posts/images'）
        filename: ファイル名（例: 'pimg-123-456789.jpg'）
    
    Returns:
        GCS上の公開URL
    
    Raises:
        Exception: アップロードに失敗した場合
    """
    if not settings.GCS_ENABLED:
        raise ValueError("GCS is not enabled")
    
    from google.cloud import storage
    from google.oauth2 import service_account
    from pathlib import Path
    
    # 認証情報の設定
    if settings.GCS_CREDENTIALS_PATH:
        # パスの解決を試みる
        creds_path = Path(settings.GCS_CREDENTIALS_PATH)
        if not creds_path.is_absolute() or not creds_path.exists():
            # プロジェクトルートからの相対パスを試す
            base_dir = Path(settings.BASE_DIR)
            alt_path = base_dir / settings.GCS_CREDENTIALS_PATH.lstrip('/')
            if alt_path.exists():
                creds_path = alt_path
            else:
                # /appからのパスを試す（Docker環境）
                docker_path = Path('/app') / settings.GCS_CREDENTIALS_PATH.lstrip('/')
                if docker_path.exists():
                    creds_path = docker_path
        
        if not creds_path.exists():
            raise FileNotFoundError(
                f"GCS認証情報ファイルが見つかりません: {settings.GCS_CREDENTIALS_PATH} "
                f"(解決試行: {creds_path})"
            )
        
        logger.debug(f"Using GCS credentials from: {creds_path}")
        credentials = service_account.Credentials.from_service_account_file(
            str(creds_path)
        )
        client = storage.Client(credentials=credentials, project=settings.GCS_PROJECT_ID)
    else:
        # 環境変数GOOGLE_APPLICATION_CREDENTIALSが設定されている場合
        logger.debug("Using GOOGLE_APPLICATION_CREDENTIALS environment variable")
        client = storage.Client(project=settings.GCS_PROJECT_ID)
    
    bucket = client.bucket(settings.GCS_BUCKET_NAME)
    
    # 画像をメモリ上でJPEG形式に変換
    image_buffer = io.BytesIO()
    image.save(image_buffer, format='JPEG', quality=85)
    image_buffer.seek(0)
    
    # GCSにアップロード
    blob_path = f"{folder}/{filename}"
    blob = bucket.blob(blob_path)
    blob.content_type = 'image/jpeg'
    blob.cache_control = 'public, max-age=31536000'  # 1年間キャッシュ
    blob.upload_from_file(image_buffer)
    
    # 公開読み取り可能にする（プレビュー表示のため）
    try:
        blob.make_public()
        logger.debug(f"Made blob public: {blob_path}")
    except Exception as e:
        # 公開設定に失敗しても続行（バケット全体が公開されている場合など）
        logger.warning(f"Failed to make blob public (may already be public): {e}")
    
    # 公開URLを返す
    if settings.GCS_PUBLIC_URL:
        # GCS_PUBLIC_URLが設定されている場合、そのURLを使用
        # 末尾のスラッシュを処理
        base_url = settings.GCS_PUBLIC_URL.rstrip('/')
        # blob_pathの先頭スラッシュも削除（二重スラッシュを防ぐ）
        clean_blob_path = blob_path.lstrip('/')
        public_url = f"{base_url}/{clean_blob_path}"
    else:
        # デフォルトの公開URL（storage.googleapis.com）
        public_url = blob.public_url
    
    logger.debug(f"Generated public URL: {public_url}")
    return public_url


def save_image_locally_or_gcs(image, folder: str, filename: str, request) -> str:
    """画像をローカルまたはGCSに保存する（環境に応じて自動選択）。
    
    Args:
        image: PIL Imageオブジェクト
        folder: フォルダパス（例: 'posts/images'）
        filename: ファイル名（例: 'pimg-123-456789.jpg'）
        request: Django requestオブジェクト（ローカル保存時のURL生成に使用）
    
    Returns:
        画像の公開URL
    """
    if settings.GCS_ENABLED:
        try:
            return upload_image_to_gcs(image, folder, filename)
        except Exception as e:
            # GCSアップロードに失敗した場合はローカルにフォールバック
            error_details = {
                'error_type': type(e).__name__,
                'error_message': str(e),
                'gcs_enabled': settings.GCS_ENABLED,
                'gcs_bucket_name': settings.GCS_BUCKET_NAME,
                'gcs_project_id': settings.GCS_PROJECT_ID,
                'gcs_credentials_path': settings.GCS_CREDENTIALS_PATH,
                'has_goog_app_creds': bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS')),
            }
            logger.warning(
                f"Failed to upload to GCS: {e}, falling back to local storage. "
                f"Details: {error_details}",
                exc_info=True
            )
    
    # ローカル保存（既存の動作）
    dir_path = os.path.join(settings.MEDIA_ROOT, folder)
    os.makedirs(dir_path, exist_ok=True)
    file_path = os.path.join(dir_path, filename)
    image.save(file_path, format='JPEG', quality=85)
    
    rel_url = f"{settings.MEDIA_URL}{folder}/{filename}"
    abs_url = request.build_absolute_uri(rel_url)
    return abs_url


def delete_file_from_gcs(url: str | None) -> None:
    """GCSからファイルを削除する。
    
    Args:
        url: GCS上のファイルURL
    """
    if not url or not settings.GCS_ENABLED:
        return
    
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
        
        # 認証情報の設定
        if settings.GCS_CREDENTIALS_PATH:
            credentials = service_account.Credentials.from_service_account_file(
                settings.GCS_CREDENTIALS_PATH
            )
            client = storage.Client(credentials=credentials, project=settings.GCS_PROJECT_ID)
        else:
            # 環境変数GOOGLE_APPLICATION_CREDENTIALSが設定されている場合
            client = storage.Client(project=settings.GCS_PROJECT_ID)
        
        bucket = client.bucket(settings.GCS_BUCKET_NAME)
        
        parsed = urlparse(url)
        # URLからパスを抽出
        # 例: https://storage.googleapis.com/bucket-name/posts/images/file.jpg
        # または: https://cdn.example.com/posts/images/file.jpg
        path = parsed.path.lstrip('/')
        
        # GCS_PUBLIC_URLが設定されている場合、そのパス部分を除去
        if settings.GCS_PUBLIC_URL:
            public_url_parsed = urlparse(settings.GCS_PUBLIC_URL)
            public_path = public_url_parsed.path.lstrip('/')
            if path.startswith(public_path):
                path = path[len(public_path):].lstrip('/')
        
        # バケット名がパスに含まれている場合は除去
        if path.startswith(settings.GCS_BUCKET_NAME + '/'):
            path = path[len(settings.GCS_BUCKET_NAME) + 1:]
        
        if not path:
            return
        
        blob = bucket.blob(path)
        blob.delete()
    except Exception as e:
        logger.warning(f"Failed to delete file from GCS: {e}")


def delete_media_file_by_url(url: str | None) -> None:
    """MEDIA_URL配下のURLからローカルまたはGCSに保存されたファイルを削除する。
    
    GCSが有効な場合はGCSから、そうでない場合はローカルから削除を試みる。
    """
    if not url:
        return
    
    # GCSが有効で、URLがGCSの公開URLの場合はGCSから削除
    if settings.GCS_ENABLED and settings.GCS_PUBLIC_URL and settings.GCS_PUBLIC_URL in url:
        delete_file_from_gcs(url)
        return
    
    # storage.googleapis.comのURLもチェック
    if settings.GCS_ENABLED and 'storage.googleapis.com' in url:
        delete_file_from_gcs(url)
        return
    
    # ローカルファイルの削除（既存の動作）
    parsed = urlparse(url)
    path = parsed.path
    if not path:
        return

    media_url = settings.MEDIA_URL or ""
    media_path = urlparse(media_url).path if media_url else ""
    if not media_path:
        media_path = media_url

    if media_path and not media_path.startswith("/"):
        media_path = f"/{media_path}"

    if not media_path or not path.startswith(media_path):
        return

    relative_path = path[len(media_path) :].lstrip("/")
    if not relative_path:
        return

    if default_storage.exists(relative_path):
        default_storage.delete(relative_path)

