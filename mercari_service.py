"""
Mercari 自動下書き作成サービス
Playwright (Chromium headless) + playwright-stealth でbot検知を回避してログイン・下書き保存する
"""
import logging
import os
import tempfile
import traceback
import time
import random

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

logger = logging.getLogger(__name__)


def create_mercari_draft(
    product_info: dict,
    images_bytes: list | None = None,
    cookies_json: str | None = None,
) -> bool:
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


def _human_delay(min_ms=500, max_ms=1500):
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _run_playwright(product_info: dict, email: str, password: str, image_paths: list) -> bool:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--lang=ja-JP",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            extra_http_headers={
                "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'] });
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
        """)

        try:
            page = context.new_page()

            if HAS_STEALTH:
                stealth_sync(page)
                logger.info("playwright-stealth適用済み")
            else:
                logger.warning("playwright-stealth未インストール（bot検知リスクあり）")

            if not _login(page, email, password):
                logger.error("ログインに失敗しました")
                return False

            logger.info("ログイン成功 → 出品ページへ")
            try:
                page.goto(
                    "https://jp.mercari.com/sell",
                    wait_until="domcontentloaded",
                    timeout=40_000,
                )
                page.wait_for_load_state("networkidle", timeout=20_000)
            except PWTimeout:
                logger.error("タイムアウト: 出品ページの読み込みに失敗")
                return False

            _human_delay(2000, 4000)
            logger.info(f"出品ページURL: {page.url}")

            if image_paths:
                logger.info(f"{len(image_paths)}枚の画像をアップロード中...")
                try:
                    file_input = page.locator("input[type='file']").first
                    file_input.set_input_files(image_paths, timeout=20_000)
                    _human_delay(3000, 5000)
                    logger.info(f"画像アップロード完了: {len(image_paths)}枚")
                except PWTimeout:
                    logger.warning("画像アップロードがタイムアウト（続行）")
                except Exception as e:
                    logger.warning(f"画像アップロードエラー（続行）: {e}")

            title       = product_info.get("name", "商品")
            description = product_info.get("description", "")
            price       = str(product_info.get("price", 1000))
            condition   = product_info.get("condition", "")

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
  try:
                browser.close()
            except Exception:
                pass


def _login(page, email: str, password: str) -> bool:

    def _log_state(label: str):
        try:
            url = page.url
            all_inputs = page.evaluate("""
                () => {
                    const inputs = [];
                    document.querySelectorAll('input').forEach(el => {
                        inputs.push({
                            type: el.type,
                            name: el.name,
                            id: el.id,
                            placeholder: el.placeholder,
                            visible: el.offsetParent !== null,
                            autocomplete: el.autocomplete
                        });
                    });
                    return inputs;
                }
            """)
            all_btns = page.evaluate("""
                () => {
                    const btns = [];
                    document.querySelectorAll('button, [role="button"]').forEach(el => {
                        if (el.offsetParent !== null) {
                            btns.push(el.textContent.trim().substring(0, 30));
                        }
                    });
                    return btns.filter(t => t.length > 0).slice(0, 20);
                }
            """)
            logger.info(f"[{label}] URL: {url}")
            logger.info(f"[{label}] inputs: {all_inputs}")
            logger.info(f"[{label}] buttons: {all_btns}")
        except Exception as e:
            logger.warning(f"[{label}] ページ状態取得エラー: {e}")

    try:
        logger.info("メルカリTOPページへアクセス...")
        page.goto(
            "https://jp.mercari.com",
            wait_until="domcontentloaded",
            timeout=40_000,
        )
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        _human_delay(2000, 3000)
        _log_state("TOP")

        logger.info("ナビのログインボタンを探す...")
        login_nav_clicked = False

        for sel in [
            "a[href*='/login']",
            "button:has-text('ログイン')",
            "a:has-text('ログイン')",
        ]:
            try:
                els = page.locator(sel).all()
                for el in els[:5]:
                    try:
                        if el.is_visible(timeout=1000):
                            logger.info(f"ログインリンク発見: {sel}")
                            try:
                                with page.expect_navigation(timeout=15_000):
                                    el.click()
                                login_nav_clicked = True
                            except Exception:
                                el.click()
                                _human_delay(3000, 5000)
                                login_nav_clicked = True
                            break
                    except Exception:
                        continue
                if login_nav_clicked:
                    break
            except Exception:
                continue

        if not login_nav_clicked:
            logger.warning("ナビからのクリック失敗、直接URLへ")
            page.goto("https://jp.mercari.com/login", wait_until="domcontentloaded", timeout=30_000)

        _human_delay(2000, 3000)
        _log_state("ログインページ")

        current_url = page.url
        if "login.jp.mercari.com" not in current_url:
            for sel in ["button:has-text('ログイン')", "a:has-text('ログイン')", "[href*='login']"]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=3000):
                        logger.info(f"再試行: {sel} をクリック")
                        try:
                            with page.expect_navigation(timeout=15_000):
                                btn.click()
                        except Exception:
                            btn.click()
                            _human_delay(4000, 6000)
                        break
                except Exception:
                    continue

        _human_delay(2000, 3000)
        _log_state("auth遷移後")

        for sel in [
            "button:has-text('メールアドレスでログイン')",
            "button:has-text('メールアドレス')",
            "[data-testid='email-login']",
            "mer-button:has-text('メールアドレス')",
        ]:
            try:
                btn = page.locator(sel).first
                btn.wait_for(state="visible", timeout=5_000)
                btn.click()
                logger.info(f"メールアドレスログインボタンクリック: {sel}")
                _human_delay(1500, 2500)
                break
            except Exception:
                continue

        _log_state("メールボタン後")

        logger.info("メールアドレス入力フィールドを待機中...")
        email_css = (
            "input[type='email'], input[name='email'], "
            "input[autocomplete='email'], input[autocomplete='username'], "
            "input[placeholder*='メール'], #email"
        )
        try:
            page.wait_for_selector(email_css, state="visible", timeout=15_000)
        except PWTimeout:
            logger.error("メールアドレス入力欄が見つかりません（15秒待機後）")
            _log_state("メール入力欄タイムアウト")
            return False

        email_el = page.locator(email_css).first
        email_el.click()
        _human_delay(300, 700)
        email_el.fill(email)
        logger.info("メールアドレス入力完了")
        _human_delay(800, 1200)

        for sel in [
            "button:has-text('次へ')",
            "button[type='submit']",
            "input[type='submit']",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    logger.info(f"次へボタンクリック: {sel}")
                    break
            except Exception:
                continue

        _log_state("次へクリック直後")

        logger.info("パスワード入力フィールドを待機中（最大20秒）...")
        pw_css = (
            "input[type='password'], input[name='password'], "
            "input[autocomplete='current-password'], "
            "input[placeholder*='パスワード'], #password"
        )
        try:
            page.wait_for_selector(pw_css, state="visible", timeout=20_000)
            logger.info("パスワード入力欄が表示されました")
        except PWTimeout:
            logger.error("パスワード入力欄が見つかりません（20秒待機後）")
            _log_state("パスワード入力欄タイムアウト")
            return False

        pw_el = page.locator(pw_css).first
        pw_el.click()
        _human_delay(300, 700)
        pw_el.fill(password)
        logger.info("パスワード入力完了")
        _human_delay(500, 1000)

        for sel in [
            "button[type='submit']",
            "button:has-text('ログイン')",
            "button:has-text('サインイン')",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    logger.info(f"ログインボタンクリック: {sel}")
                    break
            except Exception:
                continue

        logger.info("ログイン完了を待機中...")
        try:
            page.wait_for_url("**/jp.mercari.com/**", timeout=25_000)
        except Exception:
            pass

        _human_delay(3000, 5000)

        current_url = page.url
        logger.info(f"ログイン後URL: {current_url}")

        if "login" in current_url.lower() or "signin" in current_url.lower():
            _log_state("ログイン失敗確認")
            return False

        logger.info("ログイン成功を確認")
        return True

    except Exception as e:
        logger.error(f"ログイン処理エラー: {e}\n{traceback.format_exc()}")
        return False


def _fill_field(page, field_type: str, value: str):
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
                logger.info(f"{field_type} ({sel}) に入力完了")
                return
        except Exception:
            continue

    logger.warning(f"{field_type} フィールドが見つかりませんでした")


def _fill_price(page, price: str):
    price_digits = "".join(filter(str.isdigit, price)) or "1000"

    for sel in [
        "input[name='price']",
        "input[placeholder*='価格']",
        "input[placeholder*='販売価格']",
        "[data-testid='price-input'] input",
        "input[type='number']",
    ]:
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
    condition_map = {
        "新品":  ["新品、未使用", "新品未使用"],
        "未使用": ["新品、未使用", "新品未使用", "未使用に近い"],
        "良好":  ["未使用に近い", "目立った傷や汚れなし"],
        "普通":  ["やや傷や汚れあり"],
        "悪い":  ["傷や汚れあり", "全体的に状態が悪い"],
    }

    try:
        cond_btn = page.locator("button:has-text('商品の状態'), [data-testid='condition']").first
        if cond_btn.is_visible(timeout=3_000):
            cond_btn.click()
            _human_delay(800, 1200)
            for label in condition_map.get(condition, [condition]):
                btn = page.locator(f"text='{label}'").first
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    logger.info(f"商品状態: {label} を選択")
                    return
    except Exception as e:
        logger.warning(f"状態選択エラー（スキップ）: {e}")


def _click_draft_button(page) -> bool:
    for text in ["下書き保存", "下書きとして保存", "下書き", "一時保存", "保存する"]:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=3_000):
                btn.click()
                logger.info(f"下書きボタン「{text}」をクリック")
                _human_delay(2000, 3000)
                return True
        except Exception:
            continue

    try:
        btn = page.locator("[aria-label*='下書き'], [aria-label*='draft']").first
        if btn.is_visible(timeout=3_000):
            btn.click()
            _human_delay(2000, 3000)
            return True
    except Exception:
        pass

    logger.error("下書き保存ボタンが見つかりませんでした")
    try:
        btns = [b.text_content() for b in page.locator("button").all()[:20] if b.is_visible()]
        logger.info(f"ページ上のボタン: {btns}")
    except Exception:
        pass

    return False
