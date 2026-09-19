"""头条登录 v5：窗口保持打开直到用户完成扫码。

判断标准（双条件）：URL 离开登录域 AND 页面进入创作者后台。
不做任何预先"已有登录态"假设——总是先等页面稳定，若在登录页则明确提示扫码。
用法: python scripts/_auto_login_robot.py robot_notes
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.utils.browser_utils import apply_stealth, build_persistent_context_options

LOGIN_MARKERS = ("/login", "/auth/", "/passport", "sso.toutiao.com", "login?")
BACKEND_OK = ("profile_v4", "graphic/publish", "mp.toutiao.com/")


def _state(url: str) -> str:
    """返回 login / backend / unknown。"""
    url_lower = (url or "").lower()
    if any(m in url_lower for m in LOGIN_MARKERS):
        return "login"
    if "mp.toutiao.com" in url_lower and ("profile_v4" in url_lower or "graphic" in url_lower):
        return "backend"
    if "toutiao.com" in url_lower:
        return "backend"
    return "unknown"


def main() -> int:
    account_key = sys.argv[1] if len(sys.argv) > 1 else "default"
    settings = load_settings(PROJECT_ROOT, account_key=account_key)
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = PROJECT_ROOT / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"account: {account_key}")
    print(f"profile_dir: {profile_dir}")

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

    login_success = False
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            **launch_options,
        )
        apply_stealth(context, fingerprint=fingerprint)
        page = context.new_page()
        page.goto(
            "https://mp.toutiao.com/profile_v4/graphic/publish",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        print("等待页面稳定（10秒）...")
        time.sleep(10)
        state = _state(page.url or "")
        print(f"初始状态: {state} | URL: {(page.url or '')[:110]}")

        if state == "backend":
            # 仍然需要二次确认：等 3 秒再看是否被踢回登录页
            time.sleep(3)
            state = _state(page.url or "")
            if state == "backend":
                print("检测到有效登录态（后台页）。")
                login_success = True

        if not login_success:
            print("当前为登录页。请在浏览器中扫码登录...")
            print("（脚本将保持运行，检测到进入后台后自动保存）")
            max_wait = 600  # 10 分钟
            for i in range(max_wait):
                time.sleep(3)
                try:
                    url = page.url or ""
                    state = _state(url)
                    if state == "backend":
                        time.sleep(3)
                        if _state(page.url or "") == "backend":
                            print(f"[{i*3}s] 检测到登录成功! URL: {url}")
                            login_success = True
                            break
                    if i % 10 == 0:
                        print(f"[{i*3}s] 等待扫码登录... 状态={state} URL: {url[:100]}")
                except Exception as e:
                    if i % 10 == 0:
                        print(f"[{i*3}s] 检测中... ({e})")

        if login_success:
            time.sleep(2)
            print("登录成功！cookie已保存。")
        else:
            print("等待超时，未检测到登录成功。")
            try:
                shot = profile_dir.parent / "login_timeout_screenshot.png"
                page.screenshot(path=str(shot), full_page=False)
                print(f"超时截图已保存: {shot}")
            except Exception:
                pass

        context.close()

    print("浏览器已关闭。")
    return 0 if login_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
