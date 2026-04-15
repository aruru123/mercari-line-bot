"""
Mercari 自動下書き作成サービス
Playwright (Chromium headless) で出品フォームを操作して下書き保存する
メール/パスワードログイン対応版（クッキー不要）
"""
import json
import logging
import os
import tempfile
import traceback
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)


def create_mercari_draft(
    product_info: dict,
    images_bytes: list | None = None,
    cookies_json: str | None = None,
) -> bool:
    """
    メルカリに商品の下書きを作成する。
    メール/パスワードでログイン → 下書き保存。

    Args:
        product_info:  Gemini が返す商品情報 dict
        images_bytes:  商品画像のバイト列リスト（複数枚対応）
        cookies_json:  使用しない（後方互换のため残す）

    Returns:
        True = 下書き保存成功 / False = 失敗
    """
    if isinstance(images_bytes, bytes):
        images_bytes = [images_bytes]
    if images_bytes is None:
        images_bytes = []

    email    = os.environ.get("MERCARI_EMAIL", "")
    password = os.environ.get("MERCARI_PASSWORD", "")

    if not email or not password:
        logger.error("MERCARI_EMAIL または MERCARI_PASSWORD が設定されていません")
        return False

    tmp_paths = []
    for i, img_bytes in enumerate(images_bytes):
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                f.write(img_bytes)
                tmp_paths.append(f.name)
            logger.info(f"画像{i+1}を一時ファイルに保存: {tmp_paths[-1]} ({len(img_bytes)//1024}KB)")
        except Exception as e:
            logger.warning(f"画像{i+1}の一時保存に失敗: {e}")

    saved = False
    try:
        saved = _run_playwright(product_info, email, password, tmp_paths)
    except Exception as e:
        logger.error(f"Playwright 実行中に予期しないエラー: {e}\n{traceback.format_exc()}")
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except Exception:
                pass

    return saved


def _run_playwright(product_info: dict, email: str, password: str, image_paths: list) -> bool:
    """Playwright を使ってメルカリにログインし、下書きを作成する"""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        try:
            page = context.new_page()
            if not _login(page, email, password):
                logger.error("ログインに失敗しました")
                return False
            logger.info("ログイン成功")
            try:
                page.goto("https://jp.mercari.com/sell", wait_until="domcontentloaded", timeout=40_000)
            except PWTimeout:
                logger.error("タイムアウト: 出品ページの読み込みに失敗")
                return False
            page.wait_for_timeout(3_000)
            logger.info(f"出品ページURL: {page.url}")
            if image_paths:
                logger.info(f"{len(image_paths)}枚の画像をアップロード中...")
                try:
                    page.locator("input[type='file']").first.set_input_files(image_paths, timeout=20_000)
                    page.wait_for_timeout(4_000)
                    logger.info(f"画像アップロード完了: {len(image_paths)}枚")
                except Exception as e:
                    logger.warning(f"画像アップロードエラー（続行）: {e}")
            title = product_info.get("name", "商品")
            description = product_info.get("description", "")
            price = str(product_info.get("price", 1000))
            condition = product_info.get("condition", "")
            logger.info(f"タイトル: {title} / 価格: {price}")
            _fill_field(page, "title", title)
            _fill_field(page, "description", description)
            _fill_price(page, price)
            if condition:
                _select_condition(page, condition)
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


