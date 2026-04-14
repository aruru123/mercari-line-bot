"""
Gemini Vision API で商品画像を解析し、メルカリ出品情報を返す
REST API を直接使用（SDK バージョン問題を回避）
"""

import json
import re
import base64
import logging
import requests as http_requests

logger = logging.getLogger(__name__)

PROMPT = """この商品画像を分析して、メルカリへの出品に必要な情報を
以下の JSON 形式で返してください。JSON のみ返し、他のテキストは不要です。

{
  "name": "商品名（ブランド名＋種別＋特徴を含む具体的な名前、40文字以内）",
  "description": "商品説明（素材・サイズ・状態・特徴などを詳細に。改行を使って読みやすく。300文字程度）",
  "condition": "状態（新品未使用 / 未使用に近い / 目立った傷や汚れなし / やや傷や汚れあり / 傷や汚れあり / 全体的に状態が悪い）",
  "price": 推定売値の整数（円）,
  "brand": "ブランド名（不明なら空文字）",
  "color": "色（不明なら空文字）",
  "size": "サイズ（不明なら空文字）"
}"""

MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]


def analyze_product_image(image_bytes: bytes, api_key: str) -> dict | None:
    """
    商品画像を Gemini Vision で解析し、出品情報を dict で返す。
    失敗時もフォールバック dict を返す。
    """
    img_size_kb = len(image_bytes) // 1024
    logger.info(f"Image size: {img_size_kb} KB")

    # 画像が大きすぎる場合はリサイズ
    if img_size_kb > 3000:
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(image_bytes))
            img.thumbnail((1920, 1920))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            image_bytes = buf.getvalue()
            img_size_kb = len(image_bytes) // 1024
            logger.info(f"Resized to: {img_size_kb} KB")
        except Exception as e:
            logger.warning(f"Resize failed: {e}")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    errors = []

    for model in MODELS:
        try:
            logger.info(f"Trying: {model}")
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models"
                f"/{model}:generateContent?key={api_key}"
            )
            payload = {
                "contents": [{
                    "parts": [
                        {"text": PROMPT},
                        {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                    ]
                }],
                "generationConfig": {"temperature": 0.1},
            }
            resp = http_requests.post(url, json=payload, timeout=90)
            logger.info(f"HTTP {resp.status_code} from {model}")

            if resp.status_code != 200:
                err = f"{model}→HTTP{resp.status_code}"
                errors.append(err)
                logger.warning(f"{err}: {resp.text[:200]}")
                continue

            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

            # コードブロック除去
            text = re.sub(r"```(?:json)?\s*", "", text).strip()
            text = re.sub(r"```\s*$", "", text).strip()

            product_info = json.loads(text)
            logger.info(f"OK ({model}): {product_info.get('name')}")
            return product_info

        except json.JSONDecodeError as e:
            logger.error(f"JSON error ({model}): {e}")
            return {
                "name": "商品（解析部分失敗）",
                "description": f"テキスト取得済みだがJSON変換失敗: {text[:200]}",
                "condition": "目立った傷や汚れなし",
                "price": 1000,
                "brand": "", "color": "", "size": "",
            }
        except Exception as e:
            err = f"{model}→{type(e).__name__}:{str(e)[:80]}"
            errors.append(err)
            logger.error(f"Error: {err}")
            continue

    # 全モデル失敗
    err_summary = " / ".join(errors) if errors else "不明"
    logger.error(f"All models failed: {err_summary}")
    return {
        "name": "商品",
        "description": f"[DEBUG] 画像{img_size_kb}KB / エラー: {err_summary[:200]}",
        "condition": "目立った傷や汚れなし",
        "price": 1000,
        "brand": "", "color": "", "size": "",
    }


CONDITION_MAP = {
    "新品未使用": 1, "未使用に近い": 2, "目立った傷や汚れなし": 3,
    "やや傷や汚れあり": 4, "傷や汚れあり": 5, "全体的に状態が悪い": 6,
}

def get_condition_id(condition_text: str) -> int:
    for key, val in CONDITION_MAP.items():
        if key in condition_text:
            return val
    return 3
