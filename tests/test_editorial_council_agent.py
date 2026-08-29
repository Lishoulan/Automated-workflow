from unittest.mock import MagicMock

from toutiao_content_factory.agents.editorial_council_agent import EditorialCouncilAgent
from toutiao_content_factory.models import (
    CacheContext,
    DataReport,
    EditorialCouncilReport,
    ExtractionResult,
    TrendReport,
)


def _build_inputs() -> tuple[ExtractionResult, TrendReport, DataReport, CacheContext]:
    extraction = ExtractionResult(
        merged_text="宁德时代储能业务同比增长67%，公开材料提到产能扩张。",
        core_data=["储能业务同比增长67%"],
        industry_trends=["储能需求继续增长"],
        growth_numbers=["67%"],
        companies=["宁德时代"],
        supply_chain_keywords=["储能", "电池"],
        key_points=["增长需要进一步观察交付兑现"],
        focus_industry="新能源",
        topic_title="储能增长背后的兑现问题",
        suggested_angle="储能的关键不只是增长，而是增长能否转化为稳定交付。",
    )
    trend = TrendReport(recommended_angle="观察增长与交付之间的落差")
    data = DataReport(
        core_data=["储能业务同比增长67%"],
        evidence_notes=["公开材料提到产能扩张"],
        risk_flags=["产能扩张不等于利润兑现"],
        watch_metrics=["交付量", "回款"],
        supply_chain_keywords=["储能", "电池"],
        focus_industry="新能源",
        topic_title=extraction.topic_title,
    )
    return extraction, trend, data, CacheContext(note="避开旧的增长复述角度")


def test_council_report_maps_to_writer_insight(
    app_settings,
    logger,
) -> None:
    prompt_repository = MagicMock()
    prompt_repository.render.return_value = "council prompt"
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {
        "topic_title": "储能增长背后的兑现问题",
        "perspectives": [
            {
                "role": "devils-advocate",
                "judgment": "产能扩张不等于订单和回款同步改善",
                "alternative_angle": "增长之后先看回款",
                "confidence": 0.8,
            }
        ],
        "chief_editor": {
            "editorial_thesis": "储能增长的分水岭在交付和回款，而不是产能公告本身。",
            "originality_angle": "从增长叙事切换到兑现链条",
            "evidence_chain": ["储能业务同比增长67%", "公开材料提到产能扩张"],
            "causal_chain": ["产能扩张 -> 供给变化 -> 交付与回款验证"],
            "industry_chain": ["电池材料 -> 电芯制造 -> 储能项目交付"],
            "counterarguments": ["产能扩张可能带来利用率压力"],
            "risk_boundaries": ["没有订单和回款数据时不能判断利润改善"],
            "evidence_gaps": ["缺少项目回款数据"],
            "follow_up_questions": ["后续交付量和回款是否同步改善？"],
            "avoid_claims": ["不要把产能公告写成利润兑现"],
            "chief_editor_note": "围绕兑现链条写作。",
        },
        "confidence_score": 0.86,
    }
    agent = EditorialCouncilAgent(app_settings, prompt_repository, openai_service, logger)
    extraction, trend, data, cache = _build_inputs()

    report = agent.run(extraction, trend, data, app_settings.style_profiles["deep_industry_analysis"], cache)

    assert report.is_usable()
    assert report.originality_angle == "从增长叙事切换到兑现链条"
    insight = report.to_insight_report()
    assert insight.main_insight.startswith("储能增长的分水岭")
    assert "交付与回款验证" in insight.causal_chain[0]
    assert insight.expert_perspectives[0]["role"] == "devils-advocate"
    prompt_repository.render.assert_called_once()


def test_council_fallback_keeps_evidence_boundaries(app_settings, logger) -> None:
    prompt_repository = MagicMock()
    prompt_repository.render.return_value = "council prompt"
    openai_service = MagicMock()
    openai_service.available = False
    agent = EditorialCouncilAgent(app_settings, prompt_repository, openai_service, logger)
    extraction, trend, data, _ = _build_inputs()

    report = agent.run(
        extraction,
        trend,
        data,
        app_settings.style_profiles["deep_industry_analysis"],
        CacheContext(),
    )

    assert isinstance(report, EditorialCouncilReport)
    assert report.is_usable()
    assert report.evidence_chain == ["储能业务同比增长67%", "公开材料提到产能扩张"]
    assert report.avoid_claims
