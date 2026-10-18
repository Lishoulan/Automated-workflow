"""Prospective work identity, money evidence and comparable learning contracts."""
from copy import deepcopy
from datetime import datetime, timedelta
import logging
import json
from types import SimpleNamespace

import pytest

from scripts.collect_growth_snapshot import attach_opening_experiments, update_checkpoints
from toutiao_content_factory.services.content_income_service import ARTICLE_INCOME_PATH
from toutiao_content_factory.services.generation_cost_service import (
    BEIJING, GenerationCostAttempt, build_cost_summary, content_cost_scope,
)
from toutiao_content_factory.services.revenue_experiment_service import RevenueExperimentService


START = datetime(2026, 10, 18, tzinfo=BEIJING)
NOW = START + timedelta(days=7)
TRIAL = {"id": "robot_notes_profit_20261018", "started_at": START.isoformat(),
         "ends_at": (START + timedelta(days=30)).isoformat(),
         "directions": ["consumer_practical", "technology_everyday"],
         "body_structure": "facts_distinction_boundary_v1"}


def cohort(now=NOW):
    created = now - timedelta(hours=96)
    exp = {"id": "story_opening_v1", "protocol_version": 4, "variable": "opening_order",
           "selection_protocol": "settled_revenue_v1", "category": "consumer_practical",
           "variant": "result_first", "trial_id": TRIAL["id"], "body_structure": TRIAL["body_structure"]}
    work = {"gid": "1879000001", "type": "weitoutiao", "title": "法院支持原合同部分，调整后的费用为何未获支持？",
            "created_at": created.isoformat(), "age_hours": 96, "impressions": 2000,
            "reads": 30, "comments": 2, "content_experiment": exp,
            "content_lineage": {"run_key": "run_a", "content_attempt_id": "a" * 32,
                                "topic_title": "格式条款案例", "source_url": "https://court.gov.cn/case"}}
    entry = deepcopy(work)
    entry["checkpoints"] = {label: {"captured_at": (created + timedelta(hours=hours)).isoformat(),
        "capture_delay_hours": 0, "impressions": 1000, "reads": 20, "comments": 1}
        for label, hours in (("24h", 24), ("72h", 72))}
    return {"account": "robot_notes", "observed_at": now.isoformat(), "operating_trial": TRIAL,
            "works": [work]}, {"works": {work["gid"]: entry}}


def report(tmp_path, snapshot, checkpoints):
    service = RevenueExperimentService(tmp_path, logging.getLogger("operating-ledger"))
    method = getattr(service, "build_operating_report", None)
    assert callable(method), "Existing revenue report must expose the prospective work ledger"
    return method(snapshot, checkpoints)


def cost(tmp_path, attempt="a" * 32, *, billed=True, failed=False, stamp=START + timedelta(hours=4)):
    settings = SimpleNamespace(output_dir=tmp_path, account_key="robot_notes", platform="toutiao")
    with content_cost_scope({"account_key": "robot_notes", "content_attempt_id": attempt, "trial_id": TRIAL["id"]}):
        with GenerationCostAttempt(settings, None, kind="text", model="existing", sdk_retries=0) as receipt:
            receipt.row["started_at"] = stamp.isoformat()
            response = {"id": "req_" + receipt.row["event_id"]}
            if billed:
                response["billing"] = {"request_id": response["id"], "status": "charged", "amount": .02, "currency": "CNY"}
            receipt.complete(response)
            if failed:
                receipt.row["status"] = "failed"
    return receipt


def income(work, **changes):
    work.update(platform_revenue=.1, platform_revenue_status="available", revenue_amount_known=True,
        revenue_gid=work["gid"], revenue_source=ARTICLE_INCOME_PATH,
        revenue_scope="cumulative_through_ready_date", revenue_as_of_date="2026-10-24", **changes)


def test_unique_submission_join_carries_lineage_without_relabeling_captured_identity():
    snapshot, checkpoints = cohort()
    work = snapshot["works"][0]
    exp, lineage = work.pop("content_experiment"), work.pop("content_lineage")
    record = {**exp, **lineage, "title": "package title is clipped", "submitted_opening": work["title"],
              "submitted_at": work["created_at"], "status": "submitted_for_review"}
    attach_opening_experiments(snapshot, {"records": {"run_a": record}})
    assert work["content_experiment"] == exp
    assert work["content_lineage"] == lineage
    changed = deepcopy(snapshot)
    changed["works"][0]["content_lineage"]["content_attempt_id"] = "b" * 32
    persisted, _ = update_checkpoints(changed, checkpoints, NOW)
    assert persisted["works"][work["gid"]]["content_lineage"] == lineage
    ambiguous = deepcopy(snapshot)
    ambiguous["works"][0].pop("content_lineage")
    ambiguous["works"][0].pop("content_experiment")
    attach_opening_experiments(ambiguous, {"records": {"a": record, "b": record}})
    assert "content_lineage" not in ambiguous["works"][0]


