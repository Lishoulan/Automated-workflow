from unittest.mock import MagicMock

from toutiao_content_factory.agents.quality_review_council_agent import QualityReviewCouncilAgent
from toutiao_content_factory.models import (
    CacheContext,
    ContentScore,
    DataReport,
    ExtractionResult,
    GeneratedContent,
    QualityReviewReport,
    TrendReport,
    WorkflowResult,
)
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService


def _data_report() -> DataReport:
    return DataReport(
        topic_title="储能增长背后的兑现问题",
        core_data=["储能业务同比增长67%"],
        growth_numbers=["67%"],
        companies=["宁德时代"],
        evidence_notes=["公开材料显示储能业务同比增长67%"],
        key_points=["增长还需要交付和回款验证"],
        watch_metrics=["交付量", "回款"],
        risk_flags=["产能扩张不等于利润兑现"],
        focus_industry="新能源",
    )


def test_quality_council_parses_structured_model_report(app_settings, logger) -> None:
    prompt_repository = MagicMock()
    prompt_repository.render.return_value = "quality review prompt"
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {
        "overall_score": 88,
        "fact_score": 92,
        "logic_score": 86,
        "structure_score": 85,
        "originality_score": 89,
        "compliance_score": 94,
        "verdict": "approved",
        "role_reviews": [
            {
                "role": "quality-fact-checker",
                "score": 92,
                "findings": ["数字可以回指数据报告"],
                "recommendation": "保持来源边界",
            }
        ],
        "chief_editor_conclusion": "文章可以进入后续发布审核。",
    }
    agent = QualityReviewCouncilAgent(app_settings, prompt_repository, openai_service, logger)

    report = agent.run(
        GeneratedContent(
            article="据公开材料显示，宁德时代储能业务同比增长67%。但后续仍要看交付和回款。",
            polished_article="据公开材料显示，宁德时代储能业务同比增长67%。但后续仍要看交付和回款。",
            recommended_titles=["储能增长背后的兑现问题"],
        ),
        _data_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        CacheContext(),
    )

    assert report.verdict == "approved"
    assert report.needs_revision is False
    assert report.overall_score == 88
    assert report.role_reviews[0]["role"] == "quality-fact-checker"
    assert report.used_fallback is False
    prompt_repository.render.assert_called_once()


def test_quality_council_fallback_blocks_unsupported_numbers_and_internal_labels(
    app_settings,
    logger,
) -> None:
    openai_service = MagicMock()
    openai_service.available = False
    agent = QualityReviewCouncilAgent(app_settings, MagicMock(), openai_service, logger)

    report = agent.run(
        GeneratedContent(
            article="据公开材料显示，宁德时代储能业务增长999%。manual_paste：待补充来源。",
            recommended_titles=["储能业务变化"],
        ),
        _data_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        CacheContext(),
    )

    assert report.used_fallback is True
    assert report.verdict == "blocked"
    assert report.needs_revision is True
    assert any("999%" in item for item in report.hard_blockers)
    assert any("内部素材" in item for item in report.hard_blockers)


def test_model_approval_cannot_override_local_hard_blockers(app_settings, logger) -> None:
    prompt_repository = MagicMock()
    prompt_repository.render.return_value = "quality review prompt"
    openai_service = MagicMock()
    openai_service.available = True
    openai_service.create_json_completion.return_value = {
        "overall_score": 96,
        "fact_score": 96,
        "logic_score": 96,
        "structure_score": 96,
        "originality_score": 96,
        "compliance_score": 96,
        "verdict": "approved",
        "role_reviews": [{"role": "chief-editor", "findings": []}],
        "chief_editor_conclusion": "模型认为可以发布。",
    }
    agent = QualityReviewCouncilAgent(app_settings, prompt_repository, openai_service, logger)

    report = agent.run(
        GeneratedContent(
            article="据公开材料显示，宁德时代储能业务增长999%。manual_paste：待补充来源。",
            recommended_titles=["储能业务变化"],
        ),
        _data_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        CacheContext(),
    )

    assert report.used_fallback is False
    assert report.verdict == "blocked"
    assert report.needs_revision is True
    assert report.overall_score <= 59
    assert report.compliance_score <= 35


