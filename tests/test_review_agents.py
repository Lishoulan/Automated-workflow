from unittest.mock import MagicMock

from toutiao_content_factory.agents.publish_review_agent import PublishReviewAgent
from toutiao_content_factory.agents.review_agent import ReviewAgent
from toutiao_content_factory.models import CacheContext, ContentScore, DataReport, GeneratedContent
from toutiao_content_factory.services.prompt_repository import PromptRepository


def _agent_dependencies(app_settings, logger, openai_service):
    prompt_repository = MagicMock(spec=PromptRepository)
    prompt_repository.render.return_value = "review prompt"
    return prompt_repository, openai_service


def _data_report() -> DataReport:
    return DataReport(
        topic_title="Robot orders and the revenue question",
        focus_industry="robotics",
        companies=["Example Robotics"],
        evidence_notes=["Public filing reports the company's disclosed order data."],
        key_points=["Delivery and service revenue need separate tracking."],
    )


def _content() -> GeneratedContent:
    return GeneratedContent(
        article=(
            "At the factory, the important question is not only how many robots were sold. "
            "The article compares disclosed orders, delivery timing, and follow-up service revenue "
            "so readers can track whether the headline growth becomes recurring business."
        ),
        summary="A reviewable robotics business analysis.",
        article_angle="Separate shipment volume from realized revenue.",
        recommended_titles=["Robot revenue map"],
        titles=["Robot revenue map"],
        tags=["robotics"],
        validation_score=90,
    )


def test_review_agent_falls_back_when_model_omits_score_fields(app_settings, logger) -> None:
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {"ai_generated_risk": 18}
    prompt_repository, openai_service = _agent_dependencies(app_settings, logger, openai_service)
    agent = ReviewAgent(app_settings, prompt_repository, openai_service, logger)
    agent.growth_ops_service = MagicMock()
    agent.growth_ops_service.get_self_benchmark_track_context.return_value = {}
    agent.growth_ops_service.build_benchmark_digest.return_value = ""

    result = agent.run(
        _content(),
        _data_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        CacheContext(),
    )

    score = result["content_score"]
    assert isinstance(score, ContentScore)
    assert score.title_attraction > 20
    assert score.information_density > 20


def test_publish_review_agent_falls_back_when_model_omits_readiness(app_settings, logger) -> None:
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {"publish_notes": []}
    prompt_repository, openai_service = _agent_dependencies(app_settings, logger, openai_service)
    agent = PublishReviewAgent(app_settings, prompt_repository, openai_service, logger)
    agent.growth_ops_service = MagicMock()
    agent.growth_ops_service.get_self_benchmark_track_context.return_value = {}
    agent.growth_ops_service.build_benchmark_digest.return_value = ""

    result = agent.run(
        _content(),
        _data_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        ContentScore(overall_score=90, ai_generated_risk=18),
    )

    assert result["publish_readiness_score"] >= 78
    assert result["publish_recommendation"] == "manual_review"


def test_score_validation_accepts_zero_but_rejects_missing_invalid_and_out_of_range(app_settings, logger):
    openai_service = MagicMock()
    openai_service.available = False
    prompt_repository = MagicMock(spec=PromptRepository)
    agent = ReviewAgent(app_settings, prompt_repository, openai_service, logger)

    assert agent._validate_score_fields({"score": 0}, ("score",)) is True
    assert agent._validate_score_fields({}, ("score",)) is False
    assert agent._validate_score_fields({"score": True}, ("score",)) is False
    assert agent._validate_score_fields({"score": 101}, ("score",)) is False


def _hot_topic_report(**kwargs) -> DataReport:
    payload = {
        "topic_title": "热榜机器人消息的商业化信号",
        "is_hot_topic": True,
        "content_format": "quick_commentary",
        "word_target": 800,
    }
    payload.update(kwargs)
    return DataReport(**payload)