def test_exact_work_income_and_cost_are_visible_without_claiming_full_profit(tmp_path):
    snapshot, checkpoints = cohort()
    income(snapshot["works"][0])
    receipt = cost(tmp_path)
    result = report(tmp_path, snapshot, checkpoints)
    row = result["works"][0]
    assert row["gid"] == snapshot["works"][0]["gid"]
    assert row["topic_title"] == "格式条款案例" and row["source_url"].startswith("https://court")
    assert row["checkpoints"]["24h"]["reads"] == 20
    assert row["income"]["amount"] == .1 and row["income"]["as_of_date"] == "2026-10-24"
    assert row["generation_costs"]["event_ids"] == [receipt.row["event_id"]]
    assert row["verified_direct_contribution"] == .08
    assert row["net_profit"] is None and result["net_profit"] is None


@pytest.mark.parametrize("changes", [
    {"revenue_gid": "another"}, {"revenue_source": "account_daily_income"},
    {"revenue_as_of_date": "2026-10-25"}, {"revenue_as_of_date": "2026-10-20"},
    {"revenue_amount_known": False}, {"revenue_scope": "estimated_allocation"},
])
def test_stale_mismatched_or_account_allocated_money_remains_unknown(tmp_path, changes):
    snapshot, checkpoints = cohort()
    income(snapshot["works"][0])
    snapshot["works"][0].update(changes)
    cost(tmp_path)
    row = report(tmp_path, snapshot, checkpoints)["works"][0]
    assert row["income"]["amount"] is None and row["verified_direct_contribution"] is None


def test_no_retroactive_enrollment_ambiguous_costs_or_late_checkpoint_learning(tmp_path):
    snapshot, checkpoints = cohort()
    cost(tmp_path, billed=False)
    historical = deepcopy(snapshot["works"][0])
    historical["gid"] = "old"
    historical["content_experiment"].pop("trial_id")
    snapshot["works"].append(historical)
    entry = checkpoints["works"][snapshot["works"][0]["gid"]]
    entry["checkpoints"]["24h"].update(capture_delay_hours=13, captured_at=(NOW - timedelta(hours=59)).isoformat())
    entry["checkpoints"]["72h"].update(capture_delay_hours=-1)
    result = report(tmp_path, snapshot, checkpoints)
    assert len(result["works"]) == 1
    row = result["works"][0]
    assert row["checkpoints"] == {"24h": None, "72h": None}
    assert row["generation_costs"]["actual_total"] is None
    assert result["weekly_review"]["opening_recommendations"] == []
    duplicate = deepcopy(snapshot["works"][0])
    duplicate["gid"] = "new_but_same_attempt"
    snapshot["works"].append(duplicate)
    checkpoints["works"][duplicate["gid"]] = {**deepcopy(entry), "gid": duplicate["gid"]}
    result = report(tmp_path, snapshot, checkpoints)
    assert all(w["generation_costs"]["actual_total"] is None for w in result["works"])
    assert all(w["cost_link_status"] == "ambiguous_attempt" for w in result["works"])


def test_changed_capture_lineage_cannot_join_or_enter_feedback(tmp_path):
    snapshot, checkpoints = cohort()
    snapshot["works"][0]["content_lineage"]["content_attempt_id"] = "b" * 32
    row = report(tmp_path, snapshot, checkpoints)["works"][0]
    assert row["cost_link_status"] == "capture_identity_changed"
    assert row["checkpoints"] == {"24h": None, "72h": None}


def test_failed_unpublished_candidate_cost_is_retained_without_allocating_to_a_work(tmp_path):
    snapshot, checkpoints = cohort()
    cost(tmp_path)
    failed = cost(tmp_path, attempt="b" * 32, failed=True)
    result = report(tmp_path, snapshot, checkpoints)
    assert result["generation_costs"]["actual_total"] == .04
    assert result["unallocated_cost_event_ids"] == [failed.row["event_id"]]
    assert result["works"][0]["generation_costs"]["actual_total"] == .02


