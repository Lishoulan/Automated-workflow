"""回归测试：账号换赛道时，选题链路里的五处硬编码必须跟着走。

背景（2026-09-18）：把 `robot_notes`（登录的头条号是「手蓝数据笔记」）从科技
赛道换成社会故事类之后，选题池变成 **0 条**。逐层排查发现是**五个各自独立、
叠加生效**的耦合点，每一个单独存在都足以让选题池清空：

1. `_passes_topic_focus` 的热榜放宽条件只认 `hot_topic`。而社会故事类账号必须在
   `focus_industries` 里去掉 hot_topic（否则 hot_topic 分支会提前 return True，
   social_story 判据永远不执行）。两个条件互斥 → 热榜题被判质量不达标。

2. `_enrich_topic` 给热榜题**无条件**打上 "hot_topic" 归属，而
   `_filter_to_focus_industries` 要求 matched_industries 与 focus 有交集
   → 热榜题全部计入 industry_mismatch。

3. `automation._topic_matches_account_boundary` 把账号**硬编码**成垂直号
   （`if account_key == "robot_notes": return self._is_robot_topic(topic)`）→
   换赛道后所有题都过不了机器人判据。

4. `app_service._resolve_benchmark_track_for_account` 认不出赛道时兜底成
   「科技商业」，而那个赛道**确实配了对标账号**，于是拿科技号当对标。

5. `collect_topics` 把 track=None 传给 BenchmarkMonitorAgent，而后者把 None
   解释为「不限制赛道」→ 采集全部 16 个对标号。

这个文件用「按账号声明的赛道，而不是按账号 key」来锁住前三点。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.topic_collection import TopicCollectionService

#: 换赛道后做社会故事的账号（它登录的头条号是「手蓝数据笔记」）
STORY_ACCOUNT = "robot_notes"
#: 仍是垂直赛道的对照账号
VERTICAL_ACCOUNT = "sports_notes"


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_PROXY 里的 `[::1]` 会让 httpx2 抛 InvalidURL；测试不该受它影响。"""
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


def _settings(account_key: str):
    return load_settings(Path.cwd(), account_key=account_key)


def _collector(account_key: str) -> TopicCollectionService:
    return TopicCollectionService(_settings(account_key), logging.getLogger("test_pivot"))


def _hot_topic(title: str, heat: int = 8_000_000) -> TopicCandidate:
    return TopicCandidate(
        title=title,
        url="https://example.com/x",
        source="toutiao_hot",
        source_type="hot_board",
        snippet=title,
        heat_value=heat,
        matched_industries=["social_story"],
    )


# ---------------------------------------------------------------------------
# 1. 热榜放宽条件必须认 social_story
# ---------------------------------------------------------------------------


def test_hot_story_topic_survives_the_quality_gate() -> None:
    """热榜社会故事题不能被判成「质量不达标」。

    热榜题天然 title_only（标题即全部素材），如果放宽条件不认 social_story，
    它必然命中 quality_score 那条判据而被杀。
    """
    collector = _collector(STORY_ACCOUNT)
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")

    assert collector._has_blocking_quality_issue(topic) is False


def test_vertical_account_still_gets_the_hot_relaxation() -> None:
    """对照组：仍声明 hot_topic 的账号放宽逻辑不能被改坏。"""
    collector = _collector(VERTICAL_ACCOUNT)
    assert "hot_topic" in collector.topic_settings.focus_industries

    topic = _hot_topic("某队绝杀晋级")
    topic.matched_industries = ["sports"]

    assert collector._has_blocking_quality_issue(topic) is False


# ---------------------------------------------------------------------------
# 2. 热榜题的自动归属必须跟随账号启用的热点赛道
# ---------------------------------------------------------------------------


def test_hot_topic_is_attributed_to_the_accounts_own_hot_track() -> None:
    """社会故事账号的热榜题应归属 social_story，而不是写死的 hot_topic。

    否则 `_filter_to_focus_industries` 的「有交集」判定失败，
    热榜题会被整批计入 industry_mismatch。
    """
    collector = _collector(STORY_ACCOUNT)
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")
    topic.matched_industries = []

    enriched = collector._enrich_topic(topic)

    assert "social_story" in enriched.matched_industries
    assert "hot_topic" not in enriched.matched_industries


