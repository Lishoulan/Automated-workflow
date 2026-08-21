"""手动登录头条创作中心，保存cookie到persistent context。

用法: py scripts/manual_login.py
浏览器会打开头条创作中心，扫码登录后按回车关闭浏览器。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright
from toutiao_content_factory.config import load_settings


def main() -> int:
    settings = load_settings()
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = PROJECT_ROOT / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"profile_dir: {profile_dir}")
    print("正在打开浏览器，请扫码登录头条创作中心...")
    print("登录成功后，请在终端按回车键关闭浏览器。")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=settings.publishing.channel or "msedge",
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto("https://mp.toutiao.com/profile_v4/graphic/publish", wait_until="domcontentloaded")

        print()
        print("浏览器已打开头条创作中心。")
        print("请完成扫码登录，登录成功后页面会跳转到创作中心。")
        print("确认登录成功后，回到此终端按回车键关闭浏览器并保存cookie。")

        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        context.close()

    print("浏览器已关闭，cookie已保存到 persistent context。")
    print("现在可以运行 --draft-only 测试了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
