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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.topic_collection import TopicCollectionService
from toutiao_content_factory.services.material_search_service import MaterialBundle, MaterialSource

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


def _strict_story_settings():
    """Exercise the supported strict-story mode, separate from the active trial."""
    settings = _settings(STORY_ACCOUNT)
    settings.revenue_optimization = replace(settings.revenue_optimization, adaptive_topic_selection=False)
    settings.topic_collection = settings.topic_collection.model_copy(update={"focus_industries": ["social_story"]})
    return settings


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


def test_story_topic_does_not_inherit_industry_writing_metadata() -> None:
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("老人拍照半分钟被弹窗近20次")
    topic.title_playbook_track = "科技商业"
    topic.topic_type = "industry_event"
    topic.content_mix = "professional"
    topic.cta_variant = "newsletter"
    topic.cta_source_code = "科技商业_medium_newsletter"

    prompt = automation._build_input_text(topic)
    enriched = automation._enrich_topic_revenue_metadata(topic)

    assert "选题类型：social_story" in prompt
    assert "标题打法赛道：社会故事" in prompt
    assert enriched.title_playbook_track == "社会故事"
    assert enriched.topic_type == "social_story"
    assert enriched.content_mix == "traffic"
    assert enriched.cta_variant == "discussion"
    assert enriched.cta_source_code.endswith("_discussion")


@pytest.mark.parametrize(
    "title",
    [
        "中国男篮大比分不敌日本",
        "菲船加速冲撞中国海警船",
        "比亚迪召回18.32万辆汽车",
    ],
)
def test_story_account_rejects_off_topic_hot_topics(title: str) -> None:
    automation = TopicAutomationService(_strict_story_settings(), logging.getLogger("test_pivot"))

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
    service = ContentFactoryAppService(_strict_story_settings(), logging.getLogger("test_pivot"))

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
    # Current approved production trial adds reader-oriented directions;
    # strict-story mode is tested independently above, not imposed on production.
    assert settings.topic_collection.focus_industries == ["social_story", "consumer_practical", "technology_everyday"]
    assert settings.revenue_optimization.adaptive_topic_selection is True
    assert set(settings.revenue_optimization.operating_trial["directions"]) == {"consumer_practical", "technology_everyday"}
    # 微头条人设
    assert settings.publishing.weitoutiao_personas == ["story_comment"]
    assert settings.topic_collection.enabled_sources == [
        "baidu_hot", "toutiao_hot", "sina_social", "chinanews_society", "ithome", "36kr",
    ]
    assert settings.publishing.allow_duplicate_topics is False


@pytest.mark.parametrize("title,allowed", [
    ("中国男篮大比分不敌日本", False),
    ("菲船加速冲撞中国海警船", False),
    ("机器人公司股价突破新高", False),
    ("消费者申请退款，商家提供预付服务记录", True),
    ("海尔发布全自主家用 AI 烹饪机器人CR3", True),
])
@pytest.mark.parametrize("selection", ["strict", "soft", "retry"])
def test_production_trial_keeps_reader_boundaries_in_all_selection_paths(tmp_path, monkeypatch, title, allowed, selection):
    from toutiao_content_factory.services import revenue_feedback as policy

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            now = datetime(2026, 10, 21, 8, tzinfo=timezone(timedelta(hours=8)))
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(policy, "datetime", Clock)
    settings = _settings(STORY_ACCOUNT)
    settings.output_dir, settings.project_root = tmp_path / "output", tmp_path
    automation = TopicAutomationService(settings, logging.getLogger("test_pivot"))
    topic = _hot_topic(title)
    if selection == "retry":
        result = automation._material_retry_candidates(_daily_report([topic]))
    else:
        select = automation._available_topics if selection == "strict" else automation._available_topics_soft
        result = select([topic], allow_generated_duplicates=True)
    assert bool(result) is allowed
    if allowed:
        assert result[0].revenue_selection["category"] in {"consumer_practical", "technology_everyday"}
        assert result[0].revenue_selection["approval"] is False
        assert getattr(result[0], "_verified_source_material", False) is False


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


