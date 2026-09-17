from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.exceptions import LowQualityAbortException
from toutiao_content_factory.models import (
    ContentScore,
    DataReport,
    ExtractionResult,
    GeneratedContent,
    TrendReport,
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


def test_abort_rejects_insufficient_source_facts_for_normal_candidate(
    workflow_service: AgentWorkflowService,
) -> None:
    extraction = ExtractionResult(
        merged_text="",
        core_data=[],
        industry_trends=[],
        growth_numbers=[],
        companies=[],
        supply_chain_keywords=[],
        key_points=[],
        topic_title="normal topic",
    )
    data_report = DataReport(
        topic_title="normal topic",
        core_data=["fact"],
        key_points=["point"],
        growth_numbers=["1%"],
        evidence_notes=["evidence"],
        companies=["Company"],
        focus_industry="industry",
        topic_quality_score=90,
        topic_quality_flags=["insufficient_source_facts"],
    )

    with pytest.raises(LowQualityAbortException, match="insufficient_source_facts"):
        workflow_service._abort_if_low_quality(extraction, data_report)


def test_abort_keeps_insufficient_hot_topic_for_manual_review(
    workflow_service: AgentWorkflowService,
) -> None:
    extraction = ExtractionResult(
        merged_text="",
        core_data=[],
        industry_trends=[],
        growth_numbers=[],
        companies=[],
        supply_chain_keywords=[],
        key_points=[],
        topic_title="hot topic",
        is_hot_topic=True,
    )
    data_report = DataReport(
        topic_title="hot topic",
        is_hot_topic=True,
        topic_quality_score=90,
        topic_quality_flags=["insufficient_source_facts"],
    )

    workflow_service._abort_if_low_quality(extraction, data_report)


def test_abort_rejects_insufficient_hot_topic_for_auto_publish(
    app_settings,
    workflow_service: AgentWorkflowService,
) -> None:
    object.__setattr__(app_settings.publishing, "enabled", True)
    object.__setattr__(app_settings.publishing, "auto_publish", True)
    extraction = ExtractionResult(
        merged_text="",
        core_data=[],
        industry_trends=[],
        growth_numbers=[],
        companies=[],
        supply_chain_keywords=[],
        key_points=[],
        topic_title="hot topic",
        is_hot_topic=True,
    )
    data_report = DataReport(
        topic_title="hot topic",
        is_hot_topic=True,
        topic_quality_score=90,
        topic_quality_flags=["insufficient_source_facts"],
    )

    with pytest.raises(LowQualityAbortException, match="insufficient_source_facts"):
        workflow_service._abort_if_low_quality(extraction, data_report)


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


class TestSupplementaryParagraphs:
    def test_supplements_are_grounded_and_skip_items_already_in_article(
        self,
        workflow_service: AgentWorkflowService,
    ) -> None:
        data_report = DataReport(
            watch_metrics=["部署数量", "重复订单"],
            risk_flags=["单机成本未披露"],
            evidence_notes=["特斯拉Optimus进入工厂"],
            growth_numbers=["12%", "18%"],
        )

        paragraphs = workflow_service._build_supplementary_paragraphs(
            data_report,
            existing_paragraphs=["部署数量已经写入正文。"],
        )
        joined = "\n".join(paragraphs)

        assert "部署数量" not in joined
        assert "重复订单" in joined
        assert "单机成本未披露" in joined
        assert "特斯拉Optimus进入工厂" in joined
        assert "12%" in joined and "18%" in joined
        assert "接下来该盯的是" not in joined
        assert "风险边界需要保留" not in joined
        assert "目前能确认的公开信息主要到这里" not in joined

    def test_has_anchor_gonggao(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("公告称") is True

    def test_has_anchor_jigou(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("机构统计") is True

    def test_has_anchor_baogao(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("报告指出") is True

    def test_no_anchor(self, workflow_service: AgentWorkflowService) -> None:
        assert workflow_service._has_source_anchor("这是一段普通文本") is False


def test_post_polish_rebuild_stays_blocked_when_model_is_unavailable(
    app_settings,
    mock_openai_service,
    logger,
    workflow_service: AgentWorkflowService,
) -> None:
    data_report = DataReport(
        topic_title="南网在深圳充电站接入虚拟电厂，规模26万千瓦",
        focus_industry="锂电",
        focus_keywords=["锂电", "充电站", "虚拟电厂"],
        supply_chain_keywords=["充电站", "虚拟电厂"],
        core_data=["2022年深圳南方电网充电站接入虚拟电厂，规模达到26万千瓦"],
        industry_trends=["充电站与虚拟电厂的协同仍需继续核对"],
        key_points=["项目规模需要结合运行口径判断"],
        growth_numbers=["2022年", "26万千瓦"],
        evidence_notes=["公开材料：2022年深圳南方电网充电站接入虚拟电厂，规模达到26万千瓦"],
        watch_metrics=["接入规模", "项目运行", "客户验收"],
        risk_flags=["项目口径和后续运行数据尚未完整披露"],
        content_format="project_breakdown",
        word_target=1800,
        evidence_strength=88,
        topic_quality_score=87,
    )
    polish_agent = PolishAgent(
        app_settings,
        prompt_repository=MagicMock(),
        openai_service=mock_openai_service,
        logger=logger,
    )
    rebuilt = polish_agent._rebuild_cautious_article(data_report, TrendReport())

    finalized = workflow_service._ensure_article_min_chars(
        rebuilt,
        data_report,
        min_chars=1500,
    )

    char_count = len(finalized.replace("\n", "").replace(" ", ""))
    assert len(rebuilt.replace("\n", "").replace(" ", "")) < 1500
    # A rebuilt short article must remain below the public gate when no model
    # is available; padding it with repeated analysis would hide the blocker.
    assert 0 < char_count < 1500
    assert "锂电订单" not in finalized
    assert "26万千瓦" in finalized
    assert workflow_service.writer_agent._unsupported_numeric_tokens(finalized, data_report) == []
    assert not any(
        fragment in finalized
        for fragment in workflow_service.writer_agent.INCOMPLETE_ARTICLE_FRAGMENTS
    )
    mock_openai_service.create_json_completion.assert_not_called()

    quality_report = workflow_service.quality_review_council_agent._fallback(
        GeneratedContent(
            article=finalized,
            polished_article=finalized,
            summary=finalized[:120],
            recommended_titles=["充电站26万千瓦：虚拟电厂如何实测"],
        ),
        data_report,
    )
    assert quality_report.needs_revision is True

    buffered = workflow_service._ensure_article_min_chars(
        rebuilt,
        data_report,
        min_chars=1750,
    )
    assert len(buffered.replace("\n", "").replace(" ", "")) < 1750


class TestReduceSourceOverlap:
    """去重合兜底：只在越线时出手，且只在改写确有下降时才采纳。

    背景：素材原文的逐字句子会经 data_report.source_facts 进入 writer 提示词，
    模型复述它们就会推高抄录率；云端曾两篇候选同时 32.2% 被 25% 红线拦下、
    当天零发布。提示词约束管不住模型，所以补一道可复测的确定性兜底。
    """

    SOURCE = (
        "人形机器人从实验室走向真实的商业场景的竞速过程中，智元机器人构建了领先的"
        "机器人本体与AI全栈技术，并实现了模型、数据和本体部署的全栈具身智能布局。"
        "端到端全栈协同能力使得它在真实作业场景中的落地速度具有先发优势。"
        "在产品矩阵上，智元构建了包含远征人形、灵犀商服、精灵工业三个系列的多元化"
        "产品体系，契合工业制造、仓储物流、商业服务等高频真实场景。"
        "公司同时披露了海外订单与交付节奏的变化，仍需以合同和回款数据核验。"
    )

    @staticmethod
    def _extraction(merged_text: str) -> ExtractionResult:
        return ExtractionResult(
            merged_text=merged_text,
            core_data=[],
            industry_trends=[],
            growth_numbers=[],
            companies=[],
            supply_chain_keywords=[],
            key_points=[],
        )

    def test_clean_article_is_not_rewritten(
        self, workflow_service: AgentWorkflowService, mock_openai_service
    ) -> None:
        article = "另起炉灶重写的一段产业分析，讨论交付节奏与回款口径。" * 40
        content = GeneratedContent(
            article=article, polished_article=article, summary=article[:80]
        )
        workflow_service._reduce_source_overlap(
            content, self._extraction(self.SOURCE), DataReport()
        )
        mock_openai_service.create_json_completion.assert_not_called()
        assert content.article == article

    def test_short_source_skips_the_check(
        self, workflow_service: AgentWorkflowService, mock_openai_service
    ) -> None:
        content = GeneratedContent(
            article=self.SOURCE, polished_article=self.SOURCE, summary="x" * 40
        )
        workflow_service._reduce_source_overlap(
            content, self._extraction("素材太短"), DataReport()
        )
        mock_openai_service.create_json_completion.assert_not_called()
        assert content.article == self.SOURCE

    def test_overlap_triggers_attempt_but_useless_rewrite_is_rejected(
        self, workflow_service: AgentWorkflowService, mock_openai_service
    ) -> None:
        # conftest 的 mock 返回 {"result": "mocked"}：既不是正文，也无法证明抄录率下降。
        content = GeneratedContent(
            article=self.SOURCE, polished_article=self.SOURCE, summary="x" * 40
        )
        workflow_service._reduce_source_overlap(
            content, self._extraction(self.SOURCE), DataReport()
        )
        mock_openai_service.create_json_completion.assert_called()
        assert content.article == self.SOURCE

    def test_only_a_measured_improvement_is_accepted(
        self, workflow_service: AgentWorkflowService, mock_openai_service
    ) -> None:
        rewritten = "从交付节奏看，这家公司的产品线覆盖了人形、商服与工业三类形态，" * 60
        mock_openai_service.create_json_completion.return_value = {
            "polished_article": rewritten
        }
        content = GeneratedContent(
            article=self.SOURCE, polished_article=self.SOURCE, summary="x" * 40
        )
        workflow_service._reduce_source_overlap(
            content, self._extraction(self.SOURCE), DataReport()
        )
        assert content.article != self.SOURCE
        assert any("去重合改写" in note for note in content.validation_notes)


def test_all_rejected_titles_clear_the_pool_instead_of_keeping_the_least_bad(
    workflow_service: AgentWorkflowService,
) -> None:
    """全部标题不合格时**清空候选**，不再保底放行「问题最少」的那一条。

    ⚠️ 2026-09-17（D2）：本用例的旧版本名为
    `test_all_rejected_titles_keep_best_one_instead_of_clearing`，断言的是
    「保底保留问题最少的一个」。**旧行为是错的**，理由有三条：

    1. **它把闸门变成了排序器。** 闸门的定义是「不合格的不许流出」；当全部候选都不
       合格时，「问题最少的那一个」按定义仍然不合格 —— 该分支保证了一条不合格标题
       一定会流出，闸门在最需要它生效的时候恰好失效。
    2. **它让故障不可观测。** 下游拿到的 `titles` 永远非空，于是没有任何地方能发现
       「这次一条合格标题都没有」，也就永远不会有人去修上游。
    3. **它送出去的正是自己点名的反例。** `其他智能12.7亿：机器人如何实测` 就是
       `_check_title()` 注释里写的「数字让一个模糊主体看起来很具体」的样本，却被这条
       保底逻辑一路送进了发布链路。

    现在改为清空 + 记录 `status=all_rejected` + 交回 topic attempt loop 换下一个选题
    （`app_service` 会在 `selected_title` 为空时把 `qualifies` 置 False 并继续下一轮）。
    """
    title = "其他智能12.7亿：机器人如何实测"
    content = GeneratedContent(
        article="公开材料显示，优必选的机器人业务仍要看交付。",
        titles=[title],
        recommended_titles=[title],
    )

    workflow_service._filter_titles_by_quality(content)

    assert content.titles == []
    assert content.recommended_titles == []
    # 失败必须是**可观测的**：status + 原因都要留下来。
    assert content.title_validation.get("status") == "all_rejected"
    assert content.title_validation.get("pass_count") == 0
    assert content.title_validation.get("reject_count") == 1
    assert content.title_validation.get("top_reasons")
    # 风险仍要记录，方便事后回看
    assert any("其他智能" in note for note in content.validation_notes)
    assert any("不再保底放行" in note for note in content.validation_notes)


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
