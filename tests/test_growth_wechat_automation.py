from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wechat_cloud_run as cloud
from toutiao_content_factory.services import wechat_execution as execution
from toutiao_content_factory.services import wechat_retry as retry
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.content_guard_service import ContentGuardService
from toutiao_content_factory.services.growth_feedback_service import build_growth_feedback, opening_experiment_summary
from toutiao_content_factory.services.wechat_service import WechatPublishService
from toutiao_content_factory.models import TopicCandidate


def enable(settings):
    settings.platform = "wechat"
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


@pytest.mark.parametrize("remaining,expected", [(86, False), (1199, False), (1200, True), (1201, True)])
def test_preparation_admission_and_relay_have_the_same_budget(remaining, expected):
    now = datetime.now(timezone.utc).isoformat()
    day = retry.day_of(now)
    state = {"schema_version": 1, "days": {day: {"runs": {
        "previous": {"status": "completed", "elapsed_seconds": 5400 - remaining}},
        "dispatches": {}, "failed_inputs": []}}}
    normal = {"status": "review_required", "publication_attempted": False, "native_exit_code": 0}
    assert retry.can_relay(state["days"][day], normal, 0) is expected
    updated, status = retry.reserve(state, day, "new", now)
    assert (status == "reserved") is expected
    assert ("new" in updated["days"][day]["runs"]) is expected


@pytest.mark.parametrize("phase,identity,expected", [
    ("preparing", "current", False), ("submitting", "current", True),
    ("preparing", "old", None), ("submitting", "old", None), ("invalid", "current", None)])
def test_phase_is_bound_to_the_run(app_settings, monkeypatch, phase, identity, expected):
    monkeypatch.setenv("WECHAT_EXECUTION_ID", identity)
    started = time.time_ns() - 1_000_000_000
    execution.write_phase(app_settings, phase, fingerprint="bound-manuscript")
    monkeypatch.setenv("WECHAT_EXECUTION_ID", "current")
    assert execution.phase_attempted(app_settings, started) is expected
    payload = json.loads((app_settings.automation_dir / "wechat_publish_phase.json").read_text())
    assert payload["fingerprint"] == "bound-manuscript" and payload["recorded_at"]
    assert not list(app_settings.automation_dir.glob("*.tmp"))


def test_stale_or_corrupt_phase_cannot_prove_no_submission(app_settings, monkeypatch):
    monkeypatch.setenv("WECHAT_EXECUTION_ID", "current")
    execution.write_phase(app_settings, "preparing")
    path = app_settings.automation_dir / "wechat_publish_phase.json"
    os.utime(path, (1000, 1000))
    assert execution.phase_attempted(app_settings, time.time_ns()) is None
    path.write_text("{broken")
    assert execution.phase_attempted(app_settings, 0) is None


@pytest.mark.parametrize("remaining,allowed", [(599, False), (601, True)])
def test_submission_keeps_ten_minutes_separate_from_completion_reserve(monkeypatch, remaining, allowed):
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + remaining))
    if allowed:
        execution.require_submission_budget()
    else:
        with pytest.raises(RuntimeError, match="budget"):
            execution.require_submission_budget()


def test_generation_cannot_spend_the_submission_window(monkeypatch):
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + 1300))
    monkeypatch.setenv("WECHAT_GENERATION_DEADLINE_EPOCH", str(time.time() + 700))
    assert 699 < execution.generation_remaining() <= 700


def test_low_budget_stops_approved_publisher_before_network(app_settings, monkeypatch, logger):
    enable(app_settings)
    run = app_settings.output_dir
    publish = run / app_settings.review.publish_dirname
    publish.mkdir(parents=True)
    (publish / "publish_meta.json").write_text(json.dumps({"selected_title": "已审核的标题"}))
    (publish / "article.md").write_text("已审核的正文。", encoding="utf-8")
    (run / "review_status.json").write_text('{"status":"approved"}')
    from toutiao_content_factory.services import wechat_revision
    monkeypatch.setattr(wechat_revision, "assert_run_automatic_approval", MagicMock())
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", str(time.time() + 599))
    service = WechatPublishService(app_settings, logger)
    network = MagicMock(side_effect=AssertionError("No network permitted"))
    monkeypatch.setattr(service, "_get_access_token", network)
    with pytest.raises(RuntimeError, match="budget"):
        service.publish_run(run, submit=True)
    network.assert_not_called()
    assert not (app_settings.automation_dir / "wechat_publish_phase.json").exists()


