
。"""
Mercari 自動下書き作成サービス
Playwright (Chromium headless) で出品フォームを操作して下書き保存する
メール/パスワードログイン対応版（クッキー不要）
"""
import logging
import os
import tempfile
import traceback

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

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
            logger.info(f"画像{i+1}を一時ファイルに保存: {len(img_bytes)//1024}KB")
        except Exception as e:
            logger.warning(f"画像{i+1}の一時保存に失敗: {e}")

    saved = False
    try:
        saved = _run_playwright(product_info, email, password, tmp_paths)
    except Exception as e:
        logger.error(f"Playwright実行エラー: {e}\n{traceback.format_exc()}")
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except Exception:
                pass

    return saved


def _run_playwright(product_info: dict, email: str, password: str, image_paths: list) -> bool:
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
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )

        try:
            page = context.new_page()

            if not _login(page, email, password):
                logger.error("ログインに失敗しました")
                return False

            logger.info("ログイン成功 → 出品ページへ")
            try:
                page.goto("https://jp.mercari.com/sell", wait_until="domcontentloaded", timeout=40_000)
            except PWTimeout:
                logger.error("出品ページタイムアウト")
                return False

            page.wait_for_timeout(3_000)
            logger.info(f"出品ページURL: {page.url}")

            if image_paths:
                try:
                    file_input = page.locator("input[type='file']").first
                    file_input.set_input_files(image_paths, timeout=20_000)
                    page.wait_for_timeout(4_000)
                    logger.info(f"画像{len(image_paths)}枚アップロード完了")
                except Exception as e:
                    logger.warning(f"画像アップロードエラー（続行）: {e}")

            title       = product_info.get("name", "商品")
            description = product_info.get("description", "")
            price       = str(product_info.get("price", 1000))
            condition   = product_info.get("condition", "")

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
    """メルカリにメール/パスワードでログインする"""

    def _log_state(label: str):
        try:
            logger.info(f"[{label}] URL={page.url}")
            # DOM上の全inputを取得（shadowDOMも含めてJSで調査）
            all_inputs = page.evaluate("""
                () => {
                    const inputs = [];
                    document.querySelectorAll('input').forEach(el => {
                        inputs.push({type: el.type, name: el.name, id: el.id,
                                     placeholder: el.placeholder, visible: el.offsetParent !== null});
                    });
                    return inputs;
                }
            """)
            logger.info(f"[{label}] DOM inputs={all_inputs[:8]}")
            btns = []
            page.locator("button").all()
            for b in page.locator("button").all()[:15]:
                try:
                    t = b.text_content()
                    if t and t.strip():
                        btns.append(t.strip())
                except Exception:
                    pass
            logger.info(f"[{label}] buttons={btns}")
        except Exception as e:
            logger.warning(f"[{label}] log error: {e}")

    try:
        logger.info("メルカリログイン開始")
        page.goto("https://jp.mercari.com/login", wait_until="networkidle", timeout=40_000)
        page.wait_for_timeout(3_000)
        _log_state("初期")

        # Step1: ナビの「ログイン」ボタンをクリック → login.jp.mercari.com に遷移
        clicked_login = False
        for btn in page.locator("button:has-text('ログイン'), a:has-text('ログイン')").all()[:5]:
            try:
                if btn.is_visible(timeout=1_000):
                    with page.expect_navigation(timeout=15_000):
                        btn.click()
                    page.wait_for_timeout(3_000)
                    logger.info(f"ログインボタンクリック完了 → {page.url}")
                    _log_state("ログインクリック後")
                    clicked_login = True
                    break
            except Exception:
                # ナビゲーションなしでクリックだけ
                try:
                    if btn.is_visible(timeout=500):
                        btn.click()
                        page.wait_for_timeout(4_000)
                        logger.info(f"ログインボタンクリック（nav無し） → {page.url}")
                        _log_state("ログインクリック後2")
                        clicked_login = True
                        break
                except Exception:
                    continue

        # Step2: 「メールアドレスでログイン」を探す
        for sel in [
            "button:has-text('メールアドレスでログイン')",
            "button:has-text('メールアドレス')",
            "a:has-text('メールアドレスでログイン')",
            "[data-testid='email-login']",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    page.wait_for_timeout(3_000)
                    logger.info(f"メールログインボタンクリック: {sel}")
                    _log_state("メールボタン後")
                    break
            except Exception:
                continue

        # Step3: メール入力欄が現れるまで待つ（最大15秒）
        email_selector = "input[type='email'], input[name='email'], input[placeholder*='メール'], input[placeholder*='mail']"
        try:
            page.wait_for_selector(email_selector, state="visible", timeout=15_000)
            logger.info("メール入力欄が表示されました")
        except PWTimeout:
            _log_state("メール入力待ちタイムアウト")
            logger.error("メール入力欄が15秒以内に表示されませんでした")
            return False

        # メール入力
        email_filled = False
        for sel in ["input[type='email']", "input[name='email']",
                    "input[placeholder*='メール']", "input[placeholder*='mail']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2_000):
                    el.click()
                    el.fill(email)
                    logger.info(f"メール入力完了: {sel}")
                    email_filled = True
                    break
            except Exception:
                continue

        if not email_filled:
            logger.error("メール入力に失敗")
            return False

        # Step4: 次へ
        for sel in ["button:has-text('次へ')", "button[type='submit']"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    logger.info(f"次へクリック: {sel}")
                    break
            except Exception:
                continue

        # Step5: パスワード入力欄が現れるまで待つ（最大15秒）
        pw_selector = "input[type='password'], input[name='password'], input[placeholder*='パスワード']"
        try:
            page.wait_for_selector(pw_selector, state="visible", timeout=15_000)
            logger.info("パスワード入力欄が表示されました")
        except PWTimeout:
            _log_state("パスワード待ちタイムアウト")
            logger.error("パスワード入力欄が15秒以内に表示されませんでした")
            return False

        # パスワード入力
        pw_filled = False
        for sel in ["input[type='password']", "input[name='password']",
                    "input[placeholder*='パスワード']", "input[autocomplete='current-password']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2_000):
                    el.click()
                    el.fill(password)
                    logger.info(f"パスワード入力完了: {sel}")
                    pw_filled = True
                    break
            except Exception:
                continue

        if not pw_filled:
            logger.error("パスワード入力に失敗")
            return False

        # Step6: ログインボタン
        for sel in ["button[type='submit']", "button:has-text('ログイン')", "button:has-text('サインイン')"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    logger.info(f"ログインボタンクリック: {sel}")
                    break
            except Exception:
                continue

        # ログイン完了待ち（最大20秒）
        try:
            page.wait_for_url("**/jp.mercari.com/**", timeout=20_000)
            logger.info(f"ログイン後URL: {page.url}")
        except PWTimeout:
            logger.info(f"URL待ちタイムアウト、現在URL: {page.url}")

        current_url = page.url
        if "login" in current_url.lower() or "signin" in current_url.lower():
            _log_state("ログイン失敗")
            logger.warning("ログイン後もlogin/signinページ")
            return False

        return True

    except Exception as e:
        logger.error(f"ログイン処理エラー: {e}\n{traceback.format_exc()}")
        return False


def _fill_field(page, field_type: str, value: str):
    if not value:
        return
    selectors = {
        "title": ["input[name='title']", "input[placeholder*='タイトル']", "input[placeholder*='商品名']"],
        "description": ["textarea[name='description']", "textarea[placeholder*='説明']", "textarea[placeholder*='商品の説明']"],
    }
    for sel in selectors.get(field_type, []):
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3_000):
                el.click()
                el.fill(value)
                logger.info(f"{field_type} 入力完了")
                return
        except Exception:
            continue
    logger.warning(f"{field_type} フィールドが見つかりませんでした")


def _fill_price(page, price: str):
    price_digits = "".join(filter(str.isdigit, price)) or "1000"
    for sel in ["input[name='price']", "input[placeholder*='価格']", "input[placeholder*='販売価格']", "input[type='number']"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3_000):
                el.click()
                el.fill(price_digits)
                logger.info(f"価格入力: {price_digits}")
                return
        except Exception:
            continue


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
            page.wait_for_timeout(1_000)
            for label in condition_map.get(condition, [condition]):
                btn = page.locator(f"text='{label}'").first
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    logger.info(f"商品状態: {label}")
                    return
    except Exception as e:
        logger.warning(f"状態選択エラー: {e}")


def _click_draft_button(page) -> bool:
    for text in ["下書き保存", "下書きとして保存", "下書き", "一時保存", "保存する"]:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=3_000):
                btn.click()
                logger.info(f"下書きボタン「{text}」クリック")
                page.wait_for_timeout(3_000)
                return True
        except Exception:
            continue
    try:
        btn = page.locator("[aria-label*='下書き'], [aria-label*='draft']").first
        if btn.is_visible(timeout=3_000):
            btn.click()
            page.wait_for_timeout(2_000)
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
