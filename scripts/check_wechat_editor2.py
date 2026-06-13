import sys, time, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("wechat_check2")

settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")

from playwright.sync_api import sync_playwright

profile_dir = settings.account_runtime_dir / "wechat_profile"
profile_dir.mkdir(parents=True, exist_ok=True)

prepare_browser_profile(
    profile_dir=profile_dir,
    project_root=settings.project_root,
    logger=logger,
    label="wechat:check2",
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
        page.wait_for_timeout(5000)

        logger.info("Home URL: %s", page.url)

        body_text = page.locator("body").inner_text(timeout=3000)
        has_draft = "草稿箱" in body_text or "近期草稿" in body_text
        logger.info("Has draft box: %s", has_draft)
        logger.info("Body text (first 500): %s", body_text[:500])

        if has_draft:
            token = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="token="]');
                for (const link of links) {
                    const match = link.href.match(/token=(\d+)/);
                    if (match) return match[1];
                }
                const match = window.location.href.match(/token=(\d+)/);
                return match ? match[1] : '';
            }""")
            logger.info("Token: %s", token)

            new_article_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77"
            if token:
                new_article_url += f"&token={token}"
            logger.info("Navigating to: %s", new_article_url)
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
                    style: (titleEl.getAttribute('style') || '').substring(0, 100),
                } : {exists: false};
                const mockIframe = document.querySelector('.mock-iframe-body');
                result.mockIframeExists = !!mockIframe;
                if (mockIframe) {
                    result.mockIframeContent = mockIframe.innerHTML.substring(0, 300);
                }
                const wordCount = document.querySelector('.js_word_count');
                result.wordCount = wordCount ? wordCount.textContent.trim() : 'N/A';
                result.UE = typeof window.UE;
                result.ueditor = typeof window.ueditor;
                return result;
            }""")
            logger.info("Editor DOM info: %s", json.dumps(dom_info, ensure_ascii=False, indent=2))

            page.screenshot(path=str(settings.output_dir / "wechat_editor_check2.png"), full_page=True)

    finally:
        browser.close()
