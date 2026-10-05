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


def test_opening_assignment_advances_only_after_accepted_model_submission(app_settings):
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    topic = _topic()
    topic._content_experiment = generator._opening_experiment()
    assert topic._content_experiment["variant"] == "result_first"
    run_dir = generator.build_run_package(topic, "事实标题\n" + "事实与分析。" * 40, "story_comment")
    generator._write_publish_result_artifact(run_dir, {"status": "publish_uncertain"})
    assert generator._opening_experiment()["variant"] == "result_first"
    generator._write_publish_result_artifact(run_dir, {"status": "submitted_for_review", "published_at": "2026-10-05T09:00:00+08:00"})
    assert generator._opening_experiment()["variant"] == "situation_first"
    generator._write_publish_result_artifact(run_dir, {"status": "submitted_for_review", "published_at": "2026-10-05T09:00:00+08:00"})
    ledger = json.loads((app_settings.output_dir / "growth_ops/performance/wtt_content_experiments.json").read_text(encoding="utf-8"))
    assert len(ledger["records"]) == 1


def test_reusing_topic_does_not_keep_an_experiment_from_another_persona(app_settings):
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    topic = _topic()
    topic._content_experiment = {"id": "story_opening_v1", "variant": "result_first"}
    generator.openai_service.create_text_completion = MagicMock(return_value="素材中的事实。" * 30)
    generator.generate_content(topic, "hot_comment")
    assert topic._content_experiment == {}


def test_large_source_copy_is_blocked_but_short_fact_matches_are_allowed(app_settings):
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    topic = _topic()
    topic._verified_source_material = True
    topic.snippet = "民警沿着监控记录中的路线找到捡到包裹的老人，核对信息后将完好的包裹交还给报警的快递员。" * 10
    copied = "包裹被寻回\n" + topic.snippet
    assert generator._model_story_fallback_reason(topic, "story_comment", copied) == "story_source_copy_guard"
    assert generator._source_copy_share(topic, "包裹被寻回\n民警交还包裹。\n从这些事实看，查到线索和找回物品是两步。") == 0


def test_transient_model_failure_uses_local_fallback_and_publishes(app_settings, tmp_path: Path) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    generator._iter_eligible_topics = MagicMock(return_value=[_topic()])
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
    generator._iter_eligible_topics = MagicMock(return_value=[_topic()])
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
    generator._iter_eligible_topics = MagicMock(return_value=[_topic()])
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
    generator._iter_eligible_topics = MagicMock(return_value=[_topic()])
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
    generator._iter_eligible_topics = MagicMock(return_value=topics)
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
    generator._iter_eligible_topics = MagicMock(return_value=[_topic()])
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


def test_generation_failure_uses_next_topic_without_fetching_unused_reserves(app_settings):
    from types import SimpleNamespace

    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.retry"))
    topics = [_topic() for _ in range(3)]
    for i, topic in enumerate(topics):
        topic.title = f"老人房产继承案例第{i}件"
        topic.rank = i + 1
    generator.topic_automation._load_daily_topics = MagicMock(return_value=SimpleNamespace(topics=topics))
    generator.topic_automation._available_topics = MagicMock(side_effect=lambda items: items)
    generator.topic_automation.topic_pool_service.build_fallback_report = MagicMock(return_value=SimpleNamespace(topics=[]))
    generator.topic_automation._hydrate_topic_material = MagicMock(return_value=True)
    generator.generate_content = MagicMock(side_effect=["字数不足", "公开报道的事件经过。" * 35])
    generator.build_local_fallback_content = MagicMock(return_value="")
    generator.publish_service.publish_weitoutiao = MagicMock(return_value={"status": "submitted_for_review"})

    result = generator.publish_standalone(count=1)

    assert result["status"] == "success"
    assert result["accepted"] == 1
    assert result["selected"] == 2
    assert result["skipped"][0]["reason"] == "content_too_short"
    assert result["published"][0]["topic_title"] == topics[1].title
    assert generator.topic_automation._hydrate_topic_material.call_count == 2
    generator.publish_service.publish_weitoutiao.assert_called_once()


