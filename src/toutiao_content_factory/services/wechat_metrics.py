"""WeChat-only operational metrics and strict publication identity checks."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

METRICS_PATH = "state/wechat_metrics.json"
PENDING = {"pending_review", "submit_uncertain"}
BEIJING = timezone(timedelta(hours=8))


def normalized_body(value: str) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", value)).casefold()


def body_hash(value: str) -> str:
    return hashlib.sha256(normalized_body(value).encode("utf-8")).hexdigest()


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_body_hash(article_html: str) -> str:
    parser = _VisibleText()
    parser.feed(article_html)
    return body_hash("".join(parser.parts))


def assert_reviewed_body_preserved(article: str, article_html: str) -> None:
    parser = _VisibleText()
    parser.feed(article_html)
    rendered = normalized_body("".join(parser.parts))
    cursor = 0
    for paragraph in re.split(r"\n\s*\n", article):
        expected = normalized_body(paragraph)
        if not expected:
            continue
        found = rendered.find(expected, cursor)
        if found < 0:
            raise ValueError("WeChat layout changed the reviewed article body")
        cursor = found + len(expected)


def parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=BEIJING) if parsed.tzinfo is None else parsed
    except (ValueError, AttributeError):
        return None


def match_publication(expected: dict, backend: dict, article: dict) -> dict | None:
    """Backend status AND public identity/time/body are all required.

    Short backend dates are checked against the public article's full date,
    never assigned the current year. An old same-title article cannot match.
    """
    from toutiao_content_factory.services.wechat_evidence import public_url
    account, title = expected.get("account", ""), expected.get("title", "")
    submitted = parse_time(expected.get("submitted_at", ""))
    published = parse_time(article.get("published_at", ""))
    backend_date = str(backend.get("published_at", "")).strip()
    url = public_url(article.get("url", ""))
    if (backend.get("status") != "published" or not account or not title or not submitted or not published
            or backend.get("account") != account or article.get("account") != account
            or normalized_body(backend.get("title", "")) != normalized_body(title)
            or normalized_body(article.get("title", "")) != normalized_body(title)
            or not url.startswith("https://mp.weixin.qq.com/s/") or public_url(backend.get("url", "")) != url
            or not expected.get("body_hash") or len(normalized_body(article.get("body", ""))) < 300
            or body_hash(article.get("body", "")) != expected["body_hash"]
            or published < submitted - timedelta(minutes=5) or published > datetime.now(BEIJING) + timedelta(minutes=5)):
        return None
    exact_backend_time = parse_time(backend_date)
    if exact_backend_time:
        if abs((exact_backend_time - published).total_seconds()) > 60:
            return None
    else:
        match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", backend_date)
        if not match or tuple(map(int, match.groups())) != (published.month, published.day):
            return None
    return {"account": account, "title": title, "url": url,
            "published_at": published.isoformat(), "body_hash": expected["body_hash"],
            "backend_status": "published", "confirmed": True}


def validate_metrics(payload: dict) -> dict:
    if not payload:
        return {"schema": 1, "runs": []}
    if payload.get("schema") != 1 or not isinstance(payload.get("runs"), list) or any(not isinstance(r, dict) for r in payload["runs"]):
        raise ValueError("Invalid WeChat metrics state")
    return payload


def merge_run(payload: dict, record: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(BEIJING)
    state = validate_metrics(payload)
    records = {row["run_key"]: dict(row) for row in state["runs"] if row.get("run_key")}
    previous = records.get(record["run_key"], {})
    if previous.get("outcome") == "published_confirmed" and record.get("outcome") != "published_confirmed":
        record = {**record, **previous}  # Never downgrade confirmed evidence.
    records[record["run_key"]] = {**previous, **record}
    cutoff = now - timedelta(days=90)
    kept = [row for row in records.values() if (parse_time(row.get("started_at", "")) or datetime.min.replace(tzinfo=BEIJING)) >= cutoff]
    return {**state, "schema": 1, "updated_at": now.isoformat(), "runs": sorted(kept, key=lambda row: (row["started_at"], row["run_key"]))}


def summary(payload: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(BEIJING)
    result = {}
    for days in (7, 30):
        cutoff = now - timedelta(days=days)
        runs = [row for row in validate_metrics(payload)["runs"]
                if row.get("mode") == "publish" and not row.get("excluded", False)
                and (parse_time(row.get("started_at", "")) or datetime.min.replace(tzinfo=BEIJING)) >= cutoff]
        articles = {row["publication"]["url"] for row in runs
                    if row.get("outcome") == "published_confirmed" and row.get("publication", {}).get("confirmed") is True}
        audited = sum(int(row.get("audited_candidate_count", 0)) for row in runs)
        approved = sum(int(row.get("review_pass_count", 0)) for row in runs)
        reasons = Counter(reason for row in runs for reason in row.get("rejection_reasons", []))
        result[str(days)] = {"actual_publish_runs": len(runs), "confirmed_articles": len(articles),
                            "publication_rate": round(len(articles) / len(runs), 4) if runs else None,
                            "eligible_candidate_count": sum(int(row.get("eligible_candidate_count", 0)) for row in runs),
                            "audited_candidate_count": audited, "review_pass_count": approved,
                            "review_pass_rate": round(approved / audited, 4) if audited else None,
                            "common_rejection_reasons": dict(reasons.most_common(8))}
    return result


def outcome(report: dict) -> str:
    if report.get("publication", {}).get("confirmed") is True:
        return "published_confirmed"
    if report.get("status") == "error":
        return "runtime_error"
    status = report.get("wechat_status", "")
    if status in {"draft_saved", "draft_created"}:
        return "draft"
    if status in {"submitted", "submitted_for_review", "success", "published"}:
        return "pending_review"
    if status == "publish_uncertain" or report.get("status") == "publish_uncertain":
        return "submit_uncertain"
    if (report.get("status") == "daily_retry_stopped"
            and report.get("retry_status") == "daily_budget_exhausted"
            and report.get("publication_attempted") is False
            and not status and report.get("native_exit_code") is None
            and not report.get("metrics_error") and not report.get("retry_state_error")):
        return "not_started"
    if report.get("status") == "review_required" and report.get("publication_attempted") is False:
        return "not_published"
    if report.get("run_outcome") == "no_topics" and report.get("publication_attempted") is False:
        return "not_published"
    return "runtime_error"
