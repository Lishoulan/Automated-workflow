from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.models import (
    ContentScore,
    DataReport,
    ExtractionResult,
    GeneratedContent,
)
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService


@pytest.fixture()
def workflow_service(app_settings, mock_openai_service, logger) -> AgentWorkflowService:
    return AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)


class TestResolveQualityScore:
    def test_uses_data_report_score_first(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=[], supply_chain_keywords=[], key_points=[],
            topic_quality_score=50,
        )
        data_report = DataReport(
            core_data=["数据1"], growth_numbers=["50%"], evidence_notes=["证据1"],
            companies=["公司A"], topic_quality_score=80,
        )
        score = workflow_service._resolve_quality_score(extraction, data_report)
        assert score == 80

    def test_uses_extraction_score_when_data_report_zero(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=[], supply_chain_keywords=[], key_points=[],
            topic_quality_score=65,
        )
        data_report = DataReport(
            core_data=[], growth_numbers=[], evidence_notes=[],
            companies=[], topic_quality_score=0,
        )
        score = workflow_service._resolve_quality_score(extraction, data_report)
        assert score == 65

    def test_calculates_from_signals_when_no_explicit_score(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=[], supply_chain_keywords=[], key_points=[],
            topic_quality_score=0,
        )
        data_report = DataReport(
            core_data=["d1", "d2"], growth_numbers=["50%"], evidence_notes=["e1"],
            companies=["公司A", "公司B", "公司C"],
            topic_quality_score=0,
        )
        score = workflow_service._resolve_quality_score(extraction, data_report)
        # signal_count = 2 + 1 + 1 = 4, company_bonus = min(12, 3*4) = 12
        # score = min(100, 4*10 + 12) = 52
        assert score == 52

    def test_score_capped_at_100(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=[], supply_chain_keywords=[], key_points=[],
            topic_quality_score=0,
        )
        data_report = DataReport(
            core_data=[f"d{i}" for i in range(20)],
            growth_numbers=[f"{i}%" for i in range(20)],
            evidence_notes=[f"e{i}" for i in range(20)],
            companies=[f"公司{i}" for i in range(10)],
            topic_quality_score=0,
        )
        score = workflow_service._resolve_quality_score(extraction, data_report)
        assert score <= 100


class TestCollectLowQualityReasons:
    def test_no_reasons_for_high_quality(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=["公司A"], supply_chain_keywords=[], key_points=[],
            focus_industry="新能源",
        )
        data_report = DataReport(
            core_data=["数据1"], key_points=["要点1"],
            growth_numbers=["50%"], evidence_notes=["证据1"],
            companies=["公司A"], focus_industry="新能源",
            topic_quality_score=80,
        )
        score = workflow_service._resolve_quality_score(extraction, data_report)
        reasons = workflow_service._collect_low_quality_reasons(extraction, data_report, score)
        assert len(reasons) == 0

    def test_low_score_triggers_reason(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=[], supply_chain_keywords=[], key_points=[],
        )
        data_report = DataReport(
            core_data=[], key_points=["要点1"],
            growth_numbers=["50%"], evidence_notes=["证据1"],
            companies=["公司A"], focus_industry="新能源",
        )
        reasons = workflow_service._collect_low_quality_reasons(extraction, data_report, 30)
        assert any("quality_score_below" in r for r in reasons)

    def test_missing_core_points(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=["公司A"], supply_chain_keywords=[], key_points=[],
            focus_industry="新能源",
        )
        data_report = DataReport(
            core_data=[], key_points=[],
            growth_numbers=["50%"], evidence_notes=["证据1"],
            companies=["公司A"], focus_industry="新能源",
            topic_quality_score=80,
        )
        reasons = workflow_service._collect_low_quality_reasons(extraction, data_report, 80)
        assert "missing_core_points" in reasons

    def test_missing_evidence(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=["公司A"], supply_chain_keywords=[], key_points=[],
            focus_industry="新能源",
        )
        data_report = DataReport(
            core_data=["数据1"], key_points=["要点1"],
            growth_numbers=[], evidence_notes=[],
            companies=["公司A"], focus_industry="新能源",
            topic_quality_score=80,
        )
        reasons = workflow_service._collect_low_quality_reasons(extraction, data_report, 80)
        assert "missing_evidence" in reasons

    def test_missing_subject_anchor(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="", core_data=[], industry_trends=[], growth_numbers=[],
            companies=[], supply_chain_keywords=[], key_points=[],
        )
        data_report = DataReport(
            core_data=["数据1"], key_points=["要点1"],
            growth_numbers=["50%"], evidence_notes=["证据1"],
            companies=[], focus_industry="",
            topic_quality_score=80,
        )
        reasons = workflow_service._collect_low_quality_reasons(extraction, data_report, 80)
        assert "missing_subject_anchor" in reasons

    def test_fallback_topic_without_hard_evidence_is_rejected(self, workflow_service: AgentWorkflowService) -> None:
        extraction = ExtractionResult(
            merged_text="",
            core_data=[],
            industry_trends=[],
            growth_numbers=[],
            companies=[],
            supply_chain_keywords=[],
            key_points=[],
            topic_quality_score=72,
        )
        extraction.topic_quality_flags = ["evergreen_fallback"]
        data_report = DataReport(
            core_data=["只有泛化判断"],
            key_points=["缺少硬数据"],
            growth_numbers=[],
            evidence_notes=["公开材料提到行业变化"],
            companies=[],
            focus_industry="工业自动化",
            topic_quality_score=72,
            evidence_strength=55,
        )
        data_report.topic_quality_flags = ["evergreen_fallback"]

        reasons = workflow_service._collect_low_quality_reasons(extraction, data_report, 72)

        assert "fallback_topic_without_hard_evidence" in reasons


