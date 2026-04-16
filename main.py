"""
LINE → Gemini → メルカリ 自動下書き作成サーバー
複数写真の同時送信を1つの下書きにまとめるバッチ処理対応
"""

import os
import json
import logging
import threading
import hmac
import hashlib
import base64
import requests
from collections import defaultdict
from flask import Flask, request, abort

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── 環境変数 ────────────────────────────────────────────
LINE_ACCESS_TOKEN   = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY", "")
MERCARI_COOKIES     = os.environ.get("MERCARI_COOKIES", "[]")

# ── 画像バッファ（バッチ処理） ────────────────────────────
BATCH_WINDOW = 5  # 最後の写真から何秒待つか
_buffers: dict = defaultdict(list)   # user_id → [image_bytes, ...]
_timers:  dict = {}                   # user_id → threading.Timer
_lock = threading.Lock()


# ── LINE ヘルパー ──────────────────────────────────────────
def verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def push_message(user_id: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": user_id, "messages": [{"type": "text", "text": text}]},
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


# ── バッチ処理 ────────────────────────────────────────────
def _fire_batch(user_id: str):
    """タイマー発火時: バッファの全画像をまとめて処理する"""
    with _lock:
        images = _buffers.pop(user_id, [])
        _timers.pop(user_id, None)

    if not images:
        return

    logger.info(f"バッチ処理開始: user={user_id}, {len(images)}枚")
    threading.Thread(
        target=process_images,
        args=(images, user_id),
        daemon=True,
    ).start()


def add_image_to_batch(message_id: str, user_id: str):
    """画像1枚をダウンロードしてバッファに追加、タイマーをリセット"""
    try:
        logger.info(f"画像ダウンロード中: {message_id}")
        image_bytes = download_image(message_id)
        logger.info(f"ダウンロード完了: {len(image_bytes)//1024}KB")
    except Exception as e:
        logger.error(f"画像ダウンロード失敗: {e}")
        push_message(user_id, f"❌ 画像のダウンロードに失敗しました: {e}")
        return

    with _lock:
        _buffers[user_id].append(image_bytes)
        count = len(_buffers[user_id])

        # 既存タイマーをキャンセルして新しくセット
        if user_id in _timers:
            _timers[user_id].cancel()
        t = threading.Timer(BATCH_WINDOW, _fire_batch, args=[user_id])
        t.daemon = True
        _timers[user_id] = t
        t.start()

    logger.info(f"バッファ: user={user_id} 計{count}枚 ({BATCH_WINDOW}秒後に処理)")

    # 1枚目受信時のみ通知
    if count == 1:
        push_message(
            user_id,
            f"📸 写真を受け取りました。\n"
            f"複数枚ある場合は {BATCH_WINDOW} 秒以内に送ってください。\n"
            f"まとめて1つの下書きを作成します✨"
        )


# ── メイン処理 ────────────────────────────────────────────
def process_images(images: list, user_id: str):
    """複数画像をまとめてGemini解析 → メルカリ下書き作成"""
    from gemini_service import analyze_product_images
    from mercari_service import create_mercari_draft

    try:
        n = len(images)
        logger.info(f"固定テストデータ使用: {n}枚")
        # Gemini無効化 - 固定データでテスト
        product_info = {
            "name": "テスト商品",
            "price": 1000,
            "description": "テストです",
            "condition": "目立った傷や汚れなし",
        }

        name  = product_info.get("name", "商品")
        price = product_info.get("price", 0)
        cond  = product_info.get("condition", "")
        desc  = product_info.get("description", "")
        logger.info(f"商品情報: {name} / ¥{price}")

        # メルカリ下書き作成（全画像を渡す）
        draft_ok = create_mercari_draft(product_info, images, MERCARI_COOKIES)

        if draft_ok:
            msg = (
                f"✅ メルカリ下書きを作成しました！\n\n"
                f"📸 写真：{n}枚\n"
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
    body_bytes = request.get_data()

    if not verify_signature(body_bytes, signature):
        abort(400)

    body = json.loads(body_bytes.decode("utf-8"))

    for event in body.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "image":
            continue

        message_id = msg["id"]
        user_id    = event["source"]["userId"]

        # バックグラウンドで画像をダウンロード & バッファに追加
        threading.Thread(
            target=add_image_to_batch,
            args=(message_id, user_id),
            daemon=True,
        ).start()

    return "OK", 200


@app.route("/", methods=["GET"])
def health():
    return "LINE→メルカリ bot running", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
