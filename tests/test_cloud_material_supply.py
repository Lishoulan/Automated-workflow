from datetime import datetime, timedelta
import json
from unittest.mock import MagicMock
import pytest

from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.topic_collection import TopicCollectionService
from toutiao_content_factory.services.publish_service import PublishService


def enable(settings):
    settings.platform = "wechat"
    object.__setattr__(settings.wechat, "enabled", True)
    object.__setattr__(settings.wechat, "auto_publish", True)


def topic(index, age=0):
    return TopicCandidate("机器人测试报告" + str(index), "https://source.test/" + str(index), "rss", "rss",
        published_at=(datetime.now() - timedelta(hours=age)).isoformat(),
        matched_industries=["humanoid_robot"], matched_keywords=["机器人"],
        evidence_strength=80, quality_score=80, snippet="机器人测试条件和结果已经披露。" * 60,
        source_records=[{"source_id": "source-1", "original_text": "机器人测试条件和结果已经披露。" * 60}])


def test_verified_material_is_bounded_and_survives_a_new_app_without_approval(app_settings, logger):
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    app._enrich_wechat_candidates([topic(i) for i in range(4)])
    later = ContentFactoryAppService(app_settings, logger)
    saved = later.topic_pool_service.list_pending_topics()
    assert len(saved) == 3 and all(item.source_records for item in saved)
    report = json.loads((app_settings.automation_dir / "wechat_supply_report.json").read_text(encoding="utf-8"))
    assert report["usable_material_count"] == 3 and report["review_approved"] is False
    assert not list(app_settings.runs_dir.glob("*/publish/article.md"))


@pytest.mark.parametrize("persisted", [False, True])
def test_reserve_requires_new_source_verification_not_only_old_scores(app_settings, logger, monkeypatch, persisted):
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    candidate = topic(1)
    candidate._wechat_from_reserve = True
    if persisted:
        candidate.quality_flags = ["cached_material"]
        stored = DailyTopicsReport(datetime.now().isoformat(), 1, 72, ["humanoid_robot"], 1, 1, topics=[candidate])
        candidate = DailyTopicsReport.from_dict(stored.to_dict()).topics[0]
        assert not getattr(candidate, "_wechat_from_reserve", False)
    hydrate = MagicMock(return_value=False)
    monkeypatch.setattr(app.topic_automation, "_hydrate_topic_material", hydrate)
    monkeypatch.setattr(TopicCollectionService, "_passes_topic_focus", lambda *a: True)
    app._enrich_wechat_candidates([candidate])
    assert hydrate.call_count == 1 and candidate._wechat_original_gate_passed is False
    assert app.topic_pool_service.list_pending_topics() == []


def test_fresh_reserve_fills_empty_collection_but_stale_material_stays_out(app_settings, logger, monkeypatch):
    enable(app_settings)
    app = ContentFactoryAppService(app_settings, logger)
    report = DailyTopicsReport(datetime.now().isoformat(), 1, 72, ["humanoid_robot"], 0, 0)
    candidates = [topic(1), topic(2, age=720)]
    app.topic_pool_service.list_pending_topics = MagicMock(return_value=candidates)
    monkeypatch.setattr(TopicCollectionService, "collect_sync", lambda self: report)
    monkeypatch.setattr(TopicCollectionService, "_passes_topic_focus", lambda *a: True)
    monkeypatch.setattr(app, "_resolve_benchmark_track_for_account", lambda: None)
    app.collect_topics()
    assert [item.title for item in report.topics] == [candidates[0].title]
    assert report.topics[0]._wechat_from_reserve is True
    assert "cached_material" in report.topics[0].quality_flags


def test_long_article_enables_ai_declaration_without_requesting_originality(app_settings, logger, monkeypatch):
    service = PublishService(app_settings, logger)
    observed = []
    monkeypatch.setattr(service, "_set_named_checkbox_state", lambda page, selectors, checked: observed.append((selectors, checked)) or selectors[0])
    service._apply_default_work_declarations(object(), [])
    assert any("引用AI" in selectors[0] and checked for selectors, checked in observed)
    assert all(checked for _, checked in observed)
    assert not any("声明原创" in selector for selectors, _ in observed for selector in selectors)
