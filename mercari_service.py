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

            logger.info("ログイン成功")

            logger.info("出品ページへ移動中...")
            try:
                page.goto(
                    "https://jp.mercari.com/sell",
                    wait_until="domcontentloaded",
                    timeout=40_000,
                )
            except PWTimeout:
                logger.error("タイムアウト: 出品ページの読み込みに失敗")
                return False

            page.wait_for_timeout(3_000)
            logger.info(f"出品ページURL: {page.url}")

            if image_paths:
                logger.info(f"{len(image_paths)}枚の画像をアップロード中...")
                try:
                    file_input = page.locator("input[type='file']").first
                    file_input.set_input_files(image_paths, timeout=20_000)
                    page.wait_for_timeout(4_000)
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


def _login(page, email: str, password: str) -> bool:
    """メルカリにメール/パスワードでログインする"""
    def _log_page_state(label: str):
        try:
            logger.info(f"[{label}] URL: {page.url}")
            inputs = page.locator("input").all()
            input_info = []
            for i in inputs[:10]:
                try:
                    if i.is_visible(timeout=300):
                        input_info.append({
                            "type": i.get_attribute("type"),
                            "name": i.get_attribute("name"),
                            "placeholder": i.get_attribute("placeholder"),
                            "id": i.get_attribute("id"),
                        })
                except Exception:
                    pass
            logger.info(f"[{label}] inputs: {input_info}")
            buttons = page.locator("button").all()
            btn_texts = []
            for b in buttons[:15]:
                try:
                    if b.is_visible(timeout=300):
                        t = b.text_content()
                        if t and t.strip():
                            btn_texts.append(t.strip())
                except Exception:
                    pass
            logger.info(f"[{label}] buttons: {btn_texts}")
        except Exception as e:
            logger.warning(f"[{label}] state error: {e}")

    try:
        logger.info("ログインページへアクセス中...")
        page.goto(
            "https://jp.mercari.com/login",
            wait_until="networkidle",
            timeout=40_000,
        )
        page.wait_for_timeout(3_000)
        _log_page_state("初期")

        # ★ Step 1: ナビの「ログイン」ボタンをクリックしてモーダルを開く
        # （/login はホーム画面が表示されるためモーダル起動が必要）
        try:
            nav_btns = page.locator("button:has-text('ログイン'), a:has-text('ログイン')").all()
            for btn in nav_btns[:5]:
                try:
                    if btn.is_visible(timeout=1_000):
                        btn.click()
                        page.wait_for_timeout(3_000)
                        logger.info("ナビのログインボタンをクリック（モーダル起動）")
                        _log_page_state("モーダル起動後")
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"ナビログインボタンエラー: {e}")

        # ★ Step 2: 「メールアドレスでログイン」を探してクリック
        email_login_selectors = [
            "button:has-text('メールアドレスでログイン')",
            "button:has-text('メールアドレス')",
            "a:has-text('メールアドレスでログイン')",
            "[data-testid='email-login']",
            "mer-button:has-text('メールアドレス')",
        ]
        for sel in email_login_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    page.wait_for_timeout(3_000)
                    logger.info(f"メールログインボタンをクリック: {sel}")
                    _log_page_state("メールボタン後")
                    break
            except Exception:
                continue

        # ★ Step 3: メールアドレス入力
        email_selectors = [
            "input[type='email']",
            "input[name='email']",
            "input[placeholder*='メールアドレス']",
            "input[placeholder*='email']",
            "input[placeholder*='Email']",
            "input[autocomplete='email']",
            "input[autocomplete='username']",
            "#email",
        ]
        email_filled = False
        for sel in email_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3_000):
                    el.click()
                    el.fill(email)
                    logger.info(f"メールアドレス入力: {sel}")
                    email_filled = True
                    break
            except Exception:
                continue

        if not email_filled:
            _log_page_state("メール入力欄なし")
            logger.error("メールアドレス入力欄が見つかりません")
            return False

        # ★ Step 4: 「次へ」ボタン
        for sel in ["button:has-text('次へ')", "button[type='submit']"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    page.wait_for_timeout(3_000)
                    logger.info(f"次へボタンクリック: {sel}")
                    break
            except Exception:
                continue

        _log_page_state("次へ後")

        # ★ Step 5: パスワード入力
        pw_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[placeholder*='パスワード']",
            "input[autocomplete='current-password']",
            "#password",
        ]
        pw_filled = False
        for sel in pw_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=5_000):
                    el.click()
                    el.fill(password)
                    logger.info(f"パスワード入力: {sel}")
                    pw_filled = True
                    break
            except Exception:
                continue

        if not pw_filled:
            _log_page_state("パスワード入力欄なし")
            logger.error("パスワード入力欄が見つかりません")
            return False

        # ★ Step 6: ログインボタン
        for sel in ["button[type='submit']", "button:has-text('ログイン')", "button:has-text('サインイン')"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    logger.info(f"ログインボタンクリック: {sel}")
                    break
            except Exception:
                continue

        page.wait_for_timeout(6_000)
        current_url = page.url
        logger.info(f"ログイン後URL: {current_url}")

        if "login" in current_url.lower() or "signin" in current_url.lower():
            _log_page_state("ログイン失敗後")
            logger.warning("ログイン後もlogin URLです")
            return False

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
    draft_texts = ["下書き保存", "下書きとして保存", "下書き", "一時保存", "保存する", "draft", "save as draft"]
    for text in draft_texts:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=3_000):
                btn.click()
                logger.info(f"下書きボタン「{text}」をクリック")
                page.wait_for_timeout(3_000)
                new_url = page.url
                logger.info(f"クリック後のURL: {new_url}")
                if "draft" in new_url or "mypage" in new_url or "complete" in new_url:
                    return True
                toast = page.locator("text='保存しました', text='下書きに保存'").first
                if toast.is_visible(timeout=3_000):
                    return True
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
        buttons = page.locator("button").all()
        btn_texts = [b.text_content() for b in buttons[:20] if b.is_visible()]
        logger.info(f"ページ上のボタン: {btn_texts}")
    except Exception:
        pass
    return False
