"""Read-only backend/public-article verification. Never creates or submits drafts."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from toutiao_content_factory.services.wechat_metrics import match_publication

BACKEND_RECORDS_JS = r"""() => {
  const account = document.querySelector('.account_box-panel-head__nickname, .acount_box-nickname')?.innerText.trim() || '';
  const records = [];
  for (const link of document.querySelectorAll('a[href^="https://mp.weixin.qq.com/s/"]')) {
    const row = link.closest('.publish_hover_content, .weui-desktop-mass__content');
    if (!row || !/^已发表$/m.test(row.innerText)) continue;
    const date = row.innerText.match(/(?:\d{4}[-年])?\d{1,2}月\d{1,2}日/);
    records.push({account, title:link.innerText.trim(), url:link.href,
      status:'published', published_at:date ? date[0] : ''});
  }
  return {account, records};
}"""
PUBLIC_ARTICLE_JS = """() => ({
  account:document.querySelector('#js_name')?.innerText.trim() || '',
  title:document.querySelector('#activity-name')?.innerText.trim() || '',
  body:document.querySelector('#js_content')?.innerText || '',
  timestamp: String(window.ct || ''),
  displayed_time: document.querySelector('#publish_time')?.innerText.trim() || ''
})"""


def inspect_backend(settings, *, timeout_seconds=60):
    """Account-bound, read-only evidence for legacy submissions lacking an expectation."""
    from playwright.sync_api import sync_playwright
    deadline = time.monotonic() + timeout_seconds
    def remaining():
        milliseconds = int((deadline - time.monotonic()) * 1000)
        if milliseconds <= 0:
            raise TimeoutError("WeChat backend inspection timed out")
        return min(25000, milliseconds)
    storage = settings.account_runtime_dir / "automation" / "browser_state" / "WeChat_storage_state.json"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=remaining())
        try:
            context = browser.new_context(storage_state=str(storage), locale="zh-CN", timezone_id="Asia/Shanghai")
            page = context.new_page()
            page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded", timeout=remaining())
            page.locator('.account_box-panel-head__nickname, .acount_box-nickname').first.wait_for(timeout=remaining())
            page.wait_for_timeout(min(2000, remaining()))
            snapshot = page.evaluate(BACKEND_RECORDS_JS)
            if not snapshot.get("account") or (settings.wechat.account_name and settings.wechat.account_name != snapshot["account"]):
                raise RuntimeError("WeChat inspection account mismatch")
            pending_count = page.evaluate("""() => [...document.querySelectorAll('.weui-desktop-mass__content,.publish_hover_content')]
                .filter(x=>/审核中|待审核|发表中|提交中/.test(x.innerText)).length""")
            return {"account": snapshot["account"], "records": snapshot["records"],
                    "pending_count": pending_count, "checked_at": datetime.now(timezone.utc).isoformat(),
                    "read_only": True}
        finally:
            browser.close()


def verify_pending(settings, expected_records: list[dict], *, timeout_seconds: int = 75) -> dict[str, dict]:
    if not expected_records:
        return {}
    from playwright.sync_api import sync_playwright
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        milliseconds = int((deadline - time.monotonic()) * 1000)
        if milliseconds <= 0:
            raise TimeoutError("WeChat read-only verification timed out")
        return min(30000, milliseconds)

    storage = settings.account_runtime_dir / "automation" / "browser_state" / "WeChat_storage_state.json"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=remaining())
        try:
            context = browser.new_context(storage_state=str(storage), locale="zh-CN", timezone_id="Asia/Shanghai")
            page = context.new_page()
            page.set_default_timeout(remaining())
            page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded", timeout=remaining())
            page.locator('.account_box-panel-head__nickname, .acount_box-nickname').first.wait_for(timeout=remaining())
            page.wait_for_timeout(min(2000, remaining()))
            snapshot = page.evaluate(BACKEND_RECORDS_JS)
            if not snapshot["account"]:
                raise RuntimeError("WeChat account identity unavailable")
            if any(row.get("account") and row["account"] != snapshot["account"] for row in expected_records):
                raise RuntimeError("WeChat verification account mismatch")
            confirmed = {}
            for expected in expected_records:
                candidates = [row for row in snapshot["records"] if row["title"] == expected.get("title")]
                matches = []
                for record in candidates:
                    public = context.new_page()
                    try:
                        public.goto(record["url"], wait_until="domcontentloaded", timeout=remaining())
                        public.locator('#js_content').wait_for(timeout=remaining())
                        article = public.evaluate(PUBLIC_ARTICLE_JS)
                        article["url"] = public.url
                        timestamp = article.pop("timestamp", "")
                        if timestamp.isdigit():
                            article["published_at"] = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat()
                        else:
                            article["published_at"] = ""
                        proof = match_publication(expected, record, article)
                        if proof:
                            matches.append(proof)
                    finally:
                        public.close()
                # Multiple matches are ambiguous; do not pick a convenient row.
                if len(matches) == 1:
                    confirmed[expected["run_key"]] = matches[0]
            return confirmed
        finally:
            browser.close()
