"""运营试验的方向分类太窄，直接把选题池砍掉 14/17（2026-10-21 实测）。

``robot_notes`` 的 30 天运营试验只保留 ``consumer_practical`` / ``technology_everyday``
两个方向（``rank_revenue_topics`` 会把不匹配的选题**直接丢弃**）。用 run 39577216468
的 ``daily_topics.json`` 复算：17 个选题只有 **3 个**通过分类，14 个被丢，
其中包含明显属于两方向意图的选题：

* ``国内黄金克价跌破890元`` —— "科技产业变化对普通人的支出…影响"（价格/支出）
* ``宝马员工争着离职挤崩系统`` —— "…工作与生活的影响"（劳动/就业）
* ``警方回应问界汽车碰撞起火：司机遇难`` —— 消费品安全对普通人的影响

分类函数 ``trial_direction`` 的关键词表过窄（只认 手机/芯片/机器人 等硬件词与
固定资产类消费词），于是试验样本被系统性偏置成"纯产业选题"，也直接压缩了
每日可用于微头条/图文的候选量。本文件锁住扩展后的分类：上述三类要能识别，
而纯产业分析（无读者影响）仍然不通过。
"""

from __future__ import annotations

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.revenue_feedback import trial_direction


def _topic(title: str, summary: str = "") -> TopicCandidate:
    return TopicCandidate(
        title=title,
        url="https://example.test/topic",
        source="公开信息",
        source_type="test",
        summary=summary,
        snippet=summary,
        matched_keywords=[],
        rank=1,
        total_score=80,
    )


def test_price_change_is_consumer_practical() -> None:
    assert trial_direction(
        _topic("国内黄金克价跌破890元", "多家金店下调零售价，消费者观望情绪上升。")
    ) == "consumer_practical"


def test_workplace_change_is_consumer_practical() -> None:
    assert trial_direction(
        _topic("宝马员工争着离职挤崩系统", "员工集中提交离职申请，人事系统一度崩溃。")
    ) == "consumer_practical"


def test_consumer_safety_incident_is_technology_everyday() -> None:
    assert trial_direction(
        _topic(
            "警方回应问界汽车碰撞起火：司机遇难",
            "一辆问界汽车发生碰撞后起火，司机不幸遇难，车主群关注电池安全。",
        )
    ) == "technology_everyday"


def test_pure_industry_analysis_stays_unclassified() -> None:
    # No reader impact: supply-chain/process news must not be forced into the trial.
    assert trial_direction(
        _topic(
            "三星电子、SK 海力士升级中国工厂，生产更先进闪存芯片",
            "两家公司调整产线与制程节点，继续扩大在华存储芯片产能。",
        )
    ) == ""


def test_story_titles_are_not_forced_into_the_trial() -> None:
    # Pure social stories already have their own account direction; the trial is
    # about consumer/tech impact, so a probate dispute stays unclassified here.
    assert trial_direction(
        _topic("陆奶奶的赠房份额被撤销", "法院认定孙女未履行赡养义务，撤销部分赠与。")
    ) == ""
