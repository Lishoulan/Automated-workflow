from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

# src/toutiao_content_factory/services/ → 上溯三级即项目根目录
CONFIG_PATH = os.getenv(
    "CONTENT_FACTORY_CONFIG_PATH",
    str(Path(__file__).resolve().parents[3] / "config.yaml"),
)


def _read_config_aigc_flag() -> bool | None:
    """读取 config.yaml 中的 compliance.aigc_declaration_enabled。

    config.yaml 虽然以 .yaml 结尾，实际内容是 JSON。
    读取失败时返回 None，由调用方回退到环境变量。
    """
    try:
        raw = Path(CONFIG_PATH).read_text(encoding="utf-8")
        data = json.loads(raw)
        value = data.get("compliance", {}).get("aigc_declaration_enabled")
        if isinstance(value, bool):
            return value
    except Exception:  # noqa: BLE001 - 配置缺失/损坏时静默回退到环境变量
        return None
    return None


def _is_platform_aigc_enabled(platform: str) -> bool:
    """通用平台 AIGC 声明启用检查函数。

    优先级：环境变量 > config.yaml。
    环境变量未显式设置时，回退到 config.yaml 的 compliance.aigc_declaration_enabled；
    两者都没有时保持历史默认行为（True），避免改变既有线上行为。
    """
    env_key = f"{platform.upper()}_AIGC_DECLARATION_ENABLED"
    raw = os.getenv(env_key)
    if raw is not None and raw.strip() != "":
        return raw.strip() == "1"
    config_flag = _read_config_aigc_flag()
    if config_flag is not None:
        return config_flag
    return True


def is_aigc_declaration_enabled() -> bool:
    return _is_platform_aigc_enabled("toutiao")


def is_wechat_aigc_declaration_enabled() -> bool:
    return _is_platform_aigc_enabled("wechat")



def get_max_daily_posts(platform: str = "toutiao") -> int:
    env_map = {
        "toutiao": "TOUTIAO_MAX_DAILY_POSTS",
        "wechat": "WECHAT_MAX_DAILY_POSTS",
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
    """统计今日已发布的文章数（按 platform 精确过滤）。

    时区修复：publish_history.json 使用本地时间（如 2026-05-31T00:27:13）记录，
    原实现使用 UTC 时间判断"今日"，导致凌晨0-8点期间都算"前一天"，
    实际放行了超过 max_daily_posts 限制的发布。
    现统一使用本地时间（datetime.now() 不带时区）与历史记录对齐。

    平台过滤修复（2026-08-27）：原实现忽略 platform 参数，导致微头条
    （platform=toutiao_weitoutiao）记录被计入图文（platform=toutiao）的
    每日发布上限——早间先发独立微头条会把当天的图文发布额度占掉。
    """
    if not history_path.exists():
        return 0
    try:
        records = json.loads(history_path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = records.get("records", records.get("history", []))
        if not isinstance(records, list):
            return 0
        today_str = datetime.now().strftime("%Y-%m-%d")
        count = 0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if str(rec.get("platform", "")).strip() != platform:
                continue
            ts = rec.get("published_at", rec.get("timestamp", rec.get("created_at", "")))
            if isinstance(ts, str) and ts.startswith(today_str):
                count += 1
        return count
    except Exception:
        return 0


def count_today_published_by_platform(history_path: Path, platform: str = "toutiao") -> int:
    """统计今日指定平台已发布的条数（platform 精确匹配）。

    用于微头条高频化：区分图文（platform=toutiao）与微头条（platform=toutiao_weitoutiao），
    独立微头条按每日目标补齐时不把图文发布数算进微头条额度。
    """
    return count_today_published(history_path, platform=platform)


def check_daily_post_limit(history_path: Path, platform: str = "toutiao") -> bool:
    """检查今日发布数是否已达上限。

    返回 True 表示可以继续发布，False 表示已达上限。
    """
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


async def _click_aigc_declaration_generic(
    page, 
    enabled_func: callable, 
    platform_name: str,
    selectors: list[str],
    check_expression: str
) -> bool:
    """通用 AIGC 声明复选框点击函数"""
    if not enabled_func():
        logger.info("%s AIGC 声明标注已禁用，跳过勾选。", platform_name)
        return False

    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() > 0:
                checked = await locator.first.evaluate(check_expression)
                if not checked:
                    await locator.first.click()
                    logger.info("已勾选 %s AIGC 声明复选框: %s", platform_name, selector)
                    await page.wait_for_timeout(500)
                else:
                    logger.info("%s AIGC 声明复选框已处于勾选状态: %s", platform_name, selector)
                return True
        except Exception as exc:
            logger.debug("尝试选择器 %s 失败: %s", selector, exc)
            continue

    logger.warning("未找到 %s AIGC 声明复选框，请手动勾选。", platform_name)
    return False


async def click_aigc_declaration_toutiao(page) -> bool:
    selectors = [
        "label.byte-checkbox:has-text('内容由AI生成')",
        "label.byte-checkbox:has-text('AI生成')",
        "label.byte-checkbox:has-text('AI辅助')",
        "label:has-text('内容由AI生成')",
        "label:has-text('声明AI生成')",
        "input[type='checkbox'][name*='ai']",
        "input[type='checkbox'][name*='AIGC']",
    ]
    check_expression = """el => {
        const input = el.querySelector('input') || el;
        return input.checked || el.classList.contains('byte-checkbox-checked');
    }"""
    return await _click_aigc_declaration_generic(
        page, is_aigc_declaration_enabled, "今日头条", selectors, check_expression
    )


async def click_aigc_declaration_wechat(page) -> bool:
    selectors = [
        "label:has-text('AI生成')",
        "label:has-text('内容由AI生成')",
        "label:has-text('声明')",
        "input[type='checkbox'][name*='ai']",
        "input[type='checkbox'][name*='AIGC']",
        ".weui-desktop-checkbox:has-text('AI')",
    ]
    check_expression = """el => {
        const input = el.querySelector('input') || el;
        return input.checked;
    }"""
    return await _click_aigc_declaration_generic(
        page, is_wechat_aigc_declaration_enabled, "微信", selectors, check_expression
    )


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
            await asyncio.sleep(wait_time)
        cls._last_switch_time = time.monotonic()
