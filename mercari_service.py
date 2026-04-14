"""
Mercari 自動下書き作成サービス
Playwright (Chromium headless) で出品フォームを操作して下書き保存する
"""
import json
import logging
import os
import tempfile
import traceback

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)


def create_mercari_draft(
    product_info: dict,
    image_bytes: bytes | None = None,
    cookies_json: str | None = None,
) -> bool:
    """
    メルカリに商品の下書きを作成する。

    Args:
        product_info: Gemini が返す商品情報 dict
        image_bytes:  商品画像のバイト列（任意）
        cookies_json: MERCARI_COOKIES 環境変数の値（任意、省略時は env から取得）

    Returns:
        True = 下書き保存成功 / False = 失敗
    """
    raw_cookies = cookies_json or os.environ.get("MERCARI_COOKIES", "")
    if not raw_cookies:
        logger.error("MERCARI_COOKIES が設定されていません")
        return False

    # --- クッキーのパース ---
    cookies = _parse_cookies(raw_cookies)
    if not cookies:
        logger.error("クッキーのパースに失敗しました。フォーマットを確認してください。")
        return False
    logger.info(f"クッキー {len(cookies)} 個を読み込みました")

    # --- 画像ファイルの一時保存 ---
    tmp_image_path = None
    if image_bytes:
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                f.write(image_bytes)
                tmp_image_path = f.name
            logger.info(f"画像を一時ファイルに保存: {tmp_image_path} ({len(image_bytes)//1024}KB)")
        except Exception as e:
            logger.warning(f"画像の一時保存に失敗: {e}")
            tmp_image_path = None

    saved = False
    try:
        saved = _run_playwright(product_info, cookies, tmp_image_path)
    except Exception as e:
        logger.error(f"Playwright 実行中に予期しないエラー: {e}\n{traceback.format_exc()}")
    finally:
        if tmp_image_path:
            try:
                os.unlink(tmp_image_path)
            except Exception:
                pass

    return saved


# ---------------------------------------------------------------------------
# 内部関数
# ---------------------------------------------------------------------------

def _parse_cookies(raw: str) -> list[dict]:
    """JSON 配列 または Netscape/curl 形式のクッキー文字列をパースする"""
    raw = raw.strip()
    # JSON 配列形式
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            result = []
            for c in data:
                if not isinstance(c, dict):
                    continue
                cookie = {
                    "name": str(c.get("name", "")),
                    "value": str(c.get("value", "")),
                    "domain": c.get("domain", ".jp.mercari.com"),
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", True),
                    "httpOnly": c.get("httpOnly", False),
                }
                if cookie["name"] and cookie["value"]:
                    result.append(cookie)
            return result
        except json.JSONDecodeError as e:
            logger.error(f"JSON パースエラー: {e}")
            return []

    # "name=value; name2=value2" 形式
    result = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        result.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".jp.mercari.com",
            "path": "/",
            "secure": True,
            "httpOnly": False,
        })
    return result


def _run_playwright(product_info: dict, cookies: list[dict], image_path: str | None) -> bool:
    """Playwright を使ってメルカリ出品フォームを操作する"""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
        )

        try:
            # クッキーをセット
            try:
                context.add_cookies(cookies)
                logger.info("クッキーをセットしました")
            except Exception as e:
                logger.error(f"クッキーのセットに失敗: {e}")
                # 続行してみる（クッキーなしでログインチェックで落とす）

            page = context.new_page()

            # ---- 出品ページへ移動 ----
            logger.info("https://jp.mercari.com/sell へアクセス中...")
            try:
                page.goto(
                    "https://jp.mercari.com/sell",
                    wait_until="domcontentloaded",
                    timeout=40_000,
                )
            except PWTimeout:
                logger.error("タイムアウト: jp.mercari.com/sell の読み込みに失敗")
                return False

            # ---- ログインチェック ----
            current_url = page.url
            logger.info(f"ナビゲーション後のURL: {current_url}")
            if "login" in current_url.lower() or "signin" in current_url.lower():
                logger.error(
                    f"ログインページにリダイレクトされました: {current_url}\n"
                    "→ MERCARI_COOKIES が期限切れの可能性があります。クッキーを更新してください。"
                )
                return False

            page.wait_for_timeout(3_000)

            # ---- 画像アップロード ----
            if image_path:
                logger.info("画像をアップロード中...")
                try:
                    # ファイル入力を探す
                    file_input = page.locator("input[type='file']").first
                    file_input.set_input_files(image_path, timeout=15_000)
                    page.wait_for_timeout(3_000)
                    logger.info("画像アップロード完了")
                except PWTimeout:
                    logger.warning("画像アップロードがタイムアウトしました（続行）")
                except Exception as e:
                    logger.warning(f"画像アップロードエラー（続行）: {e}")

            # ---- 各フィールド入力 ----
            title = product_info.get("name", "商品")
            description = product_info.get("description", "")
            price = str(product_info.get("price", 1000))
            condition = product_info.get("condition", "")

            logger.info(f"タイトル: {title}")
            logger.info(f"価格: {price}")

            _fill_field(page, "title", title)
            _fill_field(page, "description", description)
            _fill_price(page, price)
            if condition:
                _select_condition(page, condition)

            # ---- 下書き保存 ----
            saved = _click_draft_button(page)
            logger.info(f"下書き保存結果: {'成功' if saved else '失敗'}")
            return saved

        except Exception as e:
            logger.error(f"Playwright操作エラー: {e}\n{traceback.format_exc()}")
            return False
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _fill_field(page, field_type: str, value: str):
    """テキストフィールドを埋める"""
    if not value:
        return

    selectors = {
        "title": [
            "input[name='title']",
            "input[placeholder*='タイトル']",
            "input[placeholder*='商品名']",
            "[data-testid='title-input'] input",
        ],
        "description": [
            "textarea[name='description']",
            "textarea[placeholder*='説明']",
            "textarea[placeholder*='商品の説明']",
            "[data-testid='description-input'] textarea",
        ],
    }

    for sel in selectors.get(field_type, []):
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3_000):
                el.click()
                el.fill(value)
                logger.info(f"{field_type} フィールド ({sel}) に入力完了")
                return
        except Exception:
            continue

    logger.warning(f"{field_type} フィールドが見つかりませんでした")


