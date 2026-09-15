# -*- coding: utf-8 -*-
"""只读诊断：头条发布页的收益相关勾选框（不点击任何东西）。

用途：
1. 验证 robot-notes 的浏览器登录态是否有效；
2. 采集发布页上所有 checkbox 的真实文案与勾选状态，
   用于校准 publish_service._apply_safe_publish_options 的 selector。

输出：diagnostics/publish_checkboxes_<ts>.json + 截图。
"""
import asyncio
import importlib.util
import json
import os
from datetime import datetime

from playwright.async_api import async_playwright

ROOT = r"F:\Automated-workflow-main"
PROFILE = os.path.join(ROOT, ".runtime", "accounts", "robot-notes", "toutiao_profile")
PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
OUT = os.path.join(ROOT, "diagnostics")
os.makedirs(OUT, exist_ok=True)
TS = datetime.now().strftime("%Y%m%d_%H%M%S")

_spec = importlib.util.spec_from_file_location(
    "browser_utils", os.path.join(ROOT, "src", "toutiao_content_factory", "utils", "browser_utils.py")
)
browser_utils = importlib.util.module_from_spec(_spec)
import sys as _sys
_sys.modules["browser_utils"] = browser_utils
_spec.loader.exec_module(browser_utils)

COLLECT_JS = """
() => {
  const out = [];
  const seen = new Set();
  const push = (kind, text, checked, extra) => {
    const key = kind + '|' + text;
    if (!text || seen.has(key)) return;
    seen.add(key);
    out.push({ kind, text: text.slice(0, 60), checked, extra: extra || '' });
  };
  document.querySelectorAll('label.byte-checkbox, .byte-checkbox').forEach(el => {
    const input = el.querySelector('input[type=checkbox]');
    push('byte-checkbox', (el.innerText || '').replace(/\\s+/g, ' ').trim(),
         input ? input.checked : el.className.includes('checked'), el.className.slice(0, 80));
  });
  document.querySelectorAll('input[type=checkbox]').forEach(cb => {
    const label = cb.closest('label') || cb.parentElement;
    push('input-checkbox', label ? (label.innerText || '').replace(/\\s+/g, ' ').trim() : '',
         cb.checked, cb.name || cb.id || '');
  });
  document.querySelectorAll('.byte-radio, [class*=byte-radio]').forEach(el => {
    const input = el.querySelector('input[type=radio]');
    const cls = el.className || '';
    const checked = input ? input.checked : (cls.includes('checked') || !!(el.querySelector('.byte-radio-checked')));
    push('byte-radio', (el.innerText || '').replace(/\\s+/g, ' ').trim(), checked, cls.slice(0, 80));
  });
  document.querySelectorAll('label, .byte-radio__label, [class*=checkbox]').forEach(el => {
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t.includes('广告') || t.includes('首发') || t.includes('原创')) {
      push('keyword-hit', t, null, el.className.slice(0, 80));
    }
  });
  return out;
}
"""


async def main() -> int:
    result = {"profile": PROFILE, "url": PUBLISH_URL, "ts": TS, "login_ok": None, "checkboxes": [], "error": ""}
    options, _fingerprint = browser_utils.build_persistent_context_options(
        account_key="robot_notes",
        profile_dir=PROFILE,
        headless=True,
        channel="msedge",
    )
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(PROFILE, **options)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(6000)

            final_url = page.url
            result["final_url"] = final_url
            result["login_ok"] = not any(
                token in final_url.lower() for token in ("login", "sso", "passport", "auth")
            )

            # 头条后台是 iframe 渲染，主文档 + 全部 iframe 都扫一遍
            frames = [page.main_frame] + page.frames
            for i, frame in enumerate(frames):
                try:
                    rows = await frame.evaluate(COLLECT_JS)
                except Exception:
                    continue
                for row in rows or []:
                    row["frame"] = i
                    result["checkboxes"].append(row)

            shot = os.path.join(OUT, f"publish_checkboxes_{TS}.png")
            try:
                await page.screenshot(path=shot, full_page=False)
                result["screenshot"] = shot
            except Exception as exc:
                result["screenshot_error"] = str(exc)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            await context.close()

    out_path = os.path.join(OUT, f"publish_checkboxes_{TS}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(f"login_ok = {result['login_ok']}")
    print(f"final_url = {result.get('final_url', '-')}")
    print(f"checkbox/keyword 命中 {len(result['checkboxes'])} 项：")
    for row in result["checkboxes"]:
        mark = "[x]" if row.get("checked") else "[ ]" if row.get("checked") is False else "[?]"
        print(f"  {mark} f{row['frame']} <{row['kind']}> {row['text']}  {row.get('extra', '')[:40]}")
    if result["error"]:
        print(f"error = {result['error']}")
    print(f"saved -> {out_path}")
    return 0 if result["login_ok"] and not result["error"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
