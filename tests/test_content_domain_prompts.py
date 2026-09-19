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
    assert _settings("sports_notes").get_style().content_domain == "industry"
    assert _settings("robot_notes").get_style().content_domain == "narrative"


# ---------------------------------------------------------------------------
# 1b. 字数指令必须与上游一致（润色不许要求叙事稿达到 1800 字）
# ---------------------------------------------------------------------------
#
# 背景（2026-09-19 实测）：`PolishAgent._auto_publish_directive` **无条件**要求
# 「润色后仍需保持约1800字，最低1750字」，而同一篇叙事稿的上游指令是
# 「目标约1100字，最低950字」。两条互相矛盾的硬指标同时压给模型，
# 模型两边都满足不了就交残稿：润色把 843 字砍成 187 / 413 / 166 字，
# 触发「Polish was too aggressive」一次次空跑。


def _polish_agent(account_key: str):
    from toutiao_content_factory.agents.polish_agent import PolishAgent

    settings = _settings(account_key)

    class _Publishing:
        enabled = True
        auto_publish = True
        draft_only = False

    class _AutoPublishSettings:
        """只把自动发布开关打开；其余字段全部转发给真实 settings。

        真实 settings 是 frozen dataclass，不能就地改。
        """

        publishing = _Publishing()

        def __getattr__(self, name: str):
            return getattr(settings, name)

    return PolishAgent(
        _AutoPublishSettings(),
        PromptRepository(Path.cwd() / "prompts"),
        type("_Stub", (), {"model": "stub"})(),
        logging.getLogger("test_domain"),
    )


def test_narrative_polish_directive_does_not_demand_1800_chars() -> None:
    agent = _polish_agent("robot_notes")

    directive = agent._auto_publish_directive()

    assert directive, "自动发布模式下必须有润色指令"
    assert "1800" not in directive
    assert "1750" not in directive
    # 必须明确禁止把正文压短
    assert "80%" in directive


def test_industry_polish_directive_keeps_its_own_target() -> None:
    """对照：产业号仍然要求 1800 字。"""
    agent = _polish_agent("default")

    directive = agent._auto_publish_directive()

    assert "1800" in directive


# ---------------------------------------------------------------------------
# 1c. 自动发布的正文下限必须按领域分开
# ---------------------------------------------------------------------------
#
# 背景（2026-09-19 实测）：自动发布模式原来**无条件**要求 1750 字
# （`AgentWorkflowService.AUTO_PUBLISH_ARTICLE_MIN_CHARS`），
# `AppService._minimum_article_chars` 对叙事号定 950。
# 而当前免费模型 glm-4-flash 在人情故事题材上的实际产出是 556~1114 字，
# 后果不是"写出更长的稿"，而是**四次尝试全部判 review_required、一篇都发不出去**。
# 现在叙事号统一 750，并且这个数字在两处必须一致。


def test_narrative_article_floor_is_achievable() -> None:
    from toutiao_content_factory.services.agent_workflow import AgentWorkflowService

    narrative = AgentWorkflowService.NARRATIVE_ARTICLE_MIN_CHARS
    # 免费模型的实测产出中位数在 600 附近，下限不能高于它太多
    assert 600 <= narrative <= 800, f"叙事下限 {narrative} 与免费模型的实际产出不匹配"
    assert AgentWorkflowService.AUTO_PUBLISH_ARTICLE_MIN_CHARS == 1750, "产业下限不许动"


