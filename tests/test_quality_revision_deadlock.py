"""Regression tests for the quality-revision deadlock (2026-10-21).

Observed production state: every scheduled run produced an article, the
quality review council returned ``needs_revision``, and the publish gate
turned that into ``manual_review``.  Nothing could ever revise the article:

* scheduled runs execute with ``RUN_FAST_MODE=true`` (``daily-run.yml``),
  and ``can_auto_revise`` used to require ``not fast_mode``;
* narrative accounts (the current production account, ``family_story``
  style) skip the LLM polish pass by design, so the revision attempt the
  loop did make returned the unchanged manuscript and the loop broke with
  ``revision_count == 0``.

The result was a contract that could not be satisfied: the gate demanded a
revision the pipeline was structurally forbidden from performing.  These
tests pin the two halves of the fix:

1. the council revision loop must run even in fast mode;
2. when PolishAgent cannot change the manuscript, WriterAgent must be able
   to rewrite it against the council's requirements, without inventing facts.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.models import (
    CacheContext,
    DataReport,
    GeneratedContent,
    QualityReviewReport,
    TrendReport,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService


def _data_report() -> DataReport:
    return DataReport(
        topic_title="科大讯飞的AI预期，兑现了多少",
        core_data=["科大讯飞2026年上半年营收同比增长约18%"],
        growth_numbers=["18%"],
        companies=["科大讯飞"],
        evidence_notes=["公开财报显示营收同比增长约18%"],
        key_points=["营收增长需要利润兑现验证"],
        watch_metrics=["经营性现金流", "智慧教育收入占比"],
        risk_flags=["估值预期与实际兑现存在差距"],
        focus_industry="人工智能",
        source_facts=["科大讯飞发布2026年半年度报告，营收同比增长约18%。"],
    )


def _long_text(seed: str, repeat: int = 30) -> str:
    return "\n\n".join(f"{seed}{index}，这是用于测试修订流程的正文段落。" for index in range(repeat))


def _council_report(verdict: str = "needs_revision", score: int = 58) -> QualityReviewReport:
    return QualityReviewReport(
        overall_score=score,
        fact_score=score,
        logic_score=score,
        structure_score=score,
        originality_score=score,
        compliance_score=score,
        verdict=verdict,
        required_revisions=["补充市值变化的市场预期原因", "删除重复段落"],
        chief_editor_conclusion="需要返工。" if verdict != "approved" else "通过。",
    )


def test_quality_revision_loop_runs_in_fast_mode(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """Fast mode must not disable the revision the publish gate demands."""
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    original = _long_text("原稿段落")
    revised = _long_text("修订段落")
    # Keep this test about the loop; the length floor has its own test below.
    monkeypatch.setattr(
        service,
        "_ensure_article_min_chars",
        lambda article, data_report, min_chars=None: article,
    )

    first = _council_report("needs_revision", 58)
    final = _council_report("approved", 92)
    service.quality_review_council_agent.run = MagicMock(side_effect=[first, final])
    # Narrative accounts: the polish pass returns the manuscript unchanged.
    service.polish_agent.run = MagicMock(
        return_value={"polished_article": original, "validation_notes": [], "validation_score": 80}
    )
    service.writer_agent.revise_article_via_llm = MagicMock(return_value=revised)

    content = GeneratedContent(article=original)
    report = service._run_quality_review_council(
        content=content,
        trend_report=TrendReport(recommended_angle="观察兑现"),
        data_report=_data_report(),
        style=app_settings.style_profiles["deep_industry_analysis"],
        cache_context=CacheContext(),
        editorial_council=None,
        fast_mode=True,
    )

    assert report is final, "the loop must re-audit the revised manuscript"
    service.writer_agent.revise_article_via_llm.assert_called_once()
    assert service.quality_review_council_agent.run.call_count == 2
    # The length floor re-cleans/re-paragraphs the text, so compare content.
    assert "".join(service._quality_review_article(content).split()) == "".join(revised.split())


def test_polish_revision_is_preferred_over_writer_rewrite(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """A working polish pass must stay the first revision tool."""
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    original = _long_text("原稿段落")
    polished = _long_text("润色段落")

    service.quality_review_council_agent.run = MagicMock(
        side_effect=[_council_report("needs_revision", 58), _council_report("approved", 92)]
    )
    service.polish_agent.run = MagicMock(
        return_value={"polished_article": polished, "validation_notes": [], "validation_score": 90}
    )
    service.writer_agent.revise_article_via_llm = MagicMock(return_value=_long_text("不该用我"))

    def apply_revision(content, polish_pack, _data_report, **_kwargs):
        content.article = polish_pack["polished_article"]
        content.polished_article = content.article

    monkeypatch.setattr(service, "_apply_polish_pack", apply_revision)

    content = GeneratedContent(article=original)
    service._run_quality_review_council(
        content=content,
        trend_report=TrendReport(recommended_angle="观察兑现"),
        data_report=_data_report(),
        style=app_settings.style_profiles["deep_industry_analysis"],
        cache_context=CacheContext(),
        editorial_council=None,
        fast_mode=False,
    )

    service.polish_agent.run.assert_called_once()
    service.writer_agent.revise_article_via_llm.assert_not_called()
    assert service._quality_review_article(content) == polished


def _writer(app_settings, mock_openai_service) -> WriterAgent:
    service = mock_openai_service
    service.available = True
    return WriterAgent(
        app_settings,
        PromptRepository(app_settings.prompts_dir),
        service,
        __import__("logging").getLogger("test_revision"),
    )


def test_writer_revision_keeps_preexisting_numbers_and_companies(
    app_settings, mock_openai_service
) -> None:
    """重写保留原稿已有的数字/公司不算"编造"。

    2026-10-21 实测：原稿里的"市值从1150亿跌到878亿"和"英伟达"并不在
    data_report 的 growth_numbers/companies 里，整篇校验会把**任何**重写稿判成
    unsupported → 返工永远返回空串。只应校验重写新增的内容。
    """
    agent = _writer(app_settings, mock_openai_service)
    original = (
        "科大讯飞的市值从1150亿跌到878亿，市场开始重新审视AI预期。"
        "同期英伟达的算力叙事仍在延续，但两家公司面对的兑现条件并不相同。"
        + _long_text("原稿段落", repeat=20)
    )
    revised = (
        "市值从1150亿跌到878亿之后，科大讯飞面对的追问变了：市场要看到兑现。"
        "英伟达的算力叙事仍在延续，可两家公司面对的兑现条件并不相同。"
        + _long_text("修订段落", repeat=20)
    )
    mock_openai_service.create_json_completion.return_value = {"article": revised}

    result = agent.revise_article_via_llm(original, _data_report(), ["修改开头，先交代结果"])

    assert result, "保留原稿已有数字与公司的重写必须被接受"
    assert "1150亿" in result and "878亿" in result
    assert "英伟达" in result


def test_writer_revision_rejects_unsupported_numbers(app_settings, mock_openai_service) -> None:
    agent = _writer(app_settings, mock_openai_service)
    original = _long_text("原稿段落")
    mock_openai_service.create_json_completion.return_value = {
        "article": original + "\n\n2026年净利润增长 1234%，市占率 87%。"
    }

    assert agent.revise_article_via_llm(original, _data_report(), ["补充数据支撑"]) == ""


def test_writer_revision_rejects_over_compressed_output(app_settings, mock_openai_service) -> None:
    agent = _writer(app_settings, mock_openai_service)
    original = _long_text("原稿段落", repeat=40)
    mock_openai_service.create_json_completion.return_value = {"article": "只有一句话。"}

    assert agent.revise_article_via_llm(original, _data_report(), ["删除重复段落"]) == ""


def test_writer_revision_rejects_unknown_companies(app_settings, mock_openai_service) -> None:
    agent = _writer(app_settings, mock_openai_service)
    original = _long_text("原稿段落")
    mock_openai_service.create_json_completion.return_value = {
        "article": original + "\n\n据某未披露机构称，华为与英伟达已达成合作。"
    }

    assert agent.revise_article_via_llm(original, _data_report(), ["补充行业分析"]) == ""


def test_writer_revision_returns_rewritten_article(app_settings, mock_openai_service) -> None:
    agent = _writer(app_settings, mock_openai_service)
    original = _long_text("原稿段落")
    revised = _long_text("修订段落")
    mock_openai_service.create_json_completion.return_value = {"article": revised}

    result = agent.revise_article_via_llm(original, _data_report(), ["补充市值变化原因"])

    assert result, "a clean revision must be returned"
    # The cleaner reflows paragraphs, so compare the non-whitespace content.
    assert "".join(result.split()) == "".join(revised.split())


def test_writer_revision_is_disabled_for_wechat(app_settings, mock_openai_service) -> None:
    """WeChat has its own evidence/revision integrity machinery; do not bypass it."""
    wechat_settings = replace(app_settings, platform="wechat")
    agent = _writer(wechat_settings, mock_openai_service)
    original = _long_text("原稿段落")
    mock_openai_service.create_json_completion.return_value = {"article": _long_text("修订段落")}

    assert agent.revise_article_via_llm(original, _data_report(), ["补充数据"]) == ""
    mock_openai_service.create_json_completion.assert_not_called()


def test_writer_revision_requires_model_and_focus(app_settings, mock_openai_service) -> None:
    agent = _writer(app_settings, mock_openai_service)
    original = _long_text("原稿段落")

    assert agent.revise_article_via_llm(original, _data_report(), []) == ""
    mock_openai_service.available = False
    assert agent.revise_article_via_llm(original, _data_report(), ["补充数据"]) == ""


def test_writer_revision_survives_model_failure(app_settings, mock_openai_service) -> None:
    agent = _writer(app_settings, mock_openai_service)
    original = _long_text("原稿段落")
    mock_openai_service.create_json_completion.side_effect = RuntimeError("timeout")

    assert agent.revise_article_via_llm(original, _data_report(), ["补充数据"]) == ""


def test_writer_revision_reapplies_publish_length_floor(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """返工不能把"质量否决"换成"长度否决"。

    写手重写走的是旁路，不经过 `_apply_polish_pack`，而发布下限正是在那里
    重新施加的（叙事 700 / 自动发布 1750）。重写允许缩到原文 80%，850 字的
    稿件重排后可能只剩 680 字 —— 那会被 `_qualifies_for_auto_approval` 的
    长度门拦下，等于从"审核团的错"换成"字数的错"，白跑一轮。
    """
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    original = _long_text("原稿段落")
    shrunk = "重写后明显变短的正文。"
    service.writer_agent.revise_article_via_llm = MagicMock(return_value=shrunk)
    content = GeneratedContent(article=original, polished_article=original)
    floor = MagicMock(return_value=_long_text("补足长度后的正文"))
    monkeypatch.setattr(service, "_ensure_article_min_chars", floor)

    assert service._apply_writer_revision(content, _data_report(), ["删除重复段落"]) is True

    floor.assert_called_once()
    assert floor.call_args.kwargs.get("min_chars", 0) >= 700
    assert service._quality_review_article(content) == _long_text("补足长度后的正文")


def test_writer_revision_refreshes_validation_score(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """返工后必须重算 validation_score，否则自动发布拿旧稿分数放行。"""
    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)
    service.openai_service.available = True
    original = _long_text("原稿段落")
    revised = _long_text("修订段落")
    service.writer_agent.revise_article_via_llm = MagicMock(return_value=revised)
    monkeypatch.setattr(
        service,
        "_ensure_article_min_chars",
        lambda article, data_report, min_chars=None: article,
    )
    service.polish_agent.validate_article = MagicMock(return_value=(["修订后校验备注"], 72))
    content = GeneratedContent(
        article=original, polished_article=original, validation_score=100
    )

    assert (
        service._apply_writer_revision(
            content,
            _data_report(),
            ["删除重复段落"],
            TrendReport(recommended_angle="观察兑现"),
        )
        is True
    )
    assert content.validation_score == 72
    assert content.validation_notes == ["修订后校验备注"]


def test_approved_revision_reaches_the_auto_publish_path(app_settings, mock_openai_service, logger) -> None:
    """端到端：审核通过后，发布闸门必须真的能放行。

    死锁的最终表现是 `publish_recommendation=manual_review`，而
    `_qualifies_for_auto_approval` 把这个字段当成硬否决。这里锁住
    "审核通过 → 建议不被改写 → 自动发布判定为真"，以及反向的
    "needs_revision → 建议被改成 manual_review → 判定为假"。
    """
    from toutiao_content_factory.models import ContentScore

    service = AgentWorkflowService(app_settings, MagicMock(), mock_openai_service, logger)

    approved = _council_report("approved", 92)
    approved.required_revisions = []
    score = ContentScore(publish_readiness_score=92, publish_recommendation="ready_to_publish")
    service._apply_quality_review_gate(score, approved)
    assert score.publish_recommendation == "ready_to_publish"
    assert not score.publish_risk_flags, "通过审核的稿件不该被挂上返工风险"

    needs_revision = _council_report("needs_revision", 58)
    score = ContentScore(publish_readiness_score=92, publish_recommendation="ready_to_publish")
    service._apply_quality_review_gate(score, needs_revision)
    # 这一步就是死锁的入口：建议被改成 manual_review，而 AppService 的
    # `_qualifies_for_auto_approval` 只接受 ready_to_publish / approve。
    assert score.publish_recommendation == "manual_review"
    assert any("要求返工" in item for item in score.publish_risk_flags)


