from __future__ import annotations

from unittest.mock import MagicMock

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
