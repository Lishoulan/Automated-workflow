from __future__ import annotations

import json
import os
import sys
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from toutiao_content_factory.services import wechat_metrics as metrics, wechat_retry as retry
from toutiao_content_factory.services.review_service import ReviewWorkflowService, ReviewBundle
from toutiao_content_factory.services.wechat_service import WechatPublishService
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.wechat_execution import write_phase, phase_attempted
from toutiao_content_factory.services.wechat_execution import draft_delivery_state

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import wechat_cloud_run as cloud
import check_wechat_cover as cover_check


@pytest.mark.parametrize("api_succeeded", [True, False])
def test_cover_check_never_writes_delivery_history(app_settings, monkeypatch, api_succeeded):
    monkeypatch.delenv("WECHAT_FREE_COVER_ONLY", raising=False)
    monkeypatch.setattr(cover_check, "load_settings", lambda *a, **k: app_settings)
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", "0")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    history = app_settings.output_dir / "wechat_publish_history.json"
    history.parent.mkdir(parents=True)
    history.write_text('[{"app_msg_id":"existing-draft"}]')
    before = history.read_bytes()
    class FakeImages:
        def __init__(self, settings, logger):
            assert settings.platform == "wechat"
        def generate_for_run(self, run, include_infographics):
            assert include_infographics is False
            report = run / "publish/image_generation_result.json"
            report.write_text(json.dumps({"api_succeeded": api_succeeded,
                "model": "gpt-image-2", "cover_style": "warm_editorial"}))
            return {"image_generation_result": report, "cover_image": run / "images/cover.png"}
    monkeypatch.setattr(cover_check, "ImageGenerationService", FakeImages)
    assert cover_check.main() == (0 if api_succeeded else 1)
    result = json.loads((app_settings.automation_dir / "wechat_cover_check/result.json").read_text(encoding="utf-8"))
    assert result["status"] == ("success" if api_succeeded else "failed")
    assert result["publication_attempted"] is False and result["draft_saving_attempted"] is False
    assert history.read_bytes() == before


def enable(settings):
    settings.platform = "wechat"
    settings.wechat = replace(settings.wechat, enabled=True, auto_publish=True, draft_only=True)
    settings.ensure_directories()


@pytest.mark.parametrize("balance", [0, 5])
def test_relay_balance_is_checked_before_paid_cover(app_settings, monkeypatch, balance):
    monkeypatch.delenv("WECHAT_FREE_COVER_ONLY", raising=False)
    monkeypatch.setattr(cover_check, "load_settings", lambda *a, **k: app_settings)
    monkeypatch.setenv("WECHAT_RUN_DEADLINE_EPOCH", "0")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    app_settings.image_generation = replace(app_settings.image_generation,
        model_base_url="https://www.fhl.mom/v1", model_name="gpt-image-2")
    calls = []
    class FakeImages:
        def __init__(self, *a): pass
        def _get_image_api_client(self):
            def usage(path, **kwargs):
                assert path == "usage" and kwargs["options"]["timeout"] == 20
                return {"balance": balance, "private_user_id": "do-not-persist"}
            return SimpleNamespace(get=usage, models=SimpleNamespace(list=lambda: [SimpleNamespace(id="gpt-image-2")]))
        def generate_for_run(self, run, **kwargs):
            if os.environ.get("WECHAT_FREE_COVER_ONLY") == "1":
                raise RuntimeError("Free model is not installed in this fixture")
            calls.append(run)
            report = run / "publish/image_generation_result.json"
            report.write_text(json.dumps({"api_succeeded": True,
                "model": "gpt-image-2", "cover_style": "warm_editorial"}))
            return {"image_generation_result": report, "cover_image": run / "images/cover.png"}
    monkeypatch.setattr(cover_check, "ImageGenerationService", FakeImages)
    assert cover_check.main() == (0 if balance else 1)
    assert len(calls) == (1 if balance else 0)
    serialized = (app_settings.automation_dir / "wechat_cover_check/result.json").read_text(encoding="utf-8")
    assert "do-not-persist" not in serialized


