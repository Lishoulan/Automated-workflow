"""回归测试：写作提示词必须按内容领域切换，但安全约束必须一致。

背景（2026-09-18）：`WriterAgent._writer_system_prompt` 和
`prompts/article_prompt_v2.md` 里都写死了「中文科技产业文章」「产业链拆解」
「必须引用至少 3 个来自素材的具体数据点」「时间线锚定」这类要求。
`StyleProfile` 只能覆盖语气和节奏，改不了领域，于是社会故事类账号：
  - 被要求把家庭伦理事件「拆到上游材料/中游制造/下游应用」；
  - 被要求在一条只有一两个数字的社会新闻里「引用至少 3 个具体数据点」；
  - 提示词里还塞着机器人赛道的标题当「高表现篇章参考」。

前两条会把模型逼向编造，正好撞在「禁止虚构数据」的红线上；第三条是纯粹的串味。
本文件锁住修复结果，并且**重点锁住「安全约束在两套领域下都在」**——
换领域绝不能顺手把反虚构和改写距离的红线一起换掉。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.config import (
    StyleProfile,
    _default_style_payloads,
    load_settings,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository


@pytest.fixture()
def repo() -> PromptRepository:
    return PromptRepository(Path.cwd() / "prompts")


def _settings(account_key: str):
    return load_settings(Path.cwd(), account_key=account_key)


# ---------------------------------------------------------------------------
# 1. content_domain 字段本身
# ---------------------------------------------------------------------------


def test_existing_styles_default_to_the_industry_domain() -> None:
    """新增字段默认 industry —— 既有 4 个风格行为必须完全不变。"""
    payloads = _default_style_payloads()

    for key in ("deep_industry_analysis", "tech_media", "finance_report", "viral_science"):
        profile = StyleProfile.from_dict(key, payloads[key])
        assert profile.content_domain == "industry"


def test_family_story_declares_the_narrative_domain() -> None:
    profile = StyleProfile.from_dict("family_story", _default_style_payloads()["family_story"])

    assert profile.content_domain == "narrative"


def test_missing_content_domain_falls_back_to_industry() -> None:
    """老配置里没有这个字段时不能炸，也不能变成空字符串。"""
    assert StyleProfile.from_dict("x", {}).content_domain == "industry"
    assert StyleProfile.from_dict("x", {"content_domain": ""}).content_domain == "industry"


def test_the_two_accounts_resolve_to_different_domains() -> None:
    assert _settings("robot_notes").get_style().content_domain == "industry"
    assert _settings("story_notes").get_style().content_domain == "narrative"


# ---------------------------------------------------------------------------
# 2. 领域专属规则只出现在自己的那一侧
# ---------------------------------------------------------------------------


def test_industry_rules_are_industry_only() -> None:
    industry = WriterAgent._domain_thinking_rules(_settings("robot_notes").get_style())
    narrative = WriterAgent._domain_thinking_rules(_settings("story_notes").get_style())

    assert "产业链拆解" in industry
    assert "产业链拆解" not in narrative
    assert "数据锚定" in industry
    assert "数据锚定" not in narrative


def test_narrative_rules_are_narrative_only() -> None:
    industry = WriterAgent._domain_thinking_rules(_settings("robot_notes").get_style())
    narrative = WriterAgent._domain_thinking_rules(_settings("story_notes").get_style())

    assert "处境还原" in narrative
    assert "处境还原" not in industry
    assert "事实锚定" in narrative
    assert "事实锚定" not in industry


def test_execution_rules_do_not_force_three_data_points_on_stories() -> None:
    """这条最关键：社会新闻常常只有一两个数字，硬要求三个会逼模型编数字。"""
    narrative = WriterAgent._domain_execution_rules(_settings("story_notes").get_style())
    industry = WriterAgent._domain_execution_rules(_settings("robot_notes").get_style())

    assert "必须引用至少 3 个" not in narrative
    assert "必须引用至少 3 个" in industry
    assert "不要凑数字" in narrative


def test_narrative_system_prompt_forbids_inventing_people_and_stories() -> None:
    """违规红线在系统提示词里必须有明文，不能只靠风格字段描述。"""
    style = _settings("story_notes").get_style()
    text = WriterAgent._domain_writing_rules(style)

    assert "严禁编造人物与故事" in text
    assert "不得写第一人称亲历" in text


# ---------------------------------------------------------------------------
# 3. 安全约束必须在两套领域下都存在（换领域不能换掉红线）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("account_key", ["robot_notes", "story_notes"])
def test_safety_constraints_survive_in_both_domains(account_key: str, monkeypatch) -> None:
    """换领域绝不能顺手把反虚构与改写距离的红线一起换掉。

    `_writer_system_prompt` 里那两句公共约束（绝不虚构 / 改写距离原创红线）
    必须对两个领域都出现 —— 它们是合规底线，不是领域风格。
    """
    import os

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)

    logger = logging.getLogger("test_content_domain")
    settings = _settings(account_key)
    agent = WriterAgent(
        settings,
        PromptRepository(Path.cwd() / "prompts"),
        _StubOpenAIService(settings),
        logger,
    )
    text = agent._writer_system_prompt(settings.get_style())

    # 两句共用的合规底线
    assert "绝不虚构公司、人物、数字、时间、订单、收入、成本、市场份额、案例、现场经历或来源。" in text
    assert "改写距离（原创红线）" in text
    assert "事实边界 > 观点与因果 > 可读性 > 字数" in text
    # 领域措辞只在自己那侧出现
    is_narrative = settings.get_style().content_domain == "narrative"
    assert ("人情故事" in text) is is_narrative
    assert ("科技产业文章" in text) is not is_narrative


class _StubOpenAIService:
    """构造 WriterAgent 需要一个 openai 服务；本测试不发起任何请求。

    不直接构造真的 OpenAIContentService：它会建 httpx 客户端，
    而本机环境的 NO_PROXY 里带 `[::1]`，httpx2 解析该写法会抛 InvalidURL。
    """

    def __init__(self, settings) -> None:
        self.settings = settings
        self.model = "stub"
        self.available = False
        self.estimated_cost = 0.0


def test_prompt_template_keeps_the_shared_evidence_boundary(repo) -> None:
    """模板里共用的「证据边界」不能被领域参数化掉 —— 它是两套写法共有的底。"""
    common = dict(
        platform="p", brand_name="b", brand_positioning="bp", industry_focus="i",
        audience="a", style_digest="s", brand_digest="bd", tone_directive="t",
        viral_template_instruction="v", kol_voice_instruction="k", cache_note="",
        trend_json="{}", data_json="{}", article_word_target="1800", min_word_target="1750",
        content_format_label="深度分析", format_brief="f", paragraph_instruction="pp",
        evidence_strength="70", topic_quality_score="70", auto_publish_directive="",
    )
    for account_key in ("robot_notes", "story_notes"):
        style = _settings(account_key).get_style()
        text = repo.render(
            "article_prompt_v2.md",
            content_domain=style.content_domain,
            domain_execution_rules=WriterAgent._domain_execution_rules(style),
            domain_thinking_rules=WriterAgent._domain_thinking_rules(style),
            **common,
        )
        assert "证据边界" in text
        assert "禁止补全缺失事实" in text
        assert "改写距离" in text
        # 模板渲染后不能残留未替换变量
        assert "$domain_execution_rules" not in text
        assert "$domain_thinking_rules" not in text


def test_template_no_longer_carries_hardcoded_robot_examples(repo) -> None:
    """写死的「高表现篇章」曾把机器人标题（含兜底模板垃圾标题）喂给所有账号。

    注释里会提到这段历史，所以只检查**去掉 HTML 注释后的模板正文**。
    """
    import re

    raw = (Path.cwd() / "prompts" / "article_prompt_v2.md").read_text(encoding="utf-8")
    body = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)

    # 正文里不能再有那三条写死的示例标题
    assert "机器人热度之外" not in body
    assert "其他智能12.7亿" not in body
    # 正文里不能再有承载它们的那个小节标题
    assert "参考：最近的高表现篇章" not in body


# ---------------------------------------------------------------------------
# 4. PromptRepository 的领域化清理
# ---------------------------------------------------------------------------


def test_prompt_repository_does_not_rewrite_narrative_into_industry(repo) -> None:
    """_normalize_prompt 曾把所有提示词的写法改写成「产业分析写法」。"""
    template = "x.md"
    (Path.cwd() / "prompts" / template).write_text(
        "负责账号“A”的头条内容生产。请按某种的写法来写。", encoding="utf-8"
    )
    try:
        narrative = repo.render(template, content_domain="narrative")
    finally:
        (Path.cwd() / "prompts" / template).unlink(missing_ok=True)

    assert "人情故事写法" in narrative
    assert "产业分析写法" not in narrative
    assert "负责头条内容生产" in narrative
    assert "负责头条科技内容生产" not in narrative


def test_prompt_repository_keeps_the_industry_wording_by_default(repo) -> None:
    template = "y.md"
    (Path.cwd() / "prompts" / template).write_text(
        "请按某种的写法来写。", encoding="utf-8"
    )
    try:
        rendered = repo.render(template)
    finally:
        (Path.cwd() / "prompts" / template).unlink(missing_ok=True)

    assert "产业分析写法" in rendered