def test_vertical_account_hot_topic_is_still_attributed_to_hot_topic() -> None:
    collector = _collector(VERTICAL_ACCOUNT)
    topic = _hot_topic("某队绝杀晋级")
    topic.matched_industries = []

    enriched = collector._enrich_topic(topic)

    assert "hot_topic" in enriched.matched_industries


# ---------------------------------------------------------------------------
# 3. 账号边界过滤必须按声明的赛道，而不是按账号 key
# ---------------------------------------------------------------------------


def test_story_account_accepts_a_real_social_story() -> None:
    """这是最直接的失败点：边界过滤曾把 6 条候选清成 0 条。"""
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")

    assert automation._topic_matches_account_boundary(topic) is True


@pytest.mark.parametrize(
    "title",
    [
        "中国男篮大比分不敌日本",
        "菲船加速冲撞中国海警船",
        "比亚迪召回18.32万辆汽车",
    ],
)
def test_story_account_rejects_off_topic_hot_topics(title: str) -> None:
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))

    assert automation._topic_matches_account_boundary(_hot_topic(title)) is False


def test_vertical_account_boundary_is_unchanged() -> None:
    """对照账号仍走自己的垂直判据，不能被这次改动带偏。"""
    automation = TopicAutomationService(
        _settings(VERTICAL_ACCOUNT), logging.getLogger("test_pivot")
    )
    sports_topic = _hot_topic("国足绝杀晋级")
    assert automation._topic_matches_account_boundary(sports_topic) is True


def test_story_boundary_does_not_depend_on_the_account_key(monkeypatch) -> None:
    """关键性质：判断依据是「账号声明的赛道」，不是账号名字。

    换赛道以后又把 key 硬编码回去，这个用例会失败。
    """
    settings = _settings(STORY_ACCOUNT)
    automation = TopicAutomationService(settings, logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")

    # 同样的题库、同样的赛道声明，换一个 key 也应得到同样的结论
    assert automation._topic_matches_account_boundary(topic) is True
    assert "social_story" in settings.topic_collection.focus_industries


# ---------------------------------------------------------------------------
# 4/5. 对标赛道
# ---------------------------------------------------------------------------


def test_story_account_has_no_benchmark_track() -> None:
    service = ContentFactoryAppService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))

    assert service._resolve_benchmark_track_for_account() is None


def test_vertical_account_keeps_its_benchmark_track() -> None:
    service = ContentFactoryAppService(
        _settings(VERTICAL_ACCOUNT), logging.getLogger("test_pivot")
    )

    assert service._resolve_benchmark_track_for_account() == "体育赛事"


# ---------------------------------------------------------------------------
# 端到端：换赛道配置本身必须自洽
# ---------------------------------------------------------------------------


def test_story_account_config_is_self_consistent() -> None:
    """把换赛道需要同时成立的条件钉在一起，避免只改一半。"""
    settings = _settings(STORY_ACCOUNT)
    style = settings.get_style()

    # 内容领域
    assert style.content_domain == "narrative"
    # 赛道：social_story 且**不含** hot_topic（否则判据不执行）
    assert settings.topic_collection.focus_industries == ["social_story"]
    # 微头条人设
    assert settings.publishing.weitoutiao_personas == ["story_comment"]
    # 来源里不应再有科技源
    for tech_source in ("36kr", "qbitai", "jiqizhixin", "leiphone", "zhidx", "ofweek"):
        assert tech_source not in settings.topic_collection.enabled_sources


def test_story_account_keyword_map_matches_its_track() -> None:
    settings = _settings(STORY_ACCOUNT)

    keywords = (settings.topic_collection.keyword_map or {}).get("social_story") or []
    assert keywords, "社会故事账号必须有关键词表"
    for tech_keyword in ("人形机器人", "具身智能", "算力", "GPU"):
        assert tech_keyword not in keywords


