"""回归测试：没有对标账号的赛道不得借用别的赛道的对标。

背景（2026-09-18）：story_notes（社会故事类）的选题池里混进了三条科技热点 ——
「千寻智能联合创始人高阳：人形机器人最快明年就能听懂人话」
「钙钛矿光伏 I-V 测试有了统一标尺」「Anthropic 计划年底前拥有约 5 吉瓦算力」。

链路是这样的，三层兜底叠加在一起才漏出来：

1. `growth_ops_service.infer_title_playbook_track` 认不出社会故事，
   兜底返回「科技商业」；
2. 而「科技商业」在 branding/benchmark_accounts_toutiao.yaml 里**确实配了账号**
   （36氪/虎嗅/智东西/量子位），所以「赛道必须配了账号」这道检查拦不住它；
3. 就算前两层都返回 None，`BenchmarkMonitorAgent` 把 track=None 解释为
   「不限制赛道」，于是采集**全部 16 个**对标号，把科技热点合并进选题池。

所以这里要同时锁住三件事：story_notes 解析出 None、既有账号的赛道不变、
以及 collect_topics 在 track 为空时整段跳过对标采集。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService

BENCHMARK_YAML = Path("branding/benchmark_accounts_toutiao.yaml")


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """本机 NO_PROXY 里的 `[::1]` 会让 httpx2 抛 InvalidURL，测试里不该受它影响。

    注意 NO_PROXY/no_proxy 才是真正的元凶：httpx2 解析 no-proxy 列表时
    会把 `[::1]` 当成带端口的 URL，`normalize_port` 于是拿到 `:1]` 并抛错。
    只清 http(s)_proxy 是不够的。
    """
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


def _service(account_key: str, *, strict_story=False) -> ContentFactoryAppService:
    settings = load_settings(Path.cwd(), account_key=account_key)
    if strict_story:
        settings.revenue_optimization = replace(settings.revenue_optimization, adaptive_topic_selection=False)
        settings.topic_collection = settings.topic_collection.model_copy(update={"focus_industries": ["social_story"]})
    return ContentFactoryAppService(settings, logging.getLogger("test_benchmark_track"))


# ---------------------------------------------------------------------------
# 1. 赛道解析
# ---------------------------------------------------------------------------


def test_social_story_account_gets_no_benchmark_track() -> None:
    """社会故事类没有对标账号，必须解析为 None（而不是借用科技商业）。"""
    assert _service("robot_notes", strict_story=True)._resolve_benchmark_track_for_account() is None


@pytest.mark.parametrize(
    ("account_key", "expected_track"),
    [
        ("sports_notes", "体育赛事"),
        ("sports_notes", "体育赛事"),
        ("politics_notes", "国际热点"),
    ],
)
def test_existing_accounts_keep_their_tracks(account_key: str, expected_track: str) -> None:
    """既有账号的赛道不能被这次改动带偏 —— 这是底线。"""
    assert _service(account_key)._resolve_benchmark_track_for_account() == expected_track


def test_default_account_has_no_benchmark_track() -> None:
    assert _service("default")._resolve_benchmark_track_for_account() is None


def test_sports_account_is_not_misclassified_as_international() -> None:
    """顺带修掉的一个既有错判：sports_notes 曾被归到「国际热点」。

    原因是旧代码用 focus_industries 整体做 issubset，而它含 hot_topic，
    于是所有赛道分支都不命中，最后落到关键词推断 —— 而「国际大赛」里的
    「国际」先命中了国际词表。
    """
    assert _service("sports_notes")._resolve_benchmark_track_for_account() == "体育赛事"


# ---------------------------------------------------------------------------
# 2. 「赛道必须配了账号」这道检查本身
# ---------------------------------------------------------------------------


def test_unconfigured_track_is_dropped() -> None:
    service = _service("sports_notes")

    assert service._drop_unconfigured_benchmark_track("不存在的赛道") is None


@pytest.mark.parametrize("track", ["科技商业", "AI机器人", "国际热点", "体育赛事"])
def test_configured_tracks_are_kept(track: str) -> None:
    service = _service("sports_notes")

    assert service._drop_unconfigured_benchmark_track(track) == track


def test_blank_track_is_dropped() -> None:
    service = _service("sports_notes")

    assert service._drop_unconfigured_benchmark_track("") is None
    assert service._drop_unconfigured_benchmark_track(None) is None


def test_benchmark_yaml_has_no_social_story_track_yet() -> None:
    """把现状记录在案：一旦有人给社会故事加了对标账号，这个断言会失败，

    那时应当改为「story_notes 解析出社会故事赛道」而不是继续返回 None。
    """
    import yaml

    payload = yaml.safe_load(BENCHMARK_YAML.read_text(encoding="utf-8"))
    tracks = {
        str(item.get("track", "")).strip()
        for item in payload.get("accounts", [])
        if isinstance(item, dict)
    }

    assert "社会故事" not in tracks


# ---------------------------------------------------------------------------
# 3. collect_topics 不能把 None 变成「所有赛道」
# ---------------------------------------------------------------------------


def test_collect_topics_skips_benchmark_entirely_without_a_track(monkeypatch) -> None:
    """track 为空时必须整段跳过，不能把 None 传给 BenchmarkMonitorAgent。

    Agent 会把 None 当成「不限制赛道」，采集全部对标号 —— 这正是科技热点
    漏进社会故事选题池的直接原因。
    """
    service = _service("robot_notes", strict_story=True)
    called: list[dict] = []
    monkeypatch.setattr(
        service, "collect_benchmark_hot_topics", lambda **kw: called.append(kw) or {}
    )
    _stub_topic_collection(monkeypatch, service)

    result = service.collect_topics()

    assert called == [], "没有对标赛道时不应调用对标采集"
    assert isinstance(result, dict)


def test_collect_topics_calls_benchmark_when_a_track_exists(monkeypatch) -> None:
    """对照组：有赛道的账号必须照旧采集，别把功能一起关掉。"""
    service = _service("sports_notes")
    called: list[dict] = []
    monkeypatch.setattr(
        service, "collect_benchmark_hot_topics", lambda **kw: called.append(kw) or {}
    )
    _stub_topic_collection(monkeypatch, service)

    service.collect_topics()

    assert called, "有对标赛道的账号应当照旧采集对标热点"
    assert called[0].get("track") == "体育赛事"


def _stub_topic_collection(monkeypatch, service) -> None:
    """把真实采集换成空报告 —— 这两个用例只关心对标采集有没有被调用。

    真实采集会联网抓 24 条热榜 + 3 个新闻页，既慢又会让测试受网络影响。
    """
    from toutiao_content_factory.models import DailyTopicsReport

    empty_report = DailyTopicsReport(
        generated_at="2026-09-18T00:00:00",
        lookback_days=2,
        max_topic_age_hours=24,
        focus_industries=[],
        total_raw_items=0,
        total_selected_items=0,
        topics=[],
        source_stats={},
        failed_sources=[],
        filtered_out={},
    )
    monkeypatch.setattr(
        "toutiao_content_factory.services.topic_collection.TopicCollectionService.collect_sync",
        lambda self: empty_report,
    )