def test_replacement_attempts_stop_at_budget_and_publication_uncertainty(app_settings):
    from types import SimpleNamespace

    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.retry"))
    topics = [_topic() for _ in range(6)]
    for i,t in enumerate(topics):
        t.title = f"AI订单第{i}件"
        t.rank = i + 1
    generator.topic_automation._load_daily_topics = MagicMock(return_value=SimpleNamespace(topics=topics))
    generator.topic_automation._available_topics = MagicMock(side_effect=lambda items: items)
    generator.generate_content = MagicMock(return_value="短稿")
    generator.build_local_fallback_content = MagicMock(return_value="")
    generator.publish_service.publish_weitoutiao = MagicMock()
    result = generator.publish_standalone(count=1)
    assert result["selected"] == 4
    assert result["accepted"] == 0
    assert generator.generate_content.call_count == 4
    generator.publish_service.publish_weitoutiao.assert_not_called()

    for failure in [{"status": "publish_uncertain"}, {"status": "publish_login_required"},
                    {"status": "blocked_by_ip"}, {"status": "unknown"}, TimeoutError("submit may have succeeded")]:
        generator.generate_content = MagicMock(return_value="有效事件经过。" * 50)
        generator.publish_service.publish_weitoutiao = MagicMock(side_effect=[failure])
        result = generator.publish_standalone(count=1)
        assert result["accepted"] == 0
        assert result["selected"] == 1
        generator.publish_service.publish_weitoutiao.assert_called_once()


def test_case_prompt_keeps_original_year_and_limited_gift_scope(app_settings):
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    topic = TopicCandidate(
        title="奶奶赠房给孙女后被起诉，法院判决撤销本人份额赠与",
        url="https://www.court.gov.cn/zixun/xiangqing/365111.html",
        source="最高人民法院公开案例", source_type="evergreen", topic_type="evergreen",
        published_at="2022-07-11T09:33:43", summary="仅撤销本人份额，不能写成收回全部三套房。",
        snippet="真实报道正文。" * 150,
    )
    topic._verified_source_material = True
    generator.openai_service.create_text_completion = MagicMock(return_value="合格正文" * 70)
    generator.generate_content(topic, persona="story_comment")
    prompt = generator.openai_service.create_text_completion.call_args.kwargs["user_prompt"]
    assert "2022-07-11" in prompt
    assert "不得写成今日或近日" in prompt
    assert "不能写成收回全部三套房" in prompt


def test_surveillance_lead_cannot_be_rewritten_as_recovery_location(app_settings):
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    topic = TopicCandidate(title="快递员丢包裹", url="", source="公开报道", source_type="test",
                           snippet="民警调阅交警高位探头。顺着轨迹，民警找到老人，包裹完好无损。")
    assert generator._model_story_fallback_reason(topic, "story_comment", "民警在高位探头下找到了手机。") == "story_event_sequence_guard"
    assert generator._model_story_fallback_reason(topic, "story_comment", "监控记录提供线索，民警顺着轨迹找到老人并寻回包裹。") == ""


def test_verified_story_uses_corrected_review_and_review_failures_block_publish(app_settings):
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt"))
    topic = _topic()
    topic.title = "快递员丢包裹，民警沿监控线索寻回"
    topic.snippet = "【来源：公开报道】丢包者报警，民警沿轨迹找到捡包者并取回包裹。"
    topic._verified_source_material = True
    corrected = "民警沿监控线索找到捡包者，取回包裹。" * 15
    generator.openai_service.create_text_completion = MagicMock(side_effect=["丢包者没私自拆包。", corrected])
    content = generator.generate_content(topic, "story_comment")
    assert corrected in content
    assert "丢包者没私自拆包" not in content
    assert generator.openai_service.create_text_completion.call_count == 2
    review = generator.openai_service.create_text_completion.call_args.kwargs
    assert topic.snippet in review["user_prompt"]
    assert "待复核稿" in review["user_prompt"]
    assert review["temperature"] == 0.1
    generator._iter_eligible_topics = MagicMock(return_value=[topic])
    generator.publish_service.publish_weitoutiao = MagicMock()
    for failed_review in [AIRequestError("review unavailable"), "<html><body>gateway</body></html>"]:
        generator.openai_service.create_text_completion = MagicMock(side_effect=[corrected, failed_review])
        result = generator.publish_standalone(count=1)
        assert result["accepted"] == 0
        assert result["skipped_count"] == 1
    generator.publish_service.publish_weitoutiao.assert_not_called()