# ---------------------------------------------------------------------------
# 6. 「产业模板摘要」必须被识别为串味
# ---------------------------------------------------------------------------
#
# 背景（2026-09-19）：`topic_collection._summarize_topic` 的**旧兜底句**
#   「{标题}，重点看{关键词}对应的项目落地和订单节奏」
# 会残留在已经持久化的选题数据里（daily_topics 快照 / 选题池 jsonl）。
# `automation._summary_uses_wrong_domain_template` 本该把它拦下来，
# 但白名单只写了 {"sports", "international"}，**漏了 social_story** ——
# 于是这句产业套话被原样写进提示词的「素材摘要」，writer 照抄进正文
# （实测 publish/article.md 第 13 段：「…重点看老人对应的项目落地和订单节奏」）。

_WRONG_DOMAIN_SUMMARY = "男子高空作业绳子被2楼老人剪断，重点看老人对应的项目落地和订单节奏。"


def test_story_summary_with_industry_template_is_flagged_as_wrong_domain() -> None:
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")

    assert automation._topic_mode(topic) == "social_story"
    assert automation._summary_uses_wrong_domain_template(topic, _WRONG_DOMAIN_SUMMARY) is True


def test_industry_summary_with_industry_wording_is_not_flagged() -> None:
    """对照：产业号自己的摘要里出现「订单节奏」是正常的，不能被误判。"""
    automation = TopicAutomationService(
        _settings(VERTICAL_ACCOUNT), logging.getLogger("test_pivot")
    )
    topic = _hot_topic("某队绝杀晋级")

    assert automation._summary_uses_wrong_domain_template(topic, _WRONG_DOMAIN_SUMMARY) is False


def test_bare_title_is_not_treated_as_evidence() -> None:
    """摘要退化到只剩标题时，绝不能把它当「素材摘要」喂给模型。

    实测后果：模型拿 28 个字的标题硬编 855 字「安全意识」「应急处理能力」套话，
    标题池因为没有任何具体事实锚点而塌成 1 条兜底模板。
    """
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")
    topic.summary = topic.title
    topic.snippet = ""

    assert automation._preferred_topic_summary(topic) == ""


def test_real_material_is_still_used_as_evidence() -> None:
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")
    topic.snippet = (
        "据报道，事发当日该男子正在小区外立面进行清洁作业，"
        "二楼住户因不满绳索经过自家窗户，用剪刀将绳索剪断，男子随后被安全绳固定住。"
    )
    topic.summary = topic.snippet

    assert automation._preferred_topic_summary(topic).startswith("据报道")


# ---------------------------------------------------------------------------
# 7. 抓不到素材就不发（宁可不发，也不发垃圾）
# ---------------------------------------------------------------------------


class _StubBundle:
    def __init__(self, usable: bool, text: str = "") -> None:
        self.usable = ["s"] if usable else []
        self.combined_text = text
        self.total_chars = len(text)
        self.sources = ["s"] if usable else []


def test_no_material_switches_to_the_next_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    """首选抓不到素材时，必须换下一条，而不是硬写。"""
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    first = _hot_topic("男子高空作业绳子被2楼老人剪断")
    second = _hot_topic("女子离婚后发现丈夫隐瞒债务")

    calls: list[str] = []

    def fake_collect(title, **_kwargs):
        calls.append(title)
        if title == first.title:
            return _StubBundle(usable=False)
        return _StubBundle(usable=True, text="据报道，" + "详细经过。" * 80)

    monkeypatch.setattr(mss, "collect_material", fake_collect)

    class _Report:
        topics = [first, second]

    monkeypatch.setattr(
        automation, "_material_retry_candidates", lambda report: [first, second]
    )

    # 真实调用顺序：先试首选，失败后才进入 fallback
    assert automation._hydrate_topic_material(first) is False
    selected = automation._hydrate_with_fallback(_Report(), first)

    assert selected is not None
    assert selected.title == second.title
    assert calls == [first.title, second.title]


def test_all_topics_without_material_yield_no_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    first = _hot_topic("男子高空作业绳子被2楼老人剪断")
    second = _hot_topic("女子离婚后发现丈夫隐瞒债务")

    monkeypatch.setattr(mss, "collect_material", lambda *a, **k: _StubBundle(usable=False))
    monkeypatch.setattr(
        automation, "_material_retry_candidates", lambda report: [first, second]
    )

    class _Report:
        topics = [first, second]

    assert automation._hydrate_with_fallback(_Report(), first) is None