def test_search_results_drop_multi_topic_news_digests() -> None:
    from toutiao_content_factory.services.material_search_service import _filter_relevant_hits

    hits = [
        {
            "title": "早报：央行四箭齐发；那英唱弯弯的月亮；充电器忘拔烧毁整个家；多地限高份子钱",
            "url": "https://example.com/digest",
        },
        {
            "title": "充电器忘拔烧毁整个家，消防部门提醒注意这些细节",
            "url": "https://example.com/story",
        },
    ]

    kept = _filter_relevant_hits("bing", "充电器忘拔烧毁整个家", hits)

    assert [item["url"] for item in kept] == ["https://example.com/story"]


def test_search_results_drop_different_stories_with_generic_word_overlap() -> None:
    """云端真实样本：只共享“父亲/邻居/帮忙”不能算同一事件。"""
    from toutiao_content_factory.services.material_search_service import _filter_relevant_hits

    hits = [
        {
            "title": "1岁儿子凭空消失，15天后父亲在女邻居家的柴房找到",
            "snippet": "孩子失踪后，家人和警方持续寻找。",
            "url": "https://example.com/other-child",
        },
        {
            "title": "谁家有事都愿帮忙的增叔，邻居提起他都说热心",
            "snippet": "社区居民讲述多年邻里往来。",
            "url": "https://example.com/other-neighbor",
        },
        {
            "title": "罗庄一刚考上大学的女生被骗学费9900元",
            "snippet": "家人发现后报警求助。",
            "url": "https://example.com/other-fraud",
        },
        {
            "title": "女子父亲突然离世，邻居1分钟赶到帮忙",
            "snippet": "父亲离世后，邻居很快赶到现场协助处理。",
            "url": "https://example.com/target-story",
        },
    ]

    kept = _filter_relevant_hits("bing", "女子父亲突然离世邻居1分钟赶到帮忙", hits)

    assert [item["url"] for item in kept] == ["https://example.com/target-story"]


# ---------------------------------------------------------------------------
# 7. 抓不到素材就不发（宁可不发，也不发垃圾）
# ---------------------------------------------------------------------------


def _material_bundle(usable: bool, text: str = "", *, evidence_type="article") -> MaterialBundle:
    # Real collector contract: typed, attributed sources. A string stub used
    # the legacy length-only compatibility path instead of the actual fact gate.
    return MaterialBundle("test", [MaterialSource("公开报道", "https://report.example/article", text,
                                                ok=usable, evidence_type=evidence_type)] if usable else [])


def _detailed_body(lead="据报道，相关当事人提供了本案经过和完整的处理记录。"):
    # Synthetic source fixture only; distinct anchored records exercise the
    # actual evidence assessor rather than bypassing it with repeated filler.
    return lead + "\n" + "\n".join(
        f"据第{number}份现场记录，当事人在2026年9月{number}日提交了对应时间的办理材料。"
        "记录列明申请事项、经办主体、签收时间及当时的具体处理结果，公开报道同时区分了当事人主张和有关部门已经确认的范围。"
        "后续材料仅用于补充办理过程，并未改变记录原有的时间顺序和适用条件。"
        for number in range(1, 9))


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
            return _material_bundle(usable=False)
        return _material_bundle(usable=True, text=_detailed_body())

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


