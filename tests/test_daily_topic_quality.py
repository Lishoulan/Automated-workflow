from __future__ import annotations

from toutiao_content_factory.agents.data_agent import DataAgent
from toutiao_content_factory.agents.title_agent import TitleAgent
from toutiao_content_factory.models import DataReport, SourceDocument, TopicCandidate, TrendReport
from toutiao_content_factory.services.benchmark_hotspot_service import BenchmarkHotspotService
from toutiao_content_factory.services.extractor import SignalExtractor
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.topic_collection import TopicCollectionService
from toutiao_content_factory.services.automation import TopicAutomationService


RAILWAY_SNIPPET = (
    "IT之家8月30日消息，京沪高铁今日发布2026年上半年报告："
    "营业总收入：218.42亿元，同比增长3.94%；"
    "归母净利润：67.71亿元，同比增长7.21%；"
    "经营现金流：106.85亿元，同比增长4.05%；"
    "加权平均净资产收益率：3.21%；基本每股收益：0.1384元，同比增长7.37%。"
)


def _topic_settings(app_settings, *, focus_industries=None, keyword_map=None):
    return app_settings.topic_collection.model_copy(
        update={
            "focus_industries": focus_industries or [],
            "keyword_map": keyword_map or {},
        }
    )


def test_benchmark_keyword_map_does_not_broadcast_editorial_pillars(app_settings, logger) -> None:
    app_settings.topic_collection = _topic_settings(
        app_settings,
        keyword_map={
            "humanoid_robot": ["人形机器人"],
            "new_energy": ["新能源"],
            "energy_storage": ["储能"],
            "ai_hardware": ["AI硬件"],
            "industrial_automation": ["工业自动化"],
        },
    )
    service = BenchmarkHotspotService(app_settings, logger)

    keyword_map = service._build_keyword_map(
        [
            {
                "track": "科技商业",
                "name": "量子位头条号",
                "content_pillars": ["大模型", "公司战略", "AI硬件"],
            }
        ]
    )

    assert all("公司" not in values for values in keyword_map.values())
    assert all("大模型" not in values for values in keyword_map.values())
    assert "AI硬件" in keyword_map["ai_hardware"]


def test_industry_filter_rejects_generic_only_matches(app_settings, logger) -> None:
    app_settings.topic_collection = _topic_settings(
        app_settings,
        focus_industries=["humanoid_robot", "new_energy", "energy_storage", "ai_hardware", "industrial_automation"],
        keyword_map={
            "humanoid_robot": ["人形机器人", "AI", "公司"],
            "new_energy": ["新能源", "AI", "公司"],
            "energy_storage": ["储能", "AI", "公司"],
            "ai_hardware": ["AI硬件", "AI", "公司"],
            "industrial_automation": ["工业自动化", "AI", "公司"],
        },
    )
    service = TopicCollectionService(app_settings, logger)
    topic = TopicCandidate(
        title="京沪高铁2026年上半年归母净利润67.71亿元",
        url="https://example.test/railway",
        source="rss",
        source_type="rss",
        matched_industries=[
            "humanoid_robot",
            "new_energy",
            "energy_storage",
            "ai_hardware",
            "industrial_automation",
        ],
        matched_keywords=["AI", "公司"],
    )

    filtered = service._filter_to_focus_industries([topic], {"industry_mismatch": 0})

    assert filtered == []


def test_relaxed_topic_fallback_rejects_generic_only_matches(app_settings, logger) -> None:
    app_settings.topic_collection = _topic_settings(
        app_settings,
        focus_industries=["humanoid_robot", "new_energy"],
        keyword_map={
            "humanoid_robot": ["人形机器人", "AI", "公司"],
            "new_energy": ["新能源", "AI", "公司"],
        },
    )
    service = TopicCollectionService(app_settings, logger)
    topic = TopicCandidate(
        title="京沪高铁公司发布最新消息",
        url="https://example.test/railway",
        source="rss",
        source_type="rss",
        matched_industries=["humanoid_robot", "new_energy"],
        matched_keywords=["AI", "公司"],
        quality_score=80,
        evidence_strength=80,
        title_potential_score=70,
    )

    assert service._build_relaxed_candidates([topic]) == []


def test_rich_financial_snippet_is_kept_and_summarized(app_settings, logger) -> None:
    app_settings.topic_collection = _topic_settings(app_settings)
    service = TopicCollectionService(app_settings, logger)
    topic = TopicCandidate(
        title="京沪高铁2026年上半年归母净利润67.71亿元，同比增长7.21%",
        url="https://example.test/railway",
        source="ithome",
        source_type="rss",
        snippet=RAILWAY_SNIPPET,
    )

    assert service._is_unusable_topic_snippet(RAILWAY_SNIPPET) is False
    summary = service._summarize_topic(topic)
    assert "京沪高铁" in summary
    assert "218.42亿元" in summary or "67.71亿元" in summary


