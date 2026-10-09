"""Read-only article observations, separate from publication guards and run counts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import re
from urllib.parse import urlsplit

from toutiao_content_factory.services.generation_cost_service import BEIJING
from toutiao_content_factory.services.wechat_evidence import public_url

STATE_PATH = "state/wechat_growth.json"
METRICS = ("readers", "likes", "shares", "recommends")


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(BEIJING) if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _count(value):
    # A missing selector or an action such as '开启留言' is not a zero.
    text = str(value).strip() if value is not None and not isinstance(value, bool) else ""
    return int(text.replace(",", "")) if re.fullmatch(r"\d+|\d{1,3}(?:,\d{3})+", text) else None


def article_url(value):
    cleaned = public_url(str(value or ""))
    parsed = urlsplit(cleaned)
    return cleaned if parsed.scheme == "https" and parsed.netloc == "mp.weixin.qq.com" and (
        parsed.path.startswith("/s/") or parsed.path == "/s"
    ) else ""


def merge_observation(state, observation, *, account):
    if observation.get("account") != account:
        raise ValueError("WeChat account mismatch; growth state was not changed")
    observed = _stamp(observation.get("observed_at"))
    followers = _count(observation.get("followers"))
    if observed is None or followers is None:
        raise ValueError("Incomplete WeChat account observation")
    if state and (state.get("schema") != 1 or state.get("account") != account
                  or not isinstance(state.get("articles"), dict) or not isinstance(state.get("account_snapshots"), list)):
        raise ValueError("Invalid or differently scoped growth state; refusing to reset it")
    result = deepcopy(state) if state else {"schema": 1, "account": account, "articles": {}, "account_snapshots": []}
    cutoff = max(observed, _stamp(result.get("updated_at")) or observed) - timedelta(days=90)
    if observed < cutoff:
        return result
    account_rows = {row["observed_at"]: row for row in result["account_snapshots"]
                    if _stamp(row.get("observed_at")) and _stamp(row["observed_at"]) >= cutoff}
    account_rows.setdefault(observed.isoformat(), {"observed_at": observed.isoformat(), "followers": followers})
    result["account_snapshots"] = sorted(account_rows.values(), key=lambda row: _stamp(row["observed_at"]))
    for raw in observation.get("articles", []):
        url, title = article_url(raw.get("url")), str(raw.get("title", "")).strip()
        if not url or not title:
            continue
        existing = result["articles"].get(url)
        if existing and existing["title"] != title:
            # Editing a published title must not silently combine different identities.
            continue
        item = existing or {"url": url, "title": title, "published_at": None, "snapshots": []}
        published = _stamp(raw.get("published_at"))
        if published and published <= observed and not item["published_at"]:
            item["published_at"] = published.isoformat()
        rows = {row["observed_at"]: row for row in item["snapshots"]
                if _stamp(row.get("observed_at")) and _stamp(row["observed_at"]) >= cutoff}
        rows.setdefault(observed.isoformat(), {"observed_at": observed.isoformat(),
            **{name: _count(raw.get(name)) for name in METRICS}})
        item["snapshots"] = sorted(rows.values(), key=lambda row: _stamp(row["observed_at"]))
        result["articles"][url] = item
    for item in result["articles"].values():
        item["snapshots"] = [row for row in item["snapshots"]
                             if _stamp(row.get("observed_at")) and _stamp(row["observed_at"]) >= cutoff]
    latest = result["account_snapshots"][-1]
    result["updated_at"] = latest["observed_at"]
    result["coverage"] = "Latest published articles visible on the account homepage; not complete history"
    return result


def build_growth_report(state):
    account_rows = state["account_snapshots"]
    latest = account_rows[-1]
    articles = []
    for item in state["articles"].values():
        if not item["snapshots"]:
            continue
        published = _stamp(item["published_at"])
        windows = {}
        for hours, tolerance in ((24, 12), (72, 24)):
            eligible = [row for row in item["snapshots"] if published is not None
                        and hours <= (_stamp(row["observed_at"]) - published).total_seconds() / 3600 <= hours + tolerance
                        and row.get("readers") is not None]
            windows[str(hours)] = eligible[0] if eligible else None
        articles.append({"url": item["url"], "title": item["title"], "published_at": item["published_at"],
                         "latest": item["snapshots"][-1], "windows": windows,
                         "read_rate": None, "attributed_followers": None})
    articles.sort(key=lambda row: row["published_at"] or "", reverse=True)
    return {"schema": 1, "account": state["account"], "observed_at": latest["observed_at"],
            "followers": latest["followers"], "followers_to_100": max(0, 100 - latest["followers"]),
            "account_net_followers_since_tracking": latest["followers"] - account_rows[0]["followers"],
            "ad_activation": "unverified", "articles": articles, "coverage": state["coverage"],
            "note": "Readers are unique people, not page views. No impression denominator or article-attributed follows is supplied. Late observations remain visible but cannot fill 24/72-hour comparisons. 100 followers is a tracking milestone, not proof of ad activation."}


def render_growth_report(report):
    lines = [f"公众号 {report['account']}：{report['followers']} 位关注者，距离 100 位还差 {report['followers_to_100']} 位。",
             f"采集时间：{report['observed_at']}；流量主实际开通状态尚未核验。",
             "阅读人数为去重人数；单篇涨粉、阅读率和实际收益未知，不按账号总涨粉分摊。", "",
             "| 文章 | 最新阅读人数 | 分享人数 | 24小时阅读人数 | 72小时阅读人数 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    def display(value):
        return str(value) if value is not None else "未知"
    for row in report["articles"]:
        title = row['title'].replace('|', '／').replace('\n', ' ').replace('[', '［').replace(']', '］')
        lines.append(f"| [{title}]({row['url']}) | {display(row['latest']['readers'])} | {display(row['latest']['shares'])} | "
                     f"{display((row['windows']['24'] or {}).get('readers'))} | {display((row['windows']['72'] or {}).get('readers'))} |")
    lines.extend(["", report["coverage"], "迟到采集保留展示，不进入同窗口比较；暂无有效窗口时不推荐赢家。"])
    return "\n".join(lines) + "\n"


HOME_OBSERVATION_JS = r"""() => {
  const data = window.wx?.cgiData || {};
  let published = data.publish_page;
  if (typeof published === 'string') published = JSON.parse(published);
  const records = [];
  for (const row of published?.publish_list || []) {
    if (row.view?.status && row.view.status !== '已发表') continue;
    for (const article of Object.values(row.appmsg_info || {})) {
      if (typeof article !== 'object' || !article?.title) continue;
      records.push({title: article.title, time: row.sent_info?.time});
    }
  }
  const articles = [...document.querySelectorAll('.weui-desktop-mass-appmsg__bd_container')].map(card => {
    const a = card.querySelector('a.weui-desktop-mass-appmsg__title');
    const read = card.querySelector('.appmsg-view');
    if (!a || !read || !card.closest('.weui-desktop-mass-appmsg')?.parentElement.innerText.includes('已发表')) return null;
    const title = a.innerText.trim();
    const matches = records.filter(row => row.title === title);
    const time = matches.length === 1 ? Number(matches[0].time) : NaN;
    return {title, url: a.href,
      published_at: Number.isFinite(time) && time > 1000000000 ? new Date(time * 1000).toISOString() : null,
      readers: read.innerText, likes: card.querySelector('.appmsg-like')?.innerText ?? null,
      shares: card.querySelector('.appmsg-share')?.innerText ?? null,
      recommends: card.querySelector('.appmsg-haokan')?.innerText ?? null};
  }).filter(Boolean);
  return {account: document.querySelector('.acount_box-nickname')?.innerText.trim(),
    followers: data.total_friend_cnt, articles};
}"""


def collect_home_observation(storage_state, *, channel="msedge"):
    from playwright.sync_api import sync_playwright
    if not storage_state.is_file():
        raise RuntimeError("Saved WeChat login is missing; no login or publication was attempted")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=True)
        try:
            context = browser.new_context(storage_state=str(storage_state), locale="zh-CN", timezone_id="Asia/Shanghai")
            page = context.new_page()
            page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector(".acount_box-nickname", timeout=20000)
            page.wait_for_function("() => !!window.wx?.cgiData?.publish_page", timeout=20000)
            page.wait_for_selector(".appmsg-view", timeout=20000)
            observation = page.evaluate(HOME_OBSERVATION_JS)
            if not observation["articles"]:
                raise RuntimeError("No published article metrics visible; prior growth state retained")
            observation["observed_at"] = datetime.now(BEIJING).isoformat()
            return observation
        finally:
            browser.close()