def test_thin_search_snippets_do_not_count_as_publishable_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有搜索摘要不等于有足够事实；低于 400 字时必须继续换题。"""
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("妈妈拿巨型碗劝2米01儿子好好吃饭")
    thin_material = "山东一位妈妈拿出巨型碗，劝身高2米01的13岁儿子好好吃饭。" * 3
    monkeypatch.setattr(
        mss,
        "collect_material",
        lambda *args, **kwargs: _material_bundle(True, thin_material),
    )

    assert len(thin_material) < automation.MATERIAL_MIN_BODY_CHARS
    assert automation._hydrate_topic_material(topic) is False
    assert getattr(topic, "_verified_source_material", False) is False


def test_long_hot_search_digest_still_requires_article_material(monkeypatch: pytest.MonkeyPatch) -> None:
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("老人拍照半分钟被弹窗近20次")
    topic.source_type = "baidu_hot"
    topic.snippet = "【来源：搜索摘要】老人拍照半分钟被弹窗近20次。" * 35
    calls: list[str] = []

    def fake_collect(title, **_kwargs):
        calls.append(title)
        return _material_bundle(usable=False)

    monkeypatch.setattr(mss, "collect_material", fake_collect)

    assert automation._hydrate_topic_material(topic) is False
    assert calls == [topic.title]


def test_fetched_hot_article_body_reaches_the_writing_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("老人拍照半分钟被弹窗近20次")
    topic.source_type = "baidu_hot"
    topic.snippet = "【来源：搜索摘要】老人拍照半分钟被弹窗近20次。" * 35
    source_body = _detailed_body("黄山老人想拍鱼灯表演，打开相机时弹窗连续跳出。")
    monkeypatch.setattr(mss, "collect_material", lambda *a, **k: _material_bundle(True, source_body))

    raw_summary, _ = automation._build_prompt_evidence(topic)
    assert len(raw_summary) <= 160
    assert automation._hydrate_topic_material(topic) is True
    summary, extra = automation._build_prompt_evidence(topic)

    assert summary.startswith("黄山老人想拍鱼灯表演")
    assert len(summary) > 600
    assert extra == ""
    assessment = topic._material_evidence_assessment
    assert assessment["full_article_count"] == assessment["independent_source_count"] == 1
    assert assessment["factual_anchor_count"] >= 5


@pytest.mark.parametrize("evidence_type,body", [
    ("search_digest", _detailed_body()),
    ("article", "只有笼统看法，没有可定位的具体事实。" * 100),
])
def test_typed_material_cannot_pass_by_length_without_sources_and_facts(monkeypatch, evidence_type, body):
    import toutiao_content_factory.services.material_search_service as mss
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("老人拍照半分钟被弹窗近20次")
    monkeypatch.setattr(mss, "collect_material", lambda *a, **k: _material_bundle(True, body, evidence_type=evidence_type))
    assert len(body) > 900
    assert automation._hydrate_topic_material(topic) is False
    assert getattr(topic, "_verified_source_material", False) is False
    assert topic._material_evidence_assessment["reasons"]


def test_verified_hot_material_without_url_is_saved_for_copy_check(tmp_path: Path) -> None:
    settings = _settings(STORY_ACCOUNT)
    settings.output_dir = tmp_path
    automation = TopicAutomationService(settings, logging.getLogger("test_pivot"))
    topic = _hot_topic("老人拍照半分钟被弹窗近20次")
    topic.url = ""
    topic.snippet = "已抓取的报道正文。" * 100
    topic._verified_source_material = True

    path = automation._capture_source_material(topic)

    assert path is not None
    assert path.read_text(encoding="utf-8") == topic.snippet


def test_all_topics_without_material_yield_no_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    first = _hot_topic("男子高空作业绳子被2楼老人剪断")
    second = _hot_topic("女子离婚后发现丈夫隐瞒债务")

    monkeypatch.setattr(mss, "collect_material", lambda *a, **k: _material_bundle(usable=False))
    monkeypatch.setattr(
        automation, "_material_retry_candidates", lambda report: [first, second]
    )

    class _Report:
        topics = [first, second]

    assert automation._hydrate_with_fallback(_Report(), first) is None


def _daily_report(topics: list[TopicCandidate]):
    """构造一个字段完整的 DailyTopicsReport，供 `prepare_auto_run` 走完整流程。"""
    from toutiao_content_factory.models import DailyTopicsReport

    return DailyTopicsReport(
        generated_at="2026-09-19T15:00:00+08:00",
        lookback_days=1,
        max_topic_age_hours=24,
        focus_industries=["social_story"],
        total_raw_items=len(topics),
        total_selected_items=len(topics),
        topics=topics,
        source_stats={},
        failed_sources=[],
        filtered_out={},
    )


def test_auto_selection_retries_the_next_topic_when_material_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动选题抓不到素材必须换题，不能整天不发。

    实测（云端 run 35430296347）：榜首「张雪回应打车被认出"忽悠"司机」抓不到素材，
    旧逻辑把"自动选题"也当成显式指定、直接放弃整次运行，云端只有一次机会，
    于是 `Daily workflow did not produce any runnable candidates`，当天一篇都没发。
    """
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    blocked = _hot_topic("张雪回应打车被认出忽悠司机")
    good = _hot_topic("男子高空作业绳子被2楼老人剪断")
    def fake_collect(title, **_kwargs):
        if title == good.title:
            return _material_bundle(usable=True, text=_detailed_body())
        return _material_bundle(usable=False)

    monkeypatch.setattr(mss, "collect_material", fake_collect)
    monkeypatch.setattr(automation, "_select_topic", lambda *a, **k: blocked)
    monkeypatch.setattr(
        automation, "_material_retry_candidates", lambda report: [blocked, good]
    )

    report = _daily_report([blocked, good])
    monkeypatch.setattr(automation, "_load_daily_topics", lambda: report)

    selection = automation.prepare_auto_run(allow_generated_duplicates=True)

    assert selection is not None, "自动选题必须换到下一条，而不是直接放弃"
    assert selection.topic.title == good.title


