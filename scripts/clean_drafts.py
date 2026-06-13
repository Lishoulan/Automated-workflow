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
logger = logging.getLogger("clean_drafts")


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
            logger.info("Token: %s", token[:6] + "..." if token else "EMPTY")

            for _ in range(10):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            logger.info("Fetching all drafts via API...")
            all_drafts = []
            begin = 0
            while True:
                result = page.evaluate("""async (args) => {
                    const [token, begin] = args;
                    try {
                        const resp = await fetch('/cgi-bin/appmsg?action=list_card&begin=' + begin + '&count=5&type=77&token=' + token + '&lang=zh_CN&f=json');
                        return await resp.json();
                    } catch(e) { return {error: e.message}; }
                }""", [token, begin])

                if result.get("base_resp", {}).get("ret") != 0:
                    logger.warning("API error at begin=%d: %s", begin, json.dumps(result, ensure_ascii=False)[:300])
                    break

                page_info = result.get("page_info", {})
                items = page_info.get("card_items", page_info.get("items", []))
                if not items:
                    break

                for item in items:
                    app_id = item.get("appmsgid", item.get("app_id", ""))
                    title = item.get("title", "")
                    update_time = item.get("update_time", "")
                    all_drafts.append({"appmsgid": str(app_id), "title": title, "update_time": update_time})

                logger.info("Fetched %d drafts (total so far: %d)", len(items), len(all_drafts))
                begin += len(items)
                if len(items) < 5:
                    break

            logger.info("Total drafts: %d", len(all_drafts))
            for i, d in enumerate(all_drafts):
                logger.info("  [%d] id=%s title='%s' time=%s", i, d["appmsgid"], d["title"][:50], d["update_time"])

            title_counts = Counter(d["title"] for d in all_drafts)
            duplicates = {title: count for title, count in title_counts.items() if count > 1}
            logger.info("Duplicate titles: %s", json.dumps(duplicates, ensure_ascii=False))

            if not duplicates:
                logger.info("No duplicates found!")
                return

            to_delete = []
            for title, count in duplicates.items():
                matching = [d for d in all_drafts if d["title"] == title]
                matching.sort(key=lambda x: x.get("update_time", ""), reverse=True)
                keep = matching[0]
                for d in matching[1:]:
                    to_delete.append(d)
                logger.info("Keep: id=%s title='%s', Delete %d copies", keep["appmsgid"], title[:50], len(matching) - 1)

            logger.info("Will delete %d drafts:", len(to_delete))
            for d in to_delete:
                logger.info("  DELETE id=%s title='%s'", d["appmsgid"], d["title"][:50])

            deleted = 0
            for d in to_delete:
                app_id = d["appmsgid"]
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
                    logger.info("  Deleted OK (ret=%s)", ret)
                else:
                    logger.warning("  Delete failed: %s", json.dumps(delete_result, ensure_ascii=False)[:200])

                page.wait_for_timeout(500)

            logger.info("Done! Deleted %d / %d duplicates.", deleted, len(to_delete))

        except Exception as exc:
            logger.error("Failed: %s", exc, exc_info=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
