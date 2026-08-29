#!/usr/bin/env python3
"""用持久化登录态抓取头条创作者后台当天(今天)新发布的文章数据。

仅使用 .runtime/accounts/robot-notes/toutiao_profile 中已持久化的登录态，
不进行手动登录、不伪造 cookie。若该 profile 会话已过期(未登录)，
如实报告失败并提示需要重新登录。

输出:
  output/performance_today.json   原始抓取数据
"""
from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402
from toutiao_content_factory.config import load_settings  # noqa: E402
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile  # noqa: E402

PROFILE_DIR = PROJECT_ROOT / ".runtime" / "accounts" / "robot-notes" / "toutiao_profile"
MANAGE_URL = "https://mp.toutiao.com/profile_v4/graphic/manage"

ROW_SELECTORS = (
    "tbody tr",
    ".article-item",
    ".content-list-item",
    ".publish-item",
    ".list-item",
    "table tbody tr",
    "[data-testid*='article']",
    "tbody",
)


def human_pause(page, lo=1800, hi=3200):
    page.wait_for_timeout(random.randint(lo, hi))


def parse_num(text: str) -> int:
    t = re.sub(r"\s+", "", text or "")
    m = re.search(r"([\d.]+)([万千])?", t)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "万":
        val *= 10000
    elif unit == "千":
        val *= 1000
    return int(val)


def collect_rows(page) -> list[dict]:
    rows = page.evaluate(
        """
        ({ rowSelectors }) => {
          const items = [];
          const seen = new Set();
          for (const selector of rowSelectors) {
            for (const node of document.querySelectorAll(selector)) {
              const text = (node.innerText || '').trim();
              if (!text || text.length < 6) continue;
              const key = text.slice(0, 200);
              if (seen.has(key)) continue;
              seen.add(key);
              const anchor = node.querySelector('a[href]');
              const titleNode = node.querySelector('[title], .title, h1, h2, h3, h4') || anchor;
              const title =
                (titleNode && (titleNode.getAttribute('title') || titleNode.innerText || '')) ||
                (anchor && anchor.innerText) || '';
              items.push({ title: (title||'').trim(), text, href: (anchor?anchor.href:'').trim() });
            }
          }
          return items.slice(0, 500);
        }
        """,
        {"rowSelectors": list(ROW_SELECTORS)},
    )
    return [r for r in rows if isinstance(r, dict)]


def is_login_page(page) -> bool:
    url = page.url.lower()
    if "login" in url:
        return True
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        pass
    if not body_text:
        return False
    return ("扫码" in body_text and "登录" in body_text) or ("二维码" in body_text and "登录" in body_text)


def main() -> int:
    settings = load_settings(PROJECT_ROOT)
    print(f"profile_dir: {PROFILE_DIR}")
    print("使用持久化登录态打开头条创作后台(作品管理)...")

    collected: list[dict] = []
    pages_collected = 0
    login_state = "unknown"
    failure_reason = ""

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=True,
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(30000)
            page.goto(MANAGE_URL, wait_until="domcontentloaded", timeout=45000)
            human_pause(page, 2500, 4000)

            if is_login_page(page):
                login_state = "session_expired"
                raise RuntimeError("头条登录会话已过期，页面停留在登录页，需要重新扫码登录(勿伪造cookie)。")

            # 逐页抓取
            max_pages = 6
            current_page = 1
            while current_page <= max_pages:
                human_pause(page, 1500, 2500)
                rows = collect_rows(page)
                if not rows:
                    break
                collected.extend({"page": current_page, **r} for r in rows)
                pages_collected = current_page

                # 找下一页按钮
                nxt = page.evaluate(
                    """
                    () => {
                      const cands = document.querySelectorAll("a, button, li, .ant-pagination-item, [class*='page'], [class*='next']");
                      for (const el of cands) {
                        const t = (el.innerText||'').trim();
                        if (/^下一页$/.test(t) || /next|›|»/.test(el.className||'') && !/disabled/.test(el.className||'')) {
                          return { tag: el.tagName, cls: el.className||'', text: t };
                        }
                      }
                      return null;
                    }
                    """
                )
                if not nxt:
                    break
                clicked = page.evaluate(
                    """
                    () => {
                      const cands = document.querySelectorAll("a, button, li, [class*='page'], [class*='next']");
                      for (const el of cands) {
                        const t = (el.innerText||'').trim();
                        if (/^下一页$/.test(t)) { el.click(); return true; }
                        if (/[›>]/.test(el.innerText||'') && !/disabled/.test(el.className||'')) { el.click(); return true; }
                      }
                      return false;
                    }
                    """
                )
                if not clicked:
                    break
                current_page += 1
                page.wait_for_timeout(2500)
        except Exception as exc:
            if login_state == "session_expired":
                failure_reason = str(exc)
            else:
                failure_reason = f"{type(exc).__name__}: {exc}"
                # capture screenshot on error
                try:
                    shot = PROJECT_ROOT / "output" / "scrape_today_failure.png"
                    page.screenshot(path=str(shot), full_page=True)
                    failure_reason += f" | screenshot={shot}"
                except Exception:
                    pass
        finally:
            ctx.close()

    today = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "collected_at": today,
        "collected_at_ts": datetime.now().isoformat(timespec="seconds"),
        "source": "toutiao_creator_center_content_management",
        "account_profile": "robot-notes",
        "login_state": login_state,
        "failure_reason": failure_reason,
        "pages_collected": pages_collected,
        "items": collected,
    }
    out_path = PROJECT_ROOT / "output" / "performance_today.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"login_state={login_state} pages={pages_collected} items={len(collected)}")
    print(f"saved: {out_path}")
    if login_state == "session_expired":
        print("FAIL: 头条登录会话已过期，需要重新扫码登录，本次不抓取、不伪造cookie。")
        return 2
    return 0


def _dummy_logger():
    import logging
    return logging.getLogger("scrape_today")


if __name__ == "__main__":
    raise SystemExit(main())