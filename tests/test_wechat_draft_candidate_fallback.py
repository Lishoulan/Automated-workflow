import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.services.app_service import ContentFactoryAppService


@pytest.fixture
def draft_app(app_settings, logger, monkeypatch):
    app_settings.platform = "wechat"
    app_settings.wechat = replace(app_settings.wechat, enabled=True, auto_publish=True, draft_only=True)
    app_settings.publishing = replace(app_settings.publishing, enabled=False, auto_publish=False)
    app_settings.automation = replace(app_settings.automation, max_daily_topic_attempts=2)
    for key in ("WECHAT_RUN_DEADLINE_EPOCH", "WECHAT_GENERATION_DEADLINE_EPOCH"):
        monkeypatch.delenv(key, raising=False)
    app = ContentFactoryAppService(app_settings, logger)
    runs = [app_settings.runs_dir / name for name in ("rejected-title", "valid-title")]
    for run in runs:
        run.mkdir(parents=True)
    for name in ("_cleanup_stale_runs", "_heartbeat_daily_run_lock"):
        setattr(app, name, MagicMock())
    app.collect_topics = MagicMock(return_value={})
    app._local_fallback_window_block = MagicMock(return_value=None)
    app.platform_health_service.quick_health_check = MagicMock(return_value={"blocking_platforms": []})
    app.platform_health_service.ensure_publish_ready = MagicMock(return_value={"blocking_platforms": []})
    app._publish_priority_weitoutiao = MagicMock(return_value={})
    app.growth_ops_service.collect_self_benchmark_samples_from_report = MagicMock(return_value={})
    app._build_daily_candidate_specs = MagicMock(return_value=[
        {"rank": n, "title": f"source {n}", "url": f"https://example.test/{n}"} for n in (1, 2)])
    app.auto_run = MagicMock(side_effect=[{"archive_root": str(run)} for run in runs])
    app._load_run_score_snapshot = MagicMock(return_value={})
    app._load_content_guard_report = MagicMock(return_value={"blocked": False})
    # These cases exercise title/delivery failures after the candidate review
    # gate. Unapproved-candidate switching has its own regression below.
    app._qualifies_for_auto_approval = MagicMock(return_value=True)
    app.review_service.select_auto_title = MagicMock(return_value={"selected_title": "Reviewed title", "selected_title_index": 0})
    app.review_service.prepare_wechat_draft = MagicMock(side_effect=[ValueError("No candidate titles available for this run."), {}])
    app.image_generation_service.generate_for_run = MagicMock(return_value={})
    app.wechat_service.publish_run = MagicMock(return_value={})
    app._load_wechat_publish_snapshot = MagicMock(return_value={"status": "draft_saved", "app_msg_id": "actual-id"})
    app.approve_run = MagicMock(side_effect=AssertionError("Never grant approval"))
    app._run_daily_platform_automation = MagicMock(side_effect=AssertionError("Never publish"))
    return app, runs


def test_rejected_title_uses_next_topic_before_any_platform_mutation(draft_app):
    app, runs = draft_app
    result = app._daily_run_impl()
    report = json.loads(Path(result["workflow_report"]).read_text(encoding="utf-8"))
    assert report["attempts"][0]["status"] == "draft_validation_rejected"
    assert report["attempts"][1]["status"] == "draft_saved"
    app.wechat_service.publish_run.assert_called_once_with(runs[1], submit=False)
    app.image_generation_service.generate_for_run.assert_called_once_with(runs[1], include_infographics=False)
    app.approve_run.assert_not_called()


def test_all_rejected_titles_skip_with_diagnostics_and_no_upload(draft_app):
    app, _runs = draft_app
    app.review_service.prepare_wechat_draft.side_effect = ValueError("No candidate titles available")
    result = app._daily_run_impl()
    assert result["status"] == "no_topics"
    report = json.loads(Path(result["workflow_report"]).read_text(encoding="utf-8"))
    assert len(report["attempts"]) == 2
    assert all(row["status"] == "draft_validation_rejected" for row in report["attempts"])
    app.image_generation_service.generate_for_run.assert_not_called()
    app.wechat_service.publish_run.assert_not_called()


def test_expired_budget_stops_before_next_topic(draft_app):
    app, _runs = draft_app
    app._daily_run_budget_seconds = MagicMock(return_value=0)
    assert app._daily_run_impl()["status"] == "no_topics"
    assert app.auto_run.call_count == 1
    app.wechat_service.publish_run.assert_not_called()


def test_delivery_error_is_not_reinterpreted_as_rejected_title(draft_app):
    app, _runs = draft_app
    app.review_service.prepare_wechat_draft.side_effect = None
    app.review_service.prepare_wechat_draft.return_value = {}
    app.wechat_service.publish_run.side_effect = ValueError("Submission result uncertain")
    with pytest.raises(ValueError, match="Submission result uncertain"):
        app._daily_run_impl()
    assert app.auto_run.call_count == 1
    assert app.wechat_service.publish_run.call_count == 1
    app.approve_run.assert_not_called()
    app._run_daily_platform_automation.assert_not_called()


def test_unapproved_candidates_keep_review_switch_and_only_save_last_manual_draft(draft_app):
    app, runs = draft_app
    app._qualifies_for_auto_approval.return_value = False
    app.review_service.prepare_wechat_draft.side_effect = None
    app.review_service.prepare_wechat_draft.return_value = {}
    result = app._daily_run_impl()
    report = json.loads(Path(result["workflow_report"]).read_text(encoding="utf-8"))
    assert [row["status"] for row in report["attempts"]] == ["review_pending_next_candidate", "draft_saved"]
    assert all(row["qualified_for_auto_approval"] is False for row in report["attempts"])
    app.review_service.prepare_wechat_draft.assert_called_once_with(runs[1])
    app.wechat_service.publish_run.assert_called_once_with(runs[1], submit=False)
    app.image_generation_service.generate_for_run.assert_called_once_with(runs[1], include_infographics=False)
    app.approve_run.assert_not_called()
    app._run_daily_platform_automation.assert_not_called()
