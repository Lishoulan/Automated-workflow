"""精确验证头条登录态（使用与发布服务相同的 stealth 指纹）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.utils.browser_utils import apply_stealth, build_persistent_context_options


def main() -> int:
    account_key = sys.argv[1] if len(sys.argv) > 1 else "default"
    settings = load_settings(PROJECT_ROOT, account_key=account_key)
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = PROJECT_ROOT / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=PROJECT_ROOT,
        logger=__import__("logging").getLogger("verify"),
        label="verify-login",
    )

    launch_options, fingerprint = build_persistent_context_options(
        account_key=settings.account_key,
        profile_dir=profile_dir,
        proxy_server="",
        headless=False,
        channel=settings.publishing.channel or None,
        executable_path=settings.publishing.executable_path or None,
        timezone_id="Asia/Shanghai",
        locale="zh-CN",
    )

    result = {"url": "", "title": "", "login": None, "ready": False}
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            **launch_options,
        )
        apply_stealth(browser, fingerprint=fingerprint)
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(
            "https://mp.toutiao.com/profile_v4/graphic/publish",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(4000)
        result["url"] = page.url
        result["title"] = page.title()
        url_lower = page.url.lower()
        result["login"] = any(t in url_lower for t in ("login", "passport", "sso."))
        result["ready"] = ("graphic/publish" in url_lower or "profile_v4" in url_lower) and not result["login"]
        # 提取可见标记
        try:
            markers = page.evaluate(
                """() => {
                    const texts = [];
                    document.querySelectorAll('a, span, div[class*="avatar"], img[class*="avatar"]').forEach(el => {
                        const t = (el.textContent || '').trim();
                        if (t && t.length <= 30 && /[\\u4e00-\\u9fff]/.test(t) && (t.includes('创作') || t.includes('发布') || t.includes('数据') || t.includes('首页'))) texts.push(t);
                    });
                    return texts.slice(0, 15);
                }"""
            )
            result["markers"] = markers
        except Exception:
            result["markers"] = []
        browser.close()

    out_dir = PROJECT_ROOT / "output" / "accounts" / account_key / "automation"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "login_verify.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
