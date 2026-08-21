"""自动检测登录状态的头条登录脚本。

打开浏览器到头条创作中心，等待用户扫码登录，
检测到登录成功后自动关闭浏览器并保存cookie。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright
from toutiao_content_factory.config import load_settings


def main() -> int:
    settings = load_settings(PROJECT_ROOT)
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = PROJECT_ROOT / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"profile_dir: {profile_dir}")
    print("正在打开浏览器，请扫码登录头条创作中心...")
    print("脚本会自动检测登录状态，登录成功后自动关闭。")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=settings.publishing.channel or "msedge",
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto("https://mp.toutiao.com/profile_v4/graphic/publish", wait_until="domcontentloaded")

        print("浏览器已打开头条创作中心。")
        print("请在浏览器中扫码登录，脚本会自动检测登录状态...")

        # 自动检测登录状态，最多等待180秒
        login_success = False
        max_wait = 180
        for i in range(max_wait):
            time.sleep(1)
            try:
                url = page.url
                title = page.title()
                # 检测是否已跳转到创作中心（登录成功后URL不再包含login）
                if "login" not in url.lower() and "profile_v4" in url:
                    # 进一步检测页面是否有创作中心的元素
                    try:
                        # 创作中心页面通常有发布按钮或编辑器
                        editor = page.query_selector(".ProEditor, .byte-editor, .publish-content, [class*='editor'], [class*='publish']")
                        if editor or "发布" in title or "创作" in title:
                            print(f"[{i}s] 检测到登录成功! URL: {url}, Title: {title}")
                            login_success = True
                            break
                    except Exception:
                        pass
                # 检测是否有二维码登录区域
                qr = page.query_selector("[class*='qr'], [class*='qrcode'], iframe[src*='login']")
                if qr:
                    if i % 10 == 0:
                        print(f"[{i}s] 等待扫码登录... (URL: {url})")
                else:
                    if i % 10 == 0:
                        print(f"[{i}s] 等待登录... (URL: {url}, Title: {title})")
            except Exception as e:
                if i % 10 == 0:
                    print(f"[{i}s] 检测中... ({e})")

        if login_success:
            # 等待2秒确保cookie完全保存
            time.sleep(2)
            print("登录成功！cookie已保存。")
        else:
            print(f"等待{max_wait}秒后超时，未检测到登录成功。")

        context.close()

    print("浏览器已关闭。")
    return 0 if login_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