def test_fixed_trial_cost_window_does_not_lose_first_day_at_closing(tmp_path):
    receipt = cost(tmp_path)
    result = build_cost_summary(tmp_path, now=START + timedelta(days=33), since=START,
                                until=START + timedelta(days=30), account_key="robot_notes")
    assert result["event_ids"] == [receipt.row["event_id"]] and result["actual_total"] == .02
    snapshot, checkpoints = cohort(START + timedelta(days=33))
    result = report(tmp_path, snapshot, checkpoints)
    assert result["generation_costs"]["actual_total"] == .02
    assert result["status"] == "closing_review"
    assert set(result["closing_answers"]) == {"readership", "writing", "settled_income", "net_value", "decision"}
    assert result["decision"] == "do_not_expand_unproven"


def test_trial_work_survives_rolling_platform_feed_with_original_counter_and_income_dates(tmp_path):
    snapshot, checkpoints = cohort()
    income(snapshot["works"][0])
    stored, _ = update_checkpoints(snapshot, checkpoints, NOW)
    later = {**snapshot, "observed_at": (START + timedelta(days=33)).isoformat(), "works": []}
    result = report(tmp_path, later, stored)
    row = result["works"][0]
    assert row["gid"] == snapshot["works"][0]["gid"]
    assert row["counters_observed_at"] == NOW.isoformat()
    assert row["income"]["as_of_date"] == "2026-10-24"
    assert row["checkpoints"]["72h"]["reads"] == 20


def test_weekly_opening_recommendation_needs_comparable_reads_and_exposure(tmp_path):
    snapshot, checkpoints = cohort()
    base = snapshot["works"].pop()
    entry = checkpoints["works"].pop(base["gid"])
    for variant, reads in (("result_first", 40), ("situation_first", 20)):
        for n in range(15):
            work = deepcopy(base)
            work["gid"] = f"{variant}{n}"
            work["content_experiment"]["variant"] = variant
            snapshot["works"].append(work)
            captured = deepcopy(entry)
            captured.update(gid=work["gid"], content_experiment=deepcopy(work["content_experiment"]))
            for sample in captured["checkpoints"].values():
                sample["reads"] = reads
            checkpoints["works"][work["gid"]] = captured
    result = report(tmp_path, snapshot, checkpoints)
    recommendation = result["weekly_review"]["opening_recommendations"][0]
    assert recommendation["preferred_variant"] == "result_first" and recommendation["checkpoint"] == "72h"
    assert recommendation["causal_claim"] is False and recommendation["profitability_verified"] is False
    # Improved rate obtained only by sacrificing exposure cannot win.
    for entry in checkpoints["works"].values():
        if entry["content_experiment"]["variant"] == "result_first":
            for sample in entry["checkpoints"].values():
                sample["impressions"] = 500
    assert report(tmp_path, snapshot, checkpoints)["weekly_review"]["opening_recommendations"] == []


def test_production_opening_uses_only_fresh_same_trial_weekly_evidence(app_settings, monkeypatch):
    from toutiao_content_factory.services import weitoutiao_generator as module
    from toutiao_content_factory.config import RevenueOptimizationSettings

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    app_settings.account_key = "robot_notes"
    app_settings.revenue_optimization = RevenueOptimizationSettings.from_dict(
        {"adaptive_topic_selection": True, "operating_trial": TRIAL})
    path = app_settings.output_dir / "growth_ops/revenue_experiment/revenue_30d_dashboard.json"
    path.parent.mkdir(parents=True)
    operating = {"account": "robot_notes", "trial_id": TRIAL["id"], "body_structure": TRIAL["body_structure"],
        "observed_at": NOW.isoformat(), "weekly_review": {"review_day": 7, "evidence_cutoff": NOW.isoformat(),
        "opening_recommendations": [{"category": "consumer_practical", "checkpoint": "72h", "preferred_variant": "situation_first",
            "gids": [f"preferred{n}" for n in range(15)], "comparison_gids": [f"other{n}" for n in range(15)]}]}}
    path.write_text(json.dumps({"operating_trial": operating}), encoding="utf-8")
    generator = module.WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.opening"))
    kwargs = {"category": "consumer_practical", "trial_id": TRIAL["id"], "body_structure": TRIAL["body_structure"]}
    assert generator._opening_experiment(**kwargs)["variant"] == "situation_first"
    for changes in ({"trial_id": "another"}, {"body_structure": "old"}, {"account": "another"},
                    {"observed_at": (NOW - timedelta(hours=37)).isoformat()}):
        path.write_text(json.dumps({"operating_trial": {**operating, **changes}}), encoding="utf-8")
        assert generator._opening_experiment(**kwargs)["variant"] == "result_first"
