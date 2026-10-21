"""Read the creator center's published-work counters into a growth snapshot.

The cookie file is read locally and is never included in the snapshot or logs.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from toutiao_content_factory.services.growth_feedback_service import (
    build_growth_feedback,
    classify_checkpoint_capture,
    write_growth_outputs,
    normalize_title,
)
from toutiao_content_factory.services.revenue_experiment_service import (
    RevenueExperimentService,
)
from toutiao_content_factory.services.content_income_service import ARTICLE_INCOME_PATH, platform_item_id

ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))
LIST_URL = "https://mp.toutiao.com/mp/agw/creator_center/list/v2"


def attach_opening_experiments(snapshot: dict, experiments: dict) -> None:
    """Join by unique exact display text and submission time in this account.

    Micro-post submission responses do not always expose a stable work ID.
    Ambiguous, cross-type, stale or duplicate matches remain unassigned.
    """
    records = experiments.get("records", {})
    if not isinstance(records, dict):
        return
    matches: dict[str, list[tuple[dict, dict]]] = {}
    for work in snapshot.get("works", []):
        if work.get("type") != "weitoutiao":
            continue
        candidates = []
        for key, record in records.items():
            if not isinstance(record, dict) or record.get("id") != "story_opening_v1":
                continue
            if record.get("variant") not in {"result_first", "situation_first"} or record.get("status") not in {"published", "submitted", "submitted_for_review", "success"}:
                continue
            # The platform displays the first paragraph for micro-posts. A
            # protocol4 lead is longer than the package's clipped title field.
            # Use only the actual publisher text, never a prefix/fuzzy match.
            display = record.get("title", "")
            if record.get("protocol_version") == 4:
                display = record.get("submitted_opening")
                if not isinstance(display, str) or not display.strip():
                    continue
            title = normalize_title(display)
            if not title or title != normalize_title(work.get("title", "")):
                continue
            try:
                submitted = datetime.fromisoformat(record["submitted_at"])
                if submitted.tzinfo is None:
                    submitted = submitted.replace(tzinfo=BEIJING)
                created = datetime.fromisoformat(work["created_at"])
                if created.tzinfo is None or abs((created - submitted).total_seconds()) > 600:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            candidates.append((key, record))
        if len(candidates) == 1:
            key, record = candidates[0]
            matches.setdefault(key, []).append((work, record))
    for key, group in matches.items():
        if len(group) == 1:
            work, record = group[0]
            work["content_experiment"] = {
                k: record[k] for k in ("id", "variable", "variant", "protocol_version", "selection_protocol", "category",
                                      "trial_id", "body_structure") if k in record
            }
            lineage = {k: record[k] for k in ("topic_title", "source_url", "content_attempt_id") if k in record}
            if lineage:
                work["content_lineage"] = {"run_key": key, **lineage}


def attach_article_experiments(snapshot: dict, history: list) -> None:
    """Articles have stable URLs. Reuse publish history, never fuzzy titles."""
    if not isinstance(history, list):
        return
    for work in snapshot.get("works", []):
        if work.get("type") != "article":
            continue
        matches = []
        for record in history:
            if (not isinstance(record, dict) or record.get("platform") != "toutiao"
                    or record.get("status") not in {"published", "submitted_for_review", "success"}
                    or platform_item_id(record.get("external_url", "")) != work.get("gid")
                    or normalize_title(record.get("title")) != normalize_title(work.get("title"))
                    or not isinstance(record.get("content_experiment"), dict)):
                continue
            try:
                submitted = datetime.fromisoformat(record["published_at"])
                submitted = submitted if submitted.tzinfo else submitted.replace(tzinfo=BEIJING)
                created = datetime.fromisoformat(work["created_at"])
                if created.tzinfo and abs((created - submitted).total_seconds()) <= 600:
                    matches.append(record["content_experiment"])
            except (KeyError, TypeError, ValueError):
                continue
        if len(matches) == 1:
            work["content_experiment"] = dict(matches[0])


def attach_content_income(snapshot: dict, income_report: dict, observed_at: datetime) -> None:
    try:
        captured = datetime.fromisoformat(income_report["observed_at"])
        if captured.tzinfo is None or not 0 <= (observed_at - captured).total_seconds() <= 12 * 3600:
            return
    except (KeyError, ValueError, TypeError):
        return
    by_gid: dict[str, list[dict]] = {}
    for row in income_report.get("works") or []:
        if isinstance(row, dict):
            by_gid.setdefault(str(row.get("gid", "")), []).append(row)
    for work in snapshot.get("works", []):
        candidates = by_gid.get(str(work.get("gid", "")), [])
        if len(candidates) != 1:
            continue
        row = candidates[0]
        if row.get("status") != "available" or row.get("source") != ARTICLE_INCOME_PATH or row.get("scope") != "cumulative_through_ready_date":
            continue
        if row.get("type") != work.get("type") or normalize_title(row.get("title", "")) != normalize_title(work.get("title", "")):
            continue
        amount = row.get("amount")
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            continue
        try:
            created = datetime.fromisoformat(work["created_at"])
            income_created = datetime.fromisoformat(row["created_at"])
            ready = datetime.fromisoformat(row["revenue_as_of_date"]).date()
            if abs((created - income_created).total_seconds()) > 2 or not created.date() <= ready < observed_at.date():
                continue
        except (KeyError, ValueError, TypeError):
            continue
        work.update(platform_revenue=amount, platform_revenue_status="available", revenue_amount_known=True,
                    revenue_as_of_date=ready.isoformat(), revenue_scope=row["scope"],
                    revenue_gid=str(row["gid"]), revenue_source=row["source"])


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
        if entry and any(key in entry and entry[key] != work.get(key) for key in ("gid", "type", "title", "created_at")):
            continue  # Preserve the original receipt; changed identities need review.
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
        experiment = work.get("content_experiment")
        if isinstance(experiment, dict) and (not entry.get("checkpoints")
                or entry.get("content_experiment") == experiment):
            entry["content_experiment"] = dict(experiment)
        lineage = work.get("content_lineage")
        if isinstance(lineage, dict) and (not entry.get("checkpoints") or entry.get("content_lineage") == lineage):
            entry["content_lineage"] = dict(lineage)
        if work.get("revenue_amount_known") is True:
            entry["income"] = {k: work[k] for k in ("platform_revenue", "platform_revenue_status", "revenue_amount_known",
                "revenue_gid", "revenue_source", "revenue_as_of_date", "revenue_scope") if k in work}
        # Captured measurements retain their original assignment (including
        # unassigned). A later category/variant/policy change cannot relabel
        # them; snapshot/entry mismatch excludes them from adaptive feedback.
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
            # Retain only provider-supplied scope/cutoff, never invent an aligned
            # checkpoint amount from the later ready-date income table.
            for key in ("platform_revenue", "platform_revenue_status", "revenue_amount_known", "revenue_gid",
                        "revenue_settled", "revenue_scope", "revenue_source", "revenue_counters_captured_at"):
                if key in work:
                    checkpoints[label][key] = work[key]
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


def _load_json(path: Path, *, expected_type: type = dict) -> dict | list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return expected_type()
    return payload if isinstance(payload, expected_type) else expected_type()


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _cookie_summary(cookies: list[dict]) -> str:
    """Describe the cookie jar so a failure can be told apart from an outage.

    2026-10-21：采集槽失败时日志只有 ``RuntimeError``，既不知道是接口拒绝
    （cookie 过期/未登录）还是网络/风控，也无从判断该重试还是该换 cookie。
    """
    now = int(time.time())
    total = len(cookies)
    expired = sum(
        1
        for cookie in cookies
        if isinstance(cookie.get("expires"), (int, float))
        and 0 < float(cookie["expires"]) < now
    )
    return f"cookies={total} expired={expired}"


def _fetch_page(request: Request) -> dict:
    """One request, one retry for transient transport failures.

    Creator-center list calls are read-only, so a single retry after a TLS/socket
    error is safe. API-level rejections (``code != 0``) are not retried: they mean
    the session is not accepted and repeating the call only wastes the slot.
    """
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1.5)
                continue
            raise
    raise last_error if last_error else RuntimeError("Creator center request failed")


def _describe_failure(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).strip()[:300] or '(empty message)'}"


def _refresh_toutiao_cookies(account: str, *, timeout_seconds: int = 180) -> Path | None:
    """Export fresh Toutiao cookies from the persistent browser profile.

    Only called after the creator-center API rejected the cached cookie file.
    The exported file is written to a temporary location and validated before
    use, so a failed export can never clobber the cached account asset.
    """
    import subprocess
    import tempfile

    scripts_dir = Path(__file__).resolve().parent
    target = Path(tempfile.mkdtemp(prefix="growth-cookie-refresh-")) / "exported_cookies.json"
    command = [
        sys.executable,
        str(scripts_dir / "export_cookies.py"),
        "--platforms", "toutiao",
        "--output", str(target),
        "--headless",
        "--account", account,
    ]
    environment = dict(os.environ)
    environment.setdefault("PYTHONPATH", str(scripts_dir.parent / "src"))
    try:
        result = subprocess.run(
            command,
            check=False,
            timeout=timeout_seconds,
            capture_output=True,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(
            f"Toutiao cookie refresh could not run: {type(exc).__name__}",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        print(
            f"Toutiao cookie refresh exited with {result.returncode}",
            file=sys.stderr,
        )
        return None
    payload = _load_json(target, expected_type=dict)
    cookies = ((payload.get("platforms") or {}).get("toutiao") or {}).get("cookies") or []
    if not cookies:
        print("Toutiao cookie refresh produced no usable cookies", file=sys.stderr)
        return None
    return target


def fetch_with_cookie_retry(
    cookie_path: Path,
    *,
    max_pages: int,
    cutoff: datetime,
    account: str,
) -> list[dict]:
    """Fetch the published-work list, refreshing cookies once if rejected.

    2026-10-21：采集槽 growth 快照因创作者中心拒绝 cookie 而失败，当天 24h/72h
    检查点直接丢失。缓存的 ``exported_cookies.json`` 是会过期的账号资产，而浏览器
    profile 里的会话通常还活着。所以只在这个明确失败点做一次"从浏览器导出新
    cookie 再试"，失败则保留原始原因，不掩盖问题。
    """
    try:
        return fetch_published(cookie_path, max_pages=max_pages, cutoff=cutoff)
    except (RuntimeError, ValueError) as exc:
        print(
            "Toutiao creator-center rejected the cached cookies: "
            f"{_describe_failure(exc)}; refreshing from the browser profile.",
            file=sys.stderr,
        )
        refreshed = _refresh_toutiao_cookies(account)
        if refreshed is None:
            raise
        contents = fetch_published(refreshed, max_pages=max_pages, cutoff=cutoff)
        print("Toutiao cookie refresh recovered the growth snapshot.", file=sys.stderr)
        return contents


def fetch_published(cookie_path: Path, *, max_pages: int, cutoff: datetime) -> list[dict]:
    payload = json.loads(cookie_path.read_text(encoding="utf-8"))
    cookies = payload["platforms"]["toutiao"]["cookies"]
    cookie_header = "; ".join(
        f"{cookie['name']}={cookie.get('value', '')}"
        for cookie in cookies if cookie.get("name")
    )
    if not cookie_header:
        raise ValueError("Toutiao cookie file has no usable cookies")
    cookie_state = _cookie_summary([cookie for cookie in cookies if cookie.get("name")])
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
        page = _fetch_page(request)
        if page.get("code") not in (0, None) or not isinstance(page.get("contents"), list):
            raise RuntimeError(
                "Creator center did not return a published-work list: "
                f"code={page.get('code')!r} message={str(page.get('message') or '')[:120]!r} "
                f"{cookie_state}"
            )
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
        contents = fetch_with_cookie_retry(
            cookie_path,
            max_pages=args.max_pages,
            cutoff=observed_at - timedelta(days=args.days),
            account=args.account,
        )
        snapshot = build_snapshot(contents, observed_at, args.days)
        snapshot["account"] = args.account
        from toutiao_content_factory.config import load_settings
        from toutiao_content_factory.services.revenue_feedback import operating_trial
        trial = operating_trial(load_settings(ROOT, account_key=args.account))
        if trial:
            snapshot["operating_trial"] = trial
        attach_opening_experiments(snapshot, _load_json(output_dir / "growth_ops" / "performance" / "wtt_content_experiments.json"))
        attach_article_experiments(snapshot, _load_json(output_dir / "publish_history.json", expected_type=list))
        attach_content_income(snapshot, _load_json(output_dir / "growth_ops" / "performance" / "content_income_collection.json"), observed_at)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Toutiao growth snapshot unavailable: {_describe_failure(exc)}", file=sys.stderr)
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
    reader_batch = None
    from toutiao_content_factory.services.reader_value_experiment import STATE_PATH, ensure_batch, update_batch
    if args.account == "robot_notes" or (output_dir / STATE_PATH).exists():
        ensure_batch(output_dir, account=args.account)
        reader_batch = update_batch(output_dir, snapshot, checkpoints, account=args.account)
    feedback = build_growth_feedback(
        snapshot,
        checkpoints,
        generated_at=observed_at.isoformat(timespec="seconds"),
        reader_value_batch=reader_batch,
    )
    revenue_outputs = RevenueExperimentService(
        output_dir,
        logging.getLogger("revenue_experiment"),
    ).build_and_write(
        growth_snapshot=snapshot,
        growth_feedback=feedback,
    )
    operating = _load_json(revenue_outputs["dashboard"]).get("operating_trial") or {}
    if operating.get("trial_id"):
        # This file is already preserved by both Actions cache lists. Keep the
        # cache paths/version stable so earlier checkpoints/history survive.
        feedback["operating_trial"] = {key: operating[key] for key in (
            "account", "trial_id", "body_structure", "started_at", "ends_at", "observed_at", "weekly_review")}
    growth_outputs = write_growth_outputs(output_dir, feedback)
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
