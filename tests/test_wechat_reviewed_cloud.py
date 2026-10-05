from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import wechat_cloud_run as cloud
from wechat_run_report import cloud_exit_code, fresh_json, review_diagnostics, safe_text
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, QualityReviewReport, TrendReport, TopicCandidate
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.topic_collection import TopicCollectionService


def enable_wechat(settings):
    settings.platform = "wechat"
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)
    object.__setattr__(settings.publishing, "enabled", False)
    object.__setattr__(settings.publishing, "auto_publish", False)


def test_tech_filter_is_platform_scoped_and_preserves_freshness(monkeypatch, logger):
    monkeypatch.setenv("TARGET_PLATFORM__DEFAULT", "wechat")
    monkeypatch.setenv("TARGET_PLATFORM__ROBOT_NOTES", "toutiao")
    monkeypatch.setenv("WECHAT_TECH_TOPICS_ONLY", "0")
    before = load_settings(ROOT, "default")
    toutiao_before = load_settings(ROOT, "robot_notes")
    monkeypatch.setenv("WECHAT_TECH_TOPICS_ONLY", "1")
    settings = load_settings(ROOT, "default")
    assert "hot_topic" not in settings.topic_collection.focus_industries
    assert "大模型" in settings.topic_collection.keyword_map["ai_applications"]
    assert settings.topic_collection.max_topic_age_hours == before.topic_collection.max_topic_age_hours
    assert settings.topic_collection.drop_topics_without_timestamp == before.topic_collection.drop_topics_without_timestamp
    assert load_settings(ROOT, "robot_notes").topic_collection == toutiao_before.topic_collection
    service = TopicCollectionService(settings, logger)
    topic = TopicCandidate(title="球星卡市场爆火，谁在制造纸片", url="https://example.test/cards",
                           source="test", source_type="hot_board", heat_value=999999)
    from collections import defaultdict
    assert service._passes_topic_focus(topic, defaultdict(int)) is False


def test_wechat_writer_polish_and_publish_floor_agree(app_settings, mock_openai_service, logger):
    enable_wechat(app_settings)
    writer = WriterAgent(app_settings, MagicMock(), mock_openai_service, logger)
    polish = PolishAgent(app_settings, MagicMock(), mock_openai_service, logger)
    report = DataReport(content_format="quick_commentary", is_hot_topic=True, word_target=800)
    assert writer._auto_publish_mode() is True
    assert polish._auto_publish_mode() is True
    assert int(writer._min_word_target_for(report)) >= 1750
    assert int(writer._article_word_target_for_prompt(report)) >= 1800
    assert "最低1750字" in polish._auto_publish_directive()
    app = ContentFactoryAppService(app_settings, logger)
    assert app._minimum_article_chars(None, score_snapshot={"is_hot_topic": 1}) == 1750
    workflow = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    workflow._ensure_article_min_chars = MagicMock(side_effect=lambda article, *a, **kw: article)
    workflow._apply_polish_pack(GeneratedContent(article="original"), {"polished_article": "new"}, report)
    assert workflow._ensure_article_min_chars.call_args.kwargs["min_chars"] == 1750


@pytest.mark.parametrize("changed", [True, False])
def test_wechat_revision_refreshes_titles_before_audit_without_reauditing_noop(
    app_settings, mock_openai_service, logger, monkeypatch, changed
):
    enable_wechat(app_settings)
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    object.__setattr__(app_settings.quality_review, "max_auto_revisions", 2)
    content = GeneratedContent(article="old body", polished_article="old body", recommended_titles=["old title"])
    held = QualityReviewReport(verdict="needs_revision", required_revisions=["revise"])
    seen = []
    def audit(content, *a, **kw):
        seen.append((content.article, content.recommended_titles[0]))
        return held
    service.quality_review_council_agent.run = MagicMock(side_effect=audit)
    service.polish_agent.run = MagicMock(side_effect=[
        {"polished_article": "new body"}, {"polished_article": "new body"},
    ])
    def apply(content, *args):
        content.article = content.polished_article = "new body" if changed else "old body"
    def titles(content, *args):
        content.recommended_titles = ["new title"]
    monkeypatch.setattr(service, "_apply_polish_pack", apply)
    refresh = MagicMock(side_effect=titles)
    monkeypatch.setattr(service, "_refresh_titles", refresh)
    result = service._run_quality_review_council(
        content=content, trend_report=TrendReport(), data_report=DataReport(),
        style=app_settings.get_style(), cache_context=CacheContext(), editorial_council=None, fast_mode=False,
    )
    assert result is held
    assert seen == ([("old body", "old title"), ("new body", "new title")] if changed else [("old body", "old title")])
    assert refresh.call_count == int(changed)
    assert service.polish_agent.run.call_count == (2 if changed else 1)


@pytest.mark.parametrize("changes, child_code, expected", [
    ({}, 3, 0), ({"publication_attempted": True}, 3, 3),
    ({"publication_attempted": None}, 3, 3), ({"native_exit_code": 1}, 3, 3),
    ({"status": "publish_uncertain"}, 3, 3), ({"wechat_status": "draft_saved"}, 3, 3),
    ({"wechat_status": "submitted"}, 3, 3), ({"run_outcome": "blocked"}, 3, 3),
    ({}, 1, 1), ({"status": "publish_login_required"}, 2, 2),
])
def test_only_proven_review_hold_completes_normally(changes, child_code, expected):
    report = {"status": "review_required", "native_exit_code": 0, "publication_attempted": False}
    report.update(changes)
    assert cloud_exit_code(report, child_code) == expected


