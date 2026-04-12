"""
Gemini Vision API で商品画像を解析し、メルカリ出品情報を返す
"""

import json
import re
import logging
from io import BytesIO

logger = logging.getLogger(__name__)


def analyze_product_image(image_bytes: bytes, api_key: str) -> dict | None:
    """
    商品画像を Gemini Vision で解析し、出品情報を dict で返す。
    失敗時は None を返す。
    """
    try:
        import google.generativeai as genai
        from PIL import Image

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        image = Image.open(BytesIO(image_bytes))

        prompt = """この商品画像を分析して、メルカリへの出品に必要な情報を
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

        response = model.generate_content([prompt, image])
        text = response.text.strip()
        # コードブロック記号を除去
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        text = re.sub(r"```\s*$", "", text).strip()

        product_info = json.loads(text)
        logger.info(f"Gemini analysis OK: {product_info.get('name')}")
        return product_info

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nRaw: {text[:200]}")
        return None
    except Exception as e:
        logger.error(f"Gemini error: {e}", exc_info=True)
        return None


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
    return 3  # デフォ・ト：目立った傷や汚れなし