def test_story_local_fallback_has_no_industry_template_language(app_settings) -> None:
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.story"))
    topic = TopicCandidate(
        title="农民交公粮能否视同缴社保",
        url="https://example.test/story",
        source="公开信息",
        source_type="hot_board",
        summary="有观点建议研究历史交公粮记录与养老保障之间的衔接，当前仍属于公开讨论。",
        matched_keywords=[],
    )

    content = generator.build_local_fallback_content(topic, persona="story_comment")

    assert "公开讨论不等于规则已经改变" in content
    assert "#社会热点" in content
    for marker in ("产业链", "上下游", "订单", "利润兑现"):
        assert marker not in content


def test_story_policy_classifier_ignores_unrelated_snippet_and_tags(app_settings) -> None:
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.story"))
    topic = TopicCandidate(
        title="女子与家人失散48年国庆节终团圆",
        url="https://example.test/reunion",
        source="公开信息",
        source_type="hot_board",
        summary="失散多年的家人在国庆节完成认亲。",
        snippet="同一热榜还出现了退休和养老话题",
        matched_keywords=["退休"],
    )

    assert generator._is_public_policy_topic(topic, "story_comment") is False
    content = generator.build_local_fallback_content(topic, persona="story_comment")
    assert content == ""
    tags = generator._build_tags(topic, persona="story_comment")
    assert "社保" not in content
    assert "政策" not in content
    assert "#退休" not in tags
    assert "#社会热点" in tags


def test_weak_evidence_story_preview_does_not_pad_unsafe_content(app_settings) -> None:
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.story"))
    topic = TopicCandidate(
        title="地震瞬间2名护士一手抱一个婴儿疏散",
        url="https://example.test/quake",
        source="公开信息",
        source_type="hot_board",
        summary="地震发生时，两名护士迅速各抱一个婴儿疏散至停车场。",
        matched_industries=["social_story"],
        matched_keywords=[],
        quality_flags=["weak_subject", "weak_evidence"],
    )
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator._iter_eligible_topics = MagicMock(return_value=[topic])
    generator.generate_content = MagicMock(
        return_value=("两个宝宝都安全了。你会先保住孩子还是自己？" * 20)
    )

    result = generator.preview_standalone()
    assert result == {"status": "content_too_short", "exports": {}}
    assert not (app_settings.output_dir / "wtt_standalone").exists()


