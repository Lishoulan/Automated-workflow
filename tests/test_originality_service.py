"""原创性服务：连续抄录率算法与阈值判定。

背景：2026-09-15 实测一篇与素材原文连续重合 51.5% 的稿子被头条判「不原创」
扣 5 分，而本地查重原先只比自家历史，完全查不出这类风险。
"""
from __future__ import annotations

from toutiao_content_factory.services.originality_service import (
    BLOCK_THRESHOLD,
    REVIEW_THRESHOLD,
    SOURCE_MATERIAL_MIN_CHARS,
    originality_flags,
    source_copy_ratio,
)

SOURCE = (
    "人形机器人从实验室走向真实的商业场景的竞速过程中，智元机器人构建了领先的"
    "机器人本体与AI全栈技术，并实现了模型、数据和本体部署的全栈具身智能布局。"
    "端到端全栈协同能力使得它在真实作业场景中的落地速度具有先发优势。"
    "在产品矩阵上，智元并没有将目光只局限于某一个赛道，而是构建了包含远征人形、"
    "灵犀商服、精灵工业三个系列的多元化产品体系，这些产品很好地契合了工业制造、"
    "仓储物流、商业服务等高频、真实的工作场景，表现出很强的场景适应性。"
)


def test_identical_text_is_flagged_as_high_copy() -> None:
    result = source_copy_ratio(SOURCE, SOURCE)
    assert result["ratio"] is not None
    assert result["ratio"] >= BLOCK_THRESHOLD
    assert result["copied_chars"] > 0


def test_fully_rewritten_text_has_low_copy_ratio() -> None:
    rewritten = (
        "国标落地之后，行业里最先被检验的不是谁喊得响，而是交付账能不能算平。"
        "售后每年的维护开销、备件周转和现场调试时长，才是决定这门生意能不能持续的关键。"
        "从已公开的信息看，几家头部公司披露的口径并不统一，横向比较仍需谨慎。"
    )
    result = source_copy_ratio(rewritten, SOURCE)
    assert result["ratio"] is not None
    assert result["ratio"] < REVIEW_THRESHOLD


def test_short_source_is_reported_as_unjudgeable() -> None:
    result = source_copy_ratio("任何正文内容", "太短了")
    assert result["ratio"] is None
    assert "基准不足" in str(result["reason"])


def test_file_header_is_stripped_before_compare() -> None:
    header = "素材来源：ofweek\n原文标题：测试\n原文链接：https://example.com\n\n"
    body = "这是一段用于测试的正文内容。" * 30
    with_header = source_copy_ratio(body, header + body)
    without_header = source_copy_ratio(body, body)
    assert with_header["ratio"] == without_header["ratio"]


def test_min_chars_constant_matches_threshold_behaviour() -> None:
    just_under = "甲" * (SOURCE_MATERIAL_MIN_CHARS - 1)
    just_over = "甲" * (SOURCE_MATERIAL_MIN_CHARS + 50)
    assert source_copy_ratio("乙" * 100, just_under)["ratio"] is None
    assert source_copy_ratio("乙" * 100, just_over)["ratio"] is not None


def test_flags_escalate_by_ratio() -> None:
    assert originality_flags(None, "无素材原文") == ["原创性无法判断：无素材原文，需人工核对"]
    assert originality_flags(None) == []
    assert originality_flags(5.0) == []
    assert "不原创风险" in originality_flags(REVIEW_THRESHOLD)[0]
    assert "扣分" in originality_flags(BLOCK_THRESHOLD)[0]
