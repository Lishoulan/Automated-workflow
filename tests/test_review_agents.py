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
