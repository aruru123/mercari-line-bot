import logging
import traceback

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

logger = logging.getLogger(__name__)


def create_mercari_draft(
    product_info: dict,
    images_bytes: list | None = None,
    cookies_json: str | None = None,
) -> bool:
    """最小テスト: メルカリ出品ページを開いてタイトルに1文字入力する"""
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

            logger.info("メルカリ出品ページを開いています...")
            page.goto("https://jp.mercari.com/sell/photos", timeout=60000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            logger.info("ページタイトル: %s", page.title())

            # タイトル欄を探して商品名を入力
            title = product_info.get("title", "テスト商品")
            title_selectors = [
                "input[placeholder*='商品名']",
                "input[name='name']",
                "textarea[placeholder*='商品名']",
                "[data-testid*='title'] input",
                "[data-testid*='name'] input",
                "input[placeholder*='商品']",
            ]

            typed = False
            for sel in title_selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=5000):
                        el.click()
                        el.fill(title[:1])
                        logger.info("タイトル入力成功: sel=%s, text=%s", sel, title[:1])
                        typed = True
                        break
                except Exception:
                    continue

            if not typed:
                logger.warning("タイトル欄が見つかりませんでした")

            # スクリーンショット保存（デバッグ用）
            try:
                page.screenshot(path="/tmp/mercari_debug.png")
                logger.info("スクリーンショット保存: /tmp/mercari_debug.png")
            except Exception:
                pass

            browser.close()
            return typed

    except Exception as e:
        logger.error("create_mercari_draft エラー: %s\n%s", e, traceback.format_exc())
        return False