def test_quality_council_blocks_vague_title_and_malformed_body(app_settings, logger) -> None:
    openai_service = MagicMock()
    openai_service.available = False
    agent = QualityReviewCouncilAgent(app_settings, MagicMock(), openai_service, logger)

    report = agent.run(
        GeneratedContent(
            article="公开材料显示，优必选、为最主要的增长驱动力出现在现有材料中。",
            recommended_titles=["其他智能12.7亿：机器人如何实测"],
        ),
        _data_report(),
        app_settings.style_profiles["deep_industry_analysis"],
        CacheContext(),
    )

    assert report.verdict == "blocked"
    assert report.needs_revision is True
    assert any("模糊主体" in item for item in report.hard_blockers)
    assert any("残句" in item for item in report.hard_blockers)


def test_workflow_runs_one_automatic_quality_revision_then_rechecks(
    app_settings,
    mock_openai_service,
    logger,
    monkeypatch,
) -> None:
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    first_report = QualityReviewReport(
        overall_score=58,
        verdict="needs_revision",
        required_revisions=["拆分粘连段落并删除重复表达"],
        chief_editor_conclusion="需要返工。",
    )
    final_report = QualityReviewReport(
        overall_score=90,
        fact_score=90,
        logic_score=90,
        structure_score=90,
        originality_score=90,
        compliance_score=90,
        verdict="approved",
        chief_editor_conclusion="通过。",
    )
    service.quality_review_council_agent.run = MagicMock(side_effect=[first_report, final_report])
    service.polish_agent.run = MagicMock(
        return_value={"polished_article": "修订后的文章。", "validation_notes": [], "validation_score": 90}
    )

    def apply_revision(content, polish_pack, _data_report, **_kwargs):
        content.article = polish_pack["polished_article"]
        content.polished_article = content.article

    monkeypatch.setattr(service, "_apply_polish_pack", apply_revision)

    result = service._run_quality_review_council(
        content=GeneratedContent(article="原始文章。"),
        trend_report=TrendReport(recommended_angle="观察兑现"),
        data_report=_data_report(),
        style=app_settings.style_profiles["deep_industry_analysis"],
        cache_context=CacheContext(),
        editorial_council=None,
        fast_mode=False,
    )

    assert result is final_report
    assert service.quality_review_council_agent.run.call_count == 2
    service.polish_agent.run.assert_called_once()
    assert service.polish_agent.run.call_args.kwargs["revision_focus"]


