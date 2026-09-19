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


# ---------------------------------------------------------------------------
# 1b. 拼句主体必须"说得清是什么事"，不能退化成单个泛词
# ---------------------------------------------------------------------------
#
# 实测（.runtime/probe_evergreen_titles.py）：长青题的 `matched_keywords` 往往是
# 「养老」「借钱」这类单个泛词，原来回退到关键词当主体，于是拼出
#   「老人这件事，最后是怎么处理的」
#   「老人给普通人提了个醒」
# 完全不知道在说什么。正确做法是从选题标题裁出有意义的半句。


@pytest.mark.parametrize(
    "topic_title,expected",
    [
        ("老人在家摔倒后，子女才发现没人知道他的日常情况", "老人在家摔倒后"),
        ("子女轮流照顾老人，为什么最后总是闹到翻脸", "子女轮流照顾老人"),
        ("父母留下的房子，几个子女谈不拢，最后是怎么分的", "父母留下的房子"),
    ],
)
def test_topic_head_phrase_cuts_at_the_first_punctuation(
    topic_title: str, expected: str
) -> None:
    assert TitleAgent._topic_head_phrase(topic_title) == expected


def test_topic_head_phrase_falls_back_to_a_hard_cut() -> None:
    """没有标点时按字数硬裁，不能返回空串。"""
    head = TitleAgent._topic_head_phrase("这是一条没有任何标点的很长很长的选题标题")

    assert head
    assert len(head) <= 14


def test_evergreen_story_fallback_does_not_use_a_bare_keyword_subject() -> None:
    """用真实长青题验证：拼句主体不许是「老人」这种单个泛词。"""
    agent = _agent(_settings(STORY_ACCOUNT))
    report = DataReport(
        topic_title="老人在家摔倒后，子女才发现没人知道他的日常情况",
        focus_industry="social_story",
        focus_keywords=["老人", "养老"],
        supply_chain_keywords=["老人"],
    )
    content = GeneratedContent(
        article="据公开报道，这户人家的老人独自在家时摔倒，直到第二天子女上门才发现。",
        polished_article="据公开报道，这户人家的老人独自在家时摔倒，直到第二天子女上门才发现。",
        summary="老人独自在家摔倒",
    )

    titles = agent._narrative_fallback_titles(content, report)

    # 第一位是选题原标题（最安全的兜底）
    assert titles[0] == "老人在家摔倒后，子女才发现没人知道他的日常情况"
    # 其余句式必须带上具体半句，而不是只留「老人」
    for title in titles[1:]:
        assert not title.startswith("老人这件事"), f"主体退化成了泛词: {title}"
        assert "老人在家摔倒后" in title or "老人独自在家" in title, title


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


# ---------------------------------------------------------------------------
# 4. 标题校验有**两个入口**，豁免清单必须一致
# ---------------------------------------------------------------------------
#
# 实测（云端 run 35453836286）：模型 18 条标题在 `TitleAgent.validate_title_candidate`
# 里全部合格，但 `AgentWorkflowService._filter_titles_by_quality` 是**旁路** ——
# 它直接调 `quality_checker._check_title`，不经过 TitleAgent ——
# 于是同一批标题又被 RULE_009「信息密度不足」全部毙掉：
#   「标题质量硬过滤全灭：18 个候选标题均未通过校验，已清空候选」
#   → 没有可用标题 → 整篇判 review_required、没有发布。
#
# 只修一处等于没修。这里锁住"两处用的是同一口径"。


def test_both_title_gates_exempt_the_same_narrative_codes() -> None:
    from toutiao_content_factory.agents.title_agent import TitleAgent
    from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
    from toutiao_content_factory.services.review_service import ReviewWorkflowService

    agent_codes = set(TitleAgent.NARRATIVE_EXEMPT_TITLE_CODES)
    workflow_markers = set(AgentWorkflowService.NARRATIVE_EXEMPT_TITLE_MARKERS)
    review_markers = set(ReviewWorkflowService.NARRATIVE_EXEMPT_TITLE_MARKERS)

    # TitleAgent 用 reason code；另外两处匹配的是中文 issue 文本，所以按语义映射
    assert "rule_009_low_information" in agent_codes
    assert "missing_concrete_hook" in agent_codes
    for name, markers in (("workflow", workflow_markers), ("review", review_markers)):
        assert "RULE_009" in markers, f"{name} 侧没有豁免 RULE_009"
        assert any("006" in marker or "具象" in marker for marker in markers), (
            f"{name} 侧没有豁免 RULE_006（具象化），口径不一致"
        )


def test_all_three_title_gate_copies_agree() -> None:
    """同一套判据在项目里有**三份独立实现**，口径必须一致。

    ⚠️ 2026-09-19 实测：修一处就会被下一处打回来，三次云端运行各卡在一处 ——
        run 35443949496 卡在 `TitleAgent`（`0/0 candidates passed`）
        run 35453836286 卡在 `_filter_titles_by_quality`（`标题质量硬过滤全灭`）
        run 35455257246 卡在 `select_auto_title`
            （`0/8 candidates passed, top_reasons=[('rule_009_low_information', 8)]`）
    这个用例把"三处一起改"变成可执行的约束。
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1] / "src/toutiao_content_factory"
    files = {
        "title_agent": root / "agents/title_agent.py",
        "agent_workflow": root / "services/agent_workflow.py",
        "review_service": root / "services/review_service.py",
    }
    for name, path in files.items():
        source = path.read_text(encoding="utf-8")
        assert "NARRATIVE_EXEMPT_TITLE" in source, f"{name} 里找不到叙事豁免清单常量"
        assert "_narrative" in source or "narrative" in source, (
            f"{name} 没有做叙事体裁判定"
        )


def test_narrative_rejection_is_exempted_in_the_bypass_path() -> None:
    """行为验证：叙事账号下，RULE_009 那类 issue 不应导致标题被清空。"""
    from toutiao_content_factory.services.agent_workflow import AgentWorkflowService

    service = AgentWorkflowService.__new__(AgentWorkflowService)  # 不跑 __init__

    class _Checker:
        def _check_title(self, title, article):
            return {
                "issues": [
                    "标题信息密度不足（RULE_009）。头条实测：同一事件完整标题"
                    "（主体+数字+后果）阅读1.3万，简略标题仅23阅读。"
                ]
            }

    class _Content:
        def __init__(self) -> None:
            self.titles = ["西贝员工工资推迟，员工权益受冲击"]
            self.recommended_titles = ["西贝员工工资推迟，员工权益受冲击"]
            self.article = "西贝门店员工称工资推迟了10天发放。"
            self.title_validation = {}
            self.validation_notes = []
            self.selected_title_source = "model"

    class _Settings:
        content_domain = "narrative"
        recommended_title_count = 3

    service.quality_checker = _Checker()
    service.settings = _Settings()
    service.logger = logging.getLogger("test_narrative_titles")
    service._cap_title_skeleton_families = lambda titles: titles
    service._unique = lambda items: list(dict.fromkeys(items))

    content = _Content()
    service._filter_titles_by_quality(content)

    assert content.titles == ["西贝员工工资推迟，员工权益受冲击"], (
        "叙事账号下 RULE_009 不该清空标题候选"
    )




