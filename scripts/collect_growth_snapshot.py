"""Read the creator center's published-work counters into a growth snapshot.

The cookie file is read locally and is never included in the snapshot or logs.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from toutiao_content_factory.services.growth_feedback_service import (
    build_growth_feedback,
    classify_checkpoint_capture,
    write_growth_outputs,
)
from toutiao_content_factory.services.revenue_experiment_service import (
    RevenueExperimentService,
)

ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))
LIST_URL = "https://mp.toutiao.com/mp/agw/creator_center/list/v2"


def _counter(stat: dict, label: str, fallback: str) -> int:
    aliases = {
        "impression_count": {"展示", "展现"},
        "go_detail_count": {"阅读"},
        "comment_count": {"评论"},
    }.get(fallback, set())
    for item in stat.get("counters") or []:
        if item.get("Name") == label or item.get("Name") in aliases:
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


def update_checkpoints(
    snapshot: dict,
    previous: dict | None,
    observed_at: datetime,
) -> tuple[dict, dict]:
    """Persist the first real observation for each due checkpoint.

    One observation may represent only one checkpoint.  If collection starts
    after 72h, the current counters are a late 72h snapshot; they cannot also
    reconstruct what the counters were at 24h.
    """
    previous_works = previous.get("works") if isinstance(previous, dict) else {}
    works_by_gid = dict(previous_works) if isinstance(previous_works, dict) else {}
    captured_now = {"24h": 0, "72h": 0}

    for work in snapshot.get("works", []):
        gid = str(work.get("gid") or "").strip()
        if not gid:
            continue
        entry = works_by_gid.get(gid)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry.update(
            {
                "gid": gid,
                "type": str(work.get("type") or "unknown"),
                "title": str(work.get("title") or ""),
                "created_at": str(work.get("created_at") or ""),
                "last_observed_at": observed_at.isoformat(timespec="seconds"),
                "latest": {
                    "age_hours": float(work.get("age_hours") or 0),
                    "impressions": int(work.get("impressions") or 0),
                    "reads": int(work.get("reads") or 0),
                    "comments": int(work.get("comments") or 0),
                },
            }
        )
        checkpoints = entry.get("checkpoints")
        checkpoints = dict(checkpoints) if isinstance(checkpoints, dict) else {}
        # Repair ledgers produced by schema 1, which could write the same late
        # observation into both windows and then report a fake zero delta.
        first = checkpoints.get("24h")
        last = checkpoints.get("72h")
        if isinstance(first, dict) and isinstance(last, dict):
            same_capture = str(first.get("captured_at") or "") == str(last.get("captured_at") or "")
            same_counters = all(
                int(first.get(field) or 0) == int(last.get(field) or 0)
                for field in ("impressions", "reads", "comments")
            )
            if same_capture and same_counters:
                checkpoints.pop("24h", None)
                entry.pop("change_24h_to_72h", None)
        age_hours = float(work.get("age_hours") or 0)
        due = [
            (label, threshold)
            for label, threshold in (("24h", 24), ("72h", 72))
            if age_hours >= threshold
            and label not in checkpoints
            and not (label == "24h" and "72h" in checkpoints)
        ]
        # Pick the latest due window.  A later run can still add 72h when a
        # genuine 24h observation already exists.
        for label, threshold in due[-1:]:
            impressions = int(work.get("impressions") or 0)
            reads = int(work.get("reads") or 0)
            capture_delay = round(age_hours - threshold, 1)
            checkpoints[label] = {
                "captured_at": observed_at.isoformat(timespec="seconds"),
                "observed_age_hours": age_hours,
                "capture_delay_hours": capture_delay,
                "capture_quality": classify_checkpoint_capture(label, capture_delay),
                "impressions": impressions,
                "reads": reads,
                "comments": int(work.get("comments") or 0),
                "read_rate_pct": round(reads * 100 / impressions, 3) if impressions else 0.0,
            }
            captured_now[label] += 1
        for label, sample in checkpoints.items():
            if not isinstance(sample, dict):
                continue
            delay = float(sample.get("capture_delay_hours") or 0)
            sample["capture_quality"] = classify_checkpoint_capture(label, delay)
        if "24h" in checkpoints and "72h" in checkpoints:
            first = checkpoints["24h"]
            last = checkpoints["72h"]
            if (
                str(first.get("captured_at") or "") != str(last.get("captured_at") or "")
                and first.get("capture_quality") == "on_schedule"
                and last.get("capture_quality") == "on_schedule"
            ):
                entry["change_24h_to_72h"] = {
                    "impressions": int(last.get("impressions") or 0) - int(first.get("impressions") or 0),
                    "reads": int(last.get("reads") or 0) - int(first.get("reads") or 0),
                    "comments": int(last.get("comments") or 0) - int(first.get("comments") or 0),
                }
            else:
                entry.pop("change_24h_to_72h", None)
        entry["checkpoints"] = checkpoints
        works_by_gid[gid] = entry

    summary: dict[str, dict[str, int]] = {}
    for entry in works_by_gid.values():
        if not isinstance(entry, dict):
            continue
        content_type = str(entry.get("type") or "unknown")
        bucket = summary.setdefault(
            content_type,
            {"works": 0, "captured_24h": 0, "captured_72h": 0},
        )
        bucket["works"] += 1
        checkpoints = entry.get("checkpoints") or {}
        bucket["captured_24h"] += int("24h" in checkpoints)
        bucket["captured_72h"] += int("72h" in checkpoints)

    ledger = {
        "schema": 2,
        "updated_at": observed_at.isoformat(timespec="seconds"),
        "works": works_by_gid,
    }
    return ledger, {"captured_now": captured_now, "by_type": summary}


def merge_recovered_checkpoints(previous: dict, recovered: dict) -> tuple[dict, int]:
    """Restore earlier real captures without replacing latest counters or works."""
    result = copy.deepcopy(previous)
    restored = 0
    if previous.get("schema") != 2 or recovered.get("schema") != 2:
        return result, restored
    current_works = result.get("works")
    old_works = recovered.get("works")
    if not isinstance(current_works, dict) or not isinstance(old_works, dict):
        return result, restored
    for gid, old in old_works.items():
        current = current_works.get(gid)
        if not isinstance(current, dict) or not isinstance(old, dict):
            continue
        if any(current.get(k) != old.get(k) for k in ("gid", "title", "type", "created_at")):
            continue
        old_checkpoints = old.get("checkpoints")
        checkpoints = current.get("checkpoints")
        if not isinstance(old_checkpoints, dict) or not isinstance(checkpoints, dict):
            continue
        for label, threshold in (("24h", 24), ("72h", 72)):
            sample = old_checkpoints.get(label)
            if not isinstance(sample, dict):
                continue
            try:
                captured = datetime.fromisoformat(sample["captured_at"])
                created = datetime.fromisoformat(current["created_at"])
                if captured.tzinfo is None or created.tzinfo is None:
                    continue
                age = (captured - created).total_seconds() / 3600
                if age < threshold or (label == "24h" and age >= 72):
                    continue
                if any(type(sample.get(k)) is not int or sample[k] < 0 for k in ("impressions", "reads", "comments")):
                    continue
                existing = checkpoints.get(label)
                if existing and datetime.fromisoformat(existing["captured_at"]) <= captured:
                    continue
                replacement = dict(sample)
                delay = round(age - threshold, 1)
                replacement.update(observed_age_hours=round(age, 1), capture_delay_hours=delay,
                                   capture_quality=classify_checkpoint_capture(label, delay),
                                   read_rate_pct=round(sample["reads"] * 100 / sample["impressions"], 3) if sample["impressions"] else 0.0)
                checkpoints[label] = replacement
                current.pop("change_24h_to_72h", None)
                restored += 1
            except (KeyError, TypeError, ValueError):
                continue
    return result, restored


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


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
    parser.add_argument("--days", type=int, default=30)
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
    checkpoint_path = output_dir / "growth_checkpoints.json"
    previous = _load_json(checkpoint_path)
    recovery_path = output_dir / "growth_checkpoint_recovery.json"
    if recovery_path.exists():
        previous, restored = merge_recovered_checkpoints(previous, _load_json(recovery_path))
        print(f"Recovered earlier checkpoint captures: {restored}")
    checkpoints, checkpoint_summary = update_checkpoints(
        snapshot,
        previous,
        observed_at,
    )
    _write_json_atomic(checkpoint_path, checkpoints)
    snapshot["checkpoint_summary"] = checkpoint_summary
    snapshot["checkpoint_file"] = str(checkpoint_path)
    target = output_dir / "growth_snapshot.json"
    _write_json_atomic(target, snapshot)
    feedback = build_growth_feedback(
        snapshot,
        checkpoints,
        generated_at=observed_at.isoformat(timespec="seconds"),
    )
    growth_outputs = write_growth_outputs(output_dir, feedback)
    revenue_outputs = RevenueExperimentService(
        output_dir,
        logging.getLogger("revenue_experiment"),
    ).build_and_write(
        growth_snapshot=snapshot,
        growth_feedback=feedback,
    )
    print(f"Growth snapshot: {target}")
    print(
        "Growth checkpoints: "
        f"24h+={checkpoint_summary['captured_now']['24h']} "
        f"72h+={checkpoint_summary['captured_now']['72h']} file={checkpoint_path}"
    )
    print(f"Growth operations report: {growth_outputs['report_md']}")
    print(f"Revenue experiment dashboard: {revenue_outputs['dashboard_md']}")
    for content_type, metrics in snapshot["summary"].items():
        print(
            f"{content_type}: works={metrics['works']} impressions={metrics['impressions']} "
            f"reads={metrics['reads']} zero_reads={metrics['zero_read_works']} "
            f"duplicate_titles={len(metrics['duplicate_titles'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