def test_selected_topic_input_does_not_discard_rich_snippet(app_settings, logger) -> None:
    service = TopicAutomationService(app_settings, logger)
    topic = TopicCandidate(
        title="京沪高铁2026年上半年归母净利润67.71亿元，同比增长7.21%",
        url="https://example.test/railway",
        source="ithome",
        source_type="rss",
        snippet=RAILWAY_SNIPPET,
        summary="京沪高铁发布半年报，净利润67.71亿元，同比增长7.21%。",
        topic_type="earnings_signal",
        recommended_format="data_recap",
    )

    input_text = service._build_input_text(topic)

    assert "营业总收入" in input_text
    assert "218.42亿元" in input_text


def test_generated_input_keeps_facts_and_drops_editorial_controls(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    content = (
        "标题：京沪高铁2026年上半年归母净利润67.71亿元，同比增长7.21%\n"
        "来源：公开材料\n"
        "主赛道：人形机器人\n"
        "主关键词：AI、公司\n"
        "体裁要求：适合写成人形机器人项目或订单拆解稿。\n"
        "推荐主结论：人形机器人这轮升温不是空转。\n"
        f"摘要：{RAILWAY_SNIPPET}\n"
        f"补充片段：{RAILWAY_SNIPPET}\n"
        "跟踪变量：样机验证、量产节奏\n"
        "固定写法：写成项目拆解稿。"
    )
    document = SourceDocument(
        source_type="paste",
        title="manual_paste",
        content=content,
        metadata={
            "primary_industry": "人形机器人",
            "primary_keywords": "AI、公司",
            "suggested_angle": "人形机器人这轮升温不是空转。",
            "writing_brief": "写成项目拆解稿。",
            "format_brief": "适合写成人形机器人项目或订单拆解稿。",
        },
    )

    extraction = SignalExtractor().extract([document])
    assert extraction.focus_industry != "人形机器人"
    assert "体裁要求" not in extraction.merged_text
    assert any("营业总收入" in item and "218.42" in item for item in extraction.core_data)
    assert any(
        "归母净利润" in item.get("fact", "") and "67.71" in item.get("fact", "")
        for item in extraction.evidence_items
    )
    assert extraction.source_summaries[0]["title"] == "公开材料"
    assert extraction.source_summaries[0]["path"] == ""

    data_agent = DataAgent(
        app_settings,
        PromptRepository(app_settings),
        mock_openai_service,
        logger,
    )
    notes = data_agent._build_evidence_notes(extraction)
    assert notes
    assert all("manual_paste" not in item and "体裁要求" not in item for item in notes)
    assert any("218.42" in item for item in notes)


def test_data_payload_cannot_replace_facts_with_editorial_metadata(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    extraction = SignalExtractor().extract(
        [
            SourceDocument(
                source_type="rss",
                title="半年报",
                content=f"标题：京沪高铁半年报净利润67.71亿元，同比增长7.21%\n摘要：{RAILWAY_SNIPPET}",
                metadata={"topic_title": "京沪高铁半年报净利润67.71亿元，同比增长7.21%"},
            )
        ]
    )
    data_agent = DataAgent(
        app_settings,
        PromptRepository(app_settings),
        mock_openai_service,
        logger,
    )

    report = data_agent._build_from_payload(
        {
            "focus_industry": "人形机器人",
            "focus_keywords": ["AI", "公司"],
            "core_data": ["体裁要求：适合写成人形机器人项目拆解稿"],
            "industry_trends": ["固定写法：先写项目主体"],
            "key_points": ["推荐主结论：人形机器人会加速"],
            "evidence_notes": ["manual_paste：体裁要求：内部字段"],
            "watch_metrics": ["人形机器人样机验证", "订单落地"],
            "risk_flags": ["新能源行业竞争加剧"],
        },
        extraction,
        TrendReport(),
    )

    assert report.focus_industry != "人形机器人"
    assert any("营业总收入" in item for item in report.core_data)
    assert all("体裁要求" not in item and "manual_paste" not in item for item in report.core_data + report.industry_trends + report.key_points + report.evidence_notes)
    assert all("人形机器人" not in item and "新能源" not in item for item in report.watch_metrics + report.risk_flags)
    assert "订单落地" in report.watch_metrics


def test_financial_title_fallback_uses_topic_subject_and_figures(
    app_settings,
    mock_openai_service,
    logger,
) -> None:
    agent = TitleAgent(
        app_settings,
        PromptRepository(app_settings),
        mock_openai_service,
        logger,
    )
    report = DataReport(
        topic_title="京沪高铁 2026 年上半年归母净利润 67.71 亿元，同比增长 7.21%",
        growth_numbers=["7.21%", "3.94%"],
    )

    titles = agent._fallback_evidence_titles(report)

    assert titles == ["京沪高铁发布半年报：净利润67.71亿元，同比增长7.21%"]
    assert len(titles[0]) <= app_settings.publishing.title_max_length