def test_explicit_topic_title_is_never_silently_swapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户显式给了标题却抓不到素材时，放弃而不是偷偷换成别的题。"""
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    blocked = _hot_topic("张雪回应打车被认出忽悠司机")
    other = _hot_topic("男子高空作业绳子被2楼老人剪断")

    monkeypatch.setattr(mss, "collect_material", lambda *a, **k: _material_bundle(usable=False))
    monkeypatch.setattr(
        automation, "_material_retry_candidates", lambda report: [blocked, other]
    )

    class _Report:
        topics = [blocked, other]

    monkeypatch.setattr(automation, "_load_daily_topics", lambda: _Report())
    monkeypatch.setattr(
        automation,
        "_select_topic",
        lambda report, rank, title_query, allow_generated_duplicates=False: blocked,
    )

    assert automation.prepare_auto_run(title_query=blocked.title) is None


# ---------------------------------------------------------------------------
# 9. 候选循环只允许一层换题
# ---------------------------------------------------------------------------
#
# 实测（云端 run 35430893194）：`AppService` 的候选循环逐条把
# `candidate["title"]` 当 `topic_title=` 传进 `auto_run`，于是 `requested_title`
# 恒为真 → 换题逻辑**永远不生效** → 榜首那条抓不到素材就整次运行放弃，
# 云端当天一篇都发不出去（`Daily workflow did not produce any runnable candidates`）。
#
# 现在外层 candidate_specs 已经逐条换题，内层再打开全池重试会形成 N×N 次搜索。
# 因此日更循环应关闭内层许可；`prepare_auto_run` 自身的换题能力仍保留给其他调用方。


def test_candidate_loop_avoids_nested_material_retry() -> None:
    source = Path(__file__).resolve().parents[1] / (
        "src/toutiao_content_factory/services/app_service.py"
    )
    text = source.read_text(encoding="utf-8")

    assert "allow_material_retry=False" in text
    # 参数必须一路透传到 prepare_auto_run
    assert text.count("allow_material_retry=allow_material_retry") >= 3


def test_material_retry_flag_reaches_prepare_auto_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行为验证：打开许可后，机器挑的题抓不到素材会换题；关掉则不换。"""
    import toutiao_content_factory.services.material_search_service as mss

    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    blocked = _hot_topic("张雪回应打车被认出忽悠司机")
    good = _hot_topic("男子高空作业绳子被2楼老人剪断")
    def fake_collect(title, **_kwargs):
        if title == good.title:
            return _material_bundle(usable=True, text=_detailed_body())
        return _material_bundle(usable=False)

    monkeypatch.setattr(mss, "collect_material", fake_collect)
    monkeypatch.setattr(
        automation, "_material_retry_candidates", lambda report: [blocked, good]
    )
    report = _daily_report([blocked, good])
    monkeypatch.setattr(automation, "_load_daily_topics", lambda: report)
    monkeypatch.setattr(
        automation,
        "_select_topic",
        lambda report, rank, title_query, allow_generated_duplicates=False: blocked,
    )

    # 关掉许可（模拟"用户显式指定"）→ 放弃
    assert automation.prepare_auto_run(title_query=blocked.title) is None
    # 打开许可（模拟候选循环）→ 换到能抓到素材的那条
    selected = automation.prepare_auto_run(
        title_query=blocked.title, allow_material_retry=True
    )
    assert selected is not None
    assert selected.topic.title == good.title


