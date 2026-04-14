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

FALLBACK = {
    "name": "商品",
    "description": "（自動解析失敗のため手動入力が必要です）",
    "condition": "目立った傷や汚れなし",
    "price": 1000,
    "brand": "",
    "color": "",
    "size": "",
}


def _call_gemini_rest(image_bytes: bytes, api_key: str, model: str) -> str:
    """Gemini REST API を呼び出してテキストを返す"""
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
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
    resp = http_requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def analyze_product_image(image_bytes: bytes, api_key: str) -> dict | None:
    """
    商品画像を Gemini Vision で解析し、出品情報を dict で返す。
    失敗時もフォールバック dict を返す（None は返さない）。
    """
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

    for model in models:
        try:
            logger.info(f"Trying Gemini model: {model}")
            text = _call_gemini_rest(image_bytes, api_key, model)

            # コードブロック記号を除去
            text = re.sub(r"```(?:json)?\s*", "", text).strip()
            text = re.sub(r"```\s*$", "", text).strip()

            product_info = json.loads(text)
            logger.info(f"Gemini OK ({model}): {product_info.get('name')}")
            return product_info

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error ({model}): {e}")
            # JSON エラーでもテキストが取れたならフォールバックで続行
            return {**FALLBACK, "description": f"自動解析部分失敗: {text[:200]}"}

        except http_requests.HTTPError as e:
            status = e.response.status_code if e.response else "?"
            body = e.response.text[:300] if e.response else ""
            logger.warning(f"HTTP {status} for {model}: {body}")
            # 404 (model not found) なら次のモデルを試す
            if status == 404:
                continue
            # その他の HTTP エラーはフォールバック
            return {**FALLBACK, "description": f"API エラー HTTP {status}: {body[:150]}"}

        except Exception as e:
            logger.error(f"Error ({model}): {type(e).__name__}: {e}", exc_info=True)
            # 予期しないエラーは次のモデルへ
            continue

    # 全モデル失敗
    logger.error("All Gemini models failed")
    return {**FALLBACK, "description": "Gemini API 全モデル失敗。手動入力してください。"}


# ── 状態コード変換 ─────────────────────────────────────────
CONDITION_MAP = {
    "新品未使用":           1,
    "未使用に近い":         2,
    "目立った傷や汚れなし": 3,
    "やや傷や汚れあり":     4,
    "傷や汚れあり":         5,
    "全体的に状態が悪い":   6,
}


def get_condition_id(condition_text: str) -> int:
    for key, val in CONDITION_MAP.items():
        if key in condition_text:
            return val
    return 3  # デフォルト：目立った傷や汚れなし