def test_free_cloud_cover_validation_never_contacts_relay(app_settings, monkeypatch):
    monkeypatch.setenv("WECHAT_FREE_COVER_ONLY", "1")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(cover_check, "load_settings", lambda *a, **k: app_settings)
    app_settings.image_generation = replace(app_settings.image_generation, model_base_url="https://www.fhl.mom/v1")
    class FakeImages:
        def __init__(self, *args): pass
        def _get_image_api_client(self): pytest.fail("Free validation must not contact the relay")
        def generate_for_run(self, run, **kwargs):
            report = run / "publish/image_generation_result.json"
            report.write_text(json.dumps({"api_succeeded": False, "ai_generated": True,
                "cover_backend": "cloudflare_flux", "model": "flux-1-schnell", "cover_style": "warm_editorial"}))
            return {"image_generation_result": report, "cover_image": run / "images/cover.png"}
    monkeypatch.setattr(cover_check, "ImageGenerationService", FakeImages)
    assert cover_check.main() == 0
    report = json.loads((app_settings.automation_dir / "wechat_cover_check/result.json").read_text(encoding="utf-8"))
    assert report["model"] == "flux-1-schnell"
    assert report["publication_attempted"] is False and report["draft_saving_attempted"] is False


@pytest.mark.parametrize("api", [True, False])
def test_draft_delivery_cannot_publish_even_with_submit_true(app_settings, logger, monkeypatch, api):
    enable(app_settings)
    run = app_settings.runs_dir / "new"
    directory = run / app_settings.review.publish_dirname
    directory.mkdir(parents=True)
    (run / "review_status.json").write_text('{"status":"pending"}')
    (directory / "publish_meta.json").write_text('{"selected_title":"A title","approval_mode":"pending_manual_review"}')
    (directory / "article.md").write_text("A factual article.")
    service = WechatPublishService(app_settings, logger)
    monkeypatch.setattr(service, "_get_access_token", MagicMock(return_value="private") if api else MagicMock(side_effect=RuntimeError("no API")))
    monkeypatch.setattr(service, "_resolve_image_assets", lambda *a, **k: (directory / "cover.png", []))
    monkeypatch.setattr(service, "_upload_cover_material", lambda *a: "cover")
    monkeypatch.setattr(service, "_render_article_html_via_backend", lambda **k: ("<p>A factual article.</p>", {}))
    network = MagicMock(return_value={"media_id": "exact-draft-id"})
    monkeypatch.setattr(service, "_wechat_post_json", network)
    submit = MagicMock(side_effect=AssertionError("Publication is forbidden"))
    monkeypatch.setattr(service, "_submit_draft_via_browser", submit)
    browser = MagicMock(return_value={"status": "draft_saved", "app_msg_id": "exact-browser-id"})
    monkeypatch.setattr(service, "_publish_via_browser_only", browser)
    monkeypatch.setattr(service.growth_ops_service, "register_publish_event", MagicMock(return_value={}))
    exports = service.publish_run(run, submit=True)
    result = json.loads(exports["wechat_publish_result"].read_text(encoding="utf-8"))
    assert result["status"] == "draft_saved"
    assert result["submit_requested"] is False and result["publication_attempted"] is False
    assert result["manual_publication_required"] is True
    assert "expected_publication" not in result
    assert json.loads((run / "review_status.json").read_text(encoding="utf-8"))["status"] == "pending"
    submit.assert_not_called()
    if api:
        assert network.call_args.args[0] == "/cgi-bin/draft/add"
        browser.assert_not_called()
        assert result["draft_media_id"] == "exact-draft-id"
    else:
        assert browser.call_args.kwargs["submit"] is False
        assert result["app_msg_id"] == "exact-browser-id"
    assert phase_attempted(app_settings, 0) is False


@pytest.mark.parametrize("method,args", [
    ("_submit_draft_via_browser", {"draft_media_id": "id", "title": "title"}),
    ("_click_publish_button_on_wechat_page", {"page": MagicMock()}),
    ("_confirm_wechat_publish_dialog", {"page": MagicMock()}),
])
def test_low_level_publication_is_blocked(app_settings, logger, method, args):
    enable(app_settings)
    with pytest.raises(RuntimeError, match="prohibits publication"):
        getattr(WechatPublishService(app_settings, logger), method)(**args)


