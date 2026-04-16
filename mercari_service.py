import logging
import traceback
import time
import random

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)


def _human_delay(min_ms: int = 500, max_ms: int = 1500):
    time.sleep(random.randint(min_ms, max_ms) / 1000)


def create_mercari_draft(
    product_info: dict,
    images_bytes: list | None = None,
    cookies_json: str | None = None,
) -> bool:
    """メルカリにログインして下書き保存する"""
    import os
    email = os.environ.get("MERCARI_EMAIL", "")
    password = os.environ.get("MERCARI_PASSWORD", "")

    if not email or not password:
        logger.error("MERCARI_EMAIL または MERCARI_PASSWORD が未設定")
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled,CredentialManagement",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process,WebAuthentication",
                    "--lang=ja-JP",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 10; K) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Mobile Safari/537.36"
                ),
                locale="ja-JP",
                viewport={"width": 390, "height": 844},
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'] });
                window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
                try {
                    const _creds = {
                        get: () => Promise.reject(new DOMException('NotAllowed', 'NotAllowedError')),
                        create: () => Promise.reject(new DOMException('NotAllowed', 'NotAllowedError')),
                        preventSilentAccess: () => Promise.resolve(),
                        store: () => Promise.resolve(),
                    };
                    Object.defineProperty(navigator, 'credentials', { get: () => _creds });
                    window.PublicKeyCredential = undefined;
                } catch (e) {}
            """)

            page = context.new_page()
            saved = False

            try:
                if not _login(page, email, password):
                    logger.error("ログイン失敗")
                    return False

                logger.info("出品ページへ移動...")
                page.goto("https://jp.mercari.com/sell/photos", timeout=60000)
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                _human_delay(2000, 3000)
                logger.info("出品ページURL: %s", page.url)

                title = product_info.get("name", product_info.get("title", "テスト商品"))
                price = str(product_info.get("price", 1000))

                _fill_field(page, title, [
                    "input[placeholder*='商品名']",
                    "input[name='name']",
                    "textarea[placeholder*='商品名']",
                    "input[placeholder*='商品']",
                ])

                _fill_price(page, price)

                saved = _click_draft_button(page)
                logger.info("下書き保存: %s", "成功" if saved else "失敗")

            except Exception as e:
                logger.error("操作エラー: %s\n%s", e, traceback.format_exc())
                saved = False
            finally:
                try:
                    page.screenshot(path="/tmp/mercari_debug.png")
                    logger.info("スクリーンショット保存完了")
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

            return saved

    except Exception as e:
        logger.error("create_mercari_draft エラー: %s\n%s", e, traceback.format_exc())
        return False


def _login(page, email: str, password: str) -> bool:
    """メルカリにメール+パスワードでログイン"""
    logger.info("ログインページへ移動...")
    page.goto("https://jp.mercari.com/login", timeout=60000)
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    _human_delay(2000, 3000)

    for sel in ["input[type='email']", "input[name='email']", "input[placeholder*='メール']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=5000):
                el.click()
                el.fill(email)
                logger.info("メール入力完了")
                break
        except Exception:
            continue

    _human_delay(500, 1000)

    for sel in ["button:has-text('次へ')", "button:has-text('続ける')", "button[type='submit']"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                logger.info("次へボタンクリック: %s", sel)
                break
        except Exception:
            continue

    _human_delay(2000, 3000)

    for sel in [
        "button:has-text('他の方でログインする')",
        "button:has-text('パスワードでログイン')",
        "button:has-text('別の方法')",
        "button:has-text('他の方法')",
        "a:has-text('パスワードでログイン')",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                logger.info("パスキーバイパス(1): %s", sel)
                _human_delay(1000, 2000)
                break
        except Exception:
            continue

    for sel in [
        "button:has-text('パスワードでログイン')",
        "a:has-text('パスワードでログイン')",
        "button:has-text('パスワード')",
        "[data-testid='use-password']",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                logger.info("パスキーバイパス(2): %s", sel)
                _human_delay(1000, 2000)
                break
        except Exception:
            continue

    for sel in ["input[type='password']", "input[name='password']", "input[placeholder*='パスワード']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=5000):
                el.click()
                el.fill(password)
                logger.info("パスワード入力完了")
                break
        except Exception:
            continue

    _human_delay(500, 1000)

    for sel in ["button:has-text('ログイン')", "button[type='submit']"]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                logger.info("ログインボタンクリック")
                break
        except Exception:
            continue

    _human_delay(3000, 5000)
    current_url = page.url
    logger.info("ログイン後URL: %s", current_url)
    logged_in = "login" not in current_url
    logger.info("ログイン結果: %s", "成功" if logged_in else "失敗")
    return logged_in


def _fill_field(page, text: str, selectors: list) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                el.fill(text)
                logger.info("フィールド入力: %s -> %s", sel, text[:30])
                _human_delay(300, 700)
                return True
        except Exception:
            continue
    logger.warning("フィールド未発見: %s", selectors[0] if selectors else "")
    return False


def _fill_price(page, price: str) -> bool:
    for sel in [
        "input[placeholder*='販売価格']",
        "input[placeholder*='価格']",
        "input[name='price']",
        "input[type='number']",
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                el.fill(price)
                logger.info("価格入力: %s", price)
                _human_delay(300, 700)
                return True
        except Exception:
            continue
    logger.warning("価格フィールド未発見")
    return False


def _click_draft_button(page) -> bool:
    for sel in [
        "button:has-text('下書き保存')",
        "button:has-text('下書きとして保存')",
        "button:has-text('下書き')",
        "button:has-text('一時保存')",
        "button:has-text('保存する')",
        "[aria-label*='下書き']",
        "[aria-label*='draft']",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                logger.info("下書きボタンクリック: %s", sel)
                _human_delay(2000, 3000)
                return True
        except Exception:
            continue
    logger.warning("下書きボタン未発見")
    return False
