import sys
import json
import re
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
logger = logging.getLogger("list_content")


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
        label="wechat:list",
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

            logger.info("=== Draft Box (type=77) ===")
            begin = 0
            while True:
                result = page.evaluate("""async (args) => {
                    const [token, begin] = args;
                    try {
                        const resp = await fetch('/cgi-bin/appmsg?action=list_card&begin=' + begin + '&count=5&type=77&token=' + token + '&lang=zh_CN&f=json');
                        return await resp.json();
                    } catch(e) { return {error: e.message}; }
                }""", [token, begin])
                page_info = result.get("page_info", {})
                items = page_info.get("card_items", page_info.get("items", []))
                if not items:
                    break
                for item in items:
                    logger.info("  DRAFT id=%s title='%s'", item.get("appmsgid", ""), item.get("title", "")[:60])
                begin += len(items)
                if len(items) < 5:
                    break

            logger.info("=== Published Articles (appmsgpublish) ===")
            pub_result = page.evaluate("""async (args) => {
                const [token] = args;
                try {
                    const resp = await fetch('/cgi-bin/appmsgpublish?action=list_card&begin=0&count=20&type=77&token=' + token + '&lang=zh_CN&f=json');
                    return await resp.json();
                } catch(e) { return {error: e.message}; }
            }""", [token])
            if pub_result.get("base_resp", {}).get("ret") == 0:
                pub_page = pub_result.get("page_info", {})
                pub_items = pub_page.get("card_items", pub_page.get("publish_page", []))
                if isinstance(pub_items, str):
                    try:
                        pub_data = json.loads(pub_items)
                        pub_list = pub_data.get("publish_list", [])
                        for p in pub_list:
                            info = p.get("publish_info", "{}")
                            try:
                                info_data = json.loads(info) if isinstance(info, str) else info
                                title = info_data.get("title", "")
                                msg_id = info_data.get("msgid", "")
                                sent_time = info_data.get("sent_info", {}).get("time", "")
                                logger.info("  PUB msgid=%s title='%s' time=%s", msg_id, title[:60], sent_time)
                            except:
                                logger.info("  PUB raw: %s", str(info)[:100])
                    except:
                        logger.info("  Parse error: %s", str(pub_items)[:200])
                elif isinstance(pub_items, list):
                    for item in pub_items:
                        logger.info("  PUB id=%s title='%s'", item.get("appmsgid", ""), item.get("title", "")[:60])
                else:
                    logger.info("  Unknown format: %s", type(pub_items))
            else:
                logger.warning("Published list API failed: %s", json.dumps(pub_result, ensure_ascii=False)[:300])

            logger.info("=== Navigating to draft box page for visual check ===")
            draft_url = f"{base}/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card&token={token}&lang=zh_CN"
            page.goto(draft_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            for _ in range(10):
                dismiss_dialogs(page)
                page.wait_for_timeout(300)

            draft_cards = page.evaluate("""() => {
                const cards = document.querySelectorAll('.weui-desktop-card[data-appid]');
                const results = [];
                for (const c of cards) {
                    const appId = c.getAttribute('data-appid');
                    const titleEl = c.querySelector('.weui-desktop-card__title');
                    const title = titleEl ? titleEl.textContent.trim() : '';
                    results.push({appId, title: title.substring(0, 60)});
                }
                return results;
            }""")
            logger.info("Draft cards on page: %d", len(draft_cards))
            for c in draft_cards:
                logger.info("  CARD id=%s title='%s'", c["appId"], c["title"])

        except Exception as exc:
            logger.error("Failed: %s", exc, exc_info=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
