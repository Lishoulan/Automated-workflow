"""Regression for 2026-10-20 real social-story policy fallback failure.

Policy headlines are not approved by the story safety guard. A fully sourced
policy headline must not suppress discovery of a nonpolicy eligible story.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator
from toutiao_content_factory.services.topic_selection_evidence import selection_evidence


def _topic(title, url, verified=False):
    item = TopicCandidate(title, url, "rss", "rss", snippet="公开报道描述了真实事项。" * 50)
    if verified:
        item._verified_source_material = True
        item._material_evidence_assessment = {"passed": True}
    return item


def _setup(app_settings, logger, monkeypatch):
    from toutiao_content_factory.services import reader_value_experiment
    monkeypatch.setattr(reader_value_experiment, "ensure_batch", lambda *a, **k: {"status": "ready"})
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator = WeiToutiaoStandaloneGenerator(app_settings, logger)
    generator._rank_topics = lambda topics: list(topics)
    generator.topic_automation._available_topics = lambda topics: list(topics)
    generator.topic_automation.filter_topics_for_account = lambda topics: list(topics)
    generator.story_sources.can_retry = MagicMock(return_value=True)
    generator.story_sources.record_result = MagicMock()
    generator.topic_automation._hydrate_topic_material = MagicMock(return_value=True)
    generator.topic_automation.topic_pool_service.build_fallback_report = MagicMock(
        return_value=SimpleNamespace(topics=[]))
    return generator


def test_verified_policy_headline_does_not_consume_only_source_slot(app_settings, logger, monkeypatch):
    generator = _setup(app_settings, logger, monkeypatch)
    policy = _topic("医保政策调整后参保人员怎么办", "https://example.test/policy", verified=True)
    verified_story = _topic("业主退还押金后双方确认收据内容", "https://example.test/story", verified=True)
    assert selection_evidence(policy)["priority"] == 0
    generator.topic_automation._load_daily_topics = MagicMock(
        return_value=SimpleNamespace(topics=[policy]))
    generator.story_sources.candidates = MagicMock(return_value=[verified_story])

    selected = generator.select_topics(count=1)

    assert [item.title for item in selected] == [verified_story.title]
    generator.story_sources.candidates.assert_called_once()
    generator.topic_automation._hydrate_topic_material.assert_called_once()
    generator.topic_automation._hydrate_topic_material.assert_called_with(
        verified_story, deadline=generator.topic_automation._hydrate_topic_material.call_args.kwargs["deadline"])


def test_policy_only_pool_never_starts_writing_or_draft(app_settings, logger, monkeypatch):
    generator = _setup(app_settings, logger, monkeypatch)
    policy = _topic("国家新规如何影响养老保险", "https://example.test/policy", verified=True)
    generator.topic_automation._load_daily_topics = MagicMock(
        return_value=SimpleNamespace(topics=[policy]))
    generator.story_sources.candidates = MagicMock(return_value=[])
    generator.generate_content = MagicMock()
    generator.publish_service.publish_weitoutiao = MagicMock()

    result = generator.publish_standalone(count=1)

    assert result["accepted"] == 0
    assert result["selected"] == 0
    generator.generate_content.assert_not_called()
    generator.publish_service.publish_weitoutiao.assert_not_called()
