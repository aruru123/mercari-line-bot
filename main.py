"""
LINE → Gemini → メルカリ 自動下書き作成サーバー
"""

import os
import json
import logging
import threading
import hmac
import hashlib
import base64
import requests
from flask import Flask, request, abort

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── 環境変数 ────────────────────────────────────────────
LINE_ACCESS_TOKEN  = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
MERCARI_COOKIES    = os.environ.get("MERCARI_COOKIES", "[]")   # JSON配列


# ── LINE ヘルパー ──────────────────────────────────────────
def verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def reply_message(reply_token: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={
            "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"replyToken": reply_token,
              "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )


def push_message(user_id: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": user_id,
              "messages": [{"type": "text", "text": text}]},
        timeout=10,
    )


def download_image(message_id: str) -> bytes:
    resp = requests.get(
        f"https://api-data.line.me/v2/bot/message/{message_id}/content",
        headers={"Authorization": f"Bearer {LINE_ACCESS_TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


# ── バックグラウンド処理 ────────────────────────────────────────────
def process_image(message_id: str, user_id: str):
    from gemini_service import analyze_product_image
    from mercari_service import create_mercari_draft

    try:
        # 1. 画像ダウンロード
        logger.info(f"Downloading image {message_id}")
        image_bytes = download_image(message_id)

        # 2. Gemini で商品情報を解析
        logger.info("Analyzing with Gemini Vision...")
        product_info = analyze_product_image(image_bytes, GEMINI_API_KEY)
        if not product_info:
            push_message(user_id, "❌ 商品情報の解析に失敗しました。")
            return

        logger.info(f"Product info: {product_info.get('name')}")

        # 3. メルカリ下書きを作成
        logger.info("Creating Mercari draft...")
        draft_url = create_mercari_draft(product_info, image_bytes, MERCARI_COOKIES)

        # 4. 結果を LINE で通知
        name  = product_info.get("name", "")
        price = product_info.get("price", 0)
        cond  = product_info.get("condition", "")
        desc  = product_info.get("description", "")

        if draft_url:
            msg = (
                f"✅ メルカリ下書きを作成しました！\n\n"
                f"商品名：{name}\n"
                f"価格：¥{price:,}\n"
                f"状態：{cond}\n\n"
                f"👉 下書き確認：\nhttps://jp.mercari.com/mypage/listings"
            )
        else:
            msg = (
                f"📝 商品情報を解析しました\n"
                f"（自動下書き作成は失敗 — 手動でご確認ください）\n\n"
                f"【商品名】\n{name}\n\n"
                f"【価格】¥{price:,}\n\n"
                f"【状態】{cond}\n\n"
                f"【説明】\n{desc[:300]}"
            )

        push_message(user_id, msg)

    except Exception as e:
        logger.error(f"Error: {type(e).__name__}: {e}", exc_info=True)
        push_message(user_id, f"❌ エラー: {type(e).__name__}\n{str(e)[:150]}")


# ── ルート ──────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()

    if not verify_signature(body, signature):
        logger.warning("Invalid signature")
        abort(400)

    events = json.loads(body).get("events", [])

    for event in events:
        if (event.get("type") == "message"
                and event.get("message", {}).get("type") == "image"):

            message_id  = event["message"]["id"]
            user_id     = event["source"]["userId"]
            reply_token = event.get("replyToken", "")

            # すぐに受付確認を返す（reply token は 30 秒で失效）
            reply_message(
                reply_token,
                "📸 画像を受け取りました！\n"
                "商品情報を解析してメルカリ下書きを作成中です...\n"
                "（通常 30～60 秒かかります）"
            )

            # バックグラウンドで処理
            t = threading.Thread(
                target=process_image,
                args=(message_id, user_id),
                daemon=True,
            )
            t.start()

    return "OK", 200


@app.route("/health", methods=["GET"])
def health():
    cookies_ok = MERCARI_COOKIES not in ("[]", "", None)
    return json.dumps({
        "status": "ok",
        "gemini_key": bool(GEMINI_API_KEY),
        "line_token": bool(LINE_ACCESS_TOKEN),
        "mercari_cookies": cookies_ok,
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
