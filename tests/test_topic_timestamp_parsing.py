"""选题时间戳解析：ISO-8601 偏移量必须被原样保留。

## 这个 bug 长什么样

热榜采集器写的时间戳是 `datetime.now(timezone.utc).isoformat()`，形如
`2026-09-20T17:21:55+00:00`。而 `_parse_datetime` 当时只认 RFC 2822
（`parsedate_to_datetime` 对上面这个串**直接抛 ValueError**），
于是它一路掉到最底下的"仅日期"分支：

    value[:10] → "2026-09-20" → strptime("%Y-%m-%d") → 填上**当前时钟**

实测（2026-09-21 01:22 本地，max_topic_age_hours=24）：

    真实时刻  2026-09-20 17:22 UTC  =  2026-09-21 01:22 +08:00   （刚刚发布）
    解析结果  2026-09-20 01:22 +08:00  =  2026-09-19 17:22 UTC
    → 判定年龄 24 小时零几分钟 → **stale，丢掉**

后果（2026-09-21 实测）：56 条热榜题里 48 条被判 stale，剩 8 条又都是
体育/时政/娱乐，被赛道判据清空 → `daily_topics.json` 里 0 条选题 →
永远走长青兜底 → 长青题撞 7 天去重 → **当天一条都发不出去**。

表面上像"云端网络抓不到热点"，实际是这一个时区解析错误。

## 测试策略

不测实现细节，测**语义**：带偏移量的时间戳必须落回它自己那个绝对时刻。
这样以后无论谁重写 `_parse_datetime`，只要语义错了就会红。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.topic_collection import TopicCollectionService

ACCOUNT = "robot_notes"
LOCAL_TZ = datetime.now().astimezone().tzinfo


@pytest.fixture()
def collector() -> TopicCollectionService:
    settings = load_settings(Path.cwd(), account_key=ACCOUNT)
    return TopicCollectionService(settings, logging.getLogger("test_parse_datetime"))


def test_iso_timestamp_with_utc_offset_keeps_its_absolute_moment(
    collector: TopicCollectionService,
) -> None:
    """**核心回归**：+00:00 是绝对时刻，不能被换成"当前时钟"。"""
    parsed = collector._parse_datetime("2026-09-20T17:21:55+00:00")

    assert parsed is not None
    assert parsed.tzinfo is not None, "带偏移量的时间戳解析后必须仍带时区"
    assert parsed.astimezone(timezone.utc) == datetime(
        2026, 9, 20, 17, 21, 55, tzinfo=timezone.utc
    )


def test_iso_timestamp_with_z_suffix_is_accepted(collector: TopicCollectionService) -> None:
    """`Z` 是 `+00:00` 的合法写法，同样必须保留绝对时刻。"""
    parsed = collector._parse_datetime("2026-09-20T17:21:55Z")

    assert parsed is not None
    assert parsed.astimezone(timezone.utc) == datetime(
        2026, 9, 20, 17, 21, 55, tzinfo=timezone.utc
    )


def test_iso_timestamp_with_non_utc_offset(collector: TopicCollectionService) -> None:
    parsed = collector._parse_datetime("2026-09-20T17:21:55+08:00")

    assert parsed is not None
    assert parsed.astimezone(timezone.utc) == datetime(
        2026, 9, 20, 9, 21, 55, tzinfo=timezone.utc
    )


def test_iso_timestamp_without_offset_is_treated_as_local(
    collector: TopicCollectionService,
) -> None:
    """无偏移量的 ISO 串按本地时间理解，且**必须带时区**。

    若返回 naive datetime，下游 `_filter_by_freshness` 的
    `published.astimezone(timezone.utc)` 会抛 TypeError（naive 与 aware 不可比），
    或者被静默按本地时区解释 —— 两条路都不可接受。
    """
    parsed = collector._parse_datetime("2026-09-20T17:21:55")

    assert parsed is not None
    assert parsed.tzinfo is not None, "无偏移量的时间戳也必须补上时区"
    assert parsed.replace(tzinfo=None) == datetime(2026, 9, 20, 17, 21, 55)
    # 能安全参与 aware 比较，不再抛 TypeError
    assert (parsed - datetime.now(timezone.utc)).total_seconds() != 0


def test_freshly_published_hot_topic_is_not_stale(
    collector: TopicCollectionService,
) -> None:
    """刚采集到的热榜题必须能过新鲜度过滤 —— 这正是当天翻车的那一步。

    用"此刻的 UTC ISO 串"模拟采集器刚写下的时间戳，也就是生产里真实的值。
    """
    just_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    topic = TopicCandidate(
        title="刚刚发布的热榜题",
        url="https://example.test/x",
        source="toutiao_hot",
        source_type="hot_board",
        published_at=just_now,
        snippet="",
    )
    filtered_out = {"stale": 0, "missing_timestamp": 0}

    kept = collector._filter_by_freshness([topic], filtered_out)

    assert len(kept) == 1, f"刚发布的热榜题被判过期了：{filtered_out}"
    assert filtered_out["stale"] == 0


def test_topic_older_than_the_window_is_still_dropped(
    collector: TopicCollectionService,
) -> None:
    """反向保护：修复不能把新鲜度过滤整个放空。"""
    too_old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")
    topic = TopicCandidate(
        title="两天前的旧题",
        url="https://example.test/old",
        source="toutiao_hot",
        source_type="hot_board",
        published_at=too_old,
        snippet="",
    )
    filtered_out = {"stale": 0, "missing_timestamp": 0}

    kept = collector._filter_by_freshness([topic], filtered_out)

    assert kept == []
    assert filtered_out["stale"] == 1


def test_date_only_timestamp_is_stable_regardless_of_the_hour(
    collector: TopicCollectionService,
) -> None:
    """仅日期格式的判定不能随"几点跑"漂移。

    旧实现把时刻填成当前时钟，于是同一篇文章 03:00 跑会被丢掉、23:00 跑会被保留。
    按当天 00:00 折算后："今天"恒在窗口内，"昨天"恒在窗口外。
    """
    today = datetime.now(LOCAL_TZ).date()
    yesterday = today - timedelta(days=1)

    parsed_today = collector._parse_datetime(today.isoformat())
    parsed_yesterday = collector._parse_datetime(yesterday.isoformat())

    assert parsed_today is not None and parsed_yesterday is not None
    assert parsed_today.hour == 0 and parsed_today.minute == 0
    assert parsed_yesterday.hour == 0 and parsed_yesterday.minute == 0
    # 今天的条目年龄必小于 24 小时（窗口内），昨天的必大于等于 24 小时（窗口外）
    now = datetime.now(LOCAL_TZ)
    assert (now - parsed_today) < timedelta(hours=24)
    assert (now - parsed_yesterday) >= timedelta(hours=24)


def test_relative_phrases_still_work(collector: TopicCollectionService) -> None:
    """修复不能破坏原有的相对时间解析。"""
    now_local = datetime.now(LOCAL_TZ)

    just_now = collector._parse_datetime("刚刚")
    three_hours = collector._parse_datetime("3小时前")

    assert just_now is not None and abs((now_local - just_now).total_seconds()) < 5
    assert three_hours is not None
    assert abs((now_local - three_hours) - timedelta(hours=3)).total_seconds() < 5


@pytest.mark.parametrize("label", ["最新", "最新动态"])
def test_section_labels_cannot_make_undated_articles_fresh(collector, label):
    collector.topic_settings = collector.topic_settings.model_copy(update={
        "drop_topics_without_timestamp": True,
    })
    topic = TopicCandidate(
        title="Old article under a latest section",
        url="https://example.test/news/2024-11/old",
        source="industry_news", source_type="html",
        published_at=label, snippet="An archived article from 2024",
    )
    filtered_out = {"stale": 0, "missing_timestamp": 0}

    assert collector._parse_datetime(label) is None
    assert collector._filter_by_freshness([topic], filtered_out) == []
    assert filtered_out["missing_timestamp"] == 1
