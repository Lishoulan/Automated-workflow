from unittest.mock import MagicMock

from toutiao_content_factory.models import (
    CacheContext,
    ContentScore,
    DataReport,
    EditorialCouncilReport,
    ExtractionResult,
    GeneratedContent,
    TrendReport,
)
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService


def test_workflow_passes_council_decision_to_writer(
    app_settings,
    mock_openai_service,
    logger,
    monkeypatch,
) -> None:
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    app_settings.expert_council_enabled = True
    app_settings.enhanced_insights_enabled = False

    extraction = ExtractionResult(
        merged_text="公开材料显示储能业务增长67%。",
        core_data=["储能业务增长67%"],
        industry_trends=["储能需求变化"],
        growth_numbers=["67%"],
        companies=["宁德时代"],
        supply_chain_keywords=["储能"],
        key_points=["增长需要交付验证"],
        focus_industry="新能源",
        topic_title="储能增长的兑现问题",
        topic_quality_score=80,
    )
    trend_report = TrendReport(recommended_angle="从增长转向交付验证")
    data_report = DataReport(
        core_data=["储能业务增长67%"],
        evidence_notes=["公开材料显示储能业务增长67%"],
        companies=["宁德时代"],
        focus_industry="新能源",
        topic_title=extraction.topic_title,
        topic_quality_score=80,
    )
    council_report = EditorialCouncilReport(
        topic_title=extraction.topic_title,
        editorial_thesis="储能增长的分水岭在交付兑现，而不是增长数字本身。",
        originality_angle="从增长叙事切换到兑现链条",
        evidence_chain=["储能业务增长67%"],
        causal_chain=["增长 -> 交付 -> 回款"],
        confidence_score=0.9,
    )
    generated = GeneratedContent(article="一篇经过专家团裁决的文章。")

    service.evolution_agent.run = MagicMock()
    service.trend_agent.run = MagicMock(return_value=trend_report)
    service.data_agent.run = MagicMock(return_value=data_report)
    service.editorial_council_agent.run = MagicMock(return_value=council_report)
    service.writer_agent.run_with_insights = MagicMock(return_value=generated)
    service.polish_agent.run = MagicMock(return_value={})
    service.title_agent.run = MagicMock(return_value={})
    service.review_agent.run = MagicMock(return_value={"content_score": ContentScore(pass_status="approved")})
    service.publish_review_agent.run = MagicMock(
        return_value={
            "publish_readiness_score": 90,
            "publish_risk_flags": [],
            "publish_recommendation": "auto_publish",
            "publish_notes": [],
        }
    )
    monkeypatch.setattr(service, "_apply_polish_pack", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_apply_title_pack", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_filter_titles_by_quality", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_apply_review_pack", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_apply_bad_words_gate", lambda *args, **kwargs: None)

    result = service.run(
        extraction,
        style_key="deep_industry_analysis",
        cache_context=CacheContext(),
        fast_mode=True,
    )

    service.editorial_council_agent.run.assert_called_once()
    service.writer_agent.run_with_insights.assert_called_once()
    assert result.editorial_council is council_report
    passed_insight = service.writer_agent.run_with_insights.call_args.args[2]
    assert passed_insight.main_insight == council_report.editorial_thesis
    assert passed_insight.originality_angle == council_report.originality_angle