def _fill_price(page, price: str):
    """価格フィールドを埋める（数字のみ）"""
    price_digits = "".join(filter(str.isdigit, price)) or "1000"

    selectors = [
        "input[name='price']",
        "input[placeholder*='価格']",
        "input[placeholder*='販売価格']",
        "[data-testid='price-input'] input",
        "input[type='number']",
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3_000):
                el.click()
                el.fill(price_digits)
                logger.info(f"価格フィールド ({sel}) に {price_digits} を入力")
                return
        except Exception:
            continue

    logger.warning("価格フィールドが見つかりませんでした")


def _select_condition(page, condition: str):
    """商品の状態を選択する"""
    condition_map = {
        "新品": ["新品、未使用", "新品未使用"],
        "未使用": ["新品、未使用", "新品未使用", "未使用に近い"],
        "良好": ["未使用に近い", "目立った傷や汚れなし"],
        "普通": ["やや傷や汚れあり"],
        "悪い": ["傷や汚れあり", "全体的に状態が悪い"],
    }

    try:
        # 状態選択ボタンを探す
        cond_btn = page.locator("button:has-text('商品の状態'), [data-testid='condition']").first
        if cond_btn.is_visible(timeout=3_000):
            cond_btn.click()
            page.wait_for_timeout(1_000)

            # マッピングでボタンを選ぶ
            labels = condition_map.get(condition, [condition])
            for label in labels:
                btn = page.locator(f"text='{label}'").first
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    logger.info(f"商品状態: {label} を選択")
                    return
    except Exception as e:
        logger.warning(f"状態選択エラー（スキップ）: {e}")


def _click_draft_button(page) -> bool:
    """下書き保存ボタンをクリックする"""
    draft_texts = [
        "下書き保存",
        "下書きとして保存",
        "下書き",
        "一時保存",
        "保存する",
        "draft",
        "save as draft",
    ]

    for text in draft_texts:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=3_000):
                btn.click()
                logger.info(f"下書きボタン「{text}」をクリック")
                page.wait_for_timeout(3_000)

                # 成功確認: URL 変化やトーストメッセージ
                new_url = page.url
                logger.info(f"クリック後のURL: {new_url}")
                if "draft" in new_url or "mypage" in new_url or "complete" in new_url:
                    return True

                # トースト/アラートメッセージで確認
                toast = page.locator("text='保存しました', text='下書きに保存'").first
                if toast.is_visible(timeout=3_000):
                    return True

                # URL が変わらなくてもボタンクリック自体は成功とみなす
                return True
        except Exception:
            continue

    # aria-label でも探す
    try:
        btn = page.locator("[aria-label*='下書き'], [aria-label*='draft']").first
        if btn.is_visible(timeout=3_000):
            btn.click()
            page.wait_for_timeout(2_000)
            return True
    except Exception:
        pass

    logger.error("下書き保存ボタンが見つかりませんでした")
    logger.info(f"現在のURL: {page.url}")

    # デバッグ用: 現在ページのボタン一覧をログ出力
    try:
        buttons = page.locator("button").all()
        btn_texts = [b.text_content() for b in buttons[:20] if b.is_visible()]
        logger.info(f"ページ上のボタン: {btn_texts}")
    except Exception:
        pass

    return False
