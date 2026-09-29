from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

from toutiao_content_factory.exceptions import AIRequestError
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.topic_pool_service import TopicPoolService
from toutiao_content_factory.services.weitoutiao_generator import (
    WeiToutiaoStandaloneGenerator,
)


def _topic() -> TopicCandidate:
    return TopicCandidate(
        title="AI 硬件订单变化值得关注",
        url="https://example.test/topic",
        source="公开信息",
        source_type="test",
        summary="多家产业链公司近期披露了与 AI 硬件相关的订单和交付变化，市场关注点正在从概念热度转向真实需求、产能兑现和利润质量。",
        snippet="AI 硬件订单和交付变化",
        matched_keywords=["AI", "硬件", "订单"],
        rank=1,
        total_score=90,
    )


def test_transient_model_failure_uses_local_fallback_and_publishes(app_settings, tmp_path: Path) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    generator.select_topics = MagicMock(return_value=[_topic()])
    generator.generate_content = MagicMock(
        side_effect=AIRequestError("upstream returned 502 Bad Gateway")
    )
    generator.publish_service.publish_weitoutiao = MagicMock(
        return_value={
            "status": "submitted_for_review",
            "published_at": "2026-08-28T09:00:00",
            "external_url": "https://example.test/weitoutiao",
        }
    )

    result = generator.publish_standalone(count=1, purpose="test_fallback")

    assert result["status"] == "success"
    assert result["selected"] == 1
    assert result["attempted"] == 1
    assert result["generated"] == 1
    assert result["accepted"] == 1
    assert result["fallback_count"] == 1
    assert result["fallback_used"] is True
    assert result["published"][0]["generation_mode"] == "local_fallback"
    generator.publish_service.publish_weitoutiao.assert_called_once()

    run_dir = Path(result["published"][0]["run_dir"])
    publish_meta = json.loads(
        (run_dir / app_settings.review.publish_dirname / "publish_meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert publish_meta["generation_mode"] == "local_fallback"
    assert publish_meta["generation_fallback_reason"]
    assert publish_meta["content_char_count"] >= 200

    report = json.loads(Path(result["result_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "success"
    assert report["fallback_count"] == 1
    assert report["purpose"] == "test_fallback"


def test_html_model_output_is_blocked_before_publish_or_history(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    generator.select_topics = MagicMock(return_value=[_topic()])
    generator.generate_content = MagicMock(
        return_value="<!doctype html><html><body>gateway page</body></html>"
    )
    generator.publish_service.publish_weitoutiao = MagicMock()

    result = generator.publish_standalone(count=1, purpose="test_html_response")

    assert result["status"] == "failed"
    assert result["accepted"] == 0
    assert result["skipped"][0]["reason"] == "generate_failed"
    assert "HTML document" in result["skipped"][0]["error"]
    generator.publish_service.publish_weitoutiao.assert_not_called()
    history_path = app_settings.output_dir / app_settings.publishing.history_filename
    assert not history_path.exists()


def test_non_transient_model_failure_is_recorded_without_fallback(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    generator.select_topics = MagicMock(return_value=[_topic()])
    generator.generate_content = MagicMock(side_effect=AIRequestError("401 Unauthorized"))
    generator.publish_service.publish_weitoutiao = MagicMock()

    result = generator.publish_standalone(count=1, purpose="test_auth_failure")

    assert result["status"] == "failed"
    assert result["accepted"] == 0
    assert result["fallback_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"] == "generate_failed"
    assert "401" in result["skipped"][0]["error"]
    generator.publish_service.publish_weitoutiao.assert_not_called()


def test_unconfigured_model_does_not_generate_low_quality_fallback(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    generator.select_topics = MagicMock(return_value=[_topic()])
    generator.generate_content = MagicMock(
        side_effect=AIRequestError("Model client is not available")
    )
    generator.publish_service.publish_weitoutiao = MagicMock()

    result = generator.publish_standalone(count=1, purpose="test_no_model")

    assert result["status"] == "failed"
    assert result["fallback_count"] == 0
    assert result["skipped"][0]["reason"] == "generate_failed"
    generator.publish_service.publish_weitoutiao.assert_not_called()


def test_insufficient_balance_uses_local_fallback_for_all_selected_topics(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    topics = [
        _topic(),
        TopicCandidate(
            title="机器人交付节奏变化值得观察",
            url="https://example.test/topic-2",
            source="公开信息",
            source_type="test",
            summary="机器人行业的公开材料把讨论从演示效果推向交付节奏和实际应用，后续仍需等待新的可核验进展。",
            snippet="机器人交付节奏变化",
            matched_keywords=["机器人", "交付"],
            rank=2,
            total_score=85,
        ),
    ]
    generator.select_topics = MagicMock(return_value=topics)
    generator.generate_content = MagicMock(
        side_effect=[
            AIRequestError("Model request disabled for this run because the provider account balance is insufficient."),
            AIRequestError("Model client is not available."),
        ]
    )
    generator.openai_service._unavailable_reason = "Error code: 402 - account balance is insufficient"
    generator.publish_service.publish_weitoutiao = MagicMock(
        return_value={"status": "submitted_for_review"}
    )

    result = generator.publish_standalone(count=2, purpose="test_balance_fallback")

    assert result["status"] == "success"
    assert result["accepted"] == 2
    assert result["fallback_count"] == 2
    assert result["skipped_count"] == 0
    assert all(item["generation_mode"] == "local_fallback" for item in result["published"])
    assert generator.publish_service.publish_weitoutiao.call_count == 2


def test_incomplete_topic_pool_with_all_selected_items_accepted_is_success(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    generator.select_topics = MagicMock(return_value=[_topic()])
    generator.generate_content = MagicMock(return_value="有效内容 " * 80)
    generator.publish_service.publish_weitoutiao = MagicMock(
        return_value={"status": "submitted_for_review"}
    )

    result = generator.publish_standalone(count=2, purpose="test_incomplete_pool")

    assert result["status"] == "success"
    assert result["requested"] == 2
    assert result["selected"] == 1
    assert result["accepted"] == 1
    assert result["selection_shortfall"] == 1
    assert "No additional eligible topics" in result["message"]


def test_topic_ranking_uses_72_hour_feedback(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    feedback = {
        "topic_feedback": {
            "weights": {"房产继承": 18, "借贷纠纷": -24},
            "stop_titles": [],
        }
    }
    app_settings.output_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.output_dir / "growth_feedback.json").write_text(
        json.dumps(feedback, ensure_ascii=False), encoding="utf-8"
    )
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    weak = _topic()
    weak.title = "老人借钱给亲戚没打借条"
    weak.rank = 1
    strong = _topic()
    strong.title = "老人房产继承最后怎么处理"
    strong.rank = 10

    ranked = generator._rank_topics([weak, strong])

    assert ranked[0].title == strong.title


def test_fallback_report_merges_pending_and_evergreen_topics(app_settings) -> None:
    service = TopicPoolService(app_settings, logging.getLogger("test.topic_pool"))
    pending = _topic()
    evergreen = TopicCandidate(
        title="机器人交付节奏变化值得观察",
        url="evergreen://default/robot-delivery",
        source="evergreen_pool",
        source_type="evergreen",
        summary="围绕机器人交付节奏和实际应用拆解可验证的经营变量。",
        matched_industries=["humanoid_robot"],
        matched_keywords=["机器人", "交付"],
        total_score=80,
    )
    service.list_pending_topics = MagicMock(return_value=[pending])
    service._load_evergreen_topics = MagicMock(return_value=[evergreen])

    report = service.build_fallback_report()

    assert len(report.topics) == 2
    assert {topic.url for topic in report.topics} == {
        pending.url,
        evergreen.url,
    }
    assert report.source_stats["topic_pool"]["raw_count"] == 1
    assert report.source_stats["evergreen"]["raw_count"] == 1
