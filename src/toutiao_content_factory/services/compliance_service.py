from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def is_aigc_declaration_enabled() -> bool:
    return os.getenv("TOUTIAO_AIGC_DECLARATION_ENABLED", "1").strip() == "1"


def is_wechat_aigc_declaration_enabled() -> bool:
    return os.getenv("WECHAT_AIGC_DECLARATION_ENABLED", "1").strip() == "1"


def is_douyin_aigc_declaration_enabled() -> bool:
    return os.getenv("DOUYIN_AIGC_DECLARATION_ENABLED", "1").strip() == "1"


def get_max_daily_posts(platform: str = "toutiao") -> int:
    env_map = {
        "toutiao": "TOUTIAO_MAX_DAILY_POSTS",
        "wechat": "WECHAT_MAX_DAILY_POSTS",
        "douyin": "DOUYIN_MAX_DAILY_POSTS",
    }
    env_key = env_map.get(platform, "TOUTIAO_MAX_DAILY_POSTS")
    return int(os.getenv(env_key, "2").strip() or "2")


def get_proxy_server() -> str:
    return os.getenv("TOUTIAO_PROXY_SERVER", "").strip()


def get_account_login_interval() -> int:
    return int(os.getenv("ACCOUNT_LOGIN_INTERVAL_SECONDS", "300").strip() or "300")


def get_account_switch_interval() -> int:
    return int(os.getenv("ACCOUNT_SWITCH_INTERVAL_SECONDS", "60").strip() or "60")


def count_today_published(history_path: Path, platform: str = "toutiao") -> int:
    if not history_path.exists():
        return 0
    try:
        import json

        records = json.loads(history_path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = records.get("records", records.get("history", []))
        if not isinstance(records, list):
            return 0
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = 0
        for rec in records:
            ts = rec.get("published_at", rec.get("timestamp", rec.get("created_at", "")))
            if isinstance(ts, str) and today_str in ts:
                count += 1
        return count
    except Exception:
        return 0


def check_daily_post_limit(history_path: Path, platform: str = "toutiao") -> bool:
    max_posts = get_max_daily_posts(platform)
    today_count = count_today_published(history_path, platform)
    if today_count >= max_posts:
        logger.warning(
            "合规限制：今日 %s 已发布 %d 篇，达到上限 %d 篇，跳过发布。",
            platform,
            today_count,
            max_posts,
        )
        return False
    return True


async def click_aigc_declaration_toutiao(page) -> bool:
    if not is_aigc_declaration_enabled():
        logger.info("AIGC 声明标注已禁用，跳过勾选。")
        return False

    selectors = [
        "label.byte-checkbox:has-text('内容由AI生成')",
        "label.byte-checkbox:has-text('AI生成')",
        "label.byte-checkbox:has-text('AI辅助')",
        "label:has-text('内容由AI生成')",
        "label:has-text('声明AI生成')",
        "input[type='checkbox'][name*='ai']",
        "input[type='checkbox'][name*='AIGC']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() > 0:
                checked = await locator.first.evaluate(
                    """el => {
                    const input = el.querySelector('input') || el;
                    return input.checked || el.classList.contains('byte-checkbox-checked');
                }"""
                )
                if not checked:
                    await locator.first.click()
                    logger.info("已勾选今日头条 AIGC 声明复选框: %s", selector)
                    await page.wait_for_timeout(500)
                else:
                    logger.info("今日头条 AIGC 声明复选框已处于勾选状态: %s", selector)
                return True
        except Exception as exc:
            logger.debug("尝试选择器 %s 失败: %s", selector, exc)
            continue

    logger.warning("未找到今日头条 AIGC 声明复选框，请手动勾选。")
    return False


async def click_aigc_declaration_wechat(page) -> bool:
    if not is_wechat_aigc_declaration_enabled():
        logger.info("微信 AIGC 声明标注已禁用，跳过勾选。")
        return False

    selectors = [
        "label:has-text('AI生成')",
        "label:has-text('内容由AI生成')",
        "label:has-text('声明')",
        "input[type='checkbox'][name*='ai']",
        "input[type='checkbox'][name*='AIGC']",
        ".weui-desktop-checkbox:has-text('AI')",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() > 0:
                checked = await locator.first.evaluate(
                    """el => {
                    const input = el.querySelector('input') || el;
                    return input.checked;
                }"""
                )
                if not checked:
                    await locator.first.click()
                    logger.info("已勾选微信 AIGC 声明复选框: %s", selector)
                    await page.wait_for_timeout(500)
                else:
                    logger.info("微信 AIGC 声明复选框已处于勾选状态: %s", selector)
                return True
        except Exception as exc:
            logger.debug("尝试选择器 %s 失败: %s", selector, exc)
            continue

    logger.warning("未找到微信 AIGC 声明复选框，请手动勾选。")
    return False


async def apply_proxy_to_browser(launch_options: dict[str, Any]) -> dict[str, Any]:
    proxy_server = get_proxy_server()
    if proxy_server:
        launch_options["proxy"] = {"server": proxy_server}
        logger.info("已为浏览器配置代理: %s", proxy_server)
    return launch_options


class AccountLoginGuard:
    _last_login_time: dict[str, float] = {}
    _last_switch_time: float = 0.0

    @classmethod
    async def wait_before_login(cls, account_key: str) -> None:
        interval = get_account_login_interval()
        now = time.monotonic()
        last = cls._last_login_time.get(account_key, 0.0)
        elapsed = now - last
        if elapsed < interval and last > 0:
            wait_time = interval - elapsed
            logger.info(
                "合规等待：账号 %s 登录间隔不足 %ds，等待 %.1fs",
                account_key,
                interval,
                wait_time,
            )
            import asyncio

            await asyncio.sleep(wait_time)
        cls._last_login_time[account_key] = time.monotonic()

    @classmethod
    async def wait_before_switch(cls) -> None:
        interval = get_account_switch_interval()
        now = time.monotonic()
        elapsed = now - cls._last_switch_time
        if elapsed < interval and cls._last_switch_time > 0:
            wait_time = interval - elapsed
            logger.info(
                "合规等待：账号切换间隔不足 %ds，等待 %.1fs",
                interval,
                wait_time,
            )
            import asyncio

            await asyncio.sleep(wait_time)
        cls._last_switch_time = time.monotonic()
