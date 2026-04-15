"""
Gemini Vision API で商品画像を解析し、メルカリ出品情報を返す
複数画像を1リクエストでまとめて解析対応
"""

import json
import re
import base64
import logging
import requests as http_requests
from io import BytesIO

logger = logging.getLogger(__name__)

PROMPT = """これらの商品画像を分析して、メルカリへの出品に必要な情報を
以下の JSON 形式で返してください。JSON のみ返し、他のテキストは不要です。
複数枚の写真がある場合は、すべての写真を参考にして詳細な情報を作成してください。

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

MAX_IMAGE_KB = 3000  # これ以上は圧縮


def _compress_image(image_bytes: bytes, max_kb: int = MAX_IMAGE_KB) -> bytes:
    """画像が大きすぎる場合はリサイズして返す"""
    if len(image_bytes) // 1024 <= max_kb:
        return image_bytes
    try:
        from PIL import Image
        img = Image.open(BytesIO(image_bytes))
        img.thumbnail((1920, 1920))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        compressed = buf.getvalue()
        logger.info(f"画像圧縮: {len(image_bytes)//1024}KB → {len(compressed)//1024}KB")
        return compressed
    except Exception as e:
        logger.warning(f"画像圧縮失敗: {e}")
        return image_bytes


def analyze_product_images(images_bytes_list: list, api_key: str) -> dict | None:
    """
    複数の商品画像を Gemini Vision で解析し、出品情報を dict で返す。
    すべての画像を1つのリクエストに含めて送信する。
    失敗時もフォールバック dict を返す。
    """
    if not images_bytes_list:
        return None

    n = len(images_bytes_list)
    logger.info(f"Gemini解析: {n}枚の画像")

    # 画像を圧縮してbase64エンコード
    encoded_images = []
    for i, img_bytes in enumerate(images_bytes_list):
        compressed = _compress_image(img_bytes)
        logger.info(f"  画像{i+1}: {len(compressed)//1024}KB")
        encoded_images.append(base64.b64encode(compressed).decode())

    errors = []
    for model in MODELS:
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={api_key}"
            )

            # プロンプト + 全画像をpartsに含める
            parts = [{"text": PROMPT}]
            for b64_data in encoded_images:
                parts.append({
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": b64_data,
                    }
                })

            payload = {
                "contents": [{"parts": parts}],
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
                "name": "商品",
                "description": f"[DEBUG] {n}枚画像 / JSONエラー: {e}",
                "condition": "目立った傷や汚れなし",
                "price": 1000,
                "brand": "",
                "color": "",
                "size": "",
            }
        except Exception as e:
            err = f"{model}→{type(e).__name__}"
            errors.append(err)
            logger.error(f"{err}: {e}")
            continue

    # 全モデル失敗
    logger.error(f"全モデル失敗: {errors}")
    return {
        "name": "商品",
        "description": f"[DEBUG] {n}枚 / エラー: {' / '.join(errors)}",
        "condition": "目立った傷や汚れなし",
        "price": 1000,
        "brand": "",
        "color": "",
        "size": "",
    }


# 後方互换のためのラッパー
def analyze_product_image(image_bytes: bytes, api_key: str) -> dict | None:
    return analyze_product_images([image_bytes], api_key)