def test_supplement_needs_explicit_top_level_arrays_even_with_no_findings():
    from toutiao_content_factory.models import QualityReviewReport
    from toutiao_content_factory.services.wechat_revision import apply_supplement
    report = QualityReviewReport(overall_score=90, verdict="needs_revision")
    with pytest.raises(ValueError, match="top-level"):
        apply_supplement(report, {"verdict": "approved", "chief_editor_conclusion": "核验完成"}, "正文", "标题", [])
    assert report.verdict == "needs_revision"


@pytest.mark.parametrize("phase,expected", [("preparing", False), ("submitting", True)])
def test_cloud_timeout_uses_submission_evidence(app_settings, monkeypatch, phase, expected):
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud, "save_session", MagicMock())
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw: {})
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.setattr(cloud.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0)))
    guard = MagicMock()
    monkeypatch.setattr(cloud, "record_publication", guard)
    def failed(command, **kwargs):
        execution.write_phase(app_settings, phase)
        raise subprocess.TimeoutExpired(command, 10)
    monkeypatch.setattr(execution, "run_process_tree", failed)
    assert cloud.main(["--mode", "publish"]) == 1
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text())
    assert report["publication_attempted"] is expected
    assert (report.get("wechat_status") == "publish_uncertain") is expected
    assert guard.call_count == int(expected)


def test_incomplete_process_shutdown_retains_uncertainty(app_settings, monkeypatch):
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud, "save_session", MagicMock())
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw: {})
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.setattr(cloud.subprocess, "run", MagicMock(return_value=MagicMock(returncode=0)))
    guard = MagicMock()
    monkeypatch.setattr(cloud, "record_publication", guard)
    monkeypatch.setattr(execution, "run_process_tree", MagicMock(side_effect=RuntimeError("shutdown unconfirmed")))
    assert cloud.main(["--mode", "publish"]) == 1
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text())
    assert report["publication_attempted"] is True and report["wechat_status"] == "publish_uncertain"
    guard.assert_called_once()


def test_timeout_stops_the_child_tree_before_it_can_write(tmp_path):
    effect = tmp_path / "late-effect"
    pid_file = tmp_path / "child-pid"
    child = "import time,pathlib; time.sleep(3); pathlib.Path(" + repr(str(effect)) + ").write_text('unsafe')"
    parent = ("import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,'-c',"
              + repr(child) + "]); pathlib.Path(" + repr(str(pid_file))
              + ").write_text(str(p.pid)); time.sleep(30)")
    with pytest.raises(subprocess.TimeoutExpired):
        execution.run_process_tree([sys.executable, "-c", parent], timeout=1)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    time.sleep(2.2)
    assert not effect.exists()


def test_wechat_source_export_preserves_every_original(app_settings, logger):
    enable(app_settings)
    app_settings.output_dir.mkdir(parents=True)
    service = TopicAutomationService(app_settings, logger)
    topic = TopicCandidate("真实产品部署", "https://example.test/report", "test", "rss")
    originals = ["第一段原文。\n\n第二段原文。" * 20, "另一报道原文。" * 40]
    topic.source_records = [{"original_text": t} for t in originals]
    path = service._capture_source_material(topic)
    text = path.read_text(encoding="utf-8")
    assert all(original in text for original in originals)
    from toutiao_content_factory.services.originality_service import source_copy_ratio
    assert source_copy_ratio("完全不同的分析和解释。" * 40, text)["ratio"] is not None
    topic.source_records = []
    topic.snippet = "无法证明是原文的摘要。" * 100
    assert service._capture_source_material(topic) is None
    assert not path.exists()


