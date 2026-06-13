import sys
import json
import re
import logging
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
logger = logging.getLogger("clean_drafts2")


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

            all_drafts = page.evaluate("""() => {
                const cards = document.querySelectorAll('.weui-desktop-card[data-appid]');
                const results = [];
                for (const c of cards) {
                    const appId = c.getAttribute('data-appid');
                    const allText = c.textContent || '';
                    const titleEl = c.querySelector('.weui-desktop-card__title') || c.querySelector('.weui-desktop-publish__title');
                    const title = titleEl ? titleEl.textContent.trim() : allText.trim().split('\\n')[0].trim();
                    const timeEl = c.querySelector('.weui-desktop-card__time') || c.querySelector('.weui-desktop-publish__time');
                    const time = timeEl ? timeEl.textContent.trim() : '';
                    results.push({appId, title: title.substring(0, 80), time});
                }
                return results;
            }""")

            logger.info("Found %d drafts:", len(all_drafts))
            for i, d in enumerate(all_drafts):
                logger.info("  [%d] id=%s title='%s' time=%s", i, d["appId"], d["title"], d.get("time", ""))

            title_counts = Counter(d["title"] for d in all_drafts)
            duplicates = {title: count for title, count in title_counts.items() if count > 1}
            logger.info("Duplicate titles: %s", json.dumps(duplicates, ensure_ascii=False))

            to_delete = []
            for title, count in duplicates.items():
                matching = [d for d in all_drafts if d["title"] == title]
                matching.sort(key=lambda x: x.get("time", ""), reverse=True)
                keep = matching[0]
                for d in matching[1:]:
                    to_delete.append(d)
                logger.info("Keep: id=%s title='%s', Delete %d copies", keep["appId"], title[:50], len(matching) - 1)

            test_drafts = [d for d in all_drafts if d["title"].startswith("测试") or d["title"].startswith("CDN") or d["title"].startswith("对话框")]
            for d in test_drafts:
                if d not in to_delete:
                    to_delete.append(d)

            if to_delete:
                logger.info("Will delete %d drafts:", len(to_delete))
                for d in to_delete:
                    logger.info("  DELETE id=%s title='%s'", d["appId"], d["title"][:50])

            deleted = 0
            for d in to_delete:
                app_id = d["appId"]
                logger.info("Deleting draft %s ('%s')...", app_id, d["title"][:30])

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
                    logger.info("  Deleted OK")
                else:
                    logger.warning("  Delete failed: %s", json.dumps(delete_result, ensure_ascii=False)[:200])

                page.wait_for_timeout(500)

            logger.info("Done! Deleted %d / %d drafts.", deleted, len(to_delete))

            logger.info("Reloading draft box to verify...")
            page.goto(draft_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            remaining = page.evaluate("""() => {
                const cards = document.querySelectorAll('.weui-desktop-card[data-appid]');
                const results = [];
                for (const c of cards) {
                    const appId = c.getAttribute('data-appid');
                    const allText = (c.textContent || '').trim();
                    const title = allText.split('\\n')[0].trim().substring(0, 80);
                    results.push({appId, title});
                }
                return results;
            }""")
            logger.info("Remaining drafts: %d", len(remaining))
            for d in remaining:
                logger.info("  id=%s title='%s'", d["appId"], d["title"])

        except Exception as exc:
            logger.error("Failed: %s", exc, exc_info=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
