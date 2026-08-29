from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any


TOUTIAO_PLATFORMS = frozenset({"toutiao", "weitoutiao", "toutiao_weitoutiao"})
_AVAILABILITY_FIELDS = ("availability_status", "snapshot_status")
_REVENUE_FIELDS = ("revenue_status", "platform_revenue_status")


def parse_policy_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.date()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_value(sources: tuple[Mapping[str, Any], ...], *fields: str) -> object:
    for source in sources:
        for field in fields:
            value = source.get(field)
            if value is not None and str(value).strip():
                return value
    return None


def infer_availability_status(
    record: Mapping[str, Any],
    *,
    observed_date: date | str | None = None,
    earnings_date: date | str | None = None,
) -> str:
    """Infer T+1 availability while remaining compatible with old rows.

    New rows carry explicit statuses. Legacy rows may only have publication and
    earnings dates, so date semantics must take precedence over a stale
    ``available`` default for a same-day Toutiao article.
    """
    metrics = _mapping(record.get("metrics_latest"))
    economics = _mapping(record.get("unit_economics"))
    sources = (metrics, economics, record)
    availability_values = {
        str(source.get(field) or "").strip().lower()
        for source in sources
        for field in _AVAILABILITY_FIELDS
    }
    explicit_availability = str(
        _first_value(sources, *_AVAILABILITY_FIELDS) or ""
    ).strip().lower()
    explicit_revenue_statuses = {
        str(source.get(field) or "").strip().lower()
        for source in sources
        for field in _REVENUE_FIELDS
    }

    if "pending_t1" in availability_values:
        return "pending_t1"
    if "not_available" in availability_values:
        return "not_available"
    if "pending_t1" in explicit_revenue_statuses:
        return "pending_t1"

    platform = str(_first_value(sources, "platform") or "toutiao").strip().lower()
    published = parse_policy_date(
        _first_value(
            sources,
            "published_date",
            "published_at",
            "publish_date",
            "publish_time",
            "published_time",
        )
    )
    if published is None:
        return explicit_availability or "available"

    resolved_observed = parse_policy_date(observed_date) or parse_policy_date(
        _first_value(sources, "collection_date", "observed_at", "snapshot_at", "updated_at")
    )
    resolved_observed = resolved_observed or date.today()
    if published > resolved_observed:
        return "not_available"

    if platform not in TOUTIAO_PLATFORMS:
        return explicit_availability or "available"

    resolved_earnings = parse_policy_date(earnings_date) or parse_policy_date(
        _first_value(sources, "earnings_date", "data_date")
    )
    if resolved_earnings is None:
        collection_date = parse_policy_date(
            _first_value(sources, "collection_date", "observed_at", "snapshot_at")
        )
        resolved_earnings = (collection_date or resolved_observed) - timedelta(days=1)
    if published > resolved_earnings:
        return "pending_t1"

    return explicit_availability or "available"


def is_pending_t1(
    record: Mapping[str, Any],
    *,
    observed_date: date | str | None = None,
    earnings_date: date | str | None = None,
) -> bool:
    return infer_availability_status(
        record,
        observed_date=observed_date,
        earnings_date=earnings_date,
    ) == "pending_t1"
