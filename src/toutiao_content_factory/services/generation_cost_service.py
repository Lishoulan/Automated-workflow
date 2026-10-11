"""Request receipts for generation costs; estimates never prove actual spending."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))


def _field(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _number(value):
    if type(value) not in {int, float}:
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except (OverflowError, ValueError):
        return None


class GenerationCostAttempt:
    """One durable receipt per transport invocation, including failures."""

    def __init__(self, settings, logger, *, kind, model, asset="", sdk_retries=0):
        self.logger = logger or logging.getLogger(__name__)
        self.path = Path(settings.output_dir) / "growth_ops/generation_costs/events" / (uuid.uuid4().hex + ".json")
        self.row = {
            "schema": 1, "event_id": self.path.stem,
            "started_at": datetime.now(BEIJING).isoformat(), "status": "started",
            "account_key": str(getattr(settings, "account_key", "default")),
            "platform": str(getattr(settings, "platform", "unknown")),
            "kind": kind, "model": model, "asset": Path(asset).name if asset else "",
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "sdk_retries": sdk_retries if type(sdk_retries) is int else None,
            "usage": None, "estimated_amount": None, "estimated_currency": None,
            "actual_amount": None, "actual_currency": None, "billing_status": "unverified",
        }

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(self.row, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(8):
                try:
                    temporary.replace(self.path)
                    break
                except PermissionError:
                    if os.name != "nt" or attempt == 7:
                        raise
                    import time
                    time.sleep(.005 * (attempt + 1))
        except (OSError, TypeError, ValueError, OverflowError):
            # Recording trouble must not turn a completed request into a retry.
            self.logger.warning("Generation cost receipt could not be persisted")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self):
        self._save()
        return self

    def complete(self, response=None, *, prompt_rate=None, completion_rate=None):
        usage = _field(response, "usage")
        counts = {key: _number(_field(usage, key)) for key in
                  ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens")}
        self.row["usage"] = {key: int(value) for key, value in counts.items() if value is not None} or None
        if (counts["prompt_tokens"] is not None and counts["completion_tokens"] is not None
                and _number(prompt_rate) is not None and _number(completion_rate) is not None):
            amount = _number((counts["prompt_tokens"] * prompt_rate + counts["completion_tokens"] * completion_rate) / 1000)
            if amount is not None:
                self.row.update(estimated_amount=round(amount, 6), estimated_currency="CNY",
                                estimate_basis="configured_or_legacy_token_rates")
        request_id = _field(response, "_request_id") or _field(response, "id")
        if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) and not request_id.startswith(("sk-", "cfut_")):
            self.row["provider_request_id"] = request_id
            billing = _field(response, "billing")
            amount, currency = _number(_field(billing, "amount")), _field(billing, "currency")
            if (amount is not None and currency in {"CNY", "USD"}
                    and _field(billing, "status") == "charged"
                    and _field(billing, "request_id") == request_id):
                self.row.update(actual_amount=amount, actual_currency=currency,
                                billing_status="provider_confirmed", billing_evidence="matched_response_receipt")
        self.row["status"] = "succeeded"

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            self.row.update(status="failed", error_type=exc_type.__name__)
        self.row["completed_at"] = datetime.now(BEIJING).isoformat()
        self._save()


def build_cost_summary(output_dir, *, now=None, days=30):
    now = now or datetime.now(BEIJING)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    rows, invalid = [], 0
    for path in (Path(output_dir) / "growth_ops/generation_costs/events").glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(row, dict):
                raise ValueError("Invalid cost receipt")
            stamp = datetime.fromisoformat(row["started_at"])
            if row.get("schema") != 1 or row.get("event_id") != path.stem or stamp.tzinfo is None:
                raise ValueError("Invalid cost receipt")
            if not all(isinstance(row.get(key), str) for key in ("kind", "status")):
                raise ValueError("Invalid cost receipt categories")
            if row.get("estimated_currency") is not None and not isinstance(row["estimated_currency"], str):
                raise ValueError("Invalid cost receipt currency")
            if now - timedelta(days=days) <= stamp <= now:
                rows.append(row)
        except (OSError, ValueError, KeyError, TypeError):
            invalid += 1
    estimates = Counter()
    actuals = Counter()
    verified = 0
    for row in rows:
        if _number(row.get("estimated_amount")) is not None and row.get("estimated_currency"):
            estimates[row["estimated_currency"]] += row["estimated_amount"]
        if (row.get("billing_status") == "provider_confirmed"
                and row.get("billing_evidence") == "matched_response_receipt"
                and row.get("provider_request_id") and row.get("sdk_retries") == 0
                and _number(row.get("actual_amount")) is not None and row.get("actual_currency") in {"CNY", "USD"}):
            verified += 1
            actuals[row["actual_currency"]] += row["actual_amount"]
    aggregation_errors = ["estimated." + key for key, value in estimates.items() if not math.isfinite(value)]
    actual_errors = ["actual." + key for key, value in actuals.items() if not math.isfinite(value)]
    aggregation_errors.extend(actual_errors)
    estimates = {key: value for key, value in estimates.items() if math.isfinite(value)}
    actuals = {key: value for key, value in actuals.items() if math.isfinite(value)}
    complete = bool(rows) and verified == len(rows) and invalid == 0 and not actual_errors
    return {
        "schema": 1, "window_days": days, "recorded_calls": len(rows), "invalid_receipts": invalid,
        "aggregation_errors": aggregation_errors,
        "status": ("tracked_cost_verified" if complete else "actual_cost_partially_verified" if verified
                   else "actual_cost_unverified" if rows else "untracked"),
        "by_kind": dict(Counter(row.get("kind", "unknown") for row in rows)),
        "by_status": dict(Counter(row.get("status", "unknown") for row in rows)),
        "missing_usage_calls": sum(row.get("usage") is None for row in rows),
        "estimated_by_currency": {key: round(value, 6) for key, value in estimates.items()},
        "verified_cost_calls": verified, "unverified_cost_calls": len(rows) - verified,
        "actual_verified_by_currency": {key: round(value, 6) for key, value in actuals.items()},
        "actual_total": round(sum(actuals.values()), 6) if complete and len(actuals) == 1 else None,
        "actual_currency": next(iter(actuals)) if complete and len(actuals) == 1 else None,
        "net_profit": None,
        "tracking_started_at": min((row["started_at"] for row in rows), default=None),
        "note": "Token prices are estimates, not provider invoices. Failed, interrupted and SDK-retried calls may be charged. Historical calls and equipment/labor costs are not fully covered; do not calculate net profit.",
    }
