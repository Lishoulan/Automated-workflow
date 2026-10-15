"""A bounded operating review cannot turn missing or incomparable data into growth."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest


NOW = datetime(2026, 10, 15, 20, tzinfo=timezone(timedelta(hours=8)))


def work(gid, created, impressions=100, reads=1):
    return {"gid": gid, "type": "weitoutiao", "title": "停车费争议" + gid,
            "created_at": created.isoformat(), "impressions": impressions, "reads": reads}


def sample(impressions=100, reads=1, delay=1):
    return {"impressions": impressions, "reads": reads, "capture_delay_hours": delay}


@pytest.fixture
def batch(tmp_path, monkeypatch):
    from toutiao_content_factory.services import reader_value_experiment as service
    monkeypatch.setattr(service, "_now", lambda: NOW)
    baseline = [work("base" + str(i), NOW - timedelta(days=10 - i)) for i in range(7)]
    snapshot = {"account": "robot_notes", "observed_at": (NOW - timedelta(minutes=1)).isoformat(), "works": baseline}
    checkpoints = {"works": {w["gid"]: {**w, "checkpoints": {"24h": sample(), "72h": sample()}} for w in baseline}}
    (tmp_path / "growth_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (tmp_path / "growth_checkpoints.json").write_text(json.dumps(checkpoints), encoding="utf-8")
    return service, tmp_path, snapshot, checkpoints


def add_new(snapshot, checkpoints, count, *, impressions=100, reads=3, delay=1):
    result = json.loads(json.dumps(snapshot))
    result["observed_at"] = (NOW + timedelta(days=8)).isoformat()
    for i in range(count):
        item = work("new" + str(i), NOW + timedelta(hours=i + 1), impressions, reads)
        result["works"].append(item)
        checkpoints["works"][item["gid"]] = {**item, "checkpoints": {
            "24h": sample(impressions, reads, delay), "72h": sample(impressions, reads, delay)}}
    return result


def state_path(root):
    return root / "growth_ops/performance/reader_value_batch.json"


def test_no_baseline_is_invented_when_no_real_snapshot_exists(tmp_path):
    from toutiao_content_factory.services.reader_value_experiment import ensure_batch
    assert ensure_batch(tmp_path, account="robot_notes")["status"] == "baseline_pending"
    assert not state_path(tmp_path).exists()


def test_baseline_is_frozen_and_repeated_collection_does_not_restart(batch):
    service, root, snapshot, checkpoints = batch
    initial = service.ensure_batch(root, account="robot_notes")
    before = json.loads(state_path(root).read_text(encoding="utf-8"))
    observed = add_new(snapshot, checkpoints, 3)
    first = service.update_batch(root, observed, checkpoints, account="robot_notes")
    again = service.update_batch(root, observed, checkpoints, account="robot_notes")
    after = json.loads(state_path(root).read_text(encoding="utf-8"))
    assert initial["status"] == "initialized"
    assert before["baseline"] == after["baseline"]
    assert before["started_at"] == after["started_at"]
    assert first["cohort_size"] == again["cohort_size"] == 3
    assert after["account"] == "robot_notes"


@pytest.mark.parametrize("kind", ["account", "strategy", "malformed"])
def test_existing_unknown_or_mismatched_state_is_never_reset(batch, kind):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    path = state_path(root)
    if kind == "malformed":
        path.write_text("{unfinished", encoding="utf-8")
    else:
        state = json.loads(path.read_text(encoding="utf-8"))
        state[kind] = "other"
        path.write_text(json.dumps(state), encoding="utf-8")
    before = path.read_bytes()
    assert service.ensure_batch(root, account="robot_notes")["status"] == "state_needs_review"
    assert service.update_batch(root, snapshot, checkpoints, account="robot_notes")["status"] == "state_needs_review"
    assert path.read_bytes() == before


def test_five_due_samples_prompt_review_and_seven_close_a_descriptive_batch(batch):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    five = service.update_batch(root, add_new(snapshot, checkpoints, 5), checkpoints, account="robot_notes")
    assert five["status"] == "review_due"
    assert five["windows"]["72h"]["verdict"] == "review_due"
    seven = service.update_batch(root, add_new(snapshot, checkpoints, 9), checkpoints, account="robot_notes")
    assert seven["cohort_size"] == 7 and len(seven["works"]) == 7
    assert seven["status"] == "completed" and seven["verdict"] == "improvement_signal"
    assert seven["windows"]["72h"]["read_rate_pct"] == 3
    assert seven["windows"]["72h"]["median_reads"] == 3
    assert seven["causal_claim"] is False and seven["automatic_switch"] is False


def test_a_higher_ratio_with_fewer_impressions_is_not_improvement(batch):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    observed = add_new(snapshot, checkpoints, 7, impressions=20, reads=3)
    result = service.update_batch(root, observed, checkpoints, account="robot_notes")
    assert result["windows"]["72h"]["read_rate_pct"] == 15
    assert result["verdict"] == "no_consistent_improvement"
    assert "复盘" in result["next_action"] and "未改善" in result["next_action"]


def test_late_captures_and_missing_windows_are_not_filled_or_mixed(batch):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    observed = add_new(snapshot, checkpoints, 7)
    checkpoints["works"]["new0"]["checkpoints"]["72h"]["capture_delay_hours"] = 25
    checkpoints["works"]["new1"]["checkpoints"].pop("72h")
    result = service.update_batch(root, observed, checkpoints, account="robot_notes")
    assert result["windows"]["24h"]["samples"] == 7
    assert result["windows"]["72h"]["samples"] == 5
    assert result["windows"]["72h"]["late_samples"] == 1
    assert result["windows"]["72h"]["verdict"] == "window_incomplete"
    assert result["verdict"] == "data_incomplete"


def test_no_baseline_window_does_not_prove_growth_from_zero(batch):
    service, root, snapshot, checkpoints = batch
    (root / "growth_checkpoints.json").write_text('{"works": {}}', encoding="utf-8")
    service.ensure_batch(root, account="robot_notes")
    result = service.update_batch(root, add_new(snapshot, checkpoints, 7), checkpoints, account="robot_notes")
    assert result["verdict"] == "data_incomplete"
    assert result["windows"]["72h"]["baseline"]["median_reads"] is None


def test_zero_exposure_is_visible_but_cannot_complete_a_comparable_batch(batch):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    observed = add_new(snapshot, checkpoints, 7)
    for label in ("24h", "72h"):
        checkpoints["works"]["new0"]["checkpoints"][label] = sample(0, 0)
    result = service.update_batch(root, observed, checkpoints, account="robot_notes")
    assert result["verdict"] == "data_incomplete"
    for window in result["windows"].values():
        assert window["samples"] == 6 and window["zero_exposure_samples"] == 1
        assert window["zero_exposure_gids"] == ["new0"]
        assert window["missing_samples"] == 0


def test_zero_exposure_baseline_cannot_prove_improvement(batch):
    service, root, snapshot, checkpoints = batch
    for entry in checkpoints["works"].values():
        for label in ("24h", "72h"):
            entry["checkpoints"][label] = sample(0, 0)
    (root / "growth_checkpoints.json").write_text(json.dumps(checkpoints), encoding="utf-8")
    service.ensure_batch(root, account="robot_notes")
    result = service.update_batch(root, add_new(snapshot, checkpoints, 7), checkpoints, account="robot_notes")
    assert result["verdict"] == "data_incomplete"
    for window in result["windows"].values():
        assert window["verdict"] == "baseline_unavailable"
        assert window["baseline"]["samples"] == 0
        assert window["baseline"]["zero_exposure_samples"] == 7
        assert window["baseline"]["read_rate_pct"] is None


def test_old_observations_edits_and_wrong_account_cannot_create_new_works(batch):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    current = add_new(snapshot, checkpoints, 2)
    service.update_batch(root, current, checkpoints, account="robot_notes")
    before = state_path(root).read_bytes()
    old = service.update_batch(root, snapshot, checkpoints, account="robot_notes")
    assert old["status"] == "stale_observation" and state_path(root).read_bytes() == before
    wrong = {**current, "account": "other"}
    assert service.update_batch(root, wrong, checkpoints, account="robot_notes")["status"] == "state_needs_review"
    current["works"][-1]["title"] = "edited title"
    result = service.update_batch(root, current, checkpoints, account="robot_notes")
    assert result["cohort_size"] == 2 and result["windows"]["24h"]["samples"] == 1


def test_parallel_baseline_initialization_has_one_immutable_owner(batch):
    service, root, _, _ = batch
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.ensure_batch(root, account="robot_notes"), range(16)))
    assert sum(row["status"] == "initialized" for row in results) == 1
    assert all(row["status"] in {"initialized", "observing"} for row in results)
    assert json.loads(state_path(root).read_text(encoding="utf-8"))["baseline"]["windows"]["72h"]["samples"] == 7


def test_future_checkpoint_or_another_works_checkpoint_cannot_pass(batch):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    observed = add_new(snapshot, checkpoints, 7)
    observed["observed_at"] = (NOW + timedelta(hours=10)).isoformat()
    early = service.update_batch(root, observed, checkpoints, account="robot_notes")
    assert early["windows"]["24h"]["samples"] == early["windows"]["72h"]["samples"] == 0
    observed["observed_at"] = (NOW + timedelta(days=8)).isoformat()
    checkpoints["works"]["new0"]["gid"] = "another-work"
    result = service.update_batch(root, observed, checkpoints, account="robot_notes")
    assert result["windows"]["72h"]["samples"] == 6
    assert result["verdict"] == "data_incomplete"


def test_concurrent_updates_keep_the_latest_snapshot_without_changing_baseline(batch):
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    baseline = json.loads(state_path(root).read_text(encoding="utf-8"))["baseline"]
    older = add_new(snapshot, checkpoints, 5)
    older["observed_at"] = (NOW + timedelta(days=7)).isoformat()
    latest = add_new(snapshot, checkpoints, 7)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: service.update_batch(root, value, checkpoints, account="robot_notes"),
                      [older, latest] * 8))
    state = json.loads(state_path(root).read_text(encoding="utf-8"))
    assert state["last_observed_at"] == latest["observed_at"]
    assert len(state["cohort"]) == 7 and state["baseline"] == baseline


def test_stale_initial_snapshot_and_conflicting_ids_never_seed_or_mutate(batch):
    service, root, snapshot, checkpoints = batch
    path = root / "growth_snapshot.json"
    old = {**snapshot, "observed_at": (NOW - timedelta(days=2)).isoformat()}
    path.write_text(json.dumps(old), encoding="utf-8")
    assert service.ensure_batch(root, account="robot_notes")["status"] == "baseline_pending"
    assert not state_path(root).exists()
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    service.ensure_batch(root, account="robot_notes")
    before = state_path(root).read_bytes()
    conflict = add_new(snapshot, checkpoints, 1)
    conflict["works"].append({**conflict["works"][-1], "title": "conflicting copy"})
    assert service.update_batch(root, conflict, checkpoints, account="robot_notes")["status"] == "state_needs_review"
    assert state_path(root).read_bytes() == before


def test_new_batch_is_visible_in_the_existing_feedback_and_revenue_actions(batch):
    from toutiao_content_factory.services.growth_feedback_service import build_growth_feedback, render_daily_growth_report
    from toutiao_content_factory.services.revenue_experiment_service import RevenueExperimentService
    from logging import getLogger
    service, root, snapshot, checkpoints = batch
    service.ensure_batch(root, account="robot_notes")
    observed = add_new(snapshot, checkpoints, 5)
    report = service.update_batch(root, observed, checkpoints, account="robot_notes")
    feedback = build_growth_feedback(observed, checkpoints, reader_value_batch=report)
    assert "5—7篇" in render_daily_growth_report(feedback)
    dashboard = RevenueExperimentService(root, getLogger("test"))._experiment_status(
        growth_snapshot=observed, growth_feedback=feedback, format_metrics={}, attribution={}, readiness={"status": "ready"})
    target = next(row for row in dashboard["targets"] if row["label"] == "微头条阅读率(%)")
    assert target["target"] == 2
    assert any("5—7篇" in action for action in dashboard["next_actions"])
    assert not any("达到30篇后再调整" in action for action in dashboard["next_actions"])
