"""Freeze one operating baseline and review the next seven real account works."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path
from statistics import median
import tempfile
import time

from toutiao_content_factory.services.growth_feedback_service import CHECKPOINT_TOLERANCE_HOURS

STRATEGY = "reader_value_20261015"
STATE_PATH = Path("growth_ops/performance/reader_value_batch.json")


def _now():
    return datetime.now(timezone(timedelta(hours=8)))


def _stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp needs a timezone")
    return parsed


def _load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@contextmanager
def _locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 2
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write(path, value):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(name, path)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def _snapshot(snapshot, account):
    if not isinstance(snapshot, dict) or snapshot.get("account", account) != account or not isinstance(snapshot.get("works"), list):
        raise ValueError("Wrong snapshot scope")
    observed = _stamp(snapshot["observed_at"])
    works = {}
    for row in snapshot["works"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid work")
        if row.get("type") != "weitoutiao":
            continue
        gid = row.get("gid")
        if not isinstance(gid, str) or not gid or gid in works or _stamp(row["created_at"]) > observed:
            raise ValueError("Ambiguous work identity")
        works[gid] = row
    return observed, works


def qualified_checkpoint_sample(entry, label, *, work=None, observed=None):
    samples = entry.get("checkpoints", {}) if isinstance(entry, dict) else {}
    row = samples.get(label) if isinstance(samples, dict) else None
    if not isinstance(row, dict) or any(type(row.get(key)) is not int or row[key] < 0 for key in ("impressions", "reads")):
        return None
    delay = row.get("capture_delay_hours")
    if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0 or delay > CHECKPOINT_TOLERANCE_HOURS[label]:
        return None
    if work is not None and observed is not None:
        if any(entry.get(key, work[key]) != work[key] for key in ("gid", "created_at", "title")):
            return None
        created = _stamp(work["created_at"])
        minimum = created + timedelta(hours=int(label[:-1]))
        captured = _stamp(row["captured_at"]) if row.get("captured_at") else minimum + timedelta(hours=delay)
        if (not minimum <= captured <= observed
                or abs((captured - minimum).total_seconds() / 3600 - delay) > .11):
            return None
    return row


def _stats(rows, *, limit=None):
    zero = [gid for gid, row in rows if row["impressions"] == 0]
    rows = [(gid, row) for gid, row in rows if row["impressions"] > 0]
    if limit is not None:
        rows = rows[:limit]
    impressions = sum(row["impressions"] for _, row in rows)
    reads = sum(row["reads"] for _, row in rows)
    return {"gids": [gid for gid, _ in rows], "samples": len(rows), "impressions": impressions, "reads": reads,
            "zero_exposure_samples": len(zero), "zero_exposure_gids": zero,
            "read_rate_pct": round(reads * 100 / impressions, 4) if impressions else None,
            "median_reads": median([row["reads"] for _, row in rows]) if rows else None,
            "median_impressions": median([row["impressions"] for _, row in rows]) if rows else None}


def _state(path, account):
    state = _load(path)
    if (state.get("schema") != 1 or state.get("strategy") != STRATEGY or state.get("account") != account
            or not isinstance(state.get("baseline"), dict) or not isinstance(state.get("cohort"), list)
            or len(state["cohort"]) > 7):
        raise ValueError("Unknown state")
    started = _stamp(state["started_at"])
    _stamp(state["last_observed_at"])
    if not isinstance(state["baseline"].get("gids"), list):
        raise ValueError("Missing baseline identity")
    for label in CHECKPOINT_TOLERANCE_HOURS:
        value = state["baseline"]["windows"][label]
        if type(value.get("samples")) is not int or not 0 <= value["samples"] <= 7:
            raise ValueError("Invalid baseline count")
        for field in ("median_reads", "median_impressions"):
            number = value.get(field)
            if number is not None and (type(number) not in (int, float) or not math.isfinite(number) or number < 0):
                raise ValueError("Invalid baseline metric")
    gids = set()
    for row in state["cohort"]:
        if (not isinstance(row, dict) or not row.get("gid") or row["gid"] in gids
                or row["gid"] in state["baseline"]["gids"] or _stamp(row["created_at"]) <= started):
            raise ValueError("Invalid cohort")
        gids.add(row["gid"])
    return state


def ensure_batch(output_dir: Path, *, account: str) -> dict:
    path = Path(output_dir) / STATE_PATH
    try:
        with _locked(path):
            if path.exists():
                state = _state(path, account)
                return {"status": "observing", "started_at": state["started_at"]}
            snapshot_path = Path(output_dir) / "growth_snapshot.json"
            if not snapshot_path.exists():
                return {"status": "baseline_pending", "reason": "snapshot_missing"}
            snapshot = _load(snapshot_path)
            observed, works = _snapshot(snapshot, account)
            now = _now()
            if not 0 <= (now - observed).total_seconds() <= 36 * 3600:
                return {"status": "baseline_pending", "reason": "snapshot_not_recent"}
            checkpoint_path = Path(output_dir) / "growth_checkpoints.json"
            checkpoints = _load(checkpoint_path).get("works", {}) if checkpoint_path.exists() else {}
            windows = {}
            ordered = sorted(works.values(), key=lambda row: row["created_at"], reverse=True)
            for label in CHECKPOINT_TOLERANCE_HOURS:
                rows = [(row["gid"], sample) for row in ordered
                        if (sample := qualified_checkpoint_sample(checkpoints.get(row["gid"]), label, work=row, observed=observed)) is not None]
                windows[label] = _stats(rows, limit=7)
            state = {"schema": 1, "strategy": STRATEGY, "account": account, "started_at": now.isoformat(),
                     "last_observed_at": observed.isoformat(), "baseline": {
                         "snapshot_at": observed.isoformat(), "gids": list(works), "windows": windows}, "cohort": []}
            _write(path, state)
            return {"status": "initialized", "started_at": state["started_at"]}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"status": "state_needs_review", "reason": "invalid_scope_or_state"}


def _report(state, observed, works, checkpoints):
    windows = {}
    cohort = state["cohort"]
    for label, tolerance in CHECKPOINT_TOLERANCE_HOURS.items():
        rows, late = [], 0
        for item in cohort:
            current = works.get(item["gid"])
            if not current or current.get("title") != item["title"] or current.get("created_at") != item["created_at"]:
                continue
            entry = checkpoints.get("works", {}).get(item["gid"], {})
            sample = qualified_checkpoint_sample(entry, label, work=current, observed=observed)
            if sample is not None:
                rows.append((item["gid"], sample))
            else:
                raw = entry.get("checkpoints", {}).get(label, {})
                delay = raw.get("capture_delay_hours")
                late += type(delay) in (int, float) and math.isfinite(delay) and delay > tolerance
        stats = _stats(rows)
        baseline = state["baseline"]["windows"][label]
        all_due = len(cohort) == 7 and all((observed - _stamp(row["created_at"])).total_seconds() / 3600
                                           > int(label[:-1]) + tolerance for row in cohort)
        verdict = "observing"
        if stats["samples"] == 7:
            if (not baseline["samples"] or not baseline.get("impressions")
                    or baseline.get("read_rate_pct") is None):
                verdict = "baseline_unavailable"
            else:
                improved = (stats["impressions"] > 0 and stats["reads"] * 100 >= 2 * stats["impressions"]
                            and stats["median_reads"] > baseline["median_reads"]
                            and stats["median_impressions"] >= baseline["median_impressions"])
                verdict = "improvement_signal" if improved else "no_consistent_improvement"
        elif all_due:
            verdict = "window_incomplete"
        elif stats["samples"] >= 5:
            verdict = "review_due"
        windows[label] = {**stats, "baseline": baseline, "late_samples": late,
                          "missing_samples": len(cohort) - len(rows) - late, "verdict": verdict}
    terminal = {"improvement_signal", "no_consistent_improvement", "baseline_unavailable", "window_incomplete"}
    completed = all(value["verdict"] in terminal for value in windows.values())
    verdict = None
    if completed:
        verdicts = {value["verdict"] for value in windows.values()}
        verdict = ("data_incomplete" if verdicts & {"baseline_unavailable", "window_incomplete"}
                   else "improvement_signal" if verdicts == {"improvement_signal"} else "no_consistent_improvement")
    action = ("本轮未改善，停止把该写法当成有效方案；复盘选题和正文具体答案，不重开批次获取通过。"
              if verdict == "no_consistent_improvement" else
              "本轮数据不完整，先核对缺失或迟到记录，保留未知，不用零补齐。" if verdict == "data_incomplete" else
              "出现描述性改善信号，继续核对作品与实际收益，不宣称因果或自动放大。" if verdict else
              "5篇有效记录到齐即复盘，7篇形成分窗口结论；不等待30条才改进信息价值。")
    return {"strategy": STRATEGY, "account": state["account"], "started_at": state["started_at"],
            "observed_at": observed.isoformat(), "cohort_size": len(cohort), "works": cohort,
            "windows": windows, "status": "completed" if completed else "review_due"
            if any(row["samples"] >= 5 for row in windows.values()) else "observing", "verdict": verdict,
            "review_after": 5, "limit": 7, "read_rate_target_pct": 2.0, "causal_claim": False, "automatic_switch": False,
            "next_action": action,
            "note": "该账号启用后的公开微头条，可能包含人工发表；只作描述性运营复盘，不推断因果或自动增加发表。"}


def update_batch(output_dir: Path, snapshot: dict, checkpoints: dict, *, account: str) -> dict:
    path = Path(output_dir) / STATE_PATH
    try:
        with _locked(path):
            if not path.exists():
                return {"status": "not_started"}
            state = _state(path, account)
            observed, works = _snapshot(snapshot, account)
            if observed < _stamp(state["last_observed_at"]):
                return {"status": "stale_observation"}
            seen = set(state["baseline"]["gids"]) | {row["gid"] for row in state["cohort"]}
            for row in sorted(works.values(), key=lambda row: row["created_at"]):
                if len(state["cohort"]) == 7:
                    break
                if row["gid"] not in seen and _stamp(row["created_at"]) > _stamp(state["started_at"]):
                    state["cohort"].append({key: row[key] for key in ("gid", "title", "created_at")})
                    seen.add(row["gid"])
            state["last_observed_at"] = observed.isoformat()
            report = _report(state, observed, works, checkpoints)
            _write(path, state)
            return report
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"status": "state_needs_review", "reason": "invalid_scope_or_state"}
