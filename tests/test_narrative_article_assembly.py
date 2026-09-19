"""叙事稿的正文拼接不许出现「机器拼接」痕迹。

背景（2026-09-19 真实出稿 `runs/20260919-120328_网红张凯毅官宣离婚/publish/article.md`）：
正文后段是补段拼出来的，三处问题同时出现：

1. **原始素材行被当事实拼进正文**
   「把素材里能确认的部分列一下：**【来源：网红张凯毅官宣离婚，孩子的抚养权归她…】**」
   读者看到的是抓取器的内部格式。

2. **引导句 / 边界句重复**
   「把素材里能确认的部分列一下」出现 3 次，
   「这些是目前能核实的范围，后续处理结果还没有公开」出现 3 次 ——
   因为引导句和边界句各只有 3 条，而事实最多 6 条，按 `index % len(...)` 取用必然重复。

3. **结尾问题被换成产业句**
   「官宣的2024年之后，更需要先**验证交付**，还是先看**利润和回款**？」
   一条网红离婚的稿子问起了交付与回款 —— 因为
   `_comment_prompt_is_concrete` 对叙事稿也用产业锚点判"具体性"，
   判不过就退回 `_build_concrete_comment_prompt` 的产业默认句。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import (
    CacheContext,
    DataReport,
    TrendReport,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository

STORY_ACCOUNT = "robot_notes"
INDUSTRY_ACCOUNT = "default"


class _StubOpenAI:
    model = "stub-model"


def _story_agent() -> WriterAgent:
    settings = load_settings(Path.cwd(), account_key=STORY_ACCOUNT)
    agent = WriterAgent(
        settings,
        PromptRepository(Path.cwd() / "prompts"),
        _StubOpenAI(),
        logging.getLogger("test_story_assembly"),
    )
    agent._current_content_domain = "narrative"
    return agent


def _story_report() -> DataReport:
    return DataReport(
        topic_title="网红张凯毅官宣离婚",
        focus_industry="social_story",
        focus_keywords=["离婚", "抚养权"],
        source_facts=[
            "【来源：网红张凯毅官宣离婚，孩子的抚养权归她，承认婚姻走到今天这一步】",
            "9月19日，张凯毅通过视频正式宣布，自己已经与丈夫张煜东结束婚姻关系。",
            "她在视频中透露，算上冷静期，这件事已经过去约三个月。",
        ],
    )


# ---------------------------------------------------------------------------
# 1. 原始素材标记不许进正文
# ---------------------------------------------------------------------------


def test_source_marker_lines_are_never_used_as_facts() -> None:
    agent = _story_agent()

    facts = agent._clean_hot_topic_source_facts(_story_report())

    assert facts, "正常的素材事实应该保留"
    for fact in facts:
        assert "【来源" not in fact
        assert "原文链接" not in fact
    # 带标记的那条被丢弃，正常的两条保留
    assert all("孩子的抚养权归她，承认婚姻走到今天这一步" not in fact for fact in facts)


def test_supplementary_paragraphs_contain_no_source_markers() -> None:
    agent = _story_agent()

    paragraphs = agent._build_narrative_supplementary_paragraphs(_story_report())

    joined = "\n".join(paragraphs)
    assert "【来源" not in joined
    assert "原文标题" not in joined


# ---------------------------------------------------------------------------
# 2. 引导句 / 边界句不许重复
# ---------------------------------------------------------------------------


def test_supplementary_lead_sentences_are_not_repeated() -> None:
    agent = _story_agent()

    paragraphs = agent._build_narrative_supplementary_paragraphs(_story_report())
    joined = "\n".join(paragraphs)

    for lead in agent.NARRATIVE_SUPPLEMENT_LEADS:
        assert joined.count(lead) <= 1, f"引导句重复: {lead}"

    for boundary in agent.NARRATIVE_SUPPLEMENT_BOUNDARIES:
        assert joined.count(boundary) <= 1, f"边界句重复: {boundary}"


def test_supplementary_paragraphs_are_unique() -> None:
    agent = _story_agent()

    paragraphs = agent._build_narrative_supplementary_paragraphs(_story_report())

    assert len(paragraphs) == len(set(paragraphs))


def test_generic_supplement_paragraphs_are_capped() -> None:
    """通用模板句（对任何故事都成立的那种）最多插 2 条，避免"两套稿子缝合"。"""
    agent = _story_agent()

    paragraphs = agent._build_narrative_supplementary_paragraphs(_story_report())
    joined = "\n".join(paragraphs)

    hits = [item for item in agent.NARRATIVE_SUPPLEMENT_PARAGRAPHS if item in joined]
    assert len(hits) <= agent.NARRATIVE_GENERIC_SUPPLEMENT_LIMIT


# ---------------------------------------------------------------------------
# 3b. 产业分析节拍不许套在叙事稿上
# ---------------------------------------------------------------------------


def test_self_benchmark_structure_is_skipped_for_narrative() -> None:
    """「结尾保留跟踪变量」等节拍会往正文插产业句（收入/利润/交付/回款）。"""
    agent = _story_agent()
    paragraphs = ["第一段讲事情。", "第二段讲处境。", "第三段给提醒。"]

    updated = agent._apply_self_benchmark_structure(
        list(paragraphs),
        TrendReport(),
        _story_report(),
        self_benchmark_context={"patterns": ["结尾保留跟踪变量", "利润迁移框架"]},
    )

    assert updated == paragraphs, "叙事稿不该被套上产业分析节拍"


def test_industry_account_still_applies_the_structure() -> None:
    """对照：产业号的节拍行为不变。"""
    settings = load_settings(Path.cwd(), account_key=INDUSTRY_ACCOUNT)
    agent = WriterAgent(
        settings,
        PromptRepository(Path.cwd() / "prompts"),
        _StubOpenAI(),
        logging.getLogger("test_story_assembly"),
    )
    agent._current_content_domain = "industry"
    report = DataReport(
        topic_title="比亚迪进军人形机器人",
        focus_industry="AI机器人",
        focus_keywords=["比亚迪", "人形机器人"],
        growth_numbers=["460万台"],
        companies=["比亚迪"],
        watch_metrics=["交付节奏", "毛利率"],
    )
    paragraphs = ["第一段。", "第二段。", "第三段。"]

    updated = agent._apply_self_benchmark_structure(
        list(paragraphs),
        TrendReport(),
        report,
        self_benchmark_context={"patterns": ["结尾保留跟踪变量"]},
    )

    assert updated != paragraphs, "产业号必须照旧套用节拍"


# ---------------------------------------------------------------------------
# 3. 叙事稿的结尾问题不许是产业句
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "industry_question",
    [
        "官宣的2024年之后，更需要先验证交付，还是先看利润和回款？",
        "您更看重这个方向后续先落地订单、交付，还是毛利率改善？",
    ],
)
def test_industry_closing_questions_are_replaced_for_narrative(
    industry_question: str,
) -> None:
    agent = _story_agent()

    finalized = agent._finalize_comment_prompt(industry_question, _story_report())

    for marker in ("交付", "回款", "利润", "毛利", "订单", "产能", "产业链"):
        assert marker not in finalized, f"叙事稿结尾仍问产业问题: {finalized}"


def test_good_narrative_closing_question_is_kept() -> None:
    """模型写对了就别动它。"""
    agent = _story_agent()
    good = "换成是你遇到这种情况，会怎么处理？"

    assert agent._finalize_comment_prompt(good, _story_report()) == good


def test_statement_without_question_is_not_accepted_as_closing() -> None:
    agent = _story_agent()

    finalized = agent._finalize_comment_prompt("这件事值得所有人深思。", _story_report())

    assert finalized.endswith(("？", "?"))
    assert "深思" not in finalized


# ---------------------------------------------------------------------------
# 4. 产业账号不受影响
# ---------------------------------------------------------------------------


def test_industry_account_keeps_its_industry_closing_question() -> None:
    settings = load_settings(Path.cwd(), account_key=INDUSTRY_ACCOUNT)
    agent = WriterAgent(
        settings,
        PromptRepository(Path.cwd() / "prompts"),
        _StubOpenAI(),
        logging.getLogger("test_story_assembly"),
    )
    agent._current_content_domain = "industry"
    report = DataReport(
        topic_title="比亚迪进军人形机器人",
        focus_industry="AI机器人",
        focus_keywords=["比亚迪", "人形机器人"],
        growth_numbers=["460万台"],
        companies=["比亚迪"],
        watch_metrics=["交付节奏", "毛利率"],
    )

    finalized = agent._finalize_comment_prompt(
        "比亚迪后续更该先看交付节奏，还是看毛利率？", report
    )

    assert "交付" in finalized or "毛利率" in finalized
