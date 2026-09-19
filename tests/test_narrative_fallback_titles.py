"""叙事账号（社会故事）的兜底标题不许出现产业句式。

背景（2026-09-19 真实运行，`output/accounts/robot-notes/titles.txt`）：
模型候选被闸门全拒后启用了兜底池，而 `_fallback_titles` 的模板全是产业分析句式
（样机/交付/订单/产能/重估/突围/热门公司），只用关键词做字符串拼接，于是拼出：

    老人从样机走到交付，中间卡在哪一步
    老人升温之后，先突围的不是热门公司
    老人热度之外，谁在拿走真正订单

关键词「老人」抓对了，句式完全是科技号的 —— 发出去等于把刚改好的
「人间故事」定位又毁掉。`_emergency_titles` 里的「X热度之外，谁在拿走真正订单」
是同一个毛病。

本文件锁住：**叙事域下生成的任何兜底/应急标题，都不许含产业词**。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from toutiao_content_factory.agents.title_agent import TitleAgent
from toutiao_content_factory.config import AppSettings, load_settings
from toutiao_content_factory.models import (  # noqa: E402
    CacheContext,
    DataReport,
    GeneratedContent,
    TrendReport,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository

#: 这些词出现在社会故事号的标题里就是串味
INDUSTRY_TITLE_TOKENS = (
    "样机", "交付", "订单", "产能", "产业链", "重估", "突围", "热门公司",
    "供应链", "零部件", "毛利", "回款", "融资", "技术路线", "量产",
    "市场份额", "竞争格局", "验证期", "放量", "兑现",
)

STORY_ACCOUNT = "robot_notes"
INDUSTRY_ACCOUNT = "default"


def _settings(account_key: str) -> AppSettings:
    return load_settings(Path.cwd(), account_key=account_key)


class _StubOpenAI:
    """构造器只用到 `.model`；这些方法本身不触网。"""

    model = "stub-model"


def _agent(settings: AppSettings) -> TitleAgent:
    return TitleAgent(
        settings,
        PromptRepository(Path.cwd() / "prompts"),
        _StubOpenAI(),
        logging.getLogger("test_narrative_titles"),
    )


def _story_report() -> DataReport:
    return DataReport(
        topic_title="男子高空作业绳子被2楼老人剪断",
        focus_industry="social_story",
        focus_keywords=["老人", "高空作业"],
        supply_chain_keywords=["老人"],
        growth_numbers=[],
        companies=[],
        focus_companies=[],
        core_data=["9月18日，男子30层高空作业绳子被2楼老人剪断"],
        evidence_notes=["公开报道：二楼住户用剪刀将绳索剪断"],
        source_facts=["9月18日，男子30层高空作业绳子被2楼老人剪断"],
    )


def _story_content() -> GeneratedContent:
    body = (
        "9月18日，一则关于男子30层高空作业绳子被2楼老人剪断的消息冲上热搜。"
        "据报道，该男子正在小区外立面进行清洁作业，二楼住户因不满绳索经过自家窗户，"
        "用剪刀将绳索剪断。"
    )
    return GeneratedContent(article=body, polished_article=body, summary=body[:60])


# ---------------------------------------------------------------------------
# 1. 叙事域兜底标题
# ---------------------------------------------------------------------------


def test_narrative_fallback_titles_contain_no_industry_wording() -> None:
    agent = _agent(_settings(STORY_ACCOUNT))

    titles = agent._narrative_fallback_titles(_story_content(), _story_report())

    assert titles, "故事号必须能兜出至少一个标题"
    for title in titles:
        hits = [token for token in INDUSTRY_TITLE_TOKENS if token in title]
        assert hits == [], f"兜底标题串味: {title} → {hits}"


def test_narrative_fallback_titles_respect_the_length_limit() -> None:
    """选题原标题有 18 字，再套句式就会顶到 30 字上限，必须被裁过。"""
    settings = _settings(STORY_ACCOUNT)
    agent = _agent(settings)
    limit = int(settings.publishing.title_max_length)

    titles = agent._narrative_fallback_titles(_story_content(), _story_report())

    for title in titles:
        assert len(title) <= limit, f"兜底标题超长({len(title)}>{limit}): {title}"


def test_narrative_fallback_puts_the_original_topic_title_first() -> None:
    """平台自己给这条新闻起的标题是最安全的兜底，必须排第一。"""
    agent = _agent(_settings(STORY_ACCOUNT))

    titles = agent._narrative_fallback_titles(_story_content(), _story_report())

    assert titles[0] == "男子高空作业绳子被2楼老人剪断"


def test_narrative_fallback_is_prepended_in_the_full_fallback_pool() -> None:
    """真正的修复点在 `_fallback_titles`：故事句式必须排在产业模板之前。"""
    agent = _agent(_settings(STORY_ACCOUNT))

    titles = agent._fallback_titles(
        _story_content(),
        trend_report=TrendReport(),
        data_report=_story_report(),
        style=_settings(STORY_ACCOUNT).get_style(),
        cache_context=CacheContext(),
        playbook_context=None,
    )

    assert titles, "兜底池不能为空"
    # 故事句式占据了最前面几位（产业模板仍在池子里当陪衬，但排不到前面）
    assert titles[0] == "男子高空作业绳子被2楼老人剪断"


# ---------------------------------------------------------------------------
# 2. 叙事域应急标题
# ---------------------------------------------------------------------------


def test_narrative_emergency_titles_contain_no_industry_wording() -> None:
    agent = _agent(_settings(STORY_ACCOUNT))

    titles = agent._emergency_titles(_story_content(), _story_report())

    assert titles
    for title in titles:
        hits = [token for token in INDUSTRY_TITLE_TOKENS if token in title]
        assert hits == [], f"应急标题串味: {title} → {hits}"


# ---------------------------------------------------------------------------
# 3. 产业账号必须完全不受影响
# ---------------------------------------------------------------------------


def test_industry_account_keeps_its_original_fallback_templates() -> None:
    """对照：产业号的兜底池行为一个字都不该变。"""
    agent = _agent(_settings(INDUSTRY_ACCOUNT))
    report = DataReport(
        topic_title="比亚迪玩跨界？进军人形机器人，代号尧舜禹",
        focus_industry="AI机器人",
        focus_keywords=["比亚迪", "人形机器人"],
        supply_chain_keywords=["比亚迪"],
        growth_numbers=["460万台"],
        companies=["比亚迪"],
        focus_companies=["比亚迪"],
        core_data=["比亚迪定下460万台目标"],
        evidence_notes=["公开材料：比亚迪进军人形机器人，提出460万台目标"],
    )
    content = GeneratedContent(
        article="公开材料显示，比亚迪提出460万台目标，交付节奏仍是关键。",
        polished_article="公开材料显示，比亚迪提出460万台目标，交付节奏仍是关键。",
        summary="比亚迪提出460万台目标",
    )

    titles = agent._fallback_titles(
        content,
        trend_report=TrendReport(),
        data_report=report,
        style=_settings(INDUSTRY_ACCOUNT).get_style(),
        cache_context=CacheContext(),
        playbook_context=None,
    )

    # 产业句式还在（说明没有把产业号一起改坏）
    assert any("交付" in title or "订单" in title or "产能" in title for title in titles)


def test_is_narrative_style_reads_the_account_domain() -> None:
    story_agent = _agent(_settings(STORY_ACCOUNT))
    industry_agent = _agent(_settings(INDUSTRY_ACCOUNT))

    assert story_agent._is_narrative_style(_settings(STORY_ACCOUNT).get_style()) is True
    assert industry_agent._is_narrative_style(_settings(INDUSTRY_ACCOUNT).get_style()) is False