def test_unapproved_export_retains_review_and_never_claims_approval(app_settings, logger, monkeypatch):
    enable(app_settings)
    run = app_settings.runs_dir / "candidate"
    run.mkdir()
    status = run / "review_status.json"
    status.write_text('{"status":"pending","reviewer_notes":"Check the source"}')
    review = ReviewWorkflowService(app_settings, logger)
    monkeypatch.setattr(review, "_load_bundle", lambda *a: (ReviewBundle(titles=["Actual title"],
        article="Actual source-based body.", summary="根据baidu_hot公开资料，事实仍待核对。", revision_suggestions=["Verify the conclusion"]), None))
    before = status.read_bytes()
    exported = review.prepare_wechat_draft(run)
    meta = json.loads(exported["publish_meta"].read_text(encoding="utf-8"))
    assert status.read_bytes() == before
    assert "approved_at" not in meta and meta["approval_mode"] == "pending_manual_review"
    assert meta["revision_suggestions"] == ["Verify the conclusion"]
    assert "baidu_hot" not in meta["summary"]
    with pytest.raises(RuntimeError, match="already exists"):
        review.prepare_wechat_draft(run)
    app_settings.wechat = replace(app_settings.wechat, draft_only=False)
    with pytest.raises(RuntimeError, match="requires"):
        review.prepare_wechat_draft(run)


def test_draft_export_summary_tracks_the_final_revised_body(app_settings, logger, monkeypatch):
    enable(app_settings)
    run = app_settings.runs_dir / "revised-summary"
    run.mkdir()
    body = "公司仅公布固定工位的测试记录。该记录不能证明产品已经在家庭场景投入使用。"
    review = ReviewWorkflowService(app_settings, logger)
    monkeypatch.setattr(review, "_load_bundle", lambda *a: (ReviewBundle(titles=["测试范围"], article=body,
        summary="公司已经在家庭场景实现全面交付。"), None))
    exported = review.prepare_wechat_draft(run)
    meta = json.loads(exported["publish_meta"].read_text(encoding="utf-8"))
    assert meta["summary"] == exported["publish_article"].read_text(encoding="utf-8").strip()
    assert "不能证明" in meta["summary"]


def setup_cloud(settings, monkeypatch, rows=(), used_seconds=0):
    enable(settings)
    now = datetime.now(timezone.utc).isoformat()
    day = retry.day_of(now)
    monkeypatch.setenv("GITHUB_RUN_ID", "500")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "fixed")
    monkeypatch.setenv("WECHAT_FIXED_DEVICE_DIR", str(settings.project_root / "fixed"))
    monkeypatch.setenv("WECHAT_DELIVERY_MODE", "draft")
    monkeypatch.setenv("WECHAT_SESSION_REFRESH", "0")
    for key in ("WECHAT_RUN_DEADLINE_EPOCH", "WECHAT_GENERATION_DEADLINE_EPOCH", "WECHAT_EXECUTION_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a, **k: None)
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **k: settings)
    usage = {"schema_version": 1, "days": {day: {"runs": {}, "dispatches": {}, "failed_inputs": []}}}
    if used_seconds:
        usage["days"][day]["runs"]["old:1"] = {"status": "completed", "started_at": now,
            "elapsed_seconds": used_seconds, "outcome": "review_required"}
    def reader(*a, **kw):
        if kw.get("path") == metrics.METRICS_PATH:
            return {"schema": 1, "runs": list(rows)}
        if kw.get("path") == retry.STATE_PATH:
            return usage
        return {}
    monkeypatch.setattr(cloud.xstate, "read_remote_state", reader)
    writer = MagicMock()
    monkeypatch.setattr(cloud, "write_state_file", writer)
    pending = MagicMock(side_effect=AssertionError("Draft mode cannot reconcile publications"))
    monkeypatch.setattr(cloud, "verify_pending", pending)
    child = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(cloud.subprocess, "run", child)
    return day, child, writer


