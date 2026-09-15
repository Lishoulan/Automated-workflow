"""自动修复服务：把可机械修复的问题在审核前改掉。

背景：流程跑在 GitHub Actions 上，没有人工介入。原设计「发现问题就拦」在
无人值守场景下等于每天 0 篇可发（2026-09-15 实测 23 篇全被拦）。自动化的
正确姿势是能自动修的先修，修不掉再拦。
"""
from __future__ import annotations

import re

from toutiao_content_factory.services.auto_repair_service import (
    SEQUENCE_RESIDUE_PATTERNS,
    repair_absolute_claims,
    repair_article,
    repair_internal_leaks,
    repair_prompt_narration,
    repair_sequence_artifacts,
)


def test_absolute_claims_are_softened() -> None:
    text = "这完全证明了行业成熟，产能必然过剩，投资者一定可以获得回报。"
    fixed, changes = repair_absolute_claims(text)
    assert "完全" not in fixed
    assert "必然" not in fixed
    assert "一定可以" not in fixed
    assert len(changes) == 3


def test_normal_phrases_are_not_mistakenly_rewritten() -> None:
    # 「一定程度上」是正常表达，不该被当成绝对化断言改写
    text = "这在一定程度上说明行业正在改善，产品需要一定的验证周期。"
    fixed, changes = repair_absolute_claims(text)
    assert fixed == text
    assert changes == []


def test_internal_labels_are_removed() -> None:
    text = "据公开材料显示 manual_paste：素材正文，质量审核专家团建议修改。"
    fixed, changes = repair_internal_leaks(text)
    assert "manual_paste" not in fixed
    assert "质量审核专家团" not in fixed
    assert changes


def test_sequence_artifacts_become_paragraph_breaks() -> None:
    text = "第一段结束。', '第二段开始。"
    fixed, changes = repair_sequence_artifacts(text)
    assert "', '" not in fixed
    assert "第一段结束。\n\n第二段开始。" == fixed
    assert changes


def test_repair_article_reports_all_changes() -> None:
    text = (
        "这绝对是行业的胜利，产能必然过剩。', '后半段内容。\n\n"
        "TODO 补充数据，quality_review 标记"
    )
    result = repair_article(text)
    assert result["changed"] is True
    joined = "；".join(result["changes"])
    assert "绝对" in joined
    assert "清除内部标签" in joined or "还原段落分隔" in joined
    assert "TODO" not in str(result["article"])
    assert "', '" not in str(result["article"])


def test_clean_article_is_left_untouched() -> None:
    text = (
        "优必选柳州工厂的投产，说明供给侧已经具备量产能力。\n\n"
        "但产线只证明供给侧，不证明需求侧，后续仍要看开机率与续单率。"
    )
    result = repair_article(text)
    assert result["changed"] is False
    assert result["article"] == text


def test_prompt_narration_is_stripped_from_paragraph_start() -> None:
    """实测污染：模型把写作规则当成正文开头写了进去。"""
    text = (
        "下面只按现有材料展开，材料里的推测会明确保留为推测，"
        "不把它改写成已经确认的事实先把主体和时间线放回原位。"
    )
    fixed, changes = repair_prompt_narration(text)
    assert fixed == "先把主体和时间线放回原位。"
    assert changes


def test_prompt_narration_with_sentence_ending_is_also_stripped() -> None:
    text = (
        "下面只按现有材料展开，材料里的推测会明确保留为推测，"
        "不把它改写成已经确认的事实。后面的正文继续。"
    )
    fixed, _ = repair_prompt_narration(text)
    assert fixed == "后面的正文继续。"
    assert "只按现有材料" not in fixed


def test_prompt_narration_not_at_paragraph_start_is_kept() -> None:
    """只在段首剥离——出现在句中属于正常引用，改了反而破坏句子。"""
    text = "这是正常的段落，句中提到下面只按现有材料展开这个流程，但不在段首。"
    fixed, changes = repair_prompt_narration(text)
    assert fixed == text
    assert changes == []


def test_paragraph_made_only_of_narration_is_dropped() -> None:
    text = "真正的正文段落。\n\n下面只按现有材料展开\n\n下一段正文。"
    fixed, _ = repair_prompt_narration(text)
    assert fixed == "真正的正文段落。\n\n下一段正文。"


def test_repair_article_removes_prompt_narration() -> None:
    text = "下面只按现有材料展开，材料里的推测会明确保留为推测。正文从这里开始。"
    result = repair_article(text)
    assert result["changed"] is True
    assert str(result["article"]).startswith("正文从这里开始。")
    assert any("提示词元叙述" in item for item in result["changes"])


def test_sequence_residue_detection_survives_repair() -> None:
    """修复会抹掉 `', '`，但残留检测必须仍能认出这是序列化垃圾。"""
    raw = "[{'judgment': '某段判断', 'fact': '某段事实'}]"
    fixed = str(repair_article(raw)["article"])
    assert not re.search(r"['\"]\s*,\s*['\"]", fixed)  # 原始分隔符已被替换
    assert any(pattern.search(fixed) for pattern in SEQUENCE_RESIDUE_PATTERNS)


def test_sequence_residue_detection_ignores_normal_prose() -> None:
    normal = (
        "公开材料把这条线索写得很直白：产线只是起点，后续要看开机率。\n\n"
        "行业里常说的「复制率」，指的是试点到扩线之间的比例。"
    )
    assert not any(pattern.search(normal) for pattern in SEQUENCE_RESIDUE_PATTERNS)
