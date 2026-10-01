from __future__ import annotations

import logging

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.material_search_service import (
    MaterialBundle,
    MaterialSource,
    assess_material_evidence,
)


def _article_body(prefix: str, repeats: int = 7) -> str:
    facts = [
        f"据公开报道，{prefix}公司在2026年9月28日发布了新产品。",
        f"公告显示，{prefix}产品起售价为8899元。",
        f"该公司表示，设备配备12.6英寸显示屏。",
        f"报道还提到，首批产品将在10月8日正式发售。",
        f"截至发布会结束，现场共展示了3款配置。",
        f"官方确认，产品提供4种存储容量选择。",
    ]
    return "".join(facts * repeats)


def _hot_topic(rank: int, title: str) -> TopicCandidate:
    return TopicCandidate(
        rank=rank,
        title=title,
        url="",
        source="百度热榜",
        source_type="baidu_hot",
    )


def test_repeated_search_digest_does_not_inflate_evidence() -> None:
    repeated = "三星发布新款平板，售价8899元，将于10月8日开售。" * 12
    bundle = MaterialBundle(
        query="三星发布新款平板",
        sources=[
            MaterialSource(
                title="搜索结果一",
                url="https://news.example.com/a",
                body=repeated,
                ok=True,
                evidence_type="search_snippet",
            ),
            MaterialSource(
                title="搜索结果汇总",
                url="",
                body=repeated,
                ok=True,
                evidence_type="search_digest",
            ),
        ],
    )

    assessment = assess_material_evidence(bundle)

    assert assessment.passed is False
    assert assessment.independent_source_count == 1
    assert assessment.factual_anchor_count == 1


def test_two_independent_sources_with_five_facts_pass() -> None:
    bundle = MaterialBundle(
        query="三星发布新款平板",
        sources=[
            MaterialSource(
                title="媒体甲",
                url="https://alpha.example/a",
                body=_article_body("甲", repeats=3),
                ok=True,
                evidence_type="search_snippet",
            ),
            MaterialSource(
                title="媒体乙",
                url="https://beta.example/b",
                body=_article_body("乙", repeats=3),
                ok=True,
                evidence_type="search_snippet",
            ),
        ],
    )

    assessment = assess_material_evidence(bundle)

    assert assessment.passed is True
    assert assessment.independent_source_count == 2
    assert assessment.factual_anchor_count >= 5


def test_subdomains_of_one_publisher_count_as_one_source() -> None:
    bundle = MaterialBundle(
        query="同一媒体的两篇报道",
        sources=[
            MaterialSource(
                title="新闻频道",
                url="https://news.publisher.com.cn/a",
                body=_article_body("新闻频道", repeats=3),
                ok=True,
                evidence_type="search_snippet",
            ),
            MaterialSource(
                title="财经频道",
                url="https://finance.publisher.com.cn/b",
                body=_article_body("财经频道", repeats=3),
                ok=True,
                evidence_type="search_snippet",
            ),
        ],
    )

    assessment = assess_material_evidence(bundle)

    assert assessment.passed is False
    assert assessment.independent_source_count == 1


def test_one_substantial_article_with_five_facts_passes() -> None:
    bundle = MaterialBundle(
        query="三星发布新款平板",
        sources=[
            MaterialSource(
                title="完整报道",
                url="https://publisher.example/full",
                body=_article_body("完整报道", repeats=9),
                ok=True,
                evidence_type="article",
            )
        ],
    )

    assessment = assess_material_evidence(bundle)

    assert assessment.passed is True
    assert assessment.full_article_count == 1
    assert assessment.factual_anchor_count >= 5


def test_weak_first_topic_is_rejected_so_fallback_can_select_next(
    app_settings,
    monkeypatch,
) -> None:
    import toutiao_content_factory.services.material_search_service as material_service

    automation = TopicAutomationService(app_settings, logging.getLogger("material-gate-test"))
    weak = _hot_topic(1, "只有重复摘要的热点")
    strong = _hot_topic(2, "具有完整报道的热点")
    weak_bundle = MaterialBundle(
        query=weak.title,
        sources=[
            MaterialSource(
                title="重复摘要",
                url="https://same.example/a",
                body="据报道，该事件在9月发生。" * 40,
                ok=True,
                evidence_type="search_snippet",
            )
        ],
    )
    strong_bundle = MaterialBundle(
        query=strong.title,
        sources=[
            MaterialSource(
                title="完整报道",
                url="https://publisher.example/full",
                body=_article_body("完整报道", repeats=9),
                ok=True,
                evidence_type="article",
            )
        ],
    )
    monkeypatch.setattr(
        material_service,
        "collect_material",
        lambda title, **_: weak_bundle if title == weak.title else strong_bundle,
    )
    monkeypatch.setattr(
        automation,
        "_material_retry_candidates",
        lambda _report: [weak, strong],
    )

    assert automation._hydrate_topic_material(weak) is False
    report = type("Report", (), {"topics": [weak, strong]})()
    selected = automation._hydrate_with_fallback(report, weak)

    assert selected is strong
    assert getattr(strong, "_verified_source_material", False) is True