@pytest.mark.parametrize("delivery", ["saved", "uncertain"])
def test_saved_or_uncertain_draft_blocks_duplicate_generation(app_settings, monkeypatch, delivery):
    now = datetime.now(timezone.utc).isoformat()
    setup_cloud(app_settings, monkeypatch, [{"run_key": "prior:1", "started_at": now,
        "mode": "draft", "outcome": "draft", "draft_delivery": {"status": delivery}}])
    runner = MagicMock(side_effect=AssertionError("Must not start another manuscript"))
    monkeypatch.setattr("toutiao_content_factory.services.wechat_execution.run_process_tree", runner)
    assert cloud.main(["--mode", "publish"]) == (0 if delivery == "saved" else 1)
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert report["delivery_mode"] == "draft"
    assert report["publication_attempted"] is False
    assert report["formal_publication_confirmed"] is False
    runner.assert_not_called()


def test_draft_budget_exhaustion_does_not_start(app_settings, monkeypatch):
    _, child, _ = setup_cloud(app_settings, monkeypatch, used_seconds=5300)
    assert cloud.main(["--mode", "draft"]) == 0
    child.assert_not_called()
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert report["retry_status"] == "daily_budget_exhausted"


def test_legacy_publish_command_honors_draft_configuration_without_workflow_override(app_settings, monkeypatch):
    _, child, _ = setup_cloud(app_settings, monkeypatch, used_seconds=5300)
    monkeypatch.delenv("WECHAT_DELIVERY_MODE")
    assert cloud.main(["--mode", "publish"]) == 0
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert report["delivery_mode"] == "draft" and report["publication_attempted"] is False
    child.assert_not_called()


@pytest.mark.parametrize("phase,uncertain", [("preparing", False), ("saving_draft", True)])
def test_draft_timeout_never_claims_publication(app_settings, monkeypatch, phase, uncertain):
    setup_cloud(app_settings, monkeypatch)
    publication = MagicMock(side_effect=AssertionError("Never write a publication guard"))
    monkeypatch.setattr(cloud, "record_publication", publication)
    def timeout(command, **kw):
        assert "--save-draft" in command and "--enable-auto-publish" not in command
        write_phase(app_settings, phase)
        raise subprocess.TimeoutExpired(command, kw["timeout"])
    monkeypatch.setattr("toutiao_content_factory.services.wechat_execution.run_process_tree", timeout)
    assert cloud.main(["--mode", "draft"]) == 1
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert report["draft_delivery"]["status"] == ("uncertain" if uncertain else "not_saved")
    assert report["publication_attempted"] is False
    assert "wechat_status" not in report
    publication.assert_not_called()


