"""浏览器Session保活脚本 — 定期访问各平台刷新Cookie，防止登录过期。

用法:
    python scripts/keep_alive.py                  # 保活所有已启用平台
    python scripts/keep_alive.py --platforms toutiao,wechat  # 只保活指定平台
    python scripts/keep_alive.py --headless       # 无头模式（云端使用）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 确保项目 src 在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile


def _run_playwright_in_thread(fn, *args, **kwargs):
    """在独立线程中运行 sync_playwright，避免 asyncio 循环冲突。"""
    try:
        import asyncio
        asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(fn, *args, **kwargs).result(timeout=120)
    except RuntimeError:
        return fn(*args, **kwargs)


def _keep_alive_toutiao(settings: AppSettings, logger: logging.Logger, headless: bool) -> dict:
    """访问头条创作者平台刷新Cookie。"""
    from playwright.sync_api import sync_playwright

    profile_dir = settings.publishing.browser_profile_dir or settings.account_runtime_dir / "toutiao_profile"
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logger,
        label="keep-alive:toutiao",
        extra_paths=[settings.account_runtime_dir],
    )

    result = {"platform": "toutiao", "status": "unknown", "cookies_refreshed": False}

    def _run():
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel=settings.publishing.channel or None,
                executable_path=settings.publishing.executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                # 访问头条创作者中心首页（轻量页面，刷新session）
                page.goto(
                    "https://mp.toutiao.com/profile_v4/graphic/publish",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_timeout(3000)

                current_url = page.url.lower()
                # 检查是否被重定向到登录页
                if "login" in current_url or "sso" in current_url:
                    result["status"] = "login_required"
                    result["cookies_refreshed"] = False
                    logger.warning("头条: 登录已过期，需要重新登录")
                else:
                    result["status"] = "alive"
                    result["cookies_refreshed"] = True
                    logger.info("头条: Session保活成功，Cookie已刷新")
            finally:
                browser.close()
        return result

    return _run_playwright_in_thread(_run)


def _keep_alive_wechat_135(settings: AppSettings, logger: logging.Logger, headless: bool) -> dict:
    """访问135编辑器刷新Cookie。"""
    from playwright.sync_api import sync_playwright

    profile_dir = settings.wechat.editor_135_profile_dir or settings.account_runtime_dir / "editor_135_profile"
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logger,
        label="keep-alive:wechat_135",
        extra_paths=[settings.account_runtime_dir],
    )

    result = {"platform": "wechat_135", "status": "unknown", "cookies_refreshed": False}

    def _run():
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel=settings.publishing.channel or None,
                executable_path=settings.publishing.executable_path or None,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                base_url = settings.wechat.editor_135_base_url.rstrip("/")
                page.goto(
                    f"{base_url}/beautify_editor.html",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_timeout(3000)

                # 检查是否需要登录
                current_url = page.url.lower()
                page_content = page.content()[:2000].lower()
                if "login" in current_url or "登录" in page_content and "editor" not in current_url:
                    result["status"] = "login_required"
                    result["cookies_refreshed"] = False
                    logger.warning("135编辑器: 登录已过期，需要重新登录")
                else:
                    result["status"] = "alive"
                    result["cookies_refreshed"] = True
                    logger.info("135编辑器: Session保活成功，Cookie已刷新")
            finally:
                browser.close()
        return result

    return _run_playwright_in_thread(_run)


def main():
    parser = argparse.ArgumentParser(description="浏览器Session保活")
    parser.add_argument("--platforms", default="", help="指定平台，逗号分隔 (toutiao,wechat)。默认全部")
    parser.add_argument("--headless", action="store_true", default=False, help="无头模式")
    parser.add_argument("--account", default="default", help="账号名")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("keep_alive")

    # 加载配置
    os.environ.setdefault("TOUTIAO_ACCOUNT", args.account)
    settings = AppSettings()

    # 云端默认headless
    headless = args.headless or bool(settings.publishing.headless)

    # 确定要保活的平台
    if args.platforms:
        platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    else:
        platforms = []
        if settings.publishing.enabled:
            platforms.append("toutiao")
        if settings.wechat.enabled and settings.wechat.editor_135_enabled:
            platforms.append("wechat")

    if not platforms:
        logger.info("没有需要保活的平台")
        return

    logger.info("开始Session保活: platforms=%s headless=%s", platforms, headless)

    results = {}
    for platform in platforms:
        try:
            if platform == "toutiao":
                results[platform] = _keep_alive_toutiao(settings, logger, headless)
            elif platform in ("wechat", "wechat_135"):
                results[platform] = _keep_alive_wechat_135(settings, logger, headless)
            else:
                logger.warning("未知平台: %s", platform)
        except Exception as exc:
            logger.error("保活失败 %s: %s", platform, exc)
            results[platform] = {"platform": platform, "status": "error", "error": str(exc)}

    # 输出结果
    logger.info("保活结果: %s", json.dumps(results, ensure_ascii=False, indent=2))

    # 保存结果到文件
    output_dir = settings.account_runtime_dir / "automation"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "keep_alive_result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 如果有平台需要重新登录，返回非零退出码
    needs_login = [p for p, r in results.items() if r.get("status") == "login_required"]
    if needs_login:
        logger.error("以下平台需要重新登录: %s", needs_login)
        sys.exit(1)


if __name__ == "__main__":
    main()
