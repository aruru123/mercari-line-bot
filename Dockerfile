FROM python:3.11-slim

# Playwright / Chromium に必要なシステムライブラリ
RUN apt-get update && apt-get install -y \
    wget ca-certificates gnupg \
    libnss3 libnspr4 \
    libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python パッケージインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright の Chromium をインストール
RUN playwright install chromium

# アプリコードをコピー
COPY . .

EXPOSE 5000

# タイムアヰトを長めに設定（Playwright が 60 秒程度かかる）
CMD gunicorn main:app \
    --bind 0.0.0.0:${PORT:-5000} \
    --timeout 120 \
    --workers 2 \
    --threads 4
