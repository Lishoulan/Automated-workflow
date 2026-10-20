"""WeChat should try the next ready candidate before delivering a review-only draft."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from toutiao_content_factory.services.app_service import ContentFactoryAppService


def test_wechat_prefers_second_qualified_candidate_without_formal_publication(app_settings, logger, monkeypatch):
    app_settings.platform = "wechat"
    app_settings.wechat = replace(app_settings.wechat, draft_only=True)
    app_settings.publishing = replace(app_settings.publishing, enabled=False, auto_publish=False)
    for key in ("WECHAT_RUN_DEADLINE_EPOCH", "WECHAT_GENERATION_DEADLINE_EPOCH"):
        monkeypatch.delenv(key, raising=False)
    app = ContentFactoryAppService(app_settings, logger)
    first = app_settings.runs_dir / "first"
    second = app_settings.runs_dir / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    app.collect_topics = MagicMock(return_value={})
    app._cleanup_stale_runs = MagicMock()
    app._heartbeat_daily_run_lock = MagicMock()
    app._local_fallback_window_block = MagicMock(return_value=None)
    app.platform_health_service.quick_health_check = MagicMock(return_value={"blocking_platforms": []})
    app.platform_health_service.ensure_publish_ready = MagicMock(return_value={"blocking_platforms": []})
    app._publish_priority_weitoutiao = MagicMock(return_value={})
    app.growth_ops_service.collect_self_benchmark_samples_from_report = MagicMock(return_value={})
    app._build_daily_candidate_specs = MagicMock(return_value=[
        {"rank": 1, "title": "First", "url": "https://example.test/first"},
        {"rank": 2, "title": "Second", "url": "https://example.test/second"},
    ])
    app.auto_run = MagicMock(side_effect=[
        {"archive_root": str(first)}, {"archive_root": str(second)}
    ])
    app._load_run_score_snapshot = MagicMock(side_effect=lambda run_dir: {
        "overall_score": 50 if Path(run_dir) == first else 90})
    app._load_content_guard_report = MagicMock(return_value={"blocked": False})
    app._qualifies_for_auto_approval = MagicMock(side_effect=[False, True])
    app.review_service.select_auto_title = MagicMock(return_value={"selected_title": "Second", "selected_title_index": 0})
    app.review_service.prepare_wechat_draft = MagicMock(return_value={})
    app.image_generation_service.generate_for_run = MagicMock(return_value={})
    app.wechat_service.publish_run = MagicMock(return_value={})
    app._load_wechat_publish_snapshot = MagicMock(return_value={"status": "draft_saved", "app_msg_id": "id-second"})
    app.approve_run = MagicMock(side_effect=AssertionError("Do not approve in draft mode"))
    app._run_daily_platform_automation = MagicMock(side_effect=AssertionError("Do not publish formally"))

    app._daily_run_impl()
    assert app.auto_run.call_count == 2
    app.wechat_service.publish_run.assert_called_once_with(second, submit=False)
    app.approve_run.assert_not_called()
    app._run_daily_platform_automation.assert_not_called()
