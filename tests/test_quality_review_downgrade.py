"""审核团阻断项分级：模型的主观疑虑不该一票否决。

背景：原先模型的 hard_blockers 与本地确定性硬伤被合并处理，模型一提
「可能存在误导性因果」，verdict 就变 blocked、发布就绪度归零。审核模型是
「挑刺专家」，任何稿子都能挑出「可能存在……」，于是 GitHub 上的全自动发布
永远过不了（2026-09-15 实测 23 篇全部被拦，自动发布 0 条）。

规则：只有能被代码复核的指控才允许阻断，其余降级为待改进建议。
"""
from __future__ import annotations

from toutiao_content_factory.agents.quality_review_council_agent import (
    QualityReviewCouncilAgent,
)

import logging

# 干净样本要够长，否则会被「正文过短」这类可核实的指控命中
CLEAN_ARTICLE = (
    "优必选柳州工厂的投产，说明供给侧已经具备万台级量产能力。据公开材料显示，"
    "该工厂按生产节拍每十分钟下线一台，年规划产能超过一万台。\n\n"
    "但产线只证明供给侧，不证明需求侧。后续仍要看产能利用率、交付后的开机率、"
    "客户第二年是否续单，以及单台机器人的毛利水平。这些指标决定了这门生意能否持续。\n\n"
    "从产业链分工看，本体制造只是其中一环。减速器、丝杠、传感器等核心零部件的"
    "国产化进度，会直接影响整机的成本结构和交付节奏。\n\n"
    "一个可观察的判断依据是：如果下半年公开披露的订单中，来自工业商用场景的比例"
    "持续上升，那么规模化的说法才有支撑；如果仍以科研教育采购为主，量产就更接近"
    "产能建设而非市场需求。\n\n"
    "综上，这条消息的价值在于提示产能已经就位，但真正决定长期回报的变量仍未明朗。\n\n"
    "需要区分的是产能和产量。规划产能代表工厂的理论上限，实际产量取决于订单"
    "排产情况，两者之间可能存在明显差距。同样的逻辑也适用于其他宣称万台级的项目。\n\n"
    "对读者来说，更有意义的跟踪方式是关注季报里披露的交付数字，而不是发布会上的"
    "规划数字。前者经过审计，后者只是目标。这也是判断行业是否真正走向成熟的分水岭。\n\n"
    "如果后续几个季度的交付量能够稳定爬坡，同时工业客户占比上升，那么就说明这条"
    "路径是成立的；反之，产能闲置的风险会逐步显性化，进而影响上游零部件的订单预期。"
)
LEAKY_ARTICLE = "据公开材料显示 manual_paste：素材正文内容，仅供参考。"
SHORT_ARTICLE = "太短。"


def _bare_agent() -> QualityReviewCouncilAgent:
    """构造不带 settings 的实例，只测纯逻辑方法。"""
    agent = QualityReviewCouncilAgent.__new__(QualityReviewCouncilAgent)
    agent.logger = logging.getLogger("test_quality_review")  # type: ignore[attr-defined]
    return agent


def _verify(blockers: list[str], article: str, unsupported: list[str] | None = None):
    agent = _bare_agent()
    snapshot = {"unsupported_numeric_tokens": unsupported or []}
    return agent._verify_model_blockers(blockers, snapshot, article, "")


def test_subjective_accusations_are_downgraded() -> None:
    confirmed, advisory = _verify(
        ["存在未经核实的关键事实", "可能存在误导性因果", "论证链缺乏充分证据支持"],
        CLEAN_ARTICLE,
    )
    assert confirmed == []
    assert len(advisory) == 3


def test_internal_leak_claim_needs_actual_evidence() -> None:
    # 正文干净时，模型的「内部标签可能泄露」是误报，必须降级
    confirmed, advisory = _verify(["内部标签可能泄露"], CLEAN_ARTICLE)
    assert confirmed == []
    assert advisory == ["内部标签可能泄露"]
    # 正文确实含内部标签时才允许阻断
    confirmed2, advisory2 = _verify(["内部标签可能泄露"], LEAKY_ARTICLE)
    assert confirmed2 == ["内部标签可能泄露"]
    assert advisory2 == []


def test_numeric_claim_confirmed_only_when_unsupported_numbers_exist() -> None:
    confirmed, _ = _verify(["正文存在未在数据报告中找到依据的数字：999亿"], CLEAN_ARTICLE)
    assert confirmed == []
    confirmed2, _ = _verify(
        ["正文存在未在数据报告中找到依据的数字：999亿"],
        CLEAN_ARTICLE,
        unsupported=["999亿"],
    )
    assert confirmed2 == ["正文存在未在数据报告中找到依据的数字：999亿"]


def test_length_claim_confirmed_only_when_body_is_short() -> None:
    confirmed, _ = _verify(["正文过短，未达到最低字数"], CLEAN_ARTICLE)
    assert confirmed == []
    confirmed2, _ = _verify(["正文过短，未达到最低字数"], SHORT_ARTICLE)
    assert confirmed2 == ["正文过短，未达到最低字数"]


def test_absolute_claims_are_never_used_as_blockers() -> None:
    # 绝对化属于可自动修复项（auto_repair_service 会改写），不应阻断发布
    confirmed, advisory = _verify(["存在绝对化表述：完全"], "这完全说明行业成熟。")
    assert confirmed == []
    assert advisory == ["存在绝对化表述：完全"]


def test_hard_gate_returns_early_when_no_local_blockers() -> None:
    """本地无确定性硬伤时，verdict 不应停留在 blocked。"""
    from toutiao_content_factory.models import QualityReviewReport

    agent = _bare_agent()
    report = QualityReviewReport(
        article_version="post_polish",
        overall_score=80,
        fact_score=80,
        logic_score=80,
        structure_score=80,
        originality_score=80,
        compliance_score=80,
        verdict="blocked",
        hard_blockers=["存在未经核实的关键事实"],
        fact_issues=[],
        logic_issues=[],
        structure_issues=[],
        originality_issues=[],
        compliance_issues=[],
        required_revisions=[],
        optional_improvements=[],
    )
    agent._apply_local_hard_gate(
        report,
        {"hard_blockers": []},
        article=CLEAN_ARTICLE,
        title="测试标题",
    )
    assert report.hard_blockers == []
    assert report.verdict == "needs_revision"