def test_retry_candidates_stay_inside_the_account_track() -> None:
    """重试范围只许比 `_select_topic` 更窄，不许更宽。

    实测回归（verify11）：第一版 `_material_retry_candidates` 直接返回
    `_available_topics(...)`，漏了账号边界过滤，于是"换题重试"把一条**机器人**选题
    塞进了社会故事号（抓来 2066 字具身智能材料）。
    """
    automation = TopicAutomationService(_strict_story_settings(), logging.getLogger("test_pivot"))
    story = _hot_topic("男子高空作业绳子被2楼老人剪断")
    robot = _hot_topic("机器人首秀网球场，银河通用突破具身智能AstraTennis时刻")

    class _Report:
        topics = [story, robot]

    retry = automation._material_retry_candidates(_Report())

    titles = [item.title for item in retry]
    assert story.title in titles
    assert robot.title not in titles, "重试候选里出现了机器人选题"


def test_soft_fallback_does_not_cross_the_account_boundary() -> None:
    """软兜底只放宽去重条件，不许放宽赛道。

    实测回归（verify12）：`_select_topic` 的 `_available_topics_soft` 分支完全不看赛道，
    于是把「海尔发布全自主家用 AI 烹饪机器人…」（matched_industries=
    ['humanoid_robot','ai_hardware']）选成了社会故事的选题，并真的抓素材写成稿发了出去。
    """
    automation = TopicAutomationService(_strict_story_settings(), logging.getLogger("test_pivot"))
    robot = _hot_topic("海尔发布全自主家用 AI 烹饪机器人CR3")
    robot.matched_industries = ["humanoid_robot", "ai_hardware"]
    robot.matched_keywords = ["机器人", "大模型"]

    class _Report:
        topics = [robot]

    selection = automation._select_topic(
        _Report(), rank=None, title_query=None, allow_generated_duplicates=True
    )

    assert selection is None, "软兜底把赛道外的选题放进了故事号"


def test_soft_fallback_still_serves_in_scope_topics() -> None:
    """正面：赛道内的选题仍然能被软兜底救回来。"""
    automation = TopicAutomationService(_strict_story_settings(), logging.getLogger("test_pivot"))
    story = _hot_topic("男子高空作业绳子被2楼老人剪断")

    class _Report:
        topics = [story]

    selection = automation._select_topic(
        _Report(), rank=None, title_query=None, allow_generated_duplicates=True
    )

    assert selection is not None
    assert selection.title == story.title


# ---------------------------------------------------------------------------
# 8. 模式判据必须看账号赛道，而不是只看题目内容
# ---------------------------------------------------------------------------
#
# 实测（verify14 出稿）：故事号的正文里出现国际模式的写作指令
# 「请先准确理解并消化素材里的英文信息，再写成自然中文国际热点解读稿」。
# 根因：`_topic_mode` 的国际判据是在 `topic.snippet` 上做关键词匹配，而
# `_hydrate_topic_material` 会把抓来的 3700+ 字正文写进 snippet —— 正文里出现
# 一个「美国/欧洲」就让 international 抢在 social_story 之前命中。
# 一个只发人间故事的号，不存在"这条要按体育稿/国际稿写"的情况。


