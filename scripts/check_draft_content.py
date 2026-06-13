import sys
import json
import re
import base64
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
logger = logging.getLogger("check_draft")


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


def main():
    from playwright.sync_api import sync_playwright

    settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")
    profile_dir = settings.account_runtime_dir / "wechat_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logger,
        label="wechat:check",
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

            logger.info("Opening WeChat backend...")
            page.goto(base, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            token_match = re.search(r"token=(\d+)", page.url)
            token = token_match.group(1) if token_match else ""
            logger.info("Token: %s", token[:6] + "..." if token else "EMPTY")

            for _ in range(10):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            logger.info("Checking draft list via API...")
            draft_result = page.evaluate("""async (args) => {
                const [token] = args;
                try {
                    const resp = await fetch('/cgi-bin/appmsg?action=list_card&begin=0&count=5&type=77&token=' + token + '&lang=zh_CN&f=json');
                    return await resp.json();
                } catch(e) { return {error: e.message}; }
            }""", [token])

            if draft_result.get("base_resp", {}).get("ret") == 0:
                page_info = draft_result.get("page_info", {})
                items = page_info.get("card_items", page_info.get("items", []))
                logger.info("Found %d drafts", len(items))
                for item in items[:5]:
                    app_msg_id = item.get("appmsgid", item.get("app_id", ""))
                    title = item.get("title", "")
                    logger.info("  Draft: appmsgid=%s title='%s'", app_msg_id, title[:50])
            else:
                logger.warning("Draft list API failed: %s", json.dumps(draft_result, ensure_ascii=False)[:300])

            logger.info("Checking specific draft content via API...")
            app_msg_id = "100000720"
            content_result = page.evaluate("""async (args) => {
                const [token, appMsgId] = args;
                try {
                    const resp = await fetch('/cgi-bin/appmsg?action=get_edit_info&type=77&appmsgid=' + appMsgId + '&token=' + token + '&lang=zh_CN&f=json');
                    return await resp.json();
                } catch(e) { return {error: e.message}; }
            }""", [token, app_msg_id])
            logger.info("Draft %s content: %s", app_msg_id, json.dumps(content_result, ensure_ascii=False)[:1000])

            logger.info("Trying to get draft content via appmsg API...")
            content_result2 = page.evaluate("""async (args) => {
                const [token, appMsgId] = args;
                try {
                    const resp = await fetch('/cgi-bin/appmsg?action=get&type=77&appmsgid=' + appMsgId + '&token=' + token + '&lang=zh_CN&f=json');
                    return await resp.text();
                } catch(e) { return 'ERROR: ' + e.message; }
            }""", [token, app_msg_id])
            logger.info("Draft %s get result: %s", app_msg_id, content_result2[:1000])

        except Exception as exc:
            logger.error("Check failed: %s", exc, exc_info=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
