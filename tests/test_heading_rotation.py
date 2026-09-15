"""小标题模板轮转：避免每篇文章都用同一个模板开头。

背景：原先 `_build_section_headings` 固定取 `candidates[:desired]`，导致
「XX在热，兑现偏冷」成为每篇的第一个小标题，实测 43 篇雷同，账号层面会被
判定同质化。轮转后按选题确定性打散。
"""
from __future__ import annotations

from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.models import DataReport

POOL = [
    "机器人在热，兑现偏冷",
    "看着提速，实则分化",
    "数据先行，利润未满",
    "故事不小，变量更大",
    "热度上来，订单未到",
    "产线很忙，回款很慢",
    "口径变了，结论未变",
    "预期在前，验证在后",
    "数字好看，账还没算",
    "场景很热，收入很薄",
]

TOPICS = [
    "优必选柳州工厂",
    "智元机器人上市",
    "丝杠产线扩产",
    "小鹏产线投产",
    "宇树订单结构",
    "减速器涨价",
    "具身智能融资",
    "电池税落地",
]


def _agent() -> WriterAgent:
    agent = WriterAgent.__new__(WriterAgent)
    agent._primary_keyword = lambda _report: "机器人"  # type: ignore[assignment]
    return agent


def test_rotation_uses_different_openings_across_topics() -> None:
    agent = _agent()
    openings = []
    for topic in TOPICS:
        report = DataReport(topic_title=topic, focus_industry="机器人", focus_keywords=["机器人"])
        openings.append(agent._rotate_headings(POOL, 4, report)[0])
    # 8 个不同选题里，开头模板至少要有 3 种，否则说明又退化成固定模板。
    assert len(set(openings)) >= 3, f"开头模板过于集中：{openings}"


def test_rotation_is_deterministic_for_same_topic() -> None:
    agent = _agent()
    report = DataReport(topic_title="优必选柳州工厂", focus_industry="机器人", focus_keywords=["机器人"])
    first = agent._rotate_headings(POOL, 4, report)
    second = agent._rotate_headings(POOL, 4, report)
    assert first == second, "同一选题两次生成结果不一致，会破坏缓存"


def test_rotation_returns_requested_count_without_duplicates() -> None:
    agent = _agent()
    report = DataReport(topic_title="优必选柳州工厂", focus_industry="机器人", focus_keywords=["机器人"])
    headings = agent._rotate_headings(POOL, 4, report)
    assert len(headings) == 4
    assert len(set(headings)) == 4


def test_rotation_respects_short_pool() -> None:
    agent = _agent()
    report = DataReport(topic_title="优必选柳州工厂")
    assert agent._rotate_headings(["甲", "乙"], 4, report) == ["甲", "乙"]
    assert agent._rotate_headings([], 4, report) == []