def test_story_account_never_uses_international_mode() -> None:
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")
    # 抓来的正文里混进了国际词，正是实测里的情形
    topic.snippet = "据美国媒体报道，该男子在高空作业时绳索被剪断。欧洲多个城市也有类似案例。"

    assert automation._topic_mode(topic) != "international"
    assert automation._topic_mode(topic) == "social_story"


def test_story_account_never_uses_sports_mode() -> None:
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")
    topic.snippet = "当地联赛赛季期间，该男子照常作业，NBA 也报道了此事。"

    assert automation._topic_mode(topic) != "sports"


def test_story_closing_instruction_is_the_narrative_one() -> None:
    automation = TopicAutomationService(_settings(STORY_ACCOUNT), logging.getLogger("test_pivot"))
    topic = _hot_topic("男子高空作业绳子被2楼老人剪断")
    topic.snippet = "据美国媒体报道，该男子在高空作业时绳索被剪断。"

    instruction = automation._build_closing_instruction(
        topic, topic_mode=automation._topic_mode(topic), primary_industry="社会故事"
    )

    assert "人情故事" in instruction
    assert "国际热点" not in instruction
    assert "英文信息" not in instruction


def test_vertical_account_still_uses_sports_mode() -> None:
    """对照：垂直体育号不受影响。"""
    automation = TopicAutomationService(
        _settings(VERTICAL_ACCOUNT), logging.getLogger("test_pivot")
    )
    topic = _hot_topic("国足绝杀晋级，联赛赛季迎来关键一战")

    assert automation._topic_mode(topic) == "sports"


# ---------------------------------------------------------------------------
# 10. 单次运行必须有时间预算（否则定时任务会被 45 分钟上限杀掉）
# ---------------------------------------------------------------------------
#
# 实测云端单次运行会跑到 30~40 分钟：每条候选都要走一次浏览器搜索 +
# 编辑委员会 + 质量复核，而 workflow 的 `timeout-minutes` 是 45。
# 被外部超时杀掉 = 当天白跑。所以要能在接近上限时主动收敛：
# 停止换题，用手上最好的候选走发布。


def test_daily_run_budget_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from toutiao_content_factory.services.app_service import ContentFactoryAppService

    service = ContentFactoryAppService.__new__(ContentFactoryAppService)

    monkeypatch.delenv("TOUTIAO_DAILY_RUN_BUDGET_SECONDS", raising=False)
    default = service._daily_run_budget_seconds()
    assert default == ContentFactoryAppService.DAILY_RUN_BUDGET_SECONDS
    # 必须显著小于 workflow 的 45 分钟上限，给发布与收尾留出余量
    assert default < 45 * 60

    monkeypatch.setenv("TOUTIAO_DAILY_RUN_BUDGET_SECONDS", "600")
    assert service._daily_run_budget_seconds() == 600

    # 非法值与"不限制"都能安全处理
    monkeypatch.setenv("TOUTIAO_DAILY_RUN_BUDGET_SECONDS", "abc")
    assert (
        service._daily_run_budget_seconds()
        == ContentFactoryAppService.DAILY_RUN_BUDGET_SECONDS
    )
    monkeypatch.setenv("TOUTIAO_DAILY_RUN_BUDGET_SECONDS", "0")
    assert service._daily_run_budget_seconds() == float("inf")


def test_candidate_loop_honours_the_budget() -> None:
    """候选循环里必须真的检查预算并 break —— 光有时间函数不算。

    注意预算检查在 `_daily_run_impl`（`daily_run` 只是加锁与错误处理的包装层）。
    """
    import inspect

    from toutiao_content_factory.services import app_service as module

    source = inspect.getsource(module.ContentFactoryAppService._daily_run_impl)

    assert "budget_seconds" in source
    assert "停止换题" in source
    assert "break" in source

