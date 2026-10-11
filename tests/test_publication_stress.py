"""Opt-in load and fault tests over real services, with isolated state only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from toutiao_content_factory.models import DailyTopicsReport, QualityReviewReport, TopicCandidate
from toutiao_content_factory.services import wechat_execution as execution
from toutiao_content_factory.services import wechat_retry as retry
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.generation_cost_service import GenerationCostAttempt, build_cost_summary
from toutiao_content_factory.services.revenue_experiment_service import RevenueExperimentService
from toutiao_content_factory.services.topic_collection import TopicCollectionService
from toutiao_content_factory.services.topic_pool_service import TopicPoolService
from toutiao_content_factory.services.wechat_revision import audit_fact_material, manuscript_fingerprint, valid_automatic_approval

pytestmark = pytest.mark.skipif(os.environ.get("RUN_PUBLICATION_STRESS") != "1", reason="opt-in stress suite")
SCALE = max(1, min(5, int(os.environ.get("PUBLICATION_STRESS_SCALE", "1"))))


def test_concurrent_cost_receipts_and_live_reads(tmp_path, record_property):
    count = 1000 * SCALE
    settings = SimpleNamespace(output_dir=tmp_path, account_key="stress", platform="test")
    stop = threading.Event()
    live = []
    def read():
        while not stop.wait(.01):
            live.append(build_cost_summary(tmp_path))
    def write(index):
        with GenerationCostAttempt(settings, None, kind="text", model="mock") as receipt:
            if index % 7 == 0:
                return
            receipt.complete({"id": f"req_{index}", "billing": {"request_id": f"req_{index}",
                "status": "charged", "currency": "CNY", "amount": .01}})
    reader = threading.Thread(target=read)
    reader.start()
    try:
        with ThreadPoolExecutor(max_workers=32) as pool:
            list(pool.map(write, range(count)))
    finally:
        stop.set()
        reader.join(timeout=10)
    assert not reader.is_alive()
    summary = build_cost_summary(tmp_path)
    assert summary["recorded_calls"] == count and summary["invalid_receipts"] == 0
    assert summary["verified_cost_calls"] == count - len(range(0, count, 7))
    assert summary["unverified_cost_calls"] == len(range(0, count, 7))
    assert summary["actual_total"] is None and summary["net_profit"] is None
    assert all(row["actual_total"] is None for row in live if row["invalid_receipts"])
    assert not list((tmp_path / "growth_ops/generation_costs/events").glob("*.tmp"))
    record_property("concurrent_receipts", count)
    record_property("writers", 32)
    record_property("live_reads", len(live))
    record_property("transient_receipt_read_errors", sum(row["invalid_receipts"] for row in live))


def test_shared_state_compare_and_swap_conflicts_stop(tmp_path, monkeypatch, record_property):
    from scripts import wechat_cloud_run as cloud
    count = 32
    barrier = threading.Barrier(count, timeout=20)
    lock = threading.Lock()
    store = {"sha": "before", "accepted": [], "puts": 0}
    monkeypatch.setenv("GITHUB_REPOSITORY", "stress/isolated")
    def transport(command, **kwargs):
        if "--method" not in command:
            if "/git/ref/" in command[-1]:
                return subprocess.CompletedProcess(command, 0, '{"object":{"sha":"head"}}', "")
            barrier.wait()
            return subprocess.CompletedProcess(command, 0, '{"sha":"before"}', "")
        payload = json.loads(kwargs["input"])
        with lock:
            store["puts"] += 1
            if payload["sha"] != store["sha"]:
                raise subprocess.CalledProcessError(1, command, stderr="409 conflict")
            store["sha"] = "after"
            store["accepted"].append(json.loads(base64.b64decode(payload["content"])))
        return subprocess.CompletedProcess(command, 0, "{}", "")
    monkeypatch.setattr(cloud.subprocess, "run", transport)
    def write(index):
        try:
            cloud.write_state_file({"owner": index}, retry.STATE_PATH, "stress only")
            return True
        except subprocess.CalledProcessError:
            return False
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(write, range(count)))
    assert sum(results) == 1 and len(store["accepted"]) == 1 and store["puts"] == count
    root = Path(__file__).resolve().parents[1]
    for name in ("wechat-daily-run.yml", "wechat-content-metrics.yml"):
        workflow = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "group: wechat-default" in workflow and "cancel-in-progress: false" in workflow
    record_property("simulated_shared_state_writers", count)
    record_property("successful_cas_writes", 1)


def test_seeded_budget_reservations_replays_and_cross_day_recovery(record_property):
    rng = random.Random(20261011)
    cases = 2000 * SCALE
    day, now = "2026-10-11", "2026-10-11T00:00:00+00:00"
    for case in range(cases):
        count = rng.randrange(4)
        used = rng.choice([0, 1, 4199, 4200, 4201, 5399, 5400, 6000])
        entry = {"runs": {str(i): {"status": "completed", "elapsed_seconds": used / count if count else 0}
                          for i in range(count)}, "dispatches": {}, "failed_inputs": []}
        state = {"schema_version": 1, "days": {day: entry}}
        before = deepcopy(state)
        remaining = retry.remaining(entry)
        reserved, result = retry.reserve(state, day, f"new-{case}", now)
        assert state == before
        expected = count < retry.MAX_RUNS and remaining >= retry.PREPARATION_MIN_SECONDS
        assert (result == "reserved") is expected
        if not expected:
            assert result == "daily_budget_exhausted" and reserved == state
            continue
        with pytest.raises(RuntimeError, match="Unresolved"):
            retry.reserve(reserved, "2026-10-12", "late", "2026-10-12T00:00:00+00:00")
        completed = retry.complete(reserved, day, f"new-{case}", rng.randrange(1800),
            {"status": "no_topics", "delivery_mode": "draft"}, {})
        replay, status = retry.reserve(completed, day, f"new-{case}", now)
        assert status == "duplicate_run" and replay == completed
        assert not retry.can_relay(completed["days"][day], {"status": "no_topics",
            "publication_attempted": False, "native_exit_code": 0}, 0)
    record_property("seeded_budget_cases", cases)


def test_atomic_phase_observation_and_stale_run_evidence(app_settings, monkeypatch, record_property):
    monkeypatch.setenv("WECHAT_EXECUTION_ID", "stress-current")
    started = time.time_ns()
    observations = []
    conflicts = []
    stop = threading.Event()
    def writer():
        for index in range(1000 * SCALE):
            try:
                execution.write_phase(app_settings, "preparing" if index < 500 * SCALE else "submitting", fingerprint=str(index))
            except PermissionError:
                assert os.name == "nt"
                conflicts.append(index)
        stop.set()
    def read():
        local = []
        while not stop.wait(.005):
            local.append(execution.phase_attempted(app_settings, started))
        return local
    with ThreadPoolExecutor(max_workers=5) as pool:
        readers = [pool.submit(read) for _ in range(4)]
        future = pool.submit(writer)
        try:
            future.result(timeout=60)
        finally:
            stop.set()
        for future in readers:
            rows = future.result()
            observations.extend(rows)
            if True in rows:
                assert False not in rows[rows.index(True):]
    execution.write_phase(app_settings, "submitting", fingerprint="final")
    assert execution.phase_attempted(app_settings, started) is True
    assert not list(app_settings.automation_dir.glob("wechat_publish_phase.json.*.tmp"))
    monkeypatch.setenv("WECHAT_EXECUTION_ID", "stress-other")
    assert execution.phase_attempted(app_settings, started) is None
    assert execution.draft_delivery_state(app_settings, started) == "uncertain"
    assert execution.phase_attempted(app_settings, time.time_ns() + 10**9) is None
    record_property("atomic_phase_writes", 1000 * SCALE)
    record_property("phase_reads", len(observations))
    record_property("bounded_phase_write_conflicts", len(conflicts))


def test_timeout_stops_multiple_real_nested_process_trees(tmp_path, record_property):
    count = 8 * SCALE
    def check(index):
        directory = tmp_path / str(index)
        directory.mkdir()
        effect, pids = directory / "late-effect", directory / "pids.json"
        grandchild = f"import pathlib,time; time.sleep(6); pathlib.Path({str(effect)!r}).write_text('late')"
        child = ("import json,os,pathlib,subprocess,sys,time; "
                 f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}]); "
                 f"pathlib.Path({str(pids)!r}).write_text(json.dumps([os.getpid(),p.pid])); time.sleep(30)")
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        with pytest.raises(subprocess.TimeoutExpired):
            execution.run_process_tree([sys.executable, "-c", parent], timeout=3)
        assert pids.is_file(), "Nested workers must start before the timeout; otherwise cleanup was not exercised"
        for pid in json.loads(pids.read_text()):
            try:
                assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                pass
        return effect
    with ThreadPoolExecutor(max_workers=4) as pool:
        effects = list(pool.map(check, range(count)))
    time.sleep(3.2)
    assert not any(path.exists() for path in effects)
    record_property("real_timed_out_process_trees", count)
    record_property("owned_descendants", count * 2)
    record_property("late_side_effects", 0)


def test_revenue_large_duplicate_conflict_and_hostile_numeric_batches(tmp_path, logger, record_property):
    service = RevenueExperimentService(tmp_path, logger)
    now = datetime(2026, 10, 11, 12)
    base = [{"day": (now - timedelta(days=i)).date().isoformat(), "total": 1.,
             "article": .6, "weitoutiao": .4, "video": 0., "pending": False} for i in range(1, 31)]
    rows = [dict(row) for row in base for _ in range(100 * SCALE)]
    rows += [dict(base[0], total=2.)]
    for value in (None, True, False, -1, "1.00", float("nan"), float("inf"), 10**400):
        rows.extend(dict(base[1], total=value) for _ in range(50 * SCALE))
    rows += [{"day": (now + timedelta(days=2)).isoformat(), "total": 900., "pending": False},
             {"day": now.isoformat(), "total": 1000., "pending": True}]
    for seed in range(20 * SCALE):
        random.Random(seed).shuffle(rows)
        result = service._income_summary(rows, now)
        assert result["settled_total"] == 29. and result["settled_days"] == 29
        assert result["conflicting_days"] == [base[0]["day"]] and not result["coverage_complete"]
        assert result["by_format"] == {"article": 17.4, "weitoutiao": 11.6, "video": 0.}
    overflow = [dict(row, total=1e308, article=1e308) for row in base]
    result = service._income_summary(overflow, now)
    assert result["settled_total"] is None and result["by_format"]["article"] is None
    json.dumps(result, allow_nan=False)
    record_property("revenue_rows_per_batch", len(rows))
    record_property("shuffled_revenue_batches", 20 * SCALE)


def test_replayed_material_pool_preserves_selection_and_stale_filter(app_settings, logger, record_property):
    service = TopicPoolService(app_settings, logger)
    count = 500 * SCALE
    now = datetime.now()
    topics = [TopicCandidate(title=f"机器人部署核验{index}", url=f"https://source.test/{index}", source="mock", source_type="rss",
        published_at=(now - timedelta(hours=48 if index % 2 else 0)).isoformat(),
        matched_industries=["humanoid_robot"], matched_keywords=["机器人"],
        evidence_strength=80, quality_score=80, snippet="真实材料的测试条件及部署边界。" * 60,
        source_records=[{"source_id": "source-1", "original_text": "真实材料的测试条件及部署边界。" * 60}]) for index in range(count)]
    report = DailyTopicsReport(now.isoformat(), 1, 24, ["humanoid_robot"], count, count, topics=topics)
    service.ingest_report(report)
    selected = service.list_pending_topics(limit=10)
    for topic in selected:
        service.mark_selected(topic)
    for _ in range(10 * SCALE):
        TopicPoolService(app_settings, logger).ingest_report(DailyTopicsReport.from_dict(report.to_dict()))
    pending = service.list_pending_topics(limit=count)
    assert len(service._load_rows()) == count and len(pending) == count // 2 - 10
    assert not {topic.url for topic in selected} & {topic.url for topic in pending}
    fresh = TopicCollectionService(app_settings, logger)._filter_by_freshness(pending, {"stale": 0, "missing_timestamp": 0})
    assert len(fresh) == len(pending)
    app_settings.platform = "wechat"
    object.__setattr__(app_settings.wechat, "enabled", True)
    object.__setattr__(app_settings.wechat, "auto_publish", True)
    app = ContentFactoryAppService(app_settings, logger)
    app._enrich_wechat_candidates(fresh)
    supply = json.loads((app_settings.automation_dir / "wechat_supply_report.json").read_text(encoding="utf-8"))
    assert supply["usable_material_count"] == 3 and supply["review_approved"] is False
    record_property("material_rows", count)
    record_property("material_cache_replays", 10 * SCALE)


def test_approval_cannot_survive_many_content_or_fact_mutations(record_property):
    from dataclasses import asdict
    fixture = json.loads((Path(__file__).parent / "fixtures/wechat_audit_20261008.json").read_text(encoding="utf-8"))
    article, title, data = fixture["article"], fixture["title"], fixture["data_report"]
    report = QualityReviewReport(fact_score=90, verdict="approved", chief_editor_conclusion="通过")
    report.audit_integrity = {"status": "complete", "fingerprint": manuscript_fingerprint(article, title, audit_fact_material(data)),
                              "primary_report": asdict(report)}
    assert valid_automatic_approval(report, article, title, data)
    for index in range(300 * SCALE):
        changed = deepcopy(data)
        changed["fact_evidence"][0]["claim"] = "变更事实" + str(index)
        assert not valid_automatic_approval(report, article, title, changed)
        changed = deepcopy(data)
        changed["source_records"][0]["original_text"] += str(index)
        assert not valid_automatic_approval(report, article, title, changed)
        assert not valid_automatic_approval(report, article + f"新增结论{index}。", title, data)
        assert not valid_automatic_approval(report, article, title + str(index), data)
    record_property("approval_binding_mutations", 1200 * SCALE)


def test_interrupted_material_cache_retains_previous_source_rows(app_settings, logger, monkeypatch):
    pool = TopicPoolService(app_settings, logger)
    old = [{"title": "previous material", "identity_key": "old", "source_records": [{"original_text": "original"}]}]
    pool._write_rows(old)
    original = type(pool.pool_path).write_text
    def interrupted(path, data, **kwargs):
        if path.name.startswith("topic_pool.jsonl"):
            original(path, data[:15], **kwargs)
            raise OSError("simulated interrupted cache write")
        return original(path, data, **kwargs)
    monkeypatch.setattr(type(pool.pool_path), "write_text", interrupted)
    with pytest.raises(OSError):
        pool._write_rows([{"title": "new material"}])
    assert pool._load_rows() == old
    assert not list(pool.pool_dir.glob("topic_pool.jsonl.*.tmp"))


def test_large_billing_and_platform_amounts_do_not_produce_infinite_reports(tmp_path, logger):
    settings = SimpleNamespace(output_dir=tmp_path, account_key="stress", platform="test")
    for index in range(3):
        with GenerationCostAttempt(settings, logger, kind="text", model="mock") as receipt:
            receipt.complete({"id": f"req_{index}", "billing": {"request_id": f"req_{index}",
                "status": "charged", "currency": "CNY", "amount": 1e308}})
            receipt.row.update(estimated_currency="CNY", estimated_amount=1e308)
    costs = build_cost_summary(tmp_path)
    assert costs["actual_total"] is None
    json.dumps(costs, allow_nan=False)
    service = RevenueExperimentService(tmp_path, logger)
    for value in (float("inf"), float("nan"), 10**400):
        dashboard = service.build_dashboard(account_snapshot={"revenue": {"total": value, "income": {"window_income": value}}},
            growth_snapshot={}, growth_feedback={}, performance={}, readiness={"status": "unknown"})
        assert dashboard["income"]["total"] is None
        json.dumps(dashboard, allow_nan=False)


def test_exhausted_phase_io_retry_blocks_and_cleans_temp_file(app_settings, monkeypatch):
    execution.write_phase(app_settings, "preparing", fingerprint="previous")
    original = type(app_settings.automation_dir).replace
    calls = []
    def occupied(path, target):
        if path.name.startswith("wechat_publish_phase.json."):
            calls.append(1)
            raise PermissionError("simulated occupied phase file")
        return original(path, target)
    monkeypatch.setattr(type(app_settings.automation_dir), "replace", occupied)
    with pytest.raises(PermissionError):
        execution.write_phase(app_settings, "submitting", fingerprint="new")
    assert len(calls) == (8 if os.name == "nt" else 1)
    assert json.loads((app_settings.automation_dir / "wechat_publish_phase.json").read_text())["fingerprint"] == "previous"
    assert not list(app_settings.automation_dir.glob("wechat_publish_phase.json.*.tmp"))
