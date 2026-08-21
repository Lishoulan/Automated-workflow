"""临时脚本：启动 Playwright 浏览器登录 135 编辑器，手动登录后按回车保存 cookies。

用法：
    py login_135_manual.py

流程：
1. 启动非 headless 的 Chromium，打开 135 编辑器首页
2. 在弹出的浏览器窗口里手动登录
3. 登录成功后回到终端按回车
4. 脚本保存 cookies 到 .runtime/automation/exported_cookies.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from playwright.sync_api import sync_playwright

PROFILE_DIR = PROJECT_ROOT / ".runtime" / "editor_135_profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = PROJECT_ROOT / ".runtime" / "automation" / "exported_cookies.json"
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def main() -> int:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.135editor.com/editor_users/index", wait_until="domcontentloaded", timeout=30000)

        # 等待用户创建 done.flag 文件
        done_flag = PROJECT_ROOT / "login_done.flag"
        if done_flag.exists():
            done_flag.unlink()
        print("")
        print("=" * 60)
        print("浏览器窗口已打开 135 编辑器。")
        print("请在浏览器窗口中完成登录（扫码或账号密码）。")
        print("登录成功后能看到编辑器工具栏。")
        print("")
        print("完成后请在本目录创建 done.flag 文件：")
        print("  - 方式1: 在文件管理器里新建 login_done.flag")
        print("  - 方式2: 在另一个终端运行: echo ok > login_done.flag")
        print("=" * 60)
        print("")
        print("等待 login_done.flag 文件出现...", flush=True)

        import time
        timeout = 1200  # 20 分钟
        start = time.time()
        while not done_flag.exists():
            if time.time() - start > timeout:
                print("超时，未检测到 login_done.flag，放弃保存。")
                ctx.close()
                return 1
            time.sleep(2)
        print("检测到 login_done.flag，开始保存 cookies...")
        done_flag.unlink(missing_ok=True)

        # 保存 storage_state（Playwright 标准格式）
        state = ctx.storage_state()
        cookies = state.get("cookies", [])
        # 只保留 135editor 相关的 cookies
        keep_cookies = [
            c for c in cookies
            if "135editor.com" in c.get("domain", "") or "135editor" in c.get("domain", "")
        ]
        if not keep_cookies:
            keep_cookies = cookies  # 全部保留，避免漏掉

        # 写入 exported_cookies.json 格式（代码支持的双格式之一）
        payload = {
            "version": 1,
            "exported_at": "2026-07-03T11:00:00",
            "platforms": {
                "135editor": {
                    "cookies": keep_cookies,
                    "storage_state": state,
                },
            },
        }
        OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存 {len(keep_cookies)} 个 cookies 到: {OUTPUT_FILE}")
        print("现在可以运行推送命令了。")

        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