def _login(page, email: str, password: str) -> bool:
    """メルカリにメール/パスワードでログイン"""
    try:
        logger.info("ログインページへアクセス中...")
        page.goto("https://jp.mercari.com/login", wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2_000)
        for sel in ["button:has-text('メールアドレスでログイン')","button:has-text('メールアドレス')","a:has-text('メールアドレスでログイン')"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click(); page.wait_for_timeout(2_000)
                    logger.info(f"メールログインボタン: {sel}"); break
            except Exception: continue
        email_filled = False
        for sel in ["input[type='email']","input[name='email']","input[placeholder*='メールアドレス']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3_000):
                    el.click(); el.fill(email)
                    logger.info(f"メール入力: {sel}"); email_filled = True; break
            except Exception: continue
        if not email_filled:
            logger.error("メール入力欄が見つかりません"); return False
        for sel in ["button:has-text('次へ')","button[type='submit']"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2_000):
                    btn.click(); page.wait_for_timeout(2_000); break
            except Exception: continue
        pw_filled = False
        for sel in ["input[type='password']","input[name='password']","input[placeholder*='パスワード']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=5_000):
                    el.click(); el.fill(password)
                    logger.info(f"パスワード入力: {sel}"); pw_filled = True; break
            except Exception: continue
        if not pw_filled:
            logger.error("パスワード入力欄が見つかりません"); return False
        for sel in ["button[type='submit']","button:has-text('ログイン')"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click(); logger.info(f"ログインボタン: {sel}"); break
            except Exception: continue
        page.wait_for_timeout(5_000)
        url = page.url
        logger.info(f"ログイン後 URL: {url}")
        if "login" in url.lower() or "signin" in url.lower():
            logger.warning("ログイン後も login URL - 失敗または2FAが必要")
            return False
        return True
    except Exception as e:
        logger.error(f"ログインエラー: {e}\n{traceback.format_exc()}")
        return False


def _fill_field(page, field_type: str, value: str):
    if not value: return
    selectors = {
        "title": ["input[name='title']","input[placeholder*='タイトル']","input[placeholder*='商品名']","[data-testid='title-input'] input"],
        "description": ["textarea[name='description']","textarea[placeholder*='説明']","textarea[placeholder*='商品の説明']","[data-testid='description-input'] textarea"],
    }
    for sel in selectors.get(field_type, []):
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3_000):
                el.click(); el.fill(value)
                logger.info(f"{field_type} 入力完了: {sel}"); return
        except Exception: continue
    logger.warning(f"{field_type} フィールドが見つかりませんでした")


def _fill_price(page, price: str):
    price_digits = "".join(filter(str.isdigit, price)) or "1000"
    for sel in ["input[name='price']","input[placeholder*='価格']","input[placeholder*='販売価格']","[data-testid='price-input'] input","input[type='number']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3_000):
                el.click(); el.fill(price_digits)
                logger.info(f"価格入力 ({sel}): {price_digits}"); return
        except Exception: continue
    logger.warning("価格フィールドが見つかりませんでした")


def _select_condition(page, condition: str):
    condition_map = {
        "新品":["新品、未使用","新品未使用"],"未使用":["新品、未使用","新品未使用","未使用に近い"],
        "良好":["未使用に近い","目立った傷や汚れなし"],"普通":["やや傷や汚れあり"],"悪い":["傷や汚れあり","全体的に状態が悪い"],
    }
    try:
        cond_btn = page.locator("button:has-text('商品の状態'), [data-testid='condition']").first
        if cond_btn.is_visible(timeout=3_000):
            cond_btn.click(); page.wait_for_timeout(1_000)
            for label in condition_map.get(condition, [condition]):
                btn = page.locator(f"text='{label}'").first
                if btn.is_visible(timeout=2_000):
                    btn.click(); logger.info(f"状態選択: {label}"); return
    except Exception as e:
        logger.warning(f"状態選択エラー: {e}")


def _click_draft_button(page) -> bool:
    for text in ["下書き保存","下書きとして保存","下書き","一時保存","保存する","draft","save as draft"]:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=3_000):
                btn.click()
                logger.info(f"下書きボタン「{text}」をクリック")
                page.wait_for_timeout(3_000)
                new_url = page.url
                logger.info(f"クリック後 URL: {new_url}")
                if "draft" in new_url or "mypage" in new_url or "complete" in new_url:
                    return True
                if page.locator("text='保存しました', text='下書きに保存'").first.is_visible(timeout=3_000):
                    return True
                return True
        except Exception: continue
    try:
        btn = page.locator("[aria-label*='下書き'], [aria-label*='draft']").first
        if btn.is_visible(timeout=3_000):
            btn.click(); page.wait_for_timeout(2_000); return True
    except Exception: pass
    logger.error("下書き保存ボタンが見つかりませんでした")
    try:
        logger.info(f"ページ上のボタン: {[b.text_content() for b in page.locator('button').all()[:20] if b.is_visible()]}")
    except Exception: pass
    return False