def test_quality_council_receives_title_before_audit_and_after_revision(
    app_settings,
    mock_openai_service,
    logger,
    monkeypatch,
) -> None:
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    app_settings.expert_council_enabled = False
    app_settings.enhanced_insights_enabled = False

    extraction = ExtractionResult(
        merged_text="source material",
        core_data=["metric"],
        industry_trends=["trend"],
        growth_numbers=["67%"],
        companies=["Company A"],
        supply_chain_keywords=["storage"],
        key_points=["key point"],
        focus_industry="energy",
        topic_title="topic",
        topic_quality_score=80,
    )
    data_report = _data_report()
    generated = GeneratedContent(article="initial article")
    service.evolution_agent.run = MagicMock()
    service.trend_agent.run = MagicMock(return_value=TrendReport())
    service.data_agent.run = MagicMock(return_value=data_report)
    service.writer_agent.run = MagicMock(return_value=generated)
    service.polish_agent.run = MagicMock(
        side_effect=[
            {"polished_article": "first audited article", "validation_score": 90},
            {"polished_article": "revised audited article", "validation_score": 90},
        ]
    )
    service.title_agent.run = MagicMock(
        side_effect=[
            {"titles": ["title before revision"], "recommended_titles": ["title before revision"]},
            {"titles": ["title after revision"], "recommended_titles": ["title after revision"]},
        ]
    )
    observed_titles: list[list[str]] = []
    quality_reports = [
        QualityReviewReport(
            overall_score=58,
            verdict="needs_revision",
            required_revisions=["revise the article"],
        ),
        QualityReviewReport(
            overall_score=90,
            fact_score=90,
            logic_score=90,
            structure_score=90,
            originality_score=90,
            compliance_score=90,
            verdict="approved",
        ),
        QualityReviewReport(
            overall_score=91,
            fact_score=91,
            logic_score=91,
            structure_score=91,
            originality_score=91,
            compliance_score=91,
            verdict="approved",
        ),
    ]

    def quality_side_effect(content, *_args, **_kwargs):
        observed_titles.append(list(content.recommended_titles))
        return quality_reports[len(observed_titles) - 1]

    service.quality_review_council_agent.run = MagicMock(side_effect=quality_side_effect)
    service.review_agent.run = MagicMock(
        return_value={
            "content_score": ContentScore(
                pass_status="approved",
                ai_generated_risk=0,
                completion_rate_potential=90,
            )
        }
    )
    service.publish_review_agent.run = MagicMock(
        return_value={
            "publish_readiness_score": 90,
            "publish_risk_flags": [],
            "publish_recommendation": "auto_publish",
            "publish_notes": [],
        }
    )

    def apply_polish(content, polish_pack, _data_report, **_kwargs):
        content.article = polish_pack["polished_article"]
        content.polished_article = content.article
        content.validation_score = int(polish_pack.get("validation_score", 0))

    monkeypatch.setattr(service, "_apply_polish_pack", apply_polish)
    monkeypatch.setattr(service, "_filter_titles_by_quality", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_should_run_second_polish", lambda *args, **kwargs: False)
    monkeypatch.setattr(service, "_abort_if_low_quality", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_apply_bad_words_gate", lambda *args, **kwargs: None)

    result = service.run(
        extraction,
        style_key="deep_industry_analysis",
        cache_context=CacheContext(),
        fast_mode=False,
    )

    assert result.content.recommended_titles == ["title after revision"]
    assert observed_titles == [
        ["title before revision"],
        ["title before revision"],
        ["title after revision"],
    ]
    assert service.title_agent.run.call_count == 2
    assert service.quality_review_council_agent.run.call_count == 3


def test_post_review_article_change_triggers_final_quality_audit(
    app_settings,
    mock_openai_service,
    logger,
    monkeypatch,
) -> None:
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    app_settings.expert_council_enabled = False
    app_settings.enhanced_insights_enabled = False
    initial_report = QualityReviewReport(
        overall_score=90,
        fact_score=90,
        logic_score=90,
        structure_score=90,
        originality_score=90,
        compliance_score=90,
        verdict="approved",
    )
    final_report = QualityReviewReport(
        overall_score=52,
        verdict="needs_revision",
        required_revisions=["最终改写引入了新的事实风险"],
    )
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
    data_report = _data_report()
    generated = GeneratedContent(article="初始文章。")
    service.evolution_agent.run = MagicMock()
    service.trend_agent.run = MagicMock(return_value=TrendReport())
    service.data_agent.run = MagicMock(return_value=data_report)
    service.writer_agent.run = MagicMock(return_value=generated)
    service.polish_agent.run = MagicMock(
        side_effect=[
            {"polished_article": "首次润色后的文章。"},
            {"polished_article": "后置润色后的文章。"},
        ]
    )
    service.title_agent.run = MagicMock(return_value={})
    service.review_agent.run = MagicMock(
        return_value={"content_score": ContentScore(pass_status="needs_revision")}
    )
    service.publish_review_agent.run = MagicMock(
        return_value={
            "publish_readiness_score": 90,
            "publish_risk_flags": [],
            "publish_recommendation": "auto_publish",
            "publish_notes": [],
        }
    )
    quality_runner = MagicMock(side_effect=[initial_report, final_report])
    monkeypatch.setattr(
        service,
        "_run_quality_review_council",
        quality_runner,
    )
    monkeypatch.setattr(service, "_abort_if_low_quality", lambda *args, **kwargs: None)

    def apply_polish(content, polish_pack, _data_report, **_kwargs):
        content.article = polish_pack["polished_article"]
        content.polished_article = content.article

    monkeypatch.setattr(service, "_apply_polish_pack", apply_polish)
    monkeypatch.setattr(service, "_apply_title_pack", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_filter_titles_by_quality", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_apply_review_pack", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_apply_bad_words_gate", lambda *args, **kwargs: None)

    result = service.run(
        extraction,
        style_key="deep_industry_analysis",
        cache_context=CacheContext(),
        fast_mode=False,
    )

    assert result.quality_review_council is final_report
    assert quality_runner.call_count == 2
    assert quality_runner.call_args_list[1].kwargs["article_version"] == "post_review"
    assert quality_runner.call_args_list[1].kwargs["allow_auto_revise"] is False
    assert result.content.article == "后置润色后的文章。"


def test_quality_review_gate_blocks_auto_publish_until_revision(app_settings, mock_openai_service, logger) -> None:
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    score = ContentScore(
        publish_readiness_score=92,
        publish_recommendation="auto_publish",
    )
    report = QualityReviewReport(
        verdict="blocked",
        hard_blockers=["存在未核实数字。"],
        chief_editor_conclusion="阻断。",
    )

    service._apply_quality_review_gate(score, report)

    assert score.publish_readiness_score == 0
    assert score.publish_recommendation == "revise_before_publish"
    assert any("质量审核专家团阻断" in item for item in score.publish_risk_flags)


def test_quality_review_revision_lowers_readiness_without_zeroing(app_settings, mock_openai_service, logger) -> None:
    """非阻断的返工要求只降就绪度，不再一律归零。

    原实现两个分支都把 readiness 置 0，导致只要审核团跑过就绪度恒为 0，
    而自动发布要求 ≥60 —— 全自动发布在设计上就不可能通过。现在按问题
    条数折算，风险仍写进 flags 供人工/下游判断。
    """
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    score = ContentScore(
        publish_readiness_score=95,
        publish_recommendation="auto_publish",
    )
    report = QualityReviewReport(
        overall_score=72,
        verdict="needs_revision",
        required_revisions=["补充事实边界"],
    )

    service._apply_quality_review_gate(score, report)

    assert score.publish_readiness_score > 0, "非阻断不应把就绪度归零"
    assert score.publish_readiness_score < 95, "有问题就该低于原始就绪度"
    assert score.publish_recommendation in {"manual_review", "ready_to_publish"}
    assert any("质量审核专家团要求返工" in item for item in score.publish_risk_flags)


def test_quality_review_blocked_still_zeroes_readiness(app_settings, mock_openai_service, logger) -> None:
    """确有确定性硬伤时，就绪度必须归零——安全底线不能松。"""
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    score = ContentScore(publish_readiness_score=95, publish_recommendation="auto_publish")
    report = QualityReviewReport(
        overall_score=40,
        verdict="blocked",
        hard_blockers=["正文泄露了内部素材或流程标签，必须在发布前清除。"],
    )

    service._apply_quality_review_gate(score, report)

    assert score.publish_readiness_score == 0
    assert score.publish_recommendation == "revise_before_publish"


def test_quality_review_report_round_trips_in_workflow_result() -> None:
    payload = {
        "extraction": {},
        "trend_report": {},
        "data_report": {},
        "content": {},
        "content_score": {},
        "style_key": "deep_industry_analysis",
        "style_label": "深度行业分析风",
        "cache_key": "cache-key",
        "cache_status": "miss",
        "model_used": "local-draft",
        "created_at": "2026-08-29T20:00:00",
        "quality_review_council": {
            "overall_score": 87,
            "fact_score": 90,
            "verdict": "approved",
            "chief_editor_conclusion": "通过。",
        },
    }

    result = WorkflowResult.from_dict(payload)

    assert result.quality_review_council is not None
    assert result.quality_review_council.verdict == "approved"
    assert result.quality_review_council.overall_score == 87


def test_quality_review_report_parses_float_scores_without_silent_zero() -> None:
    """模型把分数写成浮点或浮点字符串时，不能静默归零。

    历史上 ``int("82.5")`` 会抛 ValueError 并退回 0，把一次正常评分变成
    「全维度 0 分」——既让 has_valid_scores 判为无效报告，也会把合规稿
    一路推到发布阻断。
    """
    report = QualityReviewReport.from_dict(
        {
            "verdict": "approved",
            "overall_score": "82.5",
            "fact_score": 80.0,
            "logic_score": "85",
            "structure_score": 78,
            "originality_score": "84.0",
            "compliance_score": 90,
        }
    )

    assert report.overall_score == 82
    assert report.fact_score == 80
    assert report.logic_score == 85
    assert report.structure_score == 78
    assert report.originality_score == 84
    assert report.compliance_score == 90
    assert report.has_valid_scores() is True
