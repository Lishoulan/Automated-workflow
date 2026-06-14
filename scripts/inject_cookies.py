"""跨平台浏览器 Cookie 注入工具 — 将明文 Cookie 注入到云端浏览器 Profile。

核心原理：
    从 exported_cookies.json 读取明文 Cookie，通过 Playwright context.add_cookies()
    注入到 Linux 云端的浏览器 Profile，绕过 Windows DPAPI 加密不兼容问题。

用法:
    python scripts/inject_cookies.py                          # 注入所有平台
    python scripts/inject_cookies.py --platforms toutiao      # 只注入头条
    python scripts/inject_cookies.py --input cookies.json     # 指定输入路径
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

logger = logging.getLogger("inject_cookies")

# 平台配置
PLATFORM_CONFIGS = {
    "toutiao": {
        "url": "https://mp.toutiao.com/profile_v4/graphic/publish",
        "verify_login_indicators": ["login", "sso"],
    },
    "135editor": {
        "url": "https://www.135editor.com/beautify_editor.html",
        "verify_login_indicators": ["login", "登录"],
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


def _inject_and_verify(
    profile_dir: Path,
    cookies: list[dict],
    url: str,
    verify_login_indicators: list[str],
    headless: bool,
) -> dict:
    """注入 Cookie 并验证 Session 是否有效。"""
    from playwright.sync_api import sync_playwright

    result = {"status": "unknown", "injected": 0, "verified": False}

    def _run():
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                # 云端使用 Playwright 自带的 Chromium
            )
            try:
                # 注入 Cookie
                injectable = []
                for c in cookies:
                    cookie = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c["path"],
                    }
                    if c.get("expires", -1) > 0:
                        cookie["expires"] = c["expires"]
                    if c.get("httpOnly"):
                        cookie["httpOnly"] = True
                    if c.get("secure"):
                        cookie["secure"] = True
                    if c.get("sameSite") in ("Strict", "Lax", "None"):
                        cookie["sameSite"] = c["sameSite"]
                    injectable.append(cookie)

                browser.add_cookies(injectable)
                result["injected"] = len(injectable)
                logger.info("已注入 %d 个 Cookie 到 %s", len(injectable), profile_dir.name)

                # 访问页面验证 Session
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)

                current_url = page.url.lower()
                is_login_page = any(ind in current_url for ind in verify_login_indicators)

                if is_login_page:
                    result["status"] = "still_expired"
                    result["verified"] = False
                    logger.warning("Cookie 注入后仍被重定向到登录页: %s", current_url)
                else:
                    result["status"] = "alive"
                    result["verified"] = True
                    logger.info("✓ Session 验证成功 (URL: %s)", page.url[:80])

                # 正常关闭浏览器，让 Playwright 将 Cookie 持久化到 Profile
            finally:
                browser.close()

    _run_playwright_in_thread(_run)
    return result


def inject_cookies(
    settings,
    platforms: list[str],
    input_path: Path,
    headless: bool = True,
) -> dict:
    """从 JSON 文件注入 Cookie 到指定平台。"""
    if not input_path.exists():
        logger.error("Cookie 文件不存在: %s", input_path)
        return {"error": f"File not found: {input_path}"}

    data = json.loads(input_path.read_text(encoding="utf-8"))
    logger.info("加载 Cookie: exported_at=%s, platforms=%s",
                data.get("exported_at", "?"), list(data.get("platforms", {}).keys()))

    results = {}

    for platform in platforms:
        platform_data = data.get("platforms", {}).get(platform)
        if not platform_data or not platform_data.get("cookies"):
            logger.warning("跳过 %s: 无可用 Cookie 数据", platform)
            results[platform] = {"status": "skipped", "reason": "no cookies"}
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
        profile_dir.mkdir(parents=True, exist_ok=True)

        logger.info("注入 %s Cookie (profile: %s, cookies: %d)...",
                     platform, profile_dir.name, platform_data["cookie_count"])

        result = _inject_and_verify(
            profile_dir=profile_dir,
            cookies=platform_data["cookies"],
            url=cfg["url"],
            verify_login_indicators=cfg["verify_login_indicators"],
            headless=headless,
        )
        results[platform] = result

    return results


def main():
    parser = argparse.ArgumentParser(description="跨平台 Cookie 注入")
    parser.add_argument(
        "--platforms", default="toutiao,135editor",
        help="平台列表，逗号分隔。默认: toutiao,135editor",
    )
    parser.add_argument(
        "--input", default="",
        help="Cookie 文件路径。默认: .runtime/automation/exported_cookies.json",
    )
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", action="store_false", dest="headless")
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

    if args.input:
        input_path = Path(args.input)
    else:
        input_path = settings.account_runtime_dir / "automation" / "exported_cookies.json"

    results = inject_cookies(settings, platforms, input_path, headless=args.headless)

    # 输出汇总
    logger.info("注入结果: %s", json.dumps(results, ensure_ascii=False, indent=2))

    success = [p for p, r in results.items() if r.get("status") == "alive"]
    failed = [p for p, r in results.items() if r.get("status") not in ("alive", "skipped")]

    if success:
        logger.info("✓ 注入成功: %s", success)
    if failed:
        logger.error("✗ 注入失败: %s", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
