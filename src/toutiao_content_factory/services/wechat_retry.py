"""Durable WeChat daily reservations and once-only relay intents."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

STATE_PATH = "state/wechat_retry_state.json"
MAX_RUNS = 3
MAX_SECONDS = 5400
BEIJING = timezone(timedelta(hours=8))


def day_of(value: str) -> str:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Retry timestamps require a timezone")
    return stamp.astimezone(BEIJING).strftime("%Y-%m-%d")


def validate(state: dict) -> dict:
    if not state:
        return {"schema_version": 1, "days": {}}
    if state.get("schema_version") != 1 or not isinstance(state.get("days"), dict):
        raise ValueError("Invalid WeChat retry state")
    for day, entry in state["days"].items():
        datetime.strptime(day, "%Y-%m-%d")
        if not isinstance(entry.get("runs"), dict) or not isinstance(entry.get("dispatches"), dict):
            raise ValueError("Invalid WeChat retry day")
        for key, run in entry["runs"].items():
            elapsed = run.get("elapsed_seconds")
            if not isinstance(key, str) or run.get("status") not in {"running", "completed"}:
                raise ValueError("Invalid WeChat reservation")
            if run["status"] == "completed" and (type(elapsed) not in {int, float} or not math.isfinite(elapsed) or elapsed < 0):
                raise ValueError("Invalid WeChat elapsed time")
    return deepcopy(state)


def today_entry(state: dict, day: str) -> dict:
    return state["days"].setdefault(day, {"runs": {}, "dispatches": {}, "failed_inputs": []})


def remaining(entry: dict) -> float:
    return max(0.0, MAX_SECONDS - sum(row.get("elapsed_seconds", 0) for row in entry["runs"].values()))


def reserve(state: dict, day: str, key: str, started_at: str, *, parent: str = "", parent_day: str = "",
            validation_id: str = "", head_sha: str = "") -> tuple[dict, str]:
    state = validate(state)
    entry = today_entry(state, day)
    if parent:
        if parent_day != day:
            return state, "stale_relay"
        intent = entry["dispatches"].get(parent)
        if not intent or intent.get("status") not in {"intent", "dispatched"} or intent.get("child"):
            return state, "duplicate_or_invalid_relay"
    # An abandoned reservation is not proof that no publication happened.
    if any(row["status"] == "running" for item in state["days"].values() for row in item["runs"].values()):
        raise RuntimeError("Unresolved WeChat daily reservation")
    if key in entry["runs"]:
        return state, "duplicate_run"
    grant = None
    if validation_id:
        grant = entry.get("manual_validation_grants", {}).get(validation_id)
        if (parent or not grant or grant.get("status") != "authorized"
                or grant.get("purpose") != "verify_formal_publication" or not head_sha
                or grant.get("head_sha") != head_sha or day_of(started_at) != day
                or not grant.get("expires_at")
                or datetime.fromisoformat(started_at.replace("Z", "+00:00")) >= datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))):
            return state, "invalid_validation_grant"
        previous = [(run_key, row) for run_key, row in entry["runs"].items() if row.get("manual_validation_id")]
        if previous:
            last_key, last_run = previous[-1]
            preparation = grant.get("resume_after_preparation_failure") == last_key and last_run.get("preparation_failure_verified") is True
            unpublished = (grant.get("resume_after_not_published") == last_key
                           and last_run.get("no_submission_verified") is True
                           and last_run.get("outcome") in {"review_required", "no_topics"})
            if not preparation and not unpublished:
                return state, "validation_already_used"
    if (len(entry["runs"]) >= MAX_RUNS and grant is None) or remaining(entry) < 120:
        return state, "daily_budget_exhausted"
    entry["runs"][key] = {"status": "running", "started_at": started_at, "parent": parent, "elapsed_seconds": 0}
    if grant is not None:
        grant.update(status="claimed", run_key=key)
        entry["runs"][key]["manual_validation_id"] = validation_id
    if parent:
        entry["dispatches"][parent].update(status="claimed", child=key)
    # Old days cannot authorize a new run. Retain a small operational window.
    for old in sorted(state["days"])[:-7]:
        del state["days"][old]
    return state, "reserved"


def complete(state: dict, day: str, key: str, elapsed: float, report: dict, diagnostics: dict) -> dict:
    state = validate(state)
    entry = today_entry(state, day)
    row = entry["runs"][key]
    row.update(status="completed", elapsed_seconds=max(0, elapsed), outcome=report.get("status", "error"))
    if row.get("manual_validation_id"):
        entry["manual_validation_grants"][row["manual_validation_id"]]["status"] = "consumed"
    for attempt in diagnostics.get("attempts", []):
        fingerprint = attempt.get("draft_fingerprint")
        if fingerprint and attempt.get("quality_review", {}).get("verdict") != "approved":
            requirements = attempt.get("required_revisions", [])
            item = {"fingerprint": fingerprint, "source_fingerprint": attempt.get("source_fingerprint", ""),
                    "requirements": requirements[:12]}
            if item not in entry["failed_inputs"]:
                entry["failed_inputs"].append(item)
    entry["failed_inputs"] = entry["failed_inputs"][-30:]
    return state


def can_relay(entry: dict, report: dict, exit_code: int) -> bool:
    return (exit_code == 0 and report.get("status") in {"review_required", "no_topics"}
            and report.get("publication_attempted") is False and not report.get("wechat_status")
            and report.get("native_exit_code") == 0
            and not report.get("metrics_error") and not report.get("retry_state_error")
            and len(entry["runs"]) < MAX_RUNS and remaining(entry) >= 120)


def dispatch_intent(state: dict, day: str, parent: str) -> dict:
    state = validate(state)
    entry = today_entry(state, day)
    if parent in entry["dispatches"]:
        raise RuntimeError("WeChat relay already has a durable intent")
    entry["dispatches"][parent] = {"status": "intent", "child": ""}
    return state


def retry_context(entry: dict) -> dict:
    return {"failed_fingerprints": [item["fingerprint"] for item in entry.get("failed_inputs", [])],
            "failed_inputs": entry.get("failed_inputs", [])}
