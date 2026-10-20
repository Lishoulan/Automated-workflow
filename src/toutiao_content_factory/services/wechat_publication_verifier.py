"""Read-only backend/public-article verification. Never creates or submits drafts."""
from __future__ import annotations

import time
import os
import logging
import re
from datetime import datetime, timezone

from toutiao_content_factory.services.wechat_metrics import match_publication

BACKEND_RECORDS_JS = r"""() => {
  const account = document.querySelector('.account_box-panel-head__nickname, .acount_box-nickname')?.innerText.trim() || '';
  const records = [];
  // The homepage labels same-day publications with 今天 HH:mm. Anchor
  // that explicit relative date to this observation in Beijing time.
  const beijing = new Date(Date.now() + 8 * 60 * 60 * 1000);
  for (const link of document.querySelectorAll('a[href^="https://mp.weixin.qq.com/s/"],a[href^="https://mp.weixin.qq.com/s?"]')) {
    const row = link.closest('.publish_hover_content, .weui-desktop-mass__content');
    if (!row || !/^已发表$/m.test(row.innerText)) continue;
    const date = row.innerText.match(/(?:\d{4}[-年])?\d{1,2}月\d{1,2}日/);
    const today = /^今天\s+\d{1,2}:\d{2}$/m.test(row.innerText);
    records.push({account, title:link.innerText.trim(), url:link.href,
      status:'published', published_at:date ? date[0] : today
        ? `${beijing.getUTCMonth() + 1}月${beijing.getUTCDate()}日` : ''});
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


def _open_backend_context(playwright, settings, remaining):
    if os.environ.get("WECHAT_EXECUTION_HOST") == "fixed":
        from toutiao_content_factory.services.wechat_service import WechatPublishService
        service = WechatPublishService(settings, logging.getLogger(__name__))
        context = service._launch_persistent_browser(
            playwright, profile_dir=service._resolve_browser_profile_dir(), headless=True)
        return context, context
    storage = settings.account_runtime_dir / "automation" / "browser_state" / "WeChat_storage_state.json"
    browser = playwright.chromium.launch(headless=True, timeout=remaining())
    try:
        context = browser.new_context(storage_state=str(storage), locale="zh-CN", timezone_id="Asia/Shanghai")
        return browser, context
    except BaseException:
        browser.close()
        raise


def inspect_backend(settings, *, timeout_seconds=60):
    """Account-bound, read-only evidence for legacy submissions lacking an expectation."""
    from playwright.sync_api import sync_playwright
    deadline = time.monotonic() + timeout_seconds
    def remaining():
        milliseconds = int((deadline - time.monotonic()) * 1000)
        if milliseconds <= 0:
            raise TimeoutError("WeChat backend inspection timed out")
        return min(25000, milliseconds)
    with sync_playwright() as playwright:
        browser, context = _open_backend_context(playwright, settings, remaining)
        try:
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

    with sync_playwright() as playwright:
        browser, context = _open_backend_context(playwright, settings, remaining)
        try:
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
            service = None
            for expected in expected_records:
                candidates = [row for row in snapshot["records"] if row["title"] == expected.get("title")]
                job_key = str(expected.get('official_job_key', ''))
                if expected.get('publish_id') and re.fullmatch(r'[0-9a-f]{64}', job_key):
                    from toutiao_content_factory.services.wechat_service import WechatPublishService
                    from toutiao_content_factory.services.wechat_official_publish import query_job
                    try:
                        service = service or WechatPublishService(settings, logging.getLogger(__name__))
                        service._api_deadline = deadline
                        token = service._get_access_token()
                        job = query_job(settings.automation_dir / 'wechat_official_jobs' / (job_key + '.json'),
                            account=settings.account_key,
                            expected={k: expected[k] for k in ('account', 'title', 'body_hash')},
                            post=lambda endpoint, payload: service._wechat_post_json(endpoint, payload, access_token=token))
                        if job.get('publish_id') == expected['publish_id'] and job.get('status') == 'platform_reported_published':
                            candidates.extend({'status': job['status'], 'publish_status': 0, 'publish_id': job['publish_id'],
                                'evidence_source': 'official_freepublish', 'account': expected['account'], 'title': expected['title'],
                                'url': url} for url in job.get('article_urls', []))
                    except (OSError, ValueError, RuntimeError):
                        logging.getLogger(__name__).warning('Official status unavailable; retaining pending job and checking existing backend evidence.')
                matches = []
                for record in candidates:
                    public = context.new_page()
                    try:
                        public.goto(record["url"], wait_until="domcontentloaded", timeout=remaining())
                        public.locator('#js_content').wait_for(timeout=remaining())
                        article = public.evaluate(PUBLIC_ARTICLE_JS)
                        article["url"] = public.url
                        if record.get('evidence_source') == 'official_freepublish':
                            record = {**record, 'api_article_url': record['url'], 'url': public.url}
                        timestamp = article.pop("timestamp", "")
                        if timestamp.isdigit():
                            article["published_at"] = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat()
                        else:
                            article["published_at"] = ""
                        proof = match_publication(expected, record, article)
                        if proof and not any(row['url'] == proof['url'] for row in matches):
                            matches.append(proof)
                    finally:
                        public.close()
                # Multiple matches are ambiguous; do not pick a convenient row.
                if len(matches) == 1:
                    confirmed[expected["run_key"]] = matches[0]
            return confirmed
        finally:
            browser.close()
