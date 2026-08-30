from __future__ import annotations

from unittest.mock import MagicMock

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.models import DataReport


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