def _publish_review_agent(app_settings, logger, openai_service) -> PublishReviewAgent:
    prompt_repository, openai_service = _agent_dependencies(app_settings, logger, openai_service)
    agent = PublishReviewAgent(app_settings, prompt_repository, openai_service, logger)
    agent.growth_ops_service = MagicMock()
    agent.growth_ops_service.get_self_benchmark_track_context.return_value = {}
    agent.growth_ops_service.build_benchmark_digest.return_value = ""
    return agent


def test_publish_review_hot_topic_note_distinguishes_manual_and_auto_contracts(
    app_settings,
    logger,
) -> None:
    openai_service = MagicMock()
    openai_service.available = False
    agent = _publish_review_agent(app_settings, logger, openai_service)

    manual_note = agent._hot_topic_publish_note(_hot_topic_report())
    assert "人工审核短评" in manual_note
    assert "最低850字" in manual_note
    assert "600-900字" not in manual_note

    object.__setattr__(app_settings.publishing, "enabled", True)
    object.__setattr__(app_settings.publishing, "auto_publish", True)
    auto_note = agent._hot_topic_publish_note(_hot_topic_report())
    assert "自动发布长文候选" in auto_note
    assert "约1800字" in auto_note
    assert "最低1750字" in auto_note
    assert "证据不足" in auto_note


def test_publish_review_length_gate_overrides_high_model_score_for_short_manual_hot_topic(
    app_settings,
    logger,
) -> None:
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {
        "publish_readiness_score": 98,
        "publish_recommendation": "ready_to_publish",
    }
    agent = _publish_review_agent(app_settings, logger, openai_service)
    content = GeneratedContent(
        article="热点机器人正在受到关注。" * 40,
        polished_article="热点机器人正在受到关注。" * 40,
        recommended_titles=["机器人热度之后，商业化看什么"],
    )

    result = agent.run(
        content,
        _hot_topic_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        ContentScore(overall_score=95, ai_generated_risk=5),
    )

    assert result["publish_readiness_score"] == 0
    assert result["publish_recommendation"] == "revise_before_publish"
    assert any("最低850字" in item for item in result["publish_risk_flags"])


def test_publish_review_length_gate_uses_final_polished_article_for_auto_publish(
    app_settings,
    logger,
) -> None:
    object.__setattr__(app_settings.publishing, "enabled", True)
    object.__setattr__(app_settings.publishing, "auto_publish", True)
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {
        "publish_readiness_score": 98,
        "publish_recommendation": "ready_to_publish",
    }
    agent = _publish_review_agent(app_settings, logger, openai_service)
    content = GeneratedContent(
        article="完整原稿。" * 500,
        polished_article="清理后的短稿。" * 200,
        recommended_titles=["机器人热度之后，商业化看什么"],
    )

    result = agent.run(
        content,
        _hot_topic_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        ContentScore(overall_score=95, ai_generated_risk=5),
    )

    assert result["publish_readiness_score"] == 0
    assert result["publish_recommendation"] == "revise_before_publish"
    assert any("最低1750字" in item for item in result["publish_risk_flags"])


def test_publish_review_length_gate_allows_manual_hot_topic_at_boundary(
    app_settings,
    logger,
) -> None:
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {
        "publish_readiness_score": 88,
        "publish_recommendation": "manual_review",
    }
    agent = _publish_review_agent(app_settings, logger, openai_service)
    unit = "机器人商业化仍需看交付和复购。"
    # 取最小重复次数，使正文刚好达到（并略高于）快评最低字数下限。
    # 用常量推导而不是写死字数，阈值后续调整时这条边界测试不会失效。
    repeat = -(-agent.HOT_TOPIC_MANUAL_MIN_CHARS // len(unit))
    content = GeneratedContent(
        article=unit * repeat,
        recommended_titles=["机器人热度之后，商业化看什么"],
    )
    article_length = len(unit * repeat)
    assert article_length >= agent.HOT_TOPIC_MANUAL_MIN_CHARS

    result = agent.run(
        content,
        _hot_topic_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        ContentScore(overall_score=90, ai_generated_risk=10),
    )

    assert result["publish_readiness_score"] == 88
    assert result["publish_recommendation"] == "manual_review"
