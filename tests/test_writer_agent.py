from __future__ import annotations

from unittest.mock import MagicMock

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.models import DataReport, TrendReport


def _build_writer_agent(app_settings, mock_openai_service, logger) -> WriterAgent:
    return WriterAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )


def test_trim_low_confidence_paragraphs_removes_cta_and_unknown_company(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        companies=["汇川技术"],
        growth_numbers=["12%"],
        evidence_notes=["公开资料显示产线在扩容"],
        core_data=["产线利用率提升"],
    )

    paragraphs = [
        "公开资料显示，汇川技术的订单节奏开始改善，产线爬坡速度比去年更快。",
        "以某头部厂商为例，陌生科技的伺服系统毛利率提升到了31%。",
        "## 延伸服务\n回复“咨询”领取行业周报。",
    ]

    cleaned = agent._trim_low_confidence_paragraphs(paragraphs, data_report)

    assert cleaned == ["公开资料显示，汇川技术的订单节奏开始改善，产线爬坡速度比去年更快。"]


def test_build_supplementary_paragraphs_stays_grounded(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        watch_metrics=["订单转化", "项目回款"],
        risk_flags=["客户验收延后"],
        evidence_notes=["财报披露新签项目增加"],
        growth_numbers=["12%", "18%"],
    )

    paragraphs = agent._build_supplementary_paragraphs(data_report, existing_paragraphs=[])
    joined = "\n".join(paragraphs)

    assert paragraphs
    assert "订单转化" in joined
    assert "客户验收延后" in joined
    assert "财报披露新签项目增加" in joined
    assert "12%" in joined and "18%" in joined


def test_supplementary_paragraphs_use_evidence_fields_without_old_template_openers(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(
        watch_metrics=["部署数量", "重复订单"],
        risk_flags=["单机成本未披露"],
        evidence_notes=["公开材料：特斯拉Optimus进入工厂"],
        growth_numbers=["12%", "18%"],
    )

    joined = "\n".join(agent._build_supplementary_paragraphs(data_report))

    assert "部署数量" in joined
    assert "单机成本未披露" in joined
    assert "特斯拉Optimus进入工厂" in joined
    assert "12%" in joined and "18%" in joined
    assert "后续需要持续跟踪的变量包括" not in joined
    assert "从已有证据看" not in joined
    assert "关键数字锚点" not in joined


def test_cautious_polish_fallback_keeps_unknown_order_claim_conditional(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(
        focus_industry="人形机器人",
        focus_companies=["特斯拉", "柏林工厂", "公开材料"],
        companies=["特斯拉", "柏林工厂", "公开材料", "AI机器人"],
        core_data=["特斯拉Optimus进入柏林工厂"],
        industry_trends=["行业从样机展示走向真实场景验证"],
        evidence_notes=["公开材料：特斯拉Optimus进入柏林工厂"],
        watch_metrics=["部署数量", "重复订单", "单机成本"],
        risk_flags=["订单金额尚未披露"],
        topic_title="特斯拉Optimus机器人进柏林工厂",
    )

    article = agent._rebuild_cautious_article(
        data_report,
        TrendReport(recommended_angle="需要继续核对任务完成率和连续运行时间"),
    )

    assert "特斯拉" in article
    assert "柏林工厂" not in agent._company_candidates(data_report)
    assert "获得样机订单" not in article
    assert "后续更值得跟踪的" not in article
    assert "风险边界也要保留" not in article
    assert "限制条件" in article


def test_finalize_article_rebuilds_unsupported_sample_order_claim(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(
        focus_industry="人形机器人",
        companies=["特斯拉"],
        focus_companies=["特斯拉"],
        core_data=["特斯拉Optimus进入柏林工厂"],
        evidence_notes=["公开材料：特斯拉Optimus进入柏林工厂"],
        watch_metrics=["部署数量", "重复订单"],
        risk_flags=["订单金额尚未披露"],
    )
    trend_report = TrendReport()
    article = "减速器、执行器和视觉传感器已经获得样机订单，这说明供应链开始放量。"

    finalized = agent._finalize_article(article, data_report, trend_report)

    assert "获得样机订单" not in finalized
    assert "特斯拉" in finalized


def test_conditional_order_question_is_not_treated_as_an_assertion(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(core_data=["订单金额尚未披露"])

    assert not agent._has_unsupported_order_claim("若后续获得订单，再判断商业化进度。", data_report)
    assert not agent._has_unsupported_order_claim("订单是否落地，仍需等待公告。", data_report)
    assert agent._has_unsupported_order_claim("公司已经获得重复订单。", data_report)


def test_unknown_known_brand_is_rejected_from_polished_article(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    data_report = DataReport(companies=["特斯拉"])

    assert not agent._has_unknown_company_mention("特斯拉已经发布新产品。", data_report)
    assert agent._has_unknown_company_mention("华为已经发布新产品。", data_report)


def test_order_evidence_must_match_company_and_order_type(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    data_report = DataReport(
        core_data=["特斯拉已经获得采购订单。"],
        companies=["特斯拉"],
    )

    assert not agent._has_unsupported_order_claim("特斯拉已经获得采购订单。", data_report)
    assert agent._has_unsupported_order_claim("比亚迪已经获得采购订单。", data_report)
    assert agent._has_unsupported_order_claim("特斯拉已经获得重复订单。", data_report)


def test_writer_system_prompt_uses_evidence_boundary_without_rigid_ai_templates(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = _build_writer_agent(app_settings, mock_openai_service, logger)
    prompt = agent._writer_system_prompt(app_settings.get_style("deep_industry_analysis"))

    assert prompt.count("你是 WriterAgent") == 1
    assert "事实边界" in prompt
    assert "绝不虚构公司、人物、数字、时间、订单" in prompt
    assert "每个段落只推进一个主要判断" in prompt
    assert "素材没有三个数据点时" in prompt
    assert "每段必须包含至少一个具体场景锚点" not in prompt
    assert "第一人称现场观察" in prompt
    assert "每个数字机械套同一个来源前缀" in prompt
    assert "You are WriterAgent" not in prompt


def test_polish_constraints_prioritize_grounding_and_natural_variation(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )

    assert "不得新增素材中没有的公司、人物、数字、时间、订单" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "不要为了满足固定段数" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "第一人称经历" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "每个数字机械添加相同" in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "采用科技媒体记者的第一人称观察视角" not in agent.ANTI_AI_FLAVOR_APPENDIX
    assert "你是 PolishAgent" in agent.POLISH_SYSTEM_PROMPT
