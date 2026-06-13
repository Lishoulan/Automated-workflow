import sys, time, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("wechat_check")

settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")

from playwright.sync_api import sync_playwright

profile_dir = settings.account_runtime_dir / "wechat_profile"
profile_dir.mkdir(parents=True, exist_ok=True)

prepare_browser_profile(
    profile_dir=profile_dir,
    project_root=settings.project_root,
    logger=logger,
    label="wechat:check",
    extra_paths=[settings.account_runtime_dir],
)


def dismiss_dialogs(page):
    page.evaluate("""() => {
        const keywords = ['切换账号', '实名', '退出登录', '插件', '安全隐患'];
        document.querySelectorAll('.weui-desktop-dialog').forEach(dialog => {
            const text = (dialog.textContent || '').trim();
            if (keywords.some(kw => text.includes(kw))) {
                for (const t of ['我知道了', '取消', '关闭']) {
                    const btns = dialog.querySelectorAll('button, a, [role="button"]');
                    for (const btn of btns) {
                        if ((btn.textContent || '').trim() === t) { btn.click(); return; }
                    }
                }
                const closeBtn = dialog.querySelector('.weui-desktop-dialog__close-btn');
                if (closeBtn) closeBtn.click();
            }
        });
        document.querySelectorAll('.weui-desktop-dialog__wrp').forEach(wrp => {
            const dialog = wrp.querySelector('.weui-desktop-dialog');
            if (dialog) {
                const text = (dialog.textContent || '').trim();
                const dismissKw = ['切换账号', '实名', '退出登录', '插件', '安全隐患'];
                if (dismissKw.some(kw => text.includes(kw))) wrp.style.display = 'none';
            }
        });
        document.querySelectorAll('.detected_dialog_wrapper').forEach(w => {
            w.style.display = 'none';
        });
    }""")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        channel="msedge",
    )
    try:
        page = browser.pages[0] if browser.pages else browser.new_page()
        base = settings.wechat.base_url.rstrip("/")

        page.goto(base, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        token_match = re.search(r"token=(\d+)", page.url)
        token = token_match.group(1) if token_match else ""

        for _ in range(15):
            dismiss_dialogs(page)
            page.wait_for_timeout(500)

        logger.info("=== Checking last published article (100000688) ===")
        edit_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&appmsgid=100000688&token={token}&lang=zh_CN"
        page.goto(edit_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(8000)
        for _ in range(15):
            dismiss_dialogs(page)
            page.wait_for_timeout(500)

        article_state = page.evaluate("""() => {
            const titleEl = document.querySelector('#title');
            const titleValue = titleEl ? titleEl.value : '';
            const editor = document.querySelector('.ProseMirror');
            const editorText = editor ? editor.textContent.trim() : '';
            const editorHtml = editor ? editor.innerHTML.substring(0, 500) : '';
            const wordCount = document.querySelector('.js_word_count');
            const wordCountText = wordCount ? wordCount.textContent.trim() : '';
            return {titleValue, editorText: editorText.substring(0, 300), editorHtml, wordCountText};
        }""")
        logger.info("Article 100000688 state: %s", json.dumps(article_state, ensure_ascii=False)[:2000])

        logger.info("=== Also check article 100000685 (from earlier scheduled run) ===")
        edit_url2 = f"{base}/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77&appmsgid=100000685&token={token}&lang=zh_CN"
        page.goto(edit_url2, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(8000)
        for _ in range(15):
            dismiss_dialogs(page)
            page.wait_for_timeout(500)

        article_state2 = page.evaluate("""() => {
            const titleEl = document.querySelector('#title');
            const titleValue = titleEl ? titleEl.value : '';
            const editor = document.querySelector('.ProseMirror');
            const editorText = editor ? editor.textContent.trim() : '';
            const editorHtml = editor ? editor.innerHTML.substring(0, 500) : '';
            const wordCount = document.querySelector('.js_word_count');
            const wordCountText = wordCount ? wordCount.textContent.trim() : '';
            return {titleValue, editorText: editorText.substring(0, 300), editorHtml, wordCountText};
        }""")
        logger.info("Article 100000685 state: %s", json.dumps(article_state2, ensure_ascii=False)[:2000])

    finally:
        browser.close()
