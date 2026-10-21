from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import json

import pytest

from toutiao_content_factory.config import RevenueOptimizationSettings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.material_search_service import MaterialBundle, MaterialSource

NOW = datetime(2026, 10, 21, 9, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def article_app(app_settings, logger, monkeypatch):
    app_settings.account_key = "robot_notes"
    app_settings.revenue_optimization = RevenueOptimizationSettings.from_dict({
        "adaptive_topic_selection": True, "operating_trial": {
            "id": "robot_notes_profit_20261018", "started_at": "2026-10-18T00:00:00+08:00",
            "directions": ["consumer_practical", "technology_everyday"]}})
    app_settings.automation = replace(app_settings.automation, max_daily_topic_attempts=2)
    app = ContentFactoryAppService(app_settings, logger)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    for module in ["app_service", "automation", "revenue_feedback", "topic_collection"]:
        monkeypatch.setattr("toutiao_content_factory.services." + module + ".datetime", Clock)
    return app


def source_body():
    return "\n".join(f"2026年10月{n}日，消费者提交第{n}份退款申请并取得签收记录。"
                    "报道区分已交付服务和未交付部分，经营者提供合同及收费明细，消费者保留付款凭证。"
                    "有关部门介绍受理程序和办理时限，核对后只确认材料所列事项，不将单个案例推广为普遍结果。"
                    for n in range(1, 11))


def write_topics(app, topics):
    report = DailyTopicsReport(NOW.isoformat(), 2, 24, [], len(topics), len(topics), topics)
    app.settings.output_dir.mkdir(parents=True, exist_ok=True)
    (app.settings.output_dir / "daily_topics.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False), encoding="utf-8")


def lead(title="会员服务出现争议", url="https://source.example/report"):
    return TopicCandidate(title, url, "公开报道", "rss", published_at=NOW.isoformat(),
                          summary="事件进展待核实", quality_flags=["title_only"],
                          evidence_strength=20, quality_score=25)


def specs(app):
    return app._build_daily_candidate_specs(topic_rank=None, topic_title=None, refresh_cache=False, dry_run=False)


def test_unclassified_thin_lead_is_verified_and_rescored_before_direction_and_writing(article_app, monkeypatch):
    topic = lead()
    write_topics(article_app, [topic])
    collect = MagicMock(return_value=MaterialBundle(topic.title, [MaterialSource(topic.title, topic.url, source_body(), True)]))
    monkeypatch.setattr("toutiao_content_factory.services.material_search_service.collect_material", collect)
    result = specs(article_app)
    assert len(result) == 1
    selection = article_app.topic_automation.prepare_auto_run(title_query=topic.title)
    assert selection is not None
    assert selection.topic._material_evidence_assessment["passed"] is True
    assert "消费者" in selection.input_text
    assert selection.topic.revenue_selection["category"] == "consumer_practical"
    assert selection.topic.revenue_selection["approval"] is False
    assert collect.call_count == 1
    funnel = article_app._article_selection_funnel
    assert funnel["after_account_and_dedup"] == funnel["after_content_guard"] == 1
    assert funnel["after_material_and_direction"] == funnel["eligible_for_writing"] == 1
    assert funnel["review_approved"] is False
    assert funnel["material_attempts"][0]["status"] == "eligible_for_writing"


@pytest.mark.parametrize("kind", ["digest", "no_direction", "no_facts"])
def test_unverified_or_out_of_direction_material_never_becomes_a_writing_candidate(article_app, monkeypatch, kind):
    topic = lead()
    body = source_body() if kind == "digest" else "\n".join(
        f"公司于2026年10月{n}日披露第{n}份上市材料，公告说明组织架构和审批进度。" * 3 for n in range(1, 12))
    if kind == "no_facts":
        body = "消费者退款应遵循具体条件，笼统建议没有可核对的事件信息。" * 60
    bundle = MaterialBundle(topic.title, [MaterialSource(topic.title, topic.url, body, True,
                             evidence_type="search_digest" if kind == "digest" else "article")])
    write_topics(article_app, [topic])
    monkeypatch.setattr("toutiao_content_factory.services.material_search_service.collect_material", MagicMock(return_value=bundle))
    assert specs(article_app) == []
    assert article_app._article_selection_funnel["eligible_for_writing"] == 0


def test_published_duplicates_are_rejected_before_material_requests(article_app, monkeypatch):
    topic = lead()
    write_topics(article_app, [topic])
    (article_app.settings.output_dir / article_app.settings.publishing.history_filename).write_text(json.dumps([
        {"topic_url": topic.url, "topic_title": topic.title, "published_at": NOW.isoformat()}]), encoding="utf-8")
    collect = MagicMock(side_effect=AssertionError("Duplicate must not retrieve material"))
    monkeypatch.setattr("toutiao_content_factory.services.material_search_service.collect_material", collect)
    assert specs(article_app) == []
    collect.assert_not_called()


def test_material_preparation_cannot_exceed_existing_topic_attempt_limit(article_app, monkeypatch):
    write_topics(article_app, [lead("待核实事件" + str(n), "https://source.example/" + str(n)) for n in range(5)])
    collect = MagicMock(return_value=MaterialBundle("none", []))
    monkeypatch.setattr("toutiao_content_factory.services.material_search_service.collect_material", collect)
    assert specs(article_app) == []
    assert collect.call_count == 2


def test_multi_news_digest_is_not_an_independent_long_article_source(article_app, monkeypatch):
    topic = lead("【钛晨报】智能制造政策；数据集团成立；DeepSeek推理框架开源")
    topic.snippet = source_body()
    write_topics(article_app, [topic])
    collect = MagicMock(side_effect=AssertionError("A digest cannot become a selected story"))
    monkeypatch.setattr("toutiao_content_factory.services.material_search_service.collect_material", collect)
    assert specs(article_app) == []
    assert article_app._article_selection_funnel["material_attempts"][0]["status"] == "multi_topic_digest_not_story"
    collect.assert_not_called()


def test_run_local_prepared_material_still_expires(article_app, monkeypatch):
    topic = lead()
    write_topics(article_app, [topic])
    collect = MagicMock(return_value=MaterialBundle(topic.title, [MaterialSource(topic.title, topic.url, source_body(), True)]))
    monkeypatch.setattr("toutiao_content_factory.services.material_search_service.collect_material", collect)
    assert len(specs(article_app)) == 1
    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            later = NOW + timedelta(days=2)
            return later.astimezone(tz) if tz else later.replace(tzinfo=None)
    monkeypatch.setattr("toutiao_content_factory.services.topic_collection.datetime", LaterClock)
    assert specs(article_app) == []
    assert collect.call_count == 1
