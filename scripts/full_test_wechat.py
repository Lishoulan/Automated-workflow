import sys, time, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("wechat_full_test")

settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")

from playwright.sync_api import sync_playwright

profile_dir = settings.account_runtime_dir / "wechat_profile"
profile_dir.mkdir(parents=True, exist_ok=True)

prepare_browser_profile(
    profile_dir=profile_dir,
    project_root=settings.project_root,
    logger=logger,
    label="wechat:full-test",
    extra_paths=[settings.account_runtime_dir],
)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="msedge",
    )
    try:
        page = browser.pages[0] if browser.pages else browser.new_page()
        base = settings.wechat.base_url.rstrip("/")
        page.goto(base, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        body_text = page.locator("body").inner_text(timeout=3000)
        if "草稿箱" not in body_text and "近期草稿" not in body_text:
            logger.info("Not logged in. Waiting for manual login...")
            print("\n" + "=" * 60)
            print("请在浏览器中扫码登录微信公众号后台")
            print("登录完成后脚本会自动继续")
            print("=" * 60 + "\n")

            deadline = time.time() + 300
            while time.time() < deadline:
                page.wait_for_timeout(2000)
                try:
                    body_text = page.locator("body").inner_text(timeout=1000)
                    if "草稿箱" in body_text or "近期草稿" in body_text or "内容管理" in body_text:
                        logger.info("Login detected!")
                        break
                except Exception:
                    pass
                current_url = page.url
                if "token=" in current_url and "home" in current_url:
                    logger.info("Login detected via URL!")
                    break
            else:
                logger.error("Login timeout!")
                raise RuntimeError("Login timeout")

        page.wait_for_timeout(3000)
        logger.info("Current URL: %s", page.url)

        current_url = page.url
        token_match = re.search(r"token=(\d+)", current_url)
        token = token_match.group(1) if token_match else ""

        if not token:
            token = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="token="]');
                for (const link of links) {
                    const match = link.href.match(/token=(\d+)/);
                    if (match) return match[1];
                }
                return '';
            }""")

        logger.info("Token: %s", token)

        new_article_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77"
        if token:
            new_article_url += f"&token={token}"
        logger.info("Navigating to editor: %s", new_article_url)
        page.goto(new_article_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(8000)

        logger.info("Editor URL: %s", page.url)

        dom_info = page.evaluate("""() => {
            const result = {};
            const contentEditables = document.querySelectorAll('[contenteditable]');
            result.contentEditables = Array.from(contentEditables).map(el => ({
                tag: el.tagName,
                id: el.id || '',
                className: (el.className || '').substring(0, 80),
                contentEditable: el.contentEditable,
                textPreview: (el.textContent || '').trim().substring(0, 100),
            }));
            const titleEl = document.querySelector('#title');
            result.titleElement = titleEl ? {
                exists: true,
                value: titleEl.value || '',
            } : {exists: false};
            const mockIframe = document.querySelector('.mock-iframe-body');
            result.mockIframeExists = !!mockIframe;
            const wordCount = document.querySelector('.js_word_count');
            result.wordCount = wordCount ? wordCount.textContent.trim() : 'N/A';
            result.UE = typeof window.UE;
            return result;
        }""")
        logger.info("Editor DOM info: %s", json.dumps(dom_info, ensure_ascii=False, indent=2))

        page.screenshot(path=str(settings.output_dir / "wechat_full_test_editor.png"), full_page=True)

    finally:
        browser.close()