@pytest.mark.parametrize("wechat", [True, False])
def test_unknown_originality_is_explicitly_blocked_only_for_auto_wechat(app_settings, logger, wechat):
    if wechat:
        enable(app_settings)
    guard = ContentGuardService(app_settings, logger)
    app_settings.output_dir.mkdir(parents=True)
    guard.evaluate_run = MagicMock(return_value={"source_copy_ratio": None})
    guard._load_title_and_article = MagicMock(return_value=("", ""))
    report = guard.build_publish_guard_report(app_settings.output_dir)
    assert any("原创性" in item for item in report["blocking_flags"]) is wechat


def experiment_works(result_impressions=100, result_reads=3):
    works = {}
    for i in range(30):
        first = i < 15
        works[str(i)] = {"gid": str(i), "type": "weitoutiao", "title": "真实事件" + str(i),
            "content_experiment": {"id": "story_opening_v1", "variant": "result_first" if first else "situation_first"},
            "checkpoints": {"24h": {"impressions": result_impressions if first else 100,
                "reads": result_reads if first else 2, "capture_delay_hours": 0}}}
    return works


@pytest.mark.parametrize("impressions,reads,preferred", [(100, 3, "result_first"), (80, 2, None), (100, 2, None)])
def test_comparison_requires_reads_and_exposure_not_just_a_higher_rate(impressions, reads, preferred):
    report = opening_experiment_summary({"works": experiment_works(impressions, reads)})
    assert report["comparisons"][0]["preferred_variant"] == preferred
    assert report["comparisons"][0]["automatic_switch"] is False
    assert report["groups"][0]["median_reads"] == reads


def test_late_winner_and_stop_never_enter_recommendations():
    works = experiment_works()
    works["0"]["checkpoints"]["24h"].update(reads=99, impressions=1000, capture_delay_hours=100)
    works["1"]["checkpoints"] = {"72h": {"reads": 0, "impressions": 1, "capture_delay_hours": 100}}
    report = build_growth_feedback({}, {"works": works})
    assert len(report["late_observations"]) == 2
    assert all(item["gid"] not in {"0", "1"} for item in report["winners"] + report["stop_loss"])
    assert "真实事件0" not in report["topic_feedback"]["winning_titles"]
    assert "真实事件1" not in report["topic_feedback"]["stop_titles"]
    assert report["opening_experiment"]["comparisons"] == []


@pytest.mark.parametrize("actual_account,expired", [("小椰科技", False), ("另一个账号", False), ("小椰科技", True)])
def test_backend_inspection_checks_identity_and_closes_browser(app_settings, monkeypatch, actual_account, expired):
    from toutiao_content_factory.services.wechat_publication_verifier import inspect_backend
    import playwright.sync_api
    object.__setattr__(app_settings.wechat, "account_name", "小椰科技")
    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    page = browser.new_context.return_value.new_page.return_value
    page.evaluate.side_effect = [{"account": actual_account, "records": []}, 0]
    if expired:
        page.locator.return_value.first.wait_for.side_effect = RuntimeError("login expired")
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", MagicMock(return_value=manager))
    if actual_account != "小椰科技" or expired:
        with pytest.raises(RuntimeError):
            inspect_backend(app_settings)
    else:
        result = inspect_backend(app_settings)
        assert result["read_only"] and result["pending_count"] == 0 and result["account"] == "小椰科技"
    browser.close.assert_called_once()
    page.click.assert_not_called()


def test_cloud_check_inspects_backend_without_starting_generation(app_settings, monkeypatch):
    from toutiao_content_factory.services import wechat_publication_verifier
    path = app_settings.account_runtime_dir / "automation" / "browser_state" / "WeChat_storage_state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud, "save_session", MagicMock())
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw: {})
    child = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(cloud.subprocess, "run", child)
    inspector = MagicMock(return_value={"account": "小椰科技", "pending_count": 0, "read_only": True})
    monkeypatch.setattr(wechat_publication_verifier, "inspect_backend", inspector)
    generation = MagicMock(side_effect=AssertionError("Read-only check must not generate"))
    monkeypatch.setattr(execution, "run_process_tree", generation)
    assert cloud.main(["--mode", "check"]) == 0
    generation.assert_not_called()
    child.assert_called_once()
    inspector.assert_called_once_with(app_settings)
    result = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text())
    assert result["backend_snapshot"]["read_only"] and result["publication_requested"] is False
