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
        url=f"evergreen://robot-notes/{abs(hash(title)) % 10**8}",
        source="evergreen_pool",
        source_type="evergreen",
        summary=str(payload.get("summary", "")),
        snippet="",
        matched_industries=list(payload.get("matched_industries", [])),
        matched_keywords=list(payload.get("matched_keywords", [])),
        topic_type="evergreen",
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


def test_evergreen_topics_are_marked_as_evergreen() -> None:
    for payload in _payloads():
        assert payload.get("topic_type") == "evergreen"
        assert str(payload.get("summary", "")).strip(), "长青题必须有摘要，否则抓素材没抓手"