def test_narrative_floor_matches_between_the_two_places() -> None:
    """两处下限必须一致，否则一边放行一边拦。

    `app_service._minimum_article_chars` 里是字面量（没有导出常量），
    所以这里按源码校验：**两条分支（自动发布 / 人工审核）都必须是 750**。
    ⚠️ 这一条有实际意义：`scripts/verify_content_drypublish.py` 会关掉发布开关，
    跑的是人工审核分支。两条分支不一致时，本地验证的下限（850）与云端生产
    的下限（750）就不是同一个数，实测一篇 838 字的稿子在本地判 review_required、
    而云端会放行 —— 验证结果无法代表云端。
    """
    from toutiao_content_factory.services import app_service as app_module
    from toutiao_content_factory.services.agent_workflow import AgentWorkflowService

    narrative = AgentWorkflowService.NARRATIVE_ARTICLE_MIN_CHARS
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert f"return {narrative}\n" in source, (
        f"agent_workflow 的 {narrative} 与 app_service 里的下限不一致"
    )
    # app_service 里叙事分支出现两次（自动发布 / 人工审核），必须都是 750
    assert source.count(f"return {narrative}\n") >= 2, (
        "app_service 的两条分支（自动发布 / 人工审核）没有都按叙事下限走"
    )
    # 产业分支必须仍保留原值
    assert "return 1750" in source
    assert "return 850 if is_hot_topic else 800" in source


# ---------------------------------------------------------------------------
# 2. 领域专属规则只出现在自己的那一侧
# ---------------------------------------------------------------------------


def test_industry_rules_are_industry_only() -> None:
    industry = WriterAgent._domain_thinking_rules(_settings("sports_notes").get_style())
    narrative = WriterAgent._domain_thinking_rules(_settings("robot_notes").get_style())

    assert "产业链拆解" in industry
    assert "产业链拆解" not in narrative
    assert "数据锚定" in industry
    assert "数据锚定" not in narrative


def test_narrative_rules_are_narrative_only() -> None:
    industry = WriterAgent._domain_thinking_rules(_settings("sports_notes").get_style())
    narrative = WriterAgent._domain_thinking_rules(_settings("robot_notes").get_style())

    assert "处境还原" in narrative
    assert "处境还原" not in industry
    assert "事实锚定" in narrative
    assert "事实锚定" not in industry


def test_execution_rules_do_not_force_three_data_points_on_stories() -> None:
    """这条最关键：社会新闻常常只有一两个数字，硬要求三个会逼模型编数字。"""
    narrative = WriterAgent._domain_execution_rules(_settings("robot_notes").get_style())
    industry = WriterAgent._domain_execution_rules(_settings("sports_notes").get_style())

    assert "必须引用至少 3 个" not in narrative
    assert "必须引用至少 3 个" in industry
    assert "不要凑数字" in narrative


def test_narrative_system_prompt_forbids_inventing_people_and_stories() -> None:
    """违规红线在系统提示词里必须有明文，不能只靠风格字段描述。"""
    style = _settings("robot_notes").get_style()
    text = WriterAgent._domain_writing_rules(style)

    assert "严禁编造人物与故事" in text
    assert "不得写第一人称亲历" in text


# ---------------------------------------------------------------------------
# 3. 安全约束必须在两套领域下都存在（换领域不能换掉红线）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("account_key", ["sports_notes", "robot_notes"])
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
    for account_key in ("sports_notes", "robot_notes"):
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


# ---------------------------------------------------------------------------
# 5. 领域措辞必须覆盖**所有**提示词，不能只有 WriterAgent 享受
# ---------------------------------------------------------------------------
#
# 背景（2026-09-18）：提示词把「你是做什么的」写死成科技号 ——
#   analysis_prompt「你是中国科技产业方向的资深编辑和行业研究员」
#   generation_prompt「你是一名中文科技产业深度作者」
#   title_prompt_v2「你要为 ${platform} 的科技产业分析文章生成标题」
#                 「标题要像头条科技分析号」
#   polish/review/trend/data 同样各有一句。
# 但只有 WriterAgent 显式传了 content_domain，TitleAgent 等全部落回 industry，
# 于是社会故事号的标题依旧被套进「科技行业」框架。

# 这些词在叙事号渲染结果里出现，就说明定位还是科技号。
_NARRATIVE_FORBIDDEN = (
    "科技产业",
    "科技行业",
    "科技分析",
    "产业分析",
    "科技新闻",
)

