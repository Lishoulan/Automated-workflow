"""用户决策（2026-10-21 Round 26）：长图文走 B+A，微头条加量。

决策：
* **B**：给叙事号补"社会故事全文本源" —— 原来的 ``sina_social`` 指向
  ``https://news.sina.com.cn/society/``（JS 外壳页，实测 200 但只有 374 字节、0 个条目），
  来源状态长期 ``ok / raw_count=0``，于是账号**没有任何社会故事素材**。
* **A**：叙事号的产业类选题只做微头条短评，不再写"用故事笔法写产业分析"的长图文
  —— 实测素材里没有人物场景，审核团（故事尺子）要求人物视角，两轮返工也不收敛。
* **加量**：微头条日目标 2 → 3（API 免费，成本不是约束）。

本文件锁住这三条：源定义可用、方向分类生效、日目标已提高。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.app_service import narrative_story_anchor_reason

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def _robot_notes_section() -> dict:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return payload["accounts"]["robot_notes"]


def _topic(title: str, summary: str = "") -> TopicCandidate:
    return TopicCandidate(
        title=title,
        url="https://example.test/topic",
        source="公开信息",
        source_type="rss",
        summary=summary,
        snippet=summary,
        matched_keywords=[],
        rank=1,
        total_score=80,
    )


def test_every_enabled_source_for_robot_notes_is_defined() -> None:
    account = _robot_notes_section()
    collection = account["topic_collection"]
    global_sources = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["topic_collection"]["sources"]
    account_sources = collection.get("sources", {})
    merged = {**global_sources, **account_sources}

    for name in collection["enabled_sources"]:
        assert name in merged, f"enabled source has no definition: {name}"
        definition = merged[name]
        assert definition.get("type"), f"source {name} has no type"
        assert definition.get("urls"), f"source {name} has no urls"


def test_social_sources_use_working_feed_types() -> None:
    account = _robot_notes_section()
    sources = account["topic_collection"]["sources"]

    sina = sources["sina_social"]
    assert sina["type"] == "rss", "JS 外壳页无法解析，必须用 RSS"
    assert any("rss.sina.com.cn" in url for url in sina["urls"])

    chinanews = sources["chinanews_society"]
    assert chinanews["type"] == "rss"
    assert any("chinanews.com.cn" in url for url in chinanews["urls"])
    assert "chinanews_society" in account["topic_collection"]["enabled_sources"]


def test_weitoutiao_daily_target_is_raised() -> None:
    account = _robot_notes_section()
    assert int(account["publishing"]["weitoutiao_daily_target"]) >= 3


def test_narrative_account_routes_industry_topic_away_from_article() -> None:
    industry = _topic(
        "科大讯飞的AI预期，兑现了多少？",
        "财报显示营收增长，市场关注其AI算力与芯片布局的兑现节奏。",
    )
    assert narrative_story_anchor_reason(industry, "narrative") == "not_a_story_topic"


def test_narrative_account_keeps_story_topic_for_article() -> None:
    story = _topic(
        "业主拒交停车费把物业告上法庭",
        "法院判决物业公开停车费收支账本，业主与物业的纠纷有了结果。",
    )
    assert narrative_story_anchor_reason(story, "narrative") == ""


def test_industry_account_is_not_restricted() -> None:
    industry = _topic(
        "科大讯飞的AI预期，兑现了多少？",
        "财报显示营收增长，市场关注其AI算力与芯片布局的兑现节奏。",
    )
    assert narrative_story_anchor_reason(industry, "industry") == ""
