import logging
import traceback
import time
import random
import base64
import os

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)


def _human_delay(min_ms: int = 500, max_ms: int = 1500):
    time.sleep(random.randint(min_ms, max_ms) / 1000)


def _screenshot_b64(page, label: str):
    """スクリーンショットをbase64でログに出力（デバッグ用）"""
    try:
        path = f"/tmp/mercari_{label}.png"
        page.screenshot(path=path)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        logger.info("[SCREENSHOT:%s] data:image/png;base64,%s", label, b64[:100] + "...")
        logger.info("[SCREENSHOT_PATH:%s] %s (size=%d bytes)", label, path, len(b64))
    except Exception as e:
        logger.warning("スクリーンショット失敗: %s", e)


def create_mercari_draft(
    product_info: dict,
    images_bytes: list | None = None,
    cookies_json: str | None = None,
) -> bool:
    """メルカリにログインして下書き保存する"""
    email = os.environ.get("MERCARI_EMAIL", "")
    password = os.environ.get("MERCARI_PASSWORD", "")

    if not email or not password:
        logger.error("MERCARI_EMAIL または MERCARI_PASSWORD が未設定")
        return False

    logger.info("メール設定確認: %s", email[:3] + "***")

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
                    logger.error("ログイン失敗 - 処理中断")
                    _screenshot_b64(page, "login_fail")
                    return False

                logger.info("ログイン成功 - 出品ページへ移動...")
                page.goto("https://jp.mercari.com/sell/photos", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                _human_delay(2000, 3000)
                logger.info("出品ページ URL: %s / タイトル: %s", page.url, page.title())
                _screenshot_b64(page, "sell_page")

                title = product_info.get("name", product_info.get("title", "テスト商品"))
                price = str(product_info.get("price", 1000))

                _fill_field(page, title, [
                    "input[placeholder*='商品名']",
                    "input[name='name']",
                    "textarea[placeholder*='商品名']",
                    "input[placeholder*='商品']",
                ])

                _fill_price(page, price)
                _screenshot_b64(page, "before_draft")

                saved = _click_draft_button(page)
                logger.info("下書き保存結果: %s", "成功" if saved else "失敗")
                _screenshot_b64(page, "after_draft")

            except Exception as e:
                logger.error("操作エラー: %s\n%s", e, traceback.format_exc())
                saved = False
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

            return saved

    except Exception as e:
        logger.error("create_mercari_draft エラー: %s\n%s", e, traceback.format_exc())
        return False


def _login(page, email: str, password: str) -> bool:
    """メルカリにログイン（詳細ログ付き）"""
    logger.info("=== ログイン開始 ===")
    page.goto("https://jp.mercari.com/login", timeout=60000)

    # networkidle まで待機（React SPAのレンダリング完了を待つ）
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        logger.warning("networkidle タイムアウト - 続行")

    logger.info("ログインページ URL: %s", page.url)
    logger.info("ログインページ タイトル: %s", page.title())
    _screenshot_b64(page, "login_page")

    # ── メールアドレス入力 ──────────────────────────
    email_selectors = [
        "input[type='email']",
        "input[name='email']",
        "input[placeholder*='メール']",
        "input[placeholder*='mail']",
        "input[placeholder*='Email']",
        "input[autocomplete='email']",
    ]
    email_filled = False
    for sel in email_selectors:
        try:
            el = page.locator(sel).first
            visible = el.is_visible(timeout=5000)
            logger.info("メールセレクタ確認: %s → visible=%s", sel, visible)
            if visible:
                el.click()
                el.fill(email)
                logger.info("✅ メール入力完了: %s", sel)
                email_filled = True
                break
        except Exception as exc:
            logger.info("メールセレクタ例外: %s → %s", sel, exc)

    if not email_filled:
        logger.warning("❌ メールフィールドが見つかりません - ページHTML確認")
        # ページの全inputを記録
        inputs = page.locator("input").all()
        logger.info("input要素数: %d", len(inputs))
        for i, inp in enumerate(inputs[:10]):
            try:
                attrs = {
                    "type": inp.get_attribute("type"),
                    "name": inp.get_attribute("name"),
                    "placeholder": inp.get_attribute("placeholder"),
                    "id": inp.get_attribute("id"),
                }
                logger.info("input[%d]: %s", i, attrs)
            except Exception:
                pass
        return False

    _human_delay(500, 1000)

    # ── 次へボタン ──────────────────────────────────
    next_selectors = [
        "button:has-text('次へ')",
        "button:has-text('続ける')",
        "button:has-text('Next')",
        "button[type='submit']",
        "button:has-text('ログイン')",
    ]
    next_clicked = False
    for sel in next_selectors:
        try:
            btn = page.locator(sel).first
            visible = btn.is_visible(timeout=3000)
            logger.info("次へセレクタ: %s → visible=%s", sel, visible)
            if visible:
                btn.click()
                logger.info("✅ 次へボタンクリック: %s", sel)
                next_clicked = True
                break
        except Exception as exc:
            logger.info("次へセレクタ例外: %s → %s", sel, exc)

    if not next_clicked:
        logger.warning("❌ 次へボタンが見つかりません")

    _human_delay(2000, 3000)
    logger.info("次へ後 URL: %s", page.url)
    _screenshot_b64(page, "after_next")

    # ── パスキーバイパス（第1段階） ──────────────────
    passkey_selectors = [
        "button:has-text('他の方でログインする')",
        "button:has-text('パスワードでログイン')",
        "button:has-text('別の方法')",
        "button:has-text('他の方法')",
        "a:has-text('パスワードでログイン')",
        "button:has-text('Use a password')",
        "button:has-text('Try another way')",
    ]
    for sel in passkey_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                logger.info("✅ パスキーバイパス(1): %s", sel)
                _human_delay(1000, 2000)
                break
        except Exception:
            pass

    # ── パスキーバイパス（第2段階） ──────────────────
    pw_login_selectors = [
        "button:has-text('パスワードでログイン')",
        "a:has-text('パスワードでログイン')",
        "button:has-text('パスワード')",
        "[data-testid='use-password']",
    ]
    for sel in pw_login_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                logger.info("✅ パスキーバイパス(2): %s", sel)
                _human_delay(1000, 2000)
                break
        except Exception:
            pass

    logger.info("パスキーバイパス後 URL: %s", page.url)
    _screenshot_b64(page, "after_passkey")

    # ── パスワード入力 ────────────────────────────────
    pw_selectors = [
        "input[type='password']",
        "input[name='password']",
        "input[placeholder*='パスワード']",
        "input[autocomplete='current-password']",
    ]
    pw_filled = False
    for sel in pw_selectors:
        try:
            el = page.locator(sel).first
            visible = el.is_visible(timeout=5000)
            logger.info("パスワードセレクタ: %s → visible=%s", sel, visible)
            if visible:
                el.click()
                el.fill(password)
                logger.info("✅ パスワード入力完了: %s", sel)
                pw_filled = True
                break
        except Exception as exc:
            logger.info("パスワードセレクタ例外: %s → %s", sel, exc)

    if not pw_filled:
        logger.warning("❌ パスワードフィールドが見つかりません")
        return False

    _human_delay(500, 1000)

    # ── ログインボタン ────────────────────────────────
    login_btn_selectors = [
        "button:has-text('ログイン')",
        "button[type='submit']",
        "button:has-text('Sign in')",
    ]
    for sel in login_btn_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                logger.info("✅ ログインボタンクリック: %s", sel)
                break
        except Exception:
            pass

    # ログイン処理を待つ
    _human_delay(4000, 6000)

    current_url = page.url
    logger.info("=== ログイン結果 URL: %s ===", current_url)
    _screenshot_b64(page, "login_result")

    logged_in = "login" not in current_url
    logger.info("ログイン: %s", "✅ 成功" if logged_in else "❌ 失敗（まだloginページ）")
    return logged_in


def _fill_field(page, text: str, selectors: list) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3000):
                el.click()
                el.fill(text)
                logger.info("フィールド入力: %s → %s", sel, text[:30])
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