_PROMPT_TEMPLATES = (
    "analysis_prompt.md",
    "article_prompt.md",
    "data_prompt.md",
    "data_prompt_v2.md",
    "editorial_council_prompt.md",
    "generation_prompt.md",
    "polish_prompt.md",
    "publish_review_prompt.md",
    "review_prompt.md",
    "title_prompt.md",
    "title_prompt_v2.md",
    "trend_prompt.md",
    "trend_prompt_v2.md",
)

# 这些是「示例数据」而不是自我定位，允许出现在示例行里。
_EXAMPLE_ANCHORS = ("宇树科技", "机器人", "产业链")


def _self_positioning_hits(text: str) -> list[str]:
    """只挑出「自我定位」性质的领域词，放过示例行里的领域词。"""
    hits: list[str] = []
    for word in _NARRATIVE_FORBIDDEN:
        for line in text.splitlines():
            if word not in line:
                continue
            if any(anchor in line for anchor in _EXAMPLE_ANCHORS):
                continue
            hits.append(word)
            break
    return hits


@pytest.mark.parametrize("template", _PROMPT_TEMPLATES)
def test_no_tech_self_positioning_in_narrative_domain(repo, template: str) -> None:
    """叙事域下每个提示词都不该再把账号定位成科技号。"""
    rendered = repo.render(
        template,
        content_domain="narrative",
        platform="今日头条",
        brand_name="手蓝人间故事",
        account_name="手蓝人间故事",
    )
    assert _self_positioning_hits(rendered) == [], f"{template} 仍把账号定位成科技号"


@pytest.mark.parametrize("template", _PROMPT_TEMPLATES)
def test_industry_domain_wording_is_untouched(repo, template: str) -> None:
    """产业号的行为不能被叙事化改动误伤。"""
    raw = (Path.cwd() / "prompts" / template).read_text(encoding="utf-8")
    if "科技产业" not in raw and "科技行业" not in raw:
        pytest.skip(f"{template} 本来就不含科技定位措辞")
    rendered = repo.render(
        template, content_domain="industry", platform="今日头条", brand_name="手蓝数据笔记"
    )
    assert "科技产业" in rendered or "科技行业" in rendered


def test_title_prompt_speaks_the_account_domain(repo) -> None:
    """标题提示词第一句决定了模型把自己当什么号，必须跟着账号走。"""
    narrative = repo.render(
        "title_prompt_v2.md", content_domain="narrative", platform="今日头条"
    )
    industry = repo.render(
        "title_prompt_v2.md", content_domain="industry", platform="今日头条"
    )
    assert "人情故事与社会观察文章生成标题" in narrative
    assert "科技产业分析文章生成标题" in industry
    assert "头条人间故事号" in narrative
    assert "头条科技分析号" not in narrative


def test_base_agent_render_prompt_follows_account_domain() -> None:
    """BaseAgent.render_prompt 必须自动带上账号领域，agent 不必各自记得传。"""
    from toutiao_content_factory.agents.base import BaseAgent

    repo = PromptRepository(Path.cwd() / "prompts")

    class _Settings:
        def __init__(self, domain: str) -> None:
            self.content_domain = domain

    class _Agent(BaseAgent):
        def __init__(self, settings) -> None:  # noqa: D107
            self.settings = settings
            self.prompt_repository = repo
            self.logger = logging.getLogger("test")

    narrative = _Agent(_Settings("narrative")).render_prompt("title_prompt_v2.md")
    industry = _Agent(_Settings("industry")).render_prompt("title_prompt_v2.md")

    assert "人情故事与社会观察文章生成标题" in narrative
    assert "科技产业分析文章生成标题" in industry

    # 调用方显式传入时优先，不被 settings 覆盖
    explicit = _Agent(_Settings("narrative")).render_prompt(
        "title_prompt_v2.md", content_domain="industry"
    )
    assert "科技产业分析文章生成标题" in explicit

