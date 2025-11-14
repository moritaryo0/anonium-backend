FROM python:3.13-slim

# Python 実行時のバイトコード生成を抑止し、ログをフラッシュ
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 依存関係を先にインストール（レイヤーキャッシュの効率化）
COPY requirements.txt /app/requirements.txt
COPY requirements/ /app/requirements/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# アプリケーションコード
COPY . /app

EXPOSE 8000

# 開発用: 自動リロードを有効にして起動
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


