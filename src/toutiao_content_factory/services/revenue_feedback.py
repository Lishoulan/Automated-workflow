"""Bounded, source-first selection using compatible observed content cohorts.

No model or network calls. Missing settlement/competition is never a zero return.
This policy ranks candidates; the existing fact and publication gates still own
whether material can be used at all.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import math
from statistics import median

from toutiao_content_factory.services.topic_selection_evidence import selection_evidence
from toutiao_content_factory.services.reader_value_service import story_reader_fit

SELECTION_PROTOCOL = "settled_revenue_v1"
MIN_SAMPLES = 15
MIN_IMPRESSIONS = 1000
MIN_REVENUE_READS = 1000
BEIJING = timezone(timedelta(hours=8))


def adaptive_selection_enabled(settings):
    config = getattr(settings, "revenue_optimization", None)
    return bool(getattr(config, "enabled", False) and getattr(config, "adaptive_topic_selection", False)
                and getattr(settings, "platform", "") != "wechat")


def _time(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timezone required")
    return stamp


def topic_category(topic):
    explicit = str(getattr(topic, "topic_bucket", "") or "").strip()
    if explicit and explicit not in {"general", "hot_topic", "social_story", "unclassified"}:
        return explicit[:80]
    fit = story_reader_fit(topic)
    if fit["tracks"]:
        return fit["tracks"][0]
    tags = [str(t).strip() for t in getattr(topic, "matched_industries", [])
            if str(t).strip() not in {"general", "hot_topic", "social_story", ""}]
    return tags[0][:80] if tags else "unclassified"


def _settled_amount(entry, sample):
    amount = sample.get("platform_revenue")
    try:
        valid = type(amount) in (int, float) and math.isfinite(amount) and amount >= 0
    except OverflowError:
        valid = False
    # Date equality cannot align intraday traffic with full-day income. This
    # contract requires the provider's exact counter cutoff, scope and work ID.
    if (valid and sample.get("revenue_amount_known") is True and sample.get("revenue_settled") is True
            and sample.get("revenue_gid") == entry.get("gid")
            and sample.get("revenue_scope") == "checkpoint_cumulative"
            and sample.get("revenue_source") == "/mp/agw/statistic/v2/toutiao_income/article_income"
            and sample.get("revenue_counters_captured_at") == sample.get("captured_at")):
        return float(amount)
    return None


def build_revenue_feedback(snapshot, checkpoints):
    # Local import avoids growth_feedback -> reader_value -> growth_feedback
    # initialization cycles while sharing the established timestamp validation.
    from toutiao_content_factory.services.reader_value_experiment import qualified_checkpoint_sample

    groups = defaultdict(list)
    excluded = 0
    try:
        observed = _time(snapshot.get("observed_at"))
    except (ValueError, TypeError):
        return {"selection_protocol": SELECTION_PROTOCOL, "status": "unknown", "groups": [], "reason": "invalid_observation"}
    works = snapshot.get("works", [])
    ids = [str(w.get("gid", "")) for w in works if isinstance(w, dict)]
    duplicates = {gid for gid, count in Counter(ids).items() if count > 1}
    ledger = checkpoints.get("works", {}) if isinstance(checkpoints, dict) else {}
    for work in works:
        if not isinstance(work, dict):
            continue
        gid = str(work.get("gid") or "")
        entry = ledger.get(gid, {}) if isinstance(ledger, dict) else {}
        experiment = entry.get("content_experiment", {})
        if (not gid or gid in duplicates or not isinstance(experiment, dict)
                or experiment != work.get("content_experiment")
                or (experiment.get("id"), experiment.get("protocol_version")) != (
                    ("story_opening_v1", 4) if work.get("type") == "weitoutiao" else ("article_title_v1", 1))
                or experiment.get("selection_protocol") != SELECTION_PROTOCOL
                or not isinstance(experiment.get("variant"), str) or not experiment["variant"]
                or work.get("type") == "weitoutiao" and experiment["variant"] not in {"result_first", "situation_first"}
                or not isinstance(experiment.get("category"), str) or not experiment["category"].strip()
                or work.get("type") not in {"article", "weitoutiao"} or entry.get("type") != work.get("type")):
            excluded += 1
            continue
        for label in ("24h", "72h"):
            try:
                if not 0 <= (observed - _time(work["created_at"])).total_seconds() <= 30 * 86400:
                    continue
                sample = qualified_checkpoint_sample(entry, label, work=work, observed=observed)
                if not sample or not sample.get("captured_at") or sample["impressions"] <= 0:
                    continue
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            key = (work["type"], experiment["category"], experiment["id"], experiment["protocol_version"],
                   experiment["selection_protocol"], experiment["variant"], label)
            groups[key].append((gid, sample, _settled_amount(entry, sample)))
    result = []
    for key, rows in sorted(groups.items()):
        impressions = sum(s["impressions"] for _, s, _ in rows)
        reads = sum(s["reads"] for _, s, _ in rows)
        amounts = [amount for _, _, amount in rows if amount is not None]
        complete = len(amounts) == len(rows) and math.isfinite(sum(amounts))
        revenue = round(sum(amounts), 4) if complete else None
        result.append(dict(zip(("format", "category", "experiment_id", "protocol_version", "selection_protocol", "variant", "checkpoint"), key),
            samples=len(rows), gids=[gid for gid, _, _ in rows], impressions=impressions, reads=reads,
            read_rate_pct=round(reads * 100 / impressions, 4), median_reads=median([s["reads"] for _, s, _ in rows]),
            median_impressions=median([s["impressions"] for _, s, _ in rows]),
            comments=sum(s.get("comments", 0) for _, s, _ in rows) if all(type(s.get("comments")) is int and s["comments"] >= 0 for _, s, _ in rows) else None,
            exact_income_samples=len(amounts), settled_revenue=revenue,
            median_revenue=median(amounts) if complete else None,
            revenue_per_1000_reads=round(revenue * 1000 / reads, 4) if complete and reads else None,
            paying_works=sum(amount > 0 for amount in amounts), priority_delta=0,
            status="comparable" if len(rows) >= MIN_SAMPLES and impressions >= MIN_IMPRESSIONS else "collecting"))
    contexts = defaultdict(list)
    for group in result:
        if group["status"] == "comparable" and group["category"] != "unclassified":
            context = tuple(group[k] for k in ("format", "experiment_id", "protocol_version", "selection_protocol", "variant", "checkpoint"))
            contexts[context].append(group)
    comparisons = []
    for context, peers in contexts.items():
        for group in peers:
            votes = []
            for other in peers:
                if group is other:
                    continue
                direction = 0
                # Higher rate alone cannot win by losing exposure or reads.
                if (group["read_rate_pct"] > other["read_rate_pct"] and group["median_reads"] > other["median_reads"]
                        and group["median_impressions"] >= other["median_impressions"]):
                    direction = 5
                if (group["read_rate_pct"] < other["read_rate_pct"] and group["median_reads"] < other["median_reads"]
                        and group["median_impressions"] <= other["median_impressions"]):
                    direction = -5
                money_ready = all(g["settled_revenue"] is not None and g["reads"] >= MIN_REVENUE_READS for g in (group, other))
                if money_ready:
                    if (group["paying_works"] >= 3 and group["revenue_per_1000_reads"] > other["revenue_per_1000_reads"]
                            and group["median_revenue"] > other["median_revenue"] and group["median_reads"] >= other["median_reads"]):
                        direction = 8
                    elif (other["paying_works"] >= 3 and group["revenue_per_1000_reads"] < other["revenue_per_1000_reads"]
                            and group["median_revenue"] < other["median_revenue"] and group["median_reads"] <= other["median_reads"]):
                        direction = -8
                votes.append(direction)
            if votes and (all(v > 0 for v in votes) or all(v < 0 for v in votes)):
                group["priority_delta"] = min(votes, key=abs)
        if len(peers) >= 2:
            comparisons.append({"context": list(context), "categories": [g["category"] for g in peers],
                                "basis": "descriptive_observation", "causal_claim": False})
    return {"selection_protocol": SELECTION_PROTOCOL, "observed_at": observed.isoformat(),
        "account": snapshot.get("account", ""), "status": "comparable" if comparisons else "collecting",
        "excluded_unassigned_or_incompatible_works": excluded, "groups": result, "comparisons": comparisons,
        "thresholds": {"samples": MIN_SAMPLES, "impressions": MIN_IMPRESSIONS, "settled_reads": MIN_REVENUE_READS},
        "note": "按体裁、类别、策略、协议、开头及24/72h分组；未知收益不等于零，不自动停产任何领域。"}


def rank_revenue_topics(topics, settings, feedback, *, content_type, now=None):
    now = now or datetime.now(BEIJING)
    section = feedback.get("revenue_feedback", {}) if isinstance(feedback, dict) else {}
    try:
        fresh = (section.get("account") == settings.account_key and section.get("selection_protocol") == SELECTION_PROTOCOL
                 and 0 <= (now - _time(section.get("observed_at"))).total_seconds() <= 36 * 3600)
    except (ValueError, TypeError):
        fresh = False
    for topic in topics:
        category = topic_category(topic)
        groups = [g for g in section.get("groups", []) if fresh and g.get("format") == content_type
                  and g.get("category") == category and g.get("selection_protocol") == SELECTION_PROTOCOL
                  and g.get("protocol_version") == (4 if content_type == "weitoutiao" else 1) and g.get("status") == "comparable"
                  and g.get("samples", 0) >= MIN_SAMPLES and g.get("impressions", 0) >= MIN_IMPRESSIONS]
        mature = [g for g in groups if g["checkpoint"] == "72h"]
        groups = mature or [g for g in groups if g["checkpoint"] == "24h"]
        deltas = [g["priority_delta"] for g in groups]
        delta = min(deltas, key=abs) if deltas and (all(d > 0 for d in deltas) or all(d < 0 for d in deltas)) else 0
        try:
            age = (now - _time(topic.published_at)).total_seconds() / 3600
            freshness = max(0, 15 - age / 2) if age >= 0 else 0
        except (ValueError, TypeError):
            freshness = 0
        fit = story_reader_fit(topic)
        components = {"freshness": round(freshness, 2), "audience_fit": 8 if fit["eligible"] else 0,
            "interest_proxy": min(8, max(0, topic.heat_score) / 10),
            "evidence": min(20, max(0, topic.evidence_strength) / 5),
            "quality": min(20, max(0, topic.quality_score) / 5),
            "editorial_prior": min(10, max(0, topic.total_score) / 10)}
        topic.revenue_selection = {"selection_protocol": SELECTION_PROTOCOL, "category": category,
            "format": content_type, "score": round(sum(components.values()) + delta, 3), "components": components,
            "competition": None, "competition_reason": "no_verified_market_measurement",
            "historical_delta": delta, "historical_gids": sorted({gid for g in groups for gid in g["gids"]}),
            "settled_rpr": [g["revenue_per_1000_reads"] for g in groups],
            "feedback_status": "current" if fresh else "missing_stale_or_wrong_account",
            "approval": False}
    return sorted(topics, key=lambda t: (selection_evidence(t)["priority"], -t.revenue_selection["score"],
                                        t.rank if t.rank > 0 else 10_000))