class TestShouldRunSecondPolish:
    def test_needs_revision_triggers(self, workflow_service: AgentWorkflowService) -> None:
        content = GeneratedContent(article="测试", validation_score=90)
        score = ContentScore(pass_status="needs_revision", ai_generated_risk=0, completion_rate_potential=90)
        assert workflow_service._should_run_second_polish(content, score) is True

    def test_high_ai_risk_triggers(self, workflow_service: AgentWorkflowService) -> None:
        content = GeneratedContent(article="测试", validation_score=90)
        score = ContentScore(pass_status="approved", ai_generated_risk=32, completion_rate_potential=90)
        assert workflow_service._should_run_second_polish(content, score) is True

    def test_low_completion_rate_triggers(self, workflow_service: AgentWorkflowService) -> None:
        content = GeneratedContent(article="测试", validation_score=90)
        score = ContentScore(pass_status="approved", ai_generated_risk=0, completion_rate_potential=84)
        assert workflow_service._should_run_second_polish(content, score) is True

    def test_low_validation_score_triggers(self, workflow_service: AgentWorkflowService) -> None:
        content = GeneratedContent(article="测试", validation_score=84)
        score = ContentScore(pass_status="approved", ai_generated_risk=0, completion_rate_potential=90)
        assert workflow_service._should_run_second_polish(content, score) is True

    def test_all_good_no_second_polish(self, workflow_service: AgentWorkflowService) -> None:
        content = GeneratedContent(
            article="据公开材料显示，新能源增长迅速。数据表明行业前景良好。",
            validation_score=90,
        )
        score = ContentScore(
            pass_status="approved",
            ai_generated_risk=0,
            completion_rate_potential=90,
        )
        assert workflow_service._should_run_second_polish(content, score) is False


class TestHasLongParagraph:
    def test_long_paragraph_detected(self, workflow_service: AgentWorkflowService) -> None:
        article = "短段落\n\n" + "很长的内容" * 50
        assert workflow_service._has_long_paragraph(article) is True

    def test_short_paragraphs_ok(self, workflow_service: AgentWorkflowService) -> None:
        article = "短段落1\n\n短段落2\n\n短段落3"
        assert workflow_service._has_long_paragraph(article) is False

    def test_exactly_180_chars(self, workflow_service: AgentWorkflowService) -> None:
        paragraph = "a" * 180
        article = paragraph
        assert workflow_service._has_long_paragraph(article) is True

    def test_179_chars_ok(self, workflow_service: AgentWorkflowService) -> None:
        paragraph = "a" * 179
        article = paragraph
        assert workflow_service._has_long_paragraph(article) is False


class TestHasSourceAnchor:
    def test_has_anchor_ju(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("据媒体报道") is True

    def test_has_anchor_shuju(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("数据显示增长") is True

    def test_has_anchor_gongkai(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("公开材料显示") is True

    def test_has_anchor_caibao(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("财报披露") is True

    def test_has_anchor_gonggao(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("公告称") is True

    def test_has_anchor_jigou(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("机构统计") is True

    def test_has_anchor_baogao(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("报告指出") is True

    def test_no_anchor(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("这是一段普通文本") is False


class TestApplyBadWordsGate:
    def test_no_bad_words(self, workflow_service: AgentWorkflowService) -> None:
        content = GeneratedContent(
            article="正常文章内容",
            recommended_titles=["标题1"],
            tags=["标签1"],
        )
        score = ContentScore(
            publish_readiness_score=80,
            publish_recommendation="auto_publish",
        )
        workflow_service._apply_bad_words_gate(content, score)
        assert score.publish_readiness_score == 80
        assert score.publish_recommendation == "auto_publish"

    def test_bad_words_detected(self, workflow_service: AgentWorkflowService) -> None:
        # Inject a bad word into the guard service
        from toutiao_content_factory.services.content_guard_service import SensitiveWordTrie
        workflow_service.content_guard_service.bad_words = ["测试违禁词x"]
        workflow_service.content_guard_service.bad_words_trie = SensitiveWordTrie(
            workflow_service.content_guard_service.bad_words
        )

        content = GeneratedContent(
            article="包含测试违禁词x的文章",
            recommended_titles=["标题1"],
            tags=["标签1"],
        )
        score = ContentScore(
            publish_readiness_score=80,
            publish_recommendation="auto_publish",
        )
        workflow_service._apply_bad_words_gate(content, score)
        assert score.publish_readiness_score == 0
        assert score.publish_recommendation == "revise_before_publish"
        assert len(score.publish_risk_flags) > 0
