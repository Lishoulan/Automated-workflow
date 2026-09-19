"""长青兜底池必须属于账号**当前**的赛道，不能停留在转型前的赛道。

背景（2026-09-19 实测）：`robot_notes` 账号从「机器人」转型成「人间故事」时，
`TopicPoolService.DEFAULT_EVERGREEN_TOPICS["robot_notes"]` 里的 6 条**漏改了**，
仍然是「人形机器人从展示走向交付，要先跨过哪三道门槛」这类科技选题。

后果不是"少发一篇"，而是**当天一篇都发不出去**：
热榜筛不出社会故事题时，流水线会启用长青兜底 →
兜底给出的机器人题又被新的赛道边界（`_topic_matches_account_boundary`）全部滤掉 →
选题池 0 条 → 云端判 `no_topics` 并跳过发布。

实测证据：2026-09-19 15:16 的云端运行日志
`Automatic publish result accepted: status=no_topics`，
且同一步日志里兜底题正是「人形机器人从展示走向交付，要先跨过哪三道门槛」。

本文件锁住两件事：
1. 长青池里每一条都必须能通过**账号边界**与**赛道信号**两道判据；
2. 池子里不许再出现转型前的领域词。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.topic_collection import TopicCollectionService
from toutiao_content_factory.services.topic_pool_service import TopicPoolService

STORY_ACCOUNT = "robot_notes"

#: 转型前的领域词 —— 出现在「人间故事」号的长青池里就是漏改
STALE_TRACK_TOKENS = (
    "人形机器人",
    "具身智能",
    "机器人",
    "出货量",
    "量产",
    "交付",
    "回款",
    "产业链",
    "毛利率",
    "算力",
)


def _payloads() -> list[dict]:
    return list(TopicPoolService.DEFAULT_EVERGREEN_TOPICS.get(STORY_ACCOUNT, []))


def _as_topic(payload: dict) -> TopicCandidate:
    title = str(payload["title"])
    return TopicCandidate(
        title=title,
        url=str(payload.get("url") or f"evergreen://robot-notes/{abs(hash(title)) % 10**8}"),
        source=str(payload.get("source", "evergreen_pool")),
        source_type=str(payload.get("source_type", "evergreen")),
        summary=str(payload.get("summary", "")),
        snippet="",
        matched_industries=list(payload.get("matched_industries", [])),
        matched_keywords=list(payload.get("matched_keywords", [])),
        topic_type=str(payload.get("topic_type", "")),
        recommended_format=str(payload.get("recommended_format", "")),
    )


@pytest.fixture()
def automation() -> TopicAutomationService:
    settings = load_settings(Path.cwd(), account_key=STORY_ACCOUNT)
    return TopicAutomationService(settings, logging.getLogger("test_evergreen"))


def test_evergreen_pool_is_not_empty() -> None:
    assert _payloads(), "社会故事号必须有自己的长青兜底池"


@pytest.mark.parametrize("payload", _payloads(), ids=lambda p: str(p["title"])[:24])
def test_every_evergreen_topic_passes_the_account_boundary(
    automation: TopicAutomationService, payload: dict
) -> None:
    """边界过滤是硬门：过不了的题等于没有题。"""
    assert automation._topic_matches_account_boundary(_as_topic(payload)) is True


@pytest.mark.parametrize("payload", _payloads(), ids=lambda p: str(p["title"])[:24])
def test_every_evergreen_topic_has_a_social_story_signal(payload: dict) -> None:
    assert TopicCollectionService._has_social_story_signal(_as_topic(payload)) is True


@pytest.mark.parametrize("payload", _payloads(), ids=lambda p: str(p["title"])[:24])
def test_every_evergreen_topic_uses_the_story_track(payload: dict) -> None:
    assert payload.get("matched_industries") == ["social_story"]


def test_evergreen_pool_has_no_stale_robot_track_wording() -> None:
    titles = " ".join(str(p.get("title", "")) for p in _payloads())

    hits = [token for token in STALE_TRACK_TOKENS if token in titles]
    assert hits == [], f"长青池里仍有转型前的领域词: {hits}"


#: 事件式标题的特征：谁遇到了什么事（有具体动作/结果，而不是设问或方法论）。
EVENT_ANCHORS = (
    "起诉",
    "起诉要回",
    "判了",
    "索赔",
    "被拒",
    "破门",
    "报警",
    "被赶出",
    "争房产",
    "摔伤",
    "摔倒",
    "过户",
    "退款",
    "起争执",
    "争吵",
)

#: 方法论/设问式标题的特征 —— 这类标题在搜索引擎上拿不到结果，连摘要都没有。
METHOD_STYLE_MARKERS = (
    "该怎么做",
    "怎么做",
    "哪几件事",
    "哪几步",
    "为什么",
    "如何",
    "值得吗",
    "算不算",
)


def test_evergreen_titles_are_event_shaped() -> None:
    """长青题标题必须写成"谁遇到了什么事"的事件式。

    ⚠️ 2026-09-19 实测：方法论/设问式标题（「…想要回来得先做哪几件事」）
    在 Bing 上**一条结果都拿不到** —— 连搜索结果摘要都拿不到，等于没有素材可写；
    事件式标题至少能拿到 1~8 条摘要，拼出 550~900 字的可用素材。
    """
    for payload in _payloads():
        title = str(payload.get("title", ""))
        assert any(anchor in title for anchor in EVENT_ANCHORS), (
            f"长青题标题不是事件式（没有具体动作/结果）: {title}"
        )


def test_evergreen_titles_avoid_method_style_phrasing() -> None:
    for payload in _payloads():
        title = str(payload.get("title", ""))
        hits = [marker for marker in METHOD_STYLE_MARKERS if marker in title]
        assert hits == [], f"长青题标题仍是设问/方法论式 {hits}: {title}"


def test_evergreen_titles_fit_the_publish_length_limit() -> None:
    for payload in _payloads():
        title = str(payload.get("title", ""))
        assert 10 <= len(title) <= 30, f"标题长度 {len(title)} 不在 10~30: {title}"


def test_evergreen_topics_are_marked_as_evergreen() -> None:
    for payload in _payloads():
        assert payload.get("topic_type") == "evergreen"
        assert str(payload.get("summary", "")).strip(), "长青题必须有摘要，否则抓素材没抓手"


# ---------------------------------------------------------------------------
# 质量门必须认长青题（否则选题被选中了、又在这里被拦掉）
# ---------------------------------------------------------------------------
#
# 实测（云端 run 35445305454）：日志里先出现
#   「[素材] 长青题不要求抓到报道正文（非新闻事件）」   ← 采集侧已豁免
# 紧接着又出现
#   「Skipping low-quality topic: … | reasons=insufficient_source_facts」
# 4 个候选全部被跳过 → `Daily workflow did not produce any runnable candidates`
# → 云端当天一篇都发不出去。
#
# 根因：`hot_topic_evidence_exempt` 的生效条件是 `is_hot_topic or is_title_only`，
# 而长青题两条都不满足 —— 两个模块对同一件事给了相反的口径。


def test_evergreen_fallback_flag_counts_as_title_only() -> None:
    """`_abort_if_low_quality` 必须把 evergreen_fallback 当成"本来就没有正文素材"。"""
    import inspect

    from toutiao_content_factory.services import agent_workflow as module

    source = inspect.getsource(module.AgentWorkflowService._abort_if_low_quality)

    assert "evergreen_fallback" in source, (
        "长青题没被算进豁免范围；采集侧放行、质量门拦截，两处口径不一致"
    )
    assert "is_title_only = is_title_only or is_evergreen" in source


def test_automation_knows_which_topics_are_evergreen() -> None:
    """采集侧的判据必须能命中 pool 里的每一条，且不误判热榜题。"""
    from toutiao_content_factory.services.automation import TopicAutomationService

    for payload in _payloads():
        assert TopicAutomationService._is_evergreen_topic(_as_topic(payload)) is True

    hot = _as_topic(
        {
            "title": "男子高空作业绳子被2楼老人剪断",
            "matched_industries": ["social_story"],
            "matched_keywords": ["老人"],
            "source": "toutiao_hot",
            "source_type": "hot_board",
            "topic_type": "project_progress",
        }
    )
    assert TopicAutomationService._is_evergreen_topic(hot) is False
