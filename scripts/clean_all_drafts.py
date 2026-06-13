import sys
import json
import re
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
logger = logging.getLogger("clean_all")


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
        label="wechat:clean",
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

            for _ in range(10):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            logger.info("Navigating to draft box...")
            draft_url = f"{base}/cgi-bin/appmsg?begin=0&count=20&type=77&action=list_card&token={token}&lang=zh_CN"
            page.goto(draft_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            for _ in range(10):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            all_ids = page.evaluate("""() => {
                const cards = document.querySelectorAll('.weui-desktop-card[data-appid]');
                return Array.from(cards).map(c => c.getAttribute('data-appid'));
            }""")
            logger.info("Found %d drafts to delete", len(all_ids))

            deleted = 0
            for app_id in all_ids:
                delete_result = page.evaluate("""async (args) => {
                    const [token, appMsgId] = args;
                    try {
                        const formData = new FormData();
                        formData.append('token', token);
                        formData.append('lang', 'zh_CN');
                        formData.append('f', 'json');
                        formData.append('ajax', '1');
                        formData.append('type', '77');
                        formData.append('AppMsgId', appMsgId);
                        const resp = await fetch('/cgi-bin/operate_appmsg?t=ajax-response&sub=del&token=' + token, {
                            method: 'POST', body: formData,
                        });
                        return await resp.json();
                    } catch(e) { return {error: e.message}; }
                }""", [token, app_id])

                ret = delete_result.get("ret", delete_result.get("base_resp", {}).get("ret", -1))
                if ret == 0 or ret == "0":
                    deleted += 1
                    logger.info("Deleted %s OK (%d/%d)", app_id, deleted, len(all_ids))
                else:
                    logger.warning("Delete %s failed: ret=%s", app_id, ret)

                page.wait_for_timeout(300)

            logger.info("Deleted %d / %d drafts", deleted, len(all_ids))

            logger.info("Checking for more drafts (page 2)...")
            page.goto(draft_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            remaining = page.evaluate("""() => {
                const cards = document.querySelectorAll('.weui-desktop-card[data-appid]');
                return Array.from(cards).map(c => c.getAttribute('data-appid'));
            }""")
            logger.info("Remaining drafts: %d", len(remaining))

            if remaining:
                for app_id in remaining:
                    delete_result = page.evaluate("""async (args) => {
                        const [token, appMsgId] = args;
                        try {
                            const formData = new FormData();
                            formData.append('token', token);
                            formData.append('lang', 'zh_CN');
                            formData.append('f', 'json');
                            formData.append('ajax', '1');
                            formData.append('type', '77');
                            formData.append('AppMsgId', appMsgId);
                            const resp = await fetch('/cgi-bin/operate_appmsg?t=ajax-response&sub=del&token=' + token, {
                                method: 'POST', body: formData,
                            });
                            return await resp.json();
                        } catch(e) { return {error: e.message}; }
                    }""", [token, app_id])
                    ret = delete_result.get("ret", delete_result.get("base_resp", {}).get("ret", -1))
                    if ret == 0 or ret == "0":
                        deleted += 1
                        logger.info("Deleted %s OK", app_id)
                    page.wait_for_timeout(300)

            logger.info("Total deleted: %d", deleted)

            page.goto(draft_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            final = page.evaluate("""() => {
                const cards = document.querySelectorAll('.weui-desktop-card[data-appid]');
                return cards.length;
            }""")
            logger.info("Final draft count: %d", final)

        except Exception as exc:
            logger.error("Failed: %s", exc, exc_info=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