def test_story_selection_replaces_headline_without_verified_material(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.material"))
    weak, rich = _topic(), _topic()
    rich.title = "社区志愿者帮老人找到走失家人"
    rich.rank = 2
    generator.topic_automation._load_daily_topics = MagicMock(
        return_value=MagicMock(topics=[weak, rich])
    )
    generator.topic_automation._available_topics = MagicMock(side_effect=lambda topics: topics)
    generator.topic_automation._hydrate_topic_material = MagicMock(side_effect=[False, True])

    selected = generator.select_topics(count=1)

    assert selected == [rich]
    assert generator.topic_automation._hydrate_topic_material.call_count == 2


def test_verified_story_uses_report_body_instead_of_short_hotlist_summary(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.material"))
    topic = _topic()
    topic.title = "广场上妈妈将孩子交给女警"
    topic.source = "baidu_hot"
    topic.quality_flags = ["weak_evidence", "title_only"]
    topic.summary = "群众参加国庆合唱活动。"
    topic.snippet = "【来源：现场报道】\n" + "广场举行合唱活动。" * 55 + "妈妈请执勤女警帮忙照看孩子。"
    topic._verified_source_material = True
    generator._iter_eligible_topics = MagicMock(return_value=[topic])
    generator.openai_service.create_text_completion = MagicMock(
        return_value="根据报道，广场举行合唱活动，妈妈请执勤女警帮忙照看孩子。" * 12
    )
    generator.publish_service.publish_weitoutiao = MagicMock()

    result = generator.preview_standalone()

    assert result["status"] == "preview_ready"
    assert result["generation_mode"] == "model"
    prompt = generator.openai_service.create_text_completion.call_args.kwargs["user_prompt"]
    assert "妈妈请执勤女警帮忙照看孩子。" in prompt
    assert "【来源：现场报道】" in prompt
    assert "baidu_hot" not in prompt
    generator.publish_service.publish_weitoutiao.assert_not_called()


def test_short_local_fallback_is_rechecked_before_publishing(app_settings) -> None:
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.short"))
    generator._iter_eligible_topics = MagicMock(return_value=[_topic()])
    generator.generate_content = MagicMock(return_value="不足字数")
    generator.build_local_fallback_content = MagicMock(return_value="仍不足字数")
    generator.publish_service.publish_weitoutiao = MagicMock()

    result = generator.publish_standalone(count=1)

    assert result["accepted"] == 0
    assert result["skipped"][0]["reason"] == "content_too_short"
    generator.publish_service.publish_weitoutiao.assert_not_called()


def test_verified_story_still_blocks_speculation_and_health_advice(app_settings) -> None:
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.guards"))
    topic = _topic()
    topic._verified_source_material = True
    generator._iter_eligible_topics = MagicMock(return_value=[topic])
    generator.publish_service.publish_weitoutiao = MagicMock()

    for unsafe_text in (
        "姑娘当时估计就是馋坏了，没控制住自己。",
        "特别是有胃病、高血压这些基础病的，更得悠着点。",
        "吃坏了肚子，别硬扛着，赶紧去医院。",
    ):
        generator.generate_content = MagicMock(return_value=unsafe_text * 30)
        result = generator.publish_standalone(count=1)
        assert result["accepted"] == 0
        assert result["skipped"][0]["reason"] == "content_too_short"
    generator.publish_service.publish_weitoutiao.assert_not_called()


def test_story_model_content_without_tags_gets_story_tags(app_settings) -> None:
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.story"))
    topic = TopicCandidate(
        title="社区志愿者帮老人找到走失家人",
        url="https://example.test/community",
        source="公开信息",
        source_type="hot_board",
        summary="社区志愿者根据公开线索帮助老人找到家人。",
        matched_industries=["social_story"],
    )
    generator.openai_service.create_text_completion = MagicMock(return_value="已核实的故事内容。" * 30)

    content = generator.generate_content(topic, persona="story_comment")

    assert "#社会热点 #生活观察" in content
    assert "#产业观察" not in content


def test_standalone_preview_builds_package_without_publishing(app_settings) -> None:
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.preview"))
    generator._iter_eligible_topics = MagicMock(return_value=[_topic()])
    generator.generate_content = MagicMock(return_value="有效的微头条正文。" * 40)
    generator.publish_service.publish_weitoutiao = MagicMock()

    result = generator.preview_standalone()

    assert result["status"] == "preview_ready"
    assert Path(result["exports"]["standalone_priority_preview"]).exists()
    assert Path(result["exports"]["standalone_priority_article"]).exists()
    generator.publish_service.publish_weitoutiao.assert_not_called()


def test_policy_story_preview_replaces_hallucinated_model_case(app_settings) -> None:
    generator = WeiToutiaoStandaloneGenerator(app_settings, logging.getLogger("test.wtt.policy"))
    topic = TopicCandidate(
        title="农民交公粮能否视同缴社保",
        url="https://example.test/story",
        source="公开信息",
        source_type="hot_board",
        summary="有观点建议研究历史交公粮记录与养老保障之间的衔接，目前仍属于讨论。",
        matched_keywords=[],
    )
    object.__setattr__(app_settings.publishing, "weitoutiao_personas", ["story_comment"])
    generator._iter_eligible_topics = MagicMock(return_value=[topic])
    generator.generate_content = MagicMock(
        return_value="当事人当年被要求交公粮，还保留了合同、收据和转账记录。" * 10
    )

    result = generator.preview_standalone()
    content = Path(result["exports"]["standalone_priority_article"]).read_text(encoding="utf-8")

    assert result["generation_mode"] == "local_fallback"
    assert "当事人" not in content
    assert "合同" not in content
    assert "收据" not in content
    assert "公开讨论不等于规则已经改变" in content


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
