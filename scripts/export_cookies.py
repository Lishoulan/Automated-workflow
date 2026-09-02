"""跨平台浏览器 Cookie 导出工具 — 从本地浏览器 Profile 导出明文 Cookie。

核心原理：
    Playwright 打开本地 Profile 时会自动解密 DPAPI 加密的 Cookie，
    通过 context.cookies() 可获取明文值，导出为 JSON 供云端 Linux 注入使用。

用法:
    python scripts/export_cookies.py                          # 导出所有平台
    python scripts/export_cookies.py --platforms toutiao      # 只导出头条
    python scripts/export_cookies.py --output cookies.json    # 指定输出路径
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import load_settings

logger = logging.getLogger("export_cookies")

# 平台配置：平台名 → (访问URL, Cookie域名列表)
PLATFORM_CONFIGS = {
    "toutiao": {
        "url": "https://mp.toutiao.com/profile_v4/graphic/publish",
        "domains": [".toutiao.com", ".snssdk.com", ".bytedance.com", ".byteimg.com"],
    },
    "135editor": {
        "url": "https://www.135editor.com/beautify_editor.html",
        "domains": [".135editor.com"],
    },
}


def _run_playwright_in_thread(fn, *args, **kwargs):
    """在独立线程中运行 sync_playwright。"""
    try:
        import asyncio
        asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(fn, *args, **kwargs).result(timeout=120)
    except RuntimeError:
        return fn(*args, **kwargs)


def _export_platform_cookies(
    profile_dir: Path,
    url: str,
    channel: str | None,
    executable_path: str | None,
    headless: bool,
) -> list[dict]:
    """打开 Profile 并导出所有 Cookie。"""
    from playwright.sync_api import sync_playwright

    cookies = []

    def _run():
        nonlocal cookies
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel=channel or None,
                executable_path=executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

                # 检查是否被重定向到登录页
                current_url = page.url.lower()
                if "login" in current_url or "sso" in current_url:
                    logger.warning("登录已过期: %s (URL: %s)", profile_dir.name, current_url)
                    cookies = []
                    return

                # 导出所有 Cookie
                raw_cookies = browser.cookies()
                cookies = []
                for c in raw_cookies:
                    cookies.append({
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c["path"],
                        "expires": c.get("expires", -1),
                        "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", False),
                        "sameSite": c.get("sameSite", "Lax"),
                    })
                logger.info("导出 %d 个 Cookie (from %s)", len(cookies), profile_dir.name)
            finally:
                browser.close()

    _run_playwright_in_thread(_run)
    return cookies


def export_cookies(
    settings,
    platforms: list[str],
    output_path: Path,
    headless: bool = False,
) -> dict:
    """导出指定平台的 Cookie 并保存为 JSON。"""
    channel = settings.publishing.channel or None
    executable_path = settings.publishing.executable_path or None

    result = {"exported_at": None, "platforms": {}}

    for platform in platforms:
        if platform == "135editor":
            logger.warning("Skipping 135editor cookie export: the integration is disabled.")
            continue

        cfg = PLATFORM_CONFIGS.get(platform)
        if not cfg:
            logger.warning("未知平台: %s", platform)
            continue

        # 确定 Profile 目录
        if platform == "toutiao":
            profile_dir = Path(settings.publishing.user_data_dir)
            if not profile_dir.is_absolute():
                profile_dir = PROJECT_ROOT / profile_dir
        elif platform == "135editor":
            profile_dir = Path(settings.wechat.editor_135_profile_dir)
            if not profile_dir.is_absolute():
                profile_dir = PROJECT_ROOT / profile_dir
        else:
            continue

        profile_dir = Path(profile_dir)
        if not profile_dir.exists():
            logger.warning("Profile 目录不存在: %s", profile_dir)
            continue

        logger.info("导出 %s Cookie (profile: %s)...", platform, profile_dir.name)
        cookies = _export_platform_cookies(
            profile_dir, cfg["url"], channel, executable_path, headless
        )

        if cookies:
            # 过滤只保留相关域名的 Cookie
            domain_cookies = [
                c for c in cookies
                if any(c["domain"].endswith(d) for d in cfg["domains"])
            ]
            result["platforms"][platform] = {
                "cookie_count": len(domain_cookies),
                "cookies": domain_cookies,
            }
            logger.info("✓ %s: 导出 %d 个相关域名 Cookie", platform, len(domain_cookies))
        else:
            result["platforms"][platform] = {"cookie_count": 0, "cookies": []}
            logger.warning("✗ %s: 无可用 Cookie（可能已过期）", platform)

    # 保存结果
    from datetime import datetime, timezone
    result["exported_at"] = datetime.now(timezone.utc).isoformat()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Cookie 已保存到: %s", output_path)

    return result


def main():
    parser = argparse.ArgumentParser(description="跨平台 Cookie 导出")
    parser.add_argument(
        "--platforms", default="toutiao",
        help="平台列表，逗号分隔 (toutiao)。默认: toutiao",
    )
    parser.add_argument(
        "--output", default="",
        help="输出文件路径。默认: .runtime/automation/exported_cookies.json",
    )
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--account", default="default")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    os.environ.setdefault("TOUTIAO_ACCOUNT", args.account)
    settings = load_settings(PROJECT_ROOT, account_key=args.account)

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = settings.account_runtime_dir / "automation" / "exported_cookies.json"

    result = export_cookies(settings, platforms, output_path, headless=args.headless)

    # 统计
    total = sum(p.get("cookie_count", 0) for p in result["platforms"].values())
    if total > 0:
        logger.info("导出完成: 共 %d 个 Cookie, %d 个平台", total, len(result["platforms"]))
    else:
        logger.error("导出失败: 没有可用的 Cookie，请先重新登录")
        sys.exit(1)


if __name__ == "__main__":
    main()