def test_draft_success_retains_delivery_id_and_excludes_publication_metrics(app_settings, monkeypatch):
    setup_cloud(app_settings, monkeypatch, [{"run_key": "legacy:1", "mode": "publish",
        "started_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "outcome": "submit_uncertain"}])
    def deliver(command, **kw):
        write_phase(app_settings, "saving_draft")
        (app_settings.automation_dir / "wechat_scheduled_result.json").write_text(json.dumps({
            "status": "success", "wechat_status": "draft_saved", "publication_attempted": False,
            "native_exit_code": 0, "draft_delivery": {"status": "saved", "app_msg_id": "exact-id"}}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr("toutiao_content_factory.services.wechat_execution.run_process_tree", deliver)
    assert cloud.main(["--mode", "draft"]) == 0
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert report["draft_delivery"]["app_msg_id"] == "exact-id"
    assert not report["formal_publication_confirmed"]
    assert report["metrics"]["7"]["actual_publish_runs"] == 1  # Only the retained legacy run.


def test_workflow_routes_scheduled_runs_to_formal_publication():
    # 2026-10-21 用户决策 C1：定时运行改为正式发表（此前 draft-only）。
    # 审核通过才会群发；审核不过仍只留草稿；workflow_dispatch 仍可用 inputs.mode 覆盖。
    workflow = yaml.load((ROOT / ".github/workflows/wechat-daily-run.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["concurrency"]["group"] == "wechat-default"
    assert workflow["jobs"]["wechat"]["env"]["WECHAT_DELIVERY_MODE"] == "publish"
    assert workflow["jobs"]["wechat"]["env"]["RUN_MODE"] == "${{ inputs.mode || 'publish' }}"
    assert workflow["jobs"]["wechat"]["env"]["WECHAT_OFFICIAL_PUBLISH_ENABLED"] == "1"


def test_browser_draft_id_is_preserved_in_workflow_snapshot(app_settings, logger):
    enable(app_settings)
    service = ContentFactoryAppService.__new__(ContentFactoryAppService)
    service.settings = app_settings
    directory = app_settings.runs_dir / "saved" / app_settings.review.publish_dirname / "wechat"
    directory.mkdir(parents=True)
    (directory / "wechat_publish_result.json").write_text(json.dumps({"status": "draft_saved",
        "draft_only": True, "app_msg_id": "exact-id", "title": "Actual title", "submit_requested": False}))
    snapshot = service._load_wechat_publish_snapshot(directory.parents[1])
    assert snapshot["app_msg_id"] == "exact-id" and snapshot["title"] == "Actual title"


@pytest.mark.parametrize("damage", ["stale", "wrong_run", "invalid_json", "missing"])
def test_draft_phase_without_current_proof_is_uncertain(app_settings, monkeypatch, damage):
    import time
    import os
    enable(app_settings)
    monkeypatch.setenv("WECHAT_EXECUTION_ID", "current")
    started = time.time_ns()
    write_phase(app_settings, "preparing")
    path = app_settings.automation_dir / "wechat_publish_phase.json"
    if damage == "stale":
        os.utime(path, ns=(started - 1000000000, started - 1000000000))
    elif damage == "wrong_run":
        value = json.loads(path.read_text(encoding="utf-8"))
        value["run_id"] = "previous"
        path.write_text(json.dumps(value))
    elif damage == "invalid_json":
        path.write_text("{")
    else:
        path.unlink()
    assert draft_delivery_state(app_settings, started) == "uncertain"


def test_success_without_draft_identifier_is_not_saved(app_settings, monkeypatch):
    setup_cloud(app_settings, monkeypatch)
    def deliver(command, **kw):
        write_phase(app_settings, "saving_draft")
        (app_settings.automation_dir / "wechat_scheduled_result.json").write_text(json.dumps({
            "status": "success", "wechat_status": "draft_saved", "native_exit_code": 0,
            "draft_delivery": {"status": "saved"}}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr("toutiao_content_factory.services.wechat_execution.run_process_tree", deliver)
    assert cloud.main(["--mode", "draft"]) == 1
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert report["draft_delivery"]["status"] == "uncertain"
    assert report["status"] == "error" and report["publication_attempted"] is False


def test_retry_reservation_preserves_saved_draft_when_metrics_write_fails(app_settings, monkeypatch):
    day, _, writer = setup_cloud(app_settings, monkeypatch)
    def fail_metrics(state, path, message):
        if path == metrics.METRICS_PATH:
            raise OSError("metrics unavailable")
    writer.side_effect = fail_metrics
    def deliver(command, **kw):
        write_phase(app_settings, "saving_draft")
        (app_settings.automation_dir / "wechat_scheduled_result.json").write_text(json.dumps({
            "status": "success", "wechat_status": "draft_saved", "native_exit_code": 0,
            "draft_delivery": {"status": "saved", "app_msg_id": "exact-id"}}))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr("toutiao_content_factory.services.wechat_execution.run_process_tree", deliver)
    assert cloud.main(["--mode", "draft"]) == 1
    states = [call.args[0] for call in writer.call_args_list if call.args[1] == retry.STATE_PATH]
    completed = states[-1]
    assert completed["days"][day]["runs"]["500:1"]["draft_delivery"]["app_msg_id"] == "exact-id"
    def reader(*a, **kw):
        return completed if kw.get("path") == retry.STATE_PATH else {}
    monkeypatch.setattr(cloud.xstate, "read_remote_state", reader)
    monkeypatch.setenv("GITHUB_RUN_ID", "501")
    blocked = MagicMock(side_effect=AssertionError("Must not duplicate delivered draft"))
    monkeypatch.setattr("toutiao_content_factory.services.wechat_execution.run_process_tree", blocked)
    assert cloud.main(["--mode", "draft"]) == 1  # Metrics remain unavailable; delivery remains confirmed.
    report = json.loads((app_settings.automation_dir / "wechat_cloud_result.json").read_text(encoding="utf-8"))
    assert report["status"] == "already_drafted"
    assert report["draft_delivery"]["app_msg_id"] == "exact-id"
    blocked.assert_not_called()


@pytest.mark.parametrize("cover_fails", [False, True])
def test_daily_workflow_delivers_pending_draft_without_approving_or_publishing(app_settings, logger, monkeypatch, cover_fails):
    enable(app_settings)
    for key in ("WECHAT_RUN_DEADLINE_EPOCH", "WECHAT_GENERATION_DEADLINE_EPOCH"):
        monkeypatch.delenv(key, raising=False)
    app_settings.publishing = replace(app_settings.publishing, enabled=False, auto_publish=False)
    app = ContentFactoryAppService(app_settings, logger)
    run = app_settings.runs_dir / "new-source-candidate"
    run.mkdir(parents=True)
    app.collect_topics = MagicMock(return_value={})
    app._local_fallback_window_block = MagicMock(return_value=None)
    app._cleanup_stale_runs = MagicMock()
    app._heartbeat_daily_run_lock = MagicMock()
    app.platform_health_service.quick_health_check = MagicMock(return_value={"blocking_platforms": []})
    app.platform_health_service.ensure_publish_ready = MagicMock(return_value={"blocking_platforms": []})
    app._publish_priority_weitoutiao = MagicMock(return_value={})
    app.growth_ops_service.collect_self_benchmark_samples_from_report = MagicMock(return_value={})
    app._build_daily_candidate_specs = MagicMock(return_value=[
        {"rank": 1, "title": "Real source", "url": "https://example.test/source"}])
    app.auto_run = MagicMock(return_value={"archive_root": str(run)})
    app._load_score_snapshot = MagicMock(return_value={})
    app._load_content_guard_report = MagicMock(return_value={"blocked": False})
    app._qualifies_for_auto_approval = MagicMock(return_value=False)
    app.review_service.prepare_wechat_draft = MagicMock(return_value={})
    steps = []
    def generate_cover(*args, **kwargs):
        steps.append("cover")
        assert args == (run,) and kwargs == {"include_infographics": False}
        if cover_fails:
            raise RuntimeError("image API unavailable")
        return {"cover_image": run / "images/cover.png"}
    app.image_generation_service.generate_for_run = MagicMock(side_effect=generate_cover)
    app.wechat_service.publish_run = MagicMock(return_value={})
    app.wechat_service.publish_run.side_effect = lambda *a, **k: steps.append("draft") or {}
    app._load_wechat_publish_snapshot = MagicMock(return_value={"status": "draft_saved", "app_msg_id": "actual-id"})
    app.approve_run = MagicMock(side_effect=AssertionError("Must not approve the pending draft"))
    app._run_daily_platform_automation = MagicMock(side_effect=AssertionError("Must not publish"))
    if cover_fails:
        with pytest.raises(RuntimeError, match="image API unavailable"):
            app._daily_run_impl()
        assert steps == ["cover"]
        app.wechat_service.publish_run.assert_not_called()
        app.approve_run.assert_not_called()
        return
    exported = app._daily_run_impl()
    assert steps == ["cover", "draft"]
    assert exported["cover_image"] == str(run / "images/cover.png")
    report = json.loads(Path(exported["workflow_report"]).read_text(encoding="utf-8"))
    assert report["status"] == "success"
    assert report["final_attempt"]["qualified_for_auto_approval"] is False
    assert report["final_attempt"]["platform_results"]["wechat"]["app_msg_id"] == "actual-id"
    app.wechat_service.publish_run.assert_called_once_with(run, submit=False)
    app.approve_run.assert_not_called()
    app._run_daily_platform_automation.assert_not_called()
