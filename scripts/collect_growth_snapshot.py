"""Read the creator center's published-work counters into a growth snapshot.

The cookie file is read locally and is never included in the snapshot or logs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))
LIST_URL = "https://mp.toutiao.com/mp/agw/creator_center/list/v2"


def _counter(stat: dict, label: str, fallback: str) -> int:
    for item in stat.get("counters") or []:
        if item.get("Name") == label:
            return int(item.get("Count") or 0)
    return int(stat.get(fallback) or 0)


def build_snapshot(contents: list[dict], observed_at: datetime, days: int) -> dict:
    cutoff = observed_at.astimezone(BEIJING) - timedelta(days=days)
    works: list[dict] = []
    for item in contents:
        attr = item.get("article_attr") or {}
        stat = item.get("article_stat") or {}
        try:
            created_at = datetime.fromtimestamp(int(attr.get("create_time")), BEIJING)
        except (TypeError, ValueError, OverflowError):
            continue
        if created_at < cutoff or int(attr.get("status") or 0) != 2:
            continue
        works.append({
            "gid": str(attr.get("gid") or ""),
            "created_at": created_at.isoformat(timespec="seconds"),
            "age_hours": round((observed_at - created_at).total_seconds() / 3600, 1),
            "type": str(attr.get("article_type") or "unknown"),
            "title": str(attr.get("title") or "").splitlines()[0].strip(),
            "impressions": _counter(stat, "展现", "impression_count"),
            "reads": _counter(stat, "阅读", "go_detail_count"),
            "comments": _counter(stat, "评论", "comment_count"),
            "has_cover": bool(attr.get("cover_image")),
        })
    summary: dict[str, dict] = {}
    for content_type in sorted({work["type"] for work in works}):
        group = [work for work in works if work["type"] == content_type]
        titles = Counter(work["title"] for work in group)
        summary[content_type] = {
            "works": len(group),
            "unique_titles": len(titles),
            "duplicate_titles": {title: count for title, count in titles.items() if count > 1},
            "impressions": sum(work["impressions"] for work in group),
            "reads": sum(work["reads"] for work in group),
            "zero_read_works": sum(work["reads"] == 0 for work in group),
            "median_impressions": median(work["impressions"] for work in group),
        }
    return {
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "window_days": days,
        "source": "toutiao_creator_center_published_works",
        "summary": summary,
        "works": works,
    }


def fetch_published(cookie_path: Path, *, max_pages: int, cutoff: datetime) -> list[dict]:
    payload = json.loads(cookie_path.read_text(encoding="utf-8"))
    cookies = payload["platforms"]["toutiao"]["cookies"]
    cookie_header = "; ".join(
        f"{cookie['name']}={cookie.get('value', '')}"
        for cookie in cookies if cookie.get("name")
    )
    if not cookie_header:
        raise ValueError("Toutiao cookie file has no usable cookies")
    contents: list[dict] = []
    cursor = ""
    for page_index in range(max_pages):
        params = {
            "type": 0, "status": 2, "page_num": 1, "page_size": 20,
            "need_stat": "true", "need_forward": "false",
        }
        if cursor:
            params["cursor"] = cursor
        request = Request(
            f"{LIST_URL}?{urlencode(params)}",
            headers={
                "Cookie": cookie_header,
                "Referer": "https://mp.toutiao.com/profile_v4/manage/content/all",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urlopen(request, timeout=30) as response:
            page = json.load(response)
        if page.get("code") not in (0, None) or not isinstance(page.get("contents"), list):
            raise RuntimeError("Creator center did not return a published-work list")
        batch = page["contents"]
        contents.extend(batch)
        if not page.get("has_more") or not batch:
            break
        oldest = (batch[-1].get("article_attr") or {}).get("create_time")
        if oldest and datetime.fromtimestamp(int(oldest), BEIJING) < cutoff:
            break
        if page_index == max_pages - 1:
            raise RuntimeError("Published-work list exceeds the configured page limit")
        cursor = str(page.get("end_cursor") or "")
        if not cursor:
            raise RuntimeError("Published-work list has no continuation cursor")
    return contents


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="robot_notes")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max-pages", type=int, default=10)
    args = parser.parse_args()
    if args.days < 1 or args.max_pages < 1:
        parser.error("days and max-pages must be positive")
    slug = args.account.replace("_", "-")
    runtime_dir = Path(os.environ.get("ACCOUNT_RUNTIME_DIR") or ROOT / ".runtime" / "accounts" / slug)
    output_dir = Path(os.environ.get("ACCOUNT_OUTPUT_DIR") or ROOT / "output" / "accounts" / slug)
    cookie_path = runtime_dir / "automation" / "exported_cookies.json"
    if not cookie_path.exists():
        print("Toutiao growth snapshot unavailable: account cookie file is missing", file=sys.stderr)
        return 2
    observed_at = datetime.now(BEIJING)
    try:
        contents = fetch_published(
            cookie_path,
            max_pages=args.max_pages,
            cutoff=observed_at - timedelta(days=args.days),
        )
        snapshot = build_snapshot(contents, observed_at, args.days)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Toutiao growth snapshot unavailable: {type(exc).__name__}", file=sys.stderr)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "growth_snapshot.json"
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Growth snapshot: {target}")
    for content_type, metrics in snapshot["summary"].items():
        print(
            f"{content_type}: works={metrics['works']} impressions={metrics['impressions']} "
            f"reads={metrics['reads']} zero_reads={metrics['zero_read_works']} "
            f"duplicate_titles={len(metrics['duplicate_titles'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
