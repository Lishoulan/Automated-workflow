"""回归测试：社会故事赛道的选题过滤。

背景（2026-09-18）：热榜上真正属于「社会民生 / 家庭伦理 / 人情冷暖」的题只占
少数，其余是体育、时政、娱乐和商业新闻。story_notes 账号第一次采集回来的 8 条
里混着「中国男篮78-97日本男篮」「10分钟内2次！菲船故意撞中方」这类完全跑题的题。
加上 `social_story` 判据后收敛到 3 条，全部是正对题的社会新闻。

这些用例不需要网络：直接构造 TopicCandidate 调判据函数。
"""

from __future__ import annotations

import logging

import pytest

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.topic_collection import TopicCollectionService


@pytest.fixture()
def collector(app_settings) -> TopicCollectionService:
    """用最小 AppSettings 构造采集器；判据本身不依赖具体账号配置。"""
    return TopicCollectionService(app_settings, logging.getLogger("test_social_story"))


def _topic(title: str, summary: str = "", snippet: str = "") -> TopicCandidate:
    return TopicCandidate(
        title=title,
        url="https://example.com/a",
        source="test",
        source_type="hot_board",
        summary=summary,
        snippet=snippet,
    )


# --- 应当保留：真实社会新闻 ---

@pytest.mark.parametrize(
    "title",
    [
        "退休民警诉讼拿下2亿物流园控股权",
        "男子30层高空作业绳子被2楼老人剪断",
        "9万买的马车祸身亡 主人获赔13.5万",
        "儿媳拒绝赡养公婆被起诉",
        "妈妈拿巨型碗劝2米01儿子好好吃饭",
        "女子下班途中帮迷路老人联系家人",
        "游客捡到钱包原地等失主两小时",
    ],
)
def test_real_social_news_keeps_its_signal(collector, title: str) -> None:
    assert collector._has_social_story_signal(_topic(title)) is True


# --- 应当丢弃：跑题的热榜题 ---

@pytest.mark.parametrize(
    "title",
    [
        "中国男篮78-97日本男篮",
        "10分钟内2次！菲船故意冲撞中方",
        "比亚迪召回18.32万辆汽车",
        "某公司发布财报 净利润同比增长30%",
        "某明星官宣恋情",
        "穆欣月成首位亚运电竞女子冠军",
    ],
)
def test_off_topic_hot_topics_are_rejected(collector, title: str) -> None:
    assert collector._has_social_story_signal(_topic(title)) is False


def test_behavior_verbs_count_as_a_social_signal(collector) -> None:
    """没有关系词，但有「骗/偷/讹」这类行为动词，也应当算社会新闻。"""
    assert collector._has_social_story_signal(_topic("男子冒充房东骗取押金")) is True


@pytest.mark.parametrize(
    "title",
    [
        "李现在闲鱼买相机被骗2000元",
        "交警称没牵绳的狗一律按野狗处理",
        "充电器忘拔烧毁整个家",
    ],
)
def test_current_life_story_hot_topics_pass_the_complete_focus_gate(
    collector, title: str
) -> None:
    collector.topic_settings = collector.topic_settings.model_copy(
        update={"focus_industries": ["social_story"]}
    )
    topic = _topic(title)
    topic.heat_value = 6_000_000
    enriched = collector._enrich_topic(topic)
    filtered = {"weak_focus": 0, "low_quality": 0}

    assert collector._passes_topic_focus(enriched, filtered) is True


def test_relaxed_story_candidates_still_require_a_story_signal(collector) -> None:
    collector.topic_settings = collector.topic_settings.model_copy(
        update={"focus_industries": ["social_story"]}
    )
    false_positive = _topic("李想回应去XX化争议")
    false_positive.matched_industries = ["social_story"]
    false_positive.matched_keywords = ["回应"]

    assert collector._build_relaxed_candidates([false_positive]) == []


# --- 应当丢弃：靠一个「人味词」蒙进来的财经/宏观题 ---
#
# 背景（2026-09-19 实测）：热榜上出现「巴菲特：时间老人已对我十分慷慨」。
# 它靠**「老人」**这个人与关系信号命中了社会故事判据，于是被当成社会故事选题，
# 随后抓来 5852 字巴菲特投资哲学材料 —— 题材是投资，不是家长里短。
# 关键词表是按「故事的样子」写的，天然会漏这种字面撞词，
# 所以低信号表必须覆盖财经人物与宏观术语。


@pytest.mark.parametrize(
    "title",
    [
        "巴菲特：时间老人已对我十分慷慨",
        "芒格生前最后一次股东大会讲话",
        "美联储宣布降息25个基点",
    ],
)
def test_finance_false_positives_are_rejected(collector, title: str) -> None:
    assert collector._has_social_story_signal(_topic(title)) is False


@pytest.mark.parametrize(
    "title",
    [
        "老人被骗走全部积蓄，民警帮忙追回",
        "儿媳拒绝赡养公婆被起诉",
    ],
)
def test_finance_filter_does_not_break_real_social_news(collector, title: str) -> None:
    """反面保险：这些词精度必须够高，不能把真实社会新闻一起误杀。"""
    assert collector._has_social_story_signal(_topic(title)) is True


def test_story_account_must_not_enable_hot_topic() -> None:
    """社会故事账号不能启用 hot_topic 赛道。

    `_passes_topic_focus` 里 hot_topic 那个分支在热度达标时**直接 return True**，
    排在任何领域判据之前。一旦社会故事账号同时启用 hot_topic，
    `_has_social_story_signal` 就永远不会执行，篮球、时政、财报都会漏进选题池。
    """
    settings = load_settings(__import__("pathlib").Path.cwd(), account_key="robot_notes")

    assert "social_story" in settings.topic_collection.focus_industries
    assert "hot_topic" not in settings.topic_collection.focus_industries


def test_story_account_declares_the_social_story_industry(collector) -> None:
    """判据只有在账号真的启用 social_story 赛道时才会被调用。"""
    settings = load_settings(__import__("pathlib").Path.cwd(), account_key="robot_notes")

    assert settings.topic_collection.focus_industries == ["social_story", "consumer_practical", "technology_everyday"]
    assert settings.revenue_optimization.adaptive_topic_selection is True
    assert set(settings.revenue_optimization.operating_trial["directions"]) == {"consumer_practical", "technology_everyday"}
    assert "hot_topic" not in settings.topic_collection.focus_industries


def test_story_topic_metadata_does_not_fall_back_to_technology(collector) -> None:
    collector.topic_settings = collector.topic_settings.model_copy(
        update={"focus_industries": ["social_story"]}
    )

    enriched = collector._enrich_topic(_topic("妈妈拿巨型碗劝2米01儿子好好吃饭"))

    assert enriched.topic_type == "social_story"
    assert enriched.topic_type_label == "社会故事"
    assert enriched.title_playbook_track == "社会故事"