def write_report(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_diagnostics_use_current_account_and_strip_private_data(app_settings, monkeypatch):
    started = time.time_ns() - 1_000_000_000
    run_dir = app_settings.runs_dir / "run"
    write_report(run_dir / "quality_review_council.json", {
        "verdict": "needs_revision", "overall_score": 82,
        "required_revisions": ["see https://mp.weixin.qq.com/cgi-bin/home?token=private-token", "private-api-key"],
    })
    write_report(run_dir / "workflow_result.json", {"content": {"article": "private article", "title_validation": {
        "status": "all_rejected", "reason_summary": {"missing_concrete_hook": 4},
    }}})
    path = app_settings.automation_dir / "last_daily_workflow_report.json"
    payload = {"account_key": "default", "status": "review_required", "attempts": [{
        "run_dir": str(run_dir), "status": "review_required", "score_snapshot": {"length_shortfall": 10},
    }]}
    write_report(path, payload)
    monkeypatch.setenv("OPENAI_API_KEY", "private-api-key")
    report = review_diagnostics(app_settings, {"status": "review_required"}, started)
    assert report["publication_attempted"] is False
    assert report["attempt_count"] == 1
    assert set(report["reason_codes"]) == {"article_too_short", "quality_review_required", "title_all_rejected"}
    serialized = json.dumps(report)
    assert "private-token" not in serialized and "private-api-key" not in serialized and "private article" not in serialized
    payload["attempts"][0]["platform_results"] = {"wechat": {"status": "publish_uncertain"}}
    write_report(path, payload)
    assert review_diagnostics(app_settings, {"status": "review_required"}, started)["publication_attempted"] is True
    payload["account_key"] = "other"
    write_report(path, payload)
    assert review_diagnostics(app_settings, {"status": "review_required"}, started)["publication_attempted"] is True


def test_stale_or_missing_reports_cannot_prove_no_publication(app_settings):
    path = app_settings.automation_dir / "last_daily_workflow_report.json"
    write_report(path, {"account_key": "default", "status": "review_required", "attempts": [{"status": "review_required"}]})
    os.utime(path, (1000, 1000))
    assert fresh_json(path, time.time_ns()) == {}
    assert review_diagnostics(app_settings, {}, time.time_ns())["publication_attempted"] is True
    assert safe_text("token=private-cookie") == "token=[redacted]"


@pytest.mark.parametrize("status, expected", [("review_required", 0), ("publish_uncertain", 3)])
def test_cloud_keeps_real_failures_and_records_uncertainty(app_settings, monkeypatch, status, expected):
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud, "save_session", MagicMock())
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw: {})
    guard = MagicMock()
    monkeypatch.setattr(cloud, "record_publication", guard)
    def child(command, **kw):
        if "--check-login" in command:
            return MagicMock(returncode=0)
        write_report(app_settings.automation_dir / "wechat_scheduled_result.json", {
            "status": status, "native_exit_code": 0, "publication_attempted": status != "review_required", "exit_code": 3,
        })
        return MagicMock(returncode=3)
    monkeypatch.setattr(cloud.subprocess, "run", child)
    assert cloud.main(["--mode", "publish"]) == expected
    assert guard.call_count == int(status == "publish_uncertain")


def test_prior_uncertain_submission_blocks_cloud_retry(app_settings, monkeypatch):
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    monkeypatch.setattr(cloud, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(cloud, "configure_environment", lambda *a: None)
    monkeypatch.setattr(cloud, "restore_session", MagicMock())
    monkeypatch.setattr(cloud.xstate, "read_remote_state", lambda *a, **kw: {
        "entries": {today + "::default": {"wechat": {"status": "publish_uncertain"}}}
    })
    child = MagicMock()
    monkeypatch.setattr(cloud.subprocess, "run", child)
    assert cloud.main(["--mode", "publish"]) == 1
    child.assert_not_called()


def test_candidate_limit_retains_evidence_gates(app_settings, logger):
    enable_wechat(app_settings)
    object.__setattr__(app_settings.automation, "max_daily_topic_attempts", 5)
    topics = [TopicCandidate(title=f"储能项目进展{i}", url=f"https://example.test/{i}", source="test", source_type="rss",
                             evidence_strength=75, quality_score=85, total_score=80) for i in range(8)]
    topics.insert(0, TopicCandidate(title="薄素材热点", url="https://example.test/thin", source="test", source_type="hot_board",
                                   evidence_strength=20, quality_score=95, total_score=99))
    app = ContentFactoryAppService(app_settings, logger)
    app.topic_automation._load_daily_topics = MagicMock(return_value=MagicMock(topics=topics))
    app.topic_automation._available_topics = MagicMock(return_value=topics)
    candidates = app._build_daily_candidate_specs(topic_rank=None, topic_title=None, refresh_cache=False, dry_run=False)
    assert len(candidates) == 5
    assert all(item["url"] != "https://example.test/thin" for item in candidates)


def test_accepted_or_uncertain_platform_prevents_switching_candidate(app_settings, logger):
    app = ContentFactoryAppService(app_settings, logger)
    for status in ("submitted", "submitted_for_review", "published", "publish_uncertain"):
        platforms = {"toutiao": {"status": "failed", "error": "duplicate title"}, "wechat": {"status": status}}
        assert app._is_safe_title_duplicate_fallback(platforms, ["duplicate title"]) is False
