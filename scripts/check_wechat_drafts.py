import sys, time, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("wechat_drafts")

settings = load_settings(Path(__file__).resolve().parent.parent, account_key="default")

from playwright.sync_api import sync_playwright

profile_dir = settings.account_runtime_dir / "wechat_profile"
profile_dir.mkdir(parents=True, exist_ok=True)

prepare_browser_profile(
    profile_dir=profile_dir,
    project_root=settings.project_root,
    logger=logger,
    label="wechat:drafts",
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
        token_match = re.search(r"token=(\d+)", current_url)
        token = token_match.group(1) if token_match else ""

        if not token:
            page.wait_for_timeout(3000)
            current_url = page.url
            token_match = re.search(r"token=(\d+)", current_url)
            token = token_match.group(1) if token_match else ""

        draft_url = f"{base}/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card&token={token}"
        logger.info("Navigating to draft box: %s", draft_url)
        page.goto(draft_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)

        draft_text = page.locator("body").inner_text(timeout=3000)
        has_target = "人形机器人订单验证" in draft_text
        logger.info("Draft box contains target title: %s", has_target)
        logger.info("Draft box text (first 1000): %s", draft_text[:1000])

        page.screenshot(path=str(settings.output_dir / "wechat_drafts_check.png"), full_page=True)

    finally:
        browser.close()
