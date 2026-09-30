from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

from toutiao_content_factory.agents.data_agent import DataAgent
from toutiao_content_factory.agents.publish_review_agent import PublishReviewAgent
from toutiao_content_factory.agents.quality_review_council_agent import QualityReviewCouncilAgent
from toutiao_content_factory.agents.review_agent import ReviewAgent
from toutiao_content_factory.agents.title_agent import TitleAgent
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import CacheContext, ContentScore, DataReport, ExtractionResult, GeneratedContent
from toutiao_content_factory.services.exporter import ExportService
from toutiao_content_factory.services.prompt_repository import PromptRepository


def _settings():
    return load_settings(Path.cwd(), account_key="robot_notes")


def _prompts() -> PromptRepository:
    return PromptRepository(Path.cwd() / "prompts")


def _story_report() -> DataReport:
    return DataReport(
        topic_title="充电器忘拔烧毁整个家",
        focus_industry="social_story",
        focus_keywords=["充电器", "火灾"],
        evidence_notes=["消防部门提醒，老化充电器长期通电可能产生安全风险。"],
        source_facts=["一户居民家中因充电器长期通电发生火灾。"],
        is_hot_topic=True,
        content_format="quick_commentary",
    )


def _story_article(chars: int = 760) -> str:
    unit = "公开材料显示，充电器长期通电引发火灾。遇到类似情况应先切断电源并联系消防。"
    body = (unit * (chars // len(unit) + 1))[:chars]
    return body + "换成是你遇到这种情况，会怎么处理？"


def test_social_story_fields_remove_industry_risks() -> None:
    settings = _settings()
    agent = DataAgent(
        settings,
        _prompts(),
        MagicMock(available=False, model="stub"),
        logging.getLogger("test_narrative_data"),
    )
    extraction = ExtractionResult(
        merged_text="一户居民家中因充电器长期通电发生火灾。",
        core_data=[],
        industry_trends=[],
        growth_numbers=[],
        companies=[],
        supply_chain_keywords=[],
        key_points=[],
        topic_title="充电器忘拔烧毁整个家",
        focus_industry="social_story",
        focus_keywords=["充电器", "火灾"],
    )

    normalized = agent._normalize_mode_fields(
        extraction=extraction,
        focus_industry="social_story",
        focus_keywords=["充电器", "火灾"],
        supply_chain_keywords=["订单", "交付"],
        core_data=[],
        industry_trends=[],
        key_points=[],
        watch_metrics=["项目交付节奏"],
        evidence_notes=["公开报道提到一户居民家中发生火灾。"],
        risk_flags=["充电热度上升，不等于所有环节都能同步兑现利润。"],
        companies=[],
        focus_companies=[],
    )

    _, _, supply_chain, _, _, key_points, watch_metrics, _, risk_flags, companies, focus_companies = normalized
    combined = " ".join([*key_points, *watch_metrics, *risk_flags])
    assert supply_chain == []
    assert companies == [] and focus_companies == []
    assert all(token not in combined for token in DataAgent.MODE_NOISE_TOKENS)
    assert "公开材料未交代" in risk_flags[0]


def test_narrative_review_length_gate_uses_the_real_publish_floor() -> None:
    settings = _settings()
    agent = ReviewAgent(
        settings,
        _prompts(),
        MagicMock(available=False, model="stub"),
        logging.getLogger("test_narrative_review"),
    )
    content = GeneratedContent(article=_story_article(754))
    score = ContentScore(pass_status="ready", completion_rate_potential=82)

    agent._apply_length_gate(score, content, _story_report())

    assert score.completion_rate_potential == 82
    assert all("最低850字" not in item for item in score.revision_suggestions)


def test_narrative_publish_review_uses_the_real_publish_floor() -> None:
    settings = _settings()
    agent = PublishReviewAgent(
        settings,
        _prompts(),
        MagicMock(available=False, model="stub"),
        logging.getLogger("test_narrative_publish_review"),
    )

    assert agent._required_article_length(_story_report()) == 700
    assert "产业链、订单、利润" in agent._hot_topic_publish_note(_story_report())


def test_narrative_quality_council_does_not_apply_industry_requirements() -> None:
    settings = _settings()
    agent = QualityReviewCouncilAgent(
        settings,
        MagicMock(),
        MagicMock(available=False, model="stub"),
        logging.getLogger("test_narrative_quality"),
    )
    article = _story_article(760)
    content = GeneratedContent(
        article=article,
        polished_article=article,
        recommended_titles=["充电器忘拔烧毁整个家"],
    )

    report = agent._fallback(content, _story_report())
    findings = " ".join(
        [*report.fact_issues, *report.logic_issues, *report.compliance_issues]
    )

    assert "RULE_009" not in findings
    assert "互动吸引力不足" not in findings
    assert "反方或失效条件" not in findings
    assert "公司/数字" not in findings


def test_narrative_quality_council_blocks_below_publish_floor() -> None:
    settings = _settings()
    agent = QualityReviewCouncilAgent(
        settings,
        MagicMock(),
        MagicMock(available=False, model="stub"),
        logging.getLogger("test_narrative_short_quality"),
    )
    article = _story_article(480)
    content = GeneratedContent(
        article=article,
        polished_article=article,
        recommended_titles=["充电器忘拔烧毁整个家"],
    )

    report = agent._fallback(content, _story_report())

    assert report.verdict == "blocked"
    assert any("700字底线" in item for item in report.hard_blockers)


def test_narrative_prompts_do_not_load_industry_benchmarks() -> None:
    settings = _settings()
    review = ReviewAgent(
        settings,
        _prompts(),
        MagicMock(available=False, model="stub"),
        logging.getLogger("test_narrative_benchmark"),
    )
    title = TitleAgent(
        settings,
        _prompts(),
        MagicMock(available=False, model="stub", force_mock=True),
        logging.getLogger("test_narrative_title_benchmark"),
    )

    digest = review.brand_digest()
    playbook = title._build_title_playbook_context(_story_report())

    assert "事件经过和人物关系" in digest
    assert "先下判断，再给数据" not in digest
    assert review.benchmark_strategy_digest(purpose="review", track_hint="social_story") == ""
    assert playbook["track"] == "社会故事"
    assert playbook["title_templates"] == []
    assert playbook["example_titles"] == []


def test_narrative_review_notes_do_not_request_industry_metrics() -> None:
    settings = _settings()
    agent = ReviewAgent(
        settings,
        _prompts(),
        MagicMock(available=False, model="stub"),
        logging.getLogger("test_narrative_review_notes"),
    )
    content = GeneratedContent(article=_story_article(760), polished_article=_story_article(760))

    notes = agent._build_review_notes(content, _story_report(), CacheContext())
    combined = " ".join(notes)

    assert notes
    assert all(token not in combined for token in ("补一句订单", "毛利指标", "利润向哪一环", "公司或订单主体"))


def test_exported_narrative_benchmark_strategy_is_story_specific() -> None:
    settings = _settings()
    exporter = ExportService(settings, logging.getLogger("test_narrative_export"))
    result = MagicMock()
    result.created_at = "2026-10-01T12:00:00+08:00"
    result.data_report = _story_report()

    strategy = exporter._build_benchmark_strategy(result)

    assert strategy["track"] == "社会故事"
    assert strategy["benchmark_accounts"] == []
    assert strategy["example_titles"] == []
    assert "人物处境" in strategy["voice_summary"]
