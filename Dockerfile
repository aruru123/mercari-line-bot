FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Python パッケージインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリコードをコピー
COPY . .

EXPOSE 5000

# タイムアウトを長めに設定（Playwright が 60 秒程度かかる）
CMD gunicorn main:app \
    --bind 0.0.0.0:${PORT:-5000} \
    --timeout 120 \
    --workers 2 \
    --worker-class sync
