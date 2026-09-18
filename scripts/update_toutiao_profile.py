"""修改头条号「用户名」与「用户简介」。

用 robot_notes 的登录态（登录的是「手蓝数据笔记」）。执行前先记录原值，
执行后重新读取页面确认是否真的生效。

用法：
    python scripts/update_toutiao_profile.py --name 手蓝人间故事 --bio "..." --dry-run
    python scripts/update_toutiao_profile.py --name 手蓝人间故事 --bio "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

INFO_URL = "https://mp.toutiao.com/profile_v4/personal/info"
# 上一次探测得到的「编辑」按钮坐标（用户名 / 简介行）
EDIT_BUTTON_Y = {"name": 185, "bio": 253}
EDIT_BUTTON_X = 1140
BACKUP = ROOT / ".runtime" / "account_profile_backup.json"


def read_current(page) -> dict:
    """读当前用户名与简介。"""
    values = page.evaluate(
        """() => {
            const out = {name: '', bio: ''};
            const vis = el => { const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0; };
            for (const el of document.querySelectorAll('input')) {
                if (!vis(el)) continue;
                if (el.getAttribute('type') === 'file') continue;
                const v = (el.value || '').trim();
                if (v && !out.name) out.name = v;
            }
            for (const el of document.querySelectorAll('textarea')) {
                if (!vis(el)) continue;
                const v = (el.value || '').trim();
                if (v && !out.bio) out.bio = v;
            }
            return out;
        }"""
    )
    return values


def click_edit(page, which: str) -> bool:
    """按坐标点开对应行的『编辑』。坐标失效时回退到 JS 定位。"""
    y = EDIT_BUTTON_Y[which]
    page.mouse.click(EDIT_BUTTON_X, y)
    page.wait_for_timeout(2200)
    # 检查是否真的弹出了输入框
    opened = page.evaluate(
        """() => {
            const vis = el => { const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0; };
            let n = 0;
            for (const el of document.querySelectorAll('input, textarea')) {
                if (vis(el) && el.getAttribute('type') !== 'file') n++;
            }
            return n;
        }"""
    )
    return opened > 0


def save(page) -> bool:
    """点『保存』。"""
    for sel in ("button:has-text('保存')", "text=保存"):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="更新头条号用户名与简介")
    parser.add_argument("--name", default="", help="新的用户名")
    parser.add_argument("--bio", default="", help="新的用户简介")
    parser.add_argument("--account", default="robot_notes", help="账号 key（决定用哪个浏览器 profile）")
    parser.add_argument("--dry-run", action="store_true", help="只读当前值，不做任何修改")
    args = parser.parse_args()

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)

    from toutiao_content_factory.config import load_settings
    from playwright.sync_api import sync_playwright

    settings = load_settings(ROOT, account_key=args.account)
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = ROOT / profile_dir
    print(f"使用 profile: {profile_dir}")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=True,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        page = ctx.new_page()
        page.goto(INFO_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(6000)

        if "/auth/page/login" in page.url:
            print("ERROR: 未登录，无法修改。请先本地扫码登录。")
            ctx.close()
            return 2

        # 打开两个弹窗分别读取当前值
        current: dict = {}
        for which in ("name", "bio"):
            if not click_edit(page, which):
                print(f"WARNING: 未能打开 {which} 的编辑弹窗（坐标可能已变）")
                continue
            current.update({k: v for k, v in read_current(page).items() if v})
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

        print(f"\n当前用户名: {current.get('name', '<未读到>')}")
        print(f"当前简介  : {current.get('bio', '<未读到>')}")
        BACKUP.write_text(
            json.dumps({"read_at": "now", "name": current.get("name"), "bio": current.get("bio")},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"原值已备份到 {BACKUP}")

        if args.dry_run:
            print("\n--dry-run：未做任何修改。")
            ctx.close()
            return 0

        results: dict = {}

        # ---- 改用户名 ----
        if args.name:
            print(f"\n>>> 修改用户名 -> {args.name}")
            if click_edit(page, "name"):
                page.evaluate(
                    """(val) => {
                        const vis = el => { const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0; };
                        for (const el of document.querySelectorAll('input')) {
                            if (!vis(el) || el.getAttribute('type') === 'file') continue;
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            setter.call(el, val);
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return;
                        }
                    }""",
                    args.name,
                )
                page.wait_for_timeout(1200)
                results["name_saved"] = save(page)
                print(f"  保存点击: {results['name_saved']}")
            else:
                print("  ERROR: 打不开用户名弹窗")
                results["name_saved"] = False
            page.wait_for_timeout(1500)

        # ---- 改简介 ----
        if args.bio:
            print(f"\n>>> 修改简介 -> {args.bio}")
            if click_edit(page, "bio"):
                page.evaluate(
                    """(val) => {
                        const vis = el => { const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0; };
                        for (const el of document.querySelectorAll('textarea')) {
                            if (!vis(el)) continue;
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLTextAreaElement.prototype, 'value').set;
                            setter.call(el, val);
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return;
                        }
                    }""",
                    args.bio,
                )
                page.wait_for_timeout(1200)
                results["bio_saved"] = save(page)
                print(f"  保存点击: {results['bio_saved']}")
            else:
                print("  ERROR: 打不开简介弹窗")
                results["bio_saved"] = False
            page.wait_for_timeout(1500)

        # ---- 复核 ----
        page.goto(INFO_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(6000)
        text = page.inner_text("body")
        print("\n=== 复核 ===")
        print(f"  页面含新用户名 {args.name!r}: {args.name in text if args.name else 'n/a'}")
        print(f"  页面含新简介片段: {args.bio[:20] in text if args.bio else 'n/a'}")

        results["verified_name"] = (args.name in text) if args.name else None
        results["verified_bio"] = (args.bio[:20] in text) if args.bio else None
        print(f"\n结果: {json.dumps(results, ensure_ascii=False)}")
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
