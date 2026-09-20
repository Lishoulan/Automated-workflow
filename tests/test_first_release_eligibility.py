"""「头条首发」资格必须由生成侧去够，而不是靠降低发布门槛。

## 背景（2026-09-21）

用户提出要开「头条首发激励」。查下来发现**三个地方在互相抵消**：

1. `WriterAgent._auto_publish_directive()` 告诉模型「目标约1100字，最低950字」
2. `WriterAgent._article_word_target_for_prompt()` 返回 `1100`
3. `TopicAutomationService._build_word_target()` 对 social_story 返回 `1000`
4. `PolishAgent._auto_publish_directive()` 明确写着「**不得**为了凑字数扩写到
   1500 字以上」

而 `PublishService` 的规则是：正文不足 **1500 字**就自动取消「头条首发」声明。
四条指令合起来等于：**系统在设计上就放弃了首发激励**。

而模型其实写得够：实测同一个免费模型（glm-4-flash-250414）在 JSON 模式下

    要求 1600 字 → 实际 1665 字
    要求 2000 字 → 实际 1848 字
    要求 2400 字 → 实际 1862 字

`finish_reason=stop`，无截断。之前只写 1000 出头是"没要求"，不是"写不出"。

## 这里锁住的三条不变式

1. 生成目标必须 ≥ 首发资格线（1500），且留有余量；
2. 生成下限（提示词里的"最低字数"）必须 ≥ 首发资格线；
3. **发布硬下限必须远低于首发资格线** —— 首发是"多赚"，发布是"有没有"。
   把发布门槛抬到 1500 会让模型状态不好时整天发不出去，
   那是把"少赚"升级成"颗粒无收"（这个坑在 4.21/4.22 反复出现过）。
"""

from __future__ import annotations

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.agents.review_agent import ReviewAgent
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService

#: 头条首发激励的正文资格线，由 PublishService 在执行时判定。
FIRST_RELEASE_LINE = 1500


def test_generation_target_clears_the_first_release_line() -> None:
    ideal = WriterAgent.NARRATIVE_ARTICLE_IDEAL_CHARS
    assert ideal >= FIRST_RELEASE_LINE, (
        f"生成目标 {ideal} 低于首发资格线 {FIRST_RELEASE_LINE}，首发永远开不起来"
    )
    # 留余量：模型实际产出会略低于要求（实测低 5%~10%）
    assert ideal >= FIRST_RELEASE_LINE + 100, "生成目标没有留出模型欠产的余量"


def test_prompt_floor_clears_the_first_release_line() -> None:
    floor = WriterAgent.NARRATIVE_ARTICLE_PROMPT_FLOOR
    assert floor >= FIRST_RELEASE_LINE, (
        f"提示词里的最低字数 {floor} 低于首发资格线，模型按最低写就拿不到首发"
    )
    assert floor < WriterAgent.NARRATIVE_ARTICLE_IDEAL_CHARS, (
        "最低字数不应高于目标字数"
    )


def test_publish_floor_stays_below_the_first_release_line() -> None:
    """发布硬下限必须显著低于首发线 —— 否则模型欠产时当天直接发不出去。"""
    publish_floor = WriterAgent.NARRATIVE_ARTICLE_MIN_CHARS
    assert publish_floor < FIRST_RELEASE_LINE, (
        "发布硬下限被抬到首发线以上了：这会把『拿不到首发』变成『发不出去』"
    )
    # 三处必须一致（历史上这里是 750/700/1750 三种值并存）
    assert (
        publish_floor
        == AgentWorkflowService.NARRATIVE_ARTICLE_MIN_CHARS
        == ContentFactoryAppService.NARRATIVE_ARTICLE_MIN_CHARS
    ), "发布硬下限在三处不一致"


def test_polish_directive_requires_reaching_the_first_release_line() -> None:
    assert PolishAgent.NARRATIVE_FIRST_RELEASE_LINE == FIRST_RELEASE_LINE
    assert PolishAgent.NARRATIVE_POLISH_TARGET_MIN >= FIRST_RELEASE_LINE, (
        "润色目标下限低于首发线，润色会把稿子停在不达标的长度上"
    )
    assert PolishAgent.NARRATIVE_POLISH_TARGET_MAX > PolishAgent.NARRATIVE_POLISH_TARGET_MIN


def test_review_word_bounds_admit_a_first_release_length_article() -> None:
    """1550~1700 字的合格稿不该因为"篇幅不合格"被扣分。

    修复前叙事稿也走产业稿那条 `min_chars=1750`，于是 1700 字的目标
    必然落进"篇幅不合格"区间。
    """
    assert ReviewAgent.NARRATIVE_WORD_FLOOR <= FIRST_RELEASE_LINE, (
        "评分区间下限高于首发线，达标稿会被判篇幅不合格"
    )
    assert ReviewAgent.NARRATIVE_WORD_CEILING >= WriterAgent.NARRATIVE_ARTICLE_IDEAL_CHARS, (
        "评分区间上限低于写作目标，正常发挥会被判过长"
    )
    target = WriterAgent.NARRATIVE_ARTICLE_IDEAL_CHARS
    assert (
        ReviewAgent.NARRATIVE_WORD_FLOOR
        <= target
        <= ReviewAgent.NARRATIVE_WORD_CEILING
    ), "写作目标落在评分区间之外"


def test_automation_word_target_matches_the_writer_target() -> None:
    """选题层给模型的"建议字数"必须与写作目标同量级。

    修复前这里是 1000，而写作指令说 1100 —— 两条都在首发线以下。
    """
    import inspect

    source = inspect.getsource(
        __import__(
            "toutiao_content_factory.services.automation",
            fromlist=["TopicAutomationService"],
        ).TopicAutomationService._build_word_target
    )
    assert "return max(1700, 800)" in source, (
        "social_story 分支的建议字数没有提到首发线以上"
    )
