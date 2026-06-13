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

        current_url = page.url
        logger.info("Current URL: %s", current_url)

        if "/cgi-bin/loginpage" in current_url or "/cgi-bin/home?t=home/login" in current_url:
            logger.error("Session expired! Need to re-login.")
        else:
            logger.info("Session is active.")

            page_text = page.locator("body").inner_text(timeout=3000)
            logger.info("Page text preview (first 500): %s", page_text[:500])

            nav_links = page.evaluate("""() => {
                const links = document.querySelectorAll('a');
                return Array.from(links).map(a => ({
                    text: a.textContent.trim().substring(0, 30),
                    href: a.href.substring(0, 80)
                })).filter(l => l.text.length > 0).slice(0, 30);
            }""")
            logger.info("Nav links: %s", json.dumps(nav_links, ensure_ascii=False, indent=2))

            token_match = re.search(r"token=(\d+)", current_url)
            token = token_match.group(1) if token_match else ""

            published_url = f"{base}/cgi-bin/appmsg?t=media/appmsg_published&action=list&token={token}"
            logger.info("Navigating to published list: %s", published_url)
            page.goto(published_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

            published_text = page.locator("body").inner_text(timeout=3000)
            has_title = "人形机器人订单验证" in published_text
            logger.info("Published page contains target title: %s", has_title)
            logger.info("Published page text (first 800): %s", published_text[:800])

            page.screenshot(path=str(settings.output_dir / "wechat_published_check.png"), full_page=True)
            logger.info("Screenshot saved to wechat_published_check.png")

            draft_url = f"{base}/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card&token={token}"
            logger.info("Navigating to draft box: %s", draft_url)
            page.goto(draft_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

            draft_text = page.locator("body").inner_text(timeout=3000)
            has_draft_title = "人形机器人订单验证" in draft_text
            logger.info("Draft box contains target title: %s", has_draft_title)
            logger.info("Draft box text (first 800): %s", draft_text[:800])

            page.screenshot(path=str(settings.output_dir / "wechat_draft_check.png"), full_page=True)
            logger.info("Screenshot saved to wechat_draft_check.png")

    finally:
        browser.close()
