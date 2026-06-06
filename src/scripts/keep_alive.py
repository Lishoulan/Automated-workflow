"""
Keep-Alive script for platform login session maintenance.
Opens Toutiao and WeChat backends silently to refresh cookie TTL.
"""
from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.utils.browser_utils import apply_stealth, random_scroll, BrowserTimeout
from toutiao_content_factory.utils.logging_utils import configure_logging

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def keep_alive_toutiao(settings, logger):
    if sync_playwright is None:
        logger.warning("Playwright not installed, skipping.")
        return
    profile_dir = Path(str(settings.project_root)) / ".runtime" / "toutiao_profile"
    if not profile_dir.exists():
        logger.warning("Toutiao profile not found, skipping.")
        return
    url = "https://mp.toutiao.com/"
    logger.info("Toutiao keep-alive...")
    try:
        with BrowserTimeout(seconds=25.0):
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir), headless=True)
                apply_stealth(context)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                random_scroll(page)
                context.close()
        logger.info("Toutiao keep-alive done")
    except Exception as exc:
        logger.warning("Toutiao keep-alive skipped: %s", exc)


def keep_alive_wechat(settings, logger):
    if sync_playwright is None:
        return
    if not settings.wechat.enabled:
        return
    profile_dir = Path(str(settings.project_root)) / ".runtime" / "wechat_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    url = "https://mp.weixin.qq.com/"
    logger.info("WeChat keep-alive...")
    try:
        with BrowserTimeout(seconds=25.0):
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir), headless=True)
                apply_stealth(context)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                random_scroll(page)
                context.close()
        logger.info("WeChat keep-alive done")
    except Exception as exc:
        logger.warning("WeChat keep-alive skipped: %s", exc)


def main(account_key="default"):
    project_root = Path(__file__).resolve().parent.parent.parent
    settings = load_settings(project_root, account_key=account_key)
    logger = configure_logging(
        settings.logs_dir, settings.logging_level,
        logger_name="keep_alive." + settings.account_slug)
    logger.info("Keep-alive for account=%s", account_key)
    keep_alive_toutiao(settings, logger)
    keep_alive_wechat(settings, logger)
    logger.info("Keep-alive done for account=%s", account_key)


if __name__ == "__main__":
    accounts = sys.argv[1:] or ["default", "energy_notes", "robot_notes"]
    for acct in accounts:
        main(acct)
        time.sleep(random.uniform(10, 30))
