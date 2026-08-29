from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from toutiao_content_factory.services.revenue_attribution_service import (
    CostRecord,
    PerformanceSnapshot,
    RevenueAttributionService,
    RevenueEvent,
)


def test_build_and_register_content_identity(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    identity = service.build_content_identity(
        account_key="default",
        platform="toutiao",
        run_dir=tmp_path / "runs" / "20260608-test",
        topic_title="机器人订单放量",
        title="机器人订单放量背后",
        published_at=datetime.now().isoformat(timespec="seconds"),
        commercial_intent="high",
        content_mix="conversion",
        cta_variant="consultation",
        cta_source_code="robot_high_consultation",
        title_variant="contrast_hook",
        cover_variant="data_anchor",
        platform_variant="recommendation_feed",
    )
    assert identity.content_id.startswith("ci_")
    assert identity.commercial_intent == "high"
    assert identity.content_mix == "conversion"

    index_path = service.register_content(identity)
    assert index_path.exists()
    payload = service._load_json(index_path, default={})
    assert identity.content_id in payload


def test_cost_and_revenue_flow(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    identity = service.build_content_identity(
        account_key="default",
        platform="toutiao",
        run_dir=tmp_path / "runs" / "20260608-cost",
        topic_title="储能利润兑现",
        title="储能利润兑现节奏",
        published_at=datetime.now().isoformat(timespec="seconds"),
    )
    service.register_content(identity)

    cost = CostRecord(api_cost=0.6, image_cost=0.3, labor_minutes=10, labor_rate_per_hour=120)
    service.record_cost(identity.content_id, cost)
    loaded_cost = service.get_cost(identity.content_id)
    assert abs(loaded_cost.total_cost - (0.6 + 0.3 + 10 * 120 / 60)) < 1e-6

    event = RevenueEvent(event_type="lead_value", amount=200.0, source_article_id=identity.content_id)
    service.record_revenue_event(identity.content_id, event)
    events = service.get_revenue_events(identity.content_id)
    assert len(events) == 1
    assert events[0].event_type == "lead_value"


def test_revenue_events_are_idempotent_and_replaceable(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    event = RevenueEvent(
        event_type="deal",
        amount=1999.0,
        lead_id="lead_1",
        source_article_id="ci_1",
        event_id="deal:lead_1",
    )

    service.record_revenue_event("ci_1", event)
    service.record_revenue_event("ci_1", event)
    assert len(service.get_revenue_events("ci_1")) == 1

    service.record_revenue_event(
        "ci_1",
        RevenueEvent(
            event_type="deal",
            amount=2999.0,
            lead_id="lead_1",
            source_article_id="ci_1",
            event_id="deal:lead_1",
        ),
        replace_existing=True,
    )
    events = service.get_revenue_events("ci_1")
    assert len(events) == 1
    assert events[0].amount == 2999.0


def test_unknown_rpm_is_not_counted_as_weak(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    identity = service.build_content_identity(
        account_key="default",
        platform="toutiao",
        run_dir=tmp_path / "run-unknown-rpm",
        topic_title="机器人订单",
        title="机器人订单观察",
        published_at=datetime.now().isoformat(timespec="seconds"),
    )
    service.register_content(identity)
    service.record_snapshot(
        identity.content_id,
        PerformanceSnapshot(
            snapshot_label="24h",
            snapshot_at=datetime.now().isoformat(timespec="seconds"),
            impression_count=1000,
            read_count=100,
        ),
    )

    report = service.build_cooling_candidates(min_samples=1)
    assert report["candidates"] == []


def test_experiment_report_uses_weighted_read_rate_and_revenue(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    for index, (impressions, reads, revenue) in enumerate(((100, 10, 100.0), (900, 90, 300.0))):
        identity = service.build_content_identity(
            account_key="default",
            platform="toutiao",
            run_dir=tmp_path / f"run-experiment-{index}",
            topic_title="储能订单",
            title=f"储能标题{index}",
            published_at=datetime.now().isoformat(timespec="seconds"),
            cta_variant="consultation",
            cta_source_code="storage_consultation",
        )
        service.register_content(identity)
        service.record_snapshot(
            identity.content_id,
            PerformanceSnapshot(
                snapshot_label="24h",
                snapshot_at=datetime.now().isoformat(timespec="seconds"),
                impression_count=impressions,
                read_count=reads,
            ),
        )
        service.record_revenue_event(
            identity.content_id,
            RevenueEvent(
                event_type="deal",
                amount=revenue,
                source_article_id=identity.content_id,
                event_id=f"deal:{index}",
            ),
        )

    report = service.build_experiment_report()
    stats = report["dimensions"]["cta_variant"]["consultation"]
    assert stats["contents"] == 2
    assert stats["impressions"] == 1000
    assert stats["reads"] == 100
    assert stats["weighted_read_rate"] == 10.0
    assert stats["revenue_amount"] == 400.0


def test_cooling_requires_mature_samples_and_protects_revenue(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    identities = []
    for index in range(2):
        identity = service.build_content_identity(
            account_key="default",
            platform="toutiao",
            run_dir=tmp_path / f"run-cooling-{index}",
            topic_title="机器人订单",
            title=f"机器人标题{index}",
            published_at=datetime.now().isoformat(timespec="seconds"),
            topic_bucket="robotics",
        )
        service.register_content(identity)
        service.record_cost(identity.content_id, CostRecord(api_cost=1.0))
        service.record_snapshot(
            identity.content_id,
            PerformanceSnapshot(
                snapshot_label="1h",
                snapshot_at=datetime.now().isoformat(timespec="seconds"),
                impression_count=1000,
                read_count=10,
            ),
        )
        identities.append(identity)

    assert service.build_cooling_candidates(min_samples=2)["candidates"] == []

    for identity in identities:
        service.record_snapshot(
            identity.content_id,
            PerformanceSnapshot(
                snapshot_label="24h",
                snapshot_at=datetime.now().isoformat(timespec="seconds"),
                impression_count=1000,
                read_count=10,
            ),
        )
    report = service.build_cooling_candidates(min_samples=2)
    assert report["candidates"][0]["recommendation"] == "stop"

    service.record_revenue_event(
        identities[0].content_id,
        RevenueEvent(
            event_type="deal",
            amount=5000.0,
            source_article_id=identities[0].content_id,
            event_id="deal:protected",
        ),
    )
    report = service.build_cooling_candidates(min_samples=2)
    assert report["candidates"][0]["recommendation"] == "cooldown"


def test_snapshot_order_follows_maturity_windows(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    labels = ("24h", "1h", "72h", "6h")
    for label in labels:
        service.record_snapshot(
            "ci_order",
            PerformanceSnapshot(snapshot_label=label, snapshot_at=datetime.now().isoformat()),
        )

    assert [item.snapshot_label for item in service.get_snapshots("ci_order")] == [
        "1h",
        "6h",
        "24h",
        "72h",
    ]


def test_unit_economics_and_reports(tmp_path: Path) -> None:
    service = RevenueAttributionService(tmp_path, logging.getLogger("test"))
    base_time = datetime.now()
    identities = []
    for idx in range(3):
        identity = service.build_content_identity(
            account_key="default",
            platform="toutiao",
            run_dir=tmp_path / f"runs/2026060{idx}-mix",
            topic_title=f"选题桶A-{idx}",
            title=f"标题{idx}",
            published_at=(base_time - timedelta(days=idx)).isoformat(timespec="seconds"),
            commercial_intent="medium",
            content_mix="professional" if idx % 2 == 0 else "traffic",
        )
        service.register_content(identity)
        service.record_cost(identity.content_id, CostRecord(api_cost=0.5))
        snapshot = PerformanceSnapshot(
            snapshot_label="24h",
            snapshot_at=base_time.isoformat(timespec="seconds"),
            impression_count=1000,
            read_count=50,
        )
        service.record_snapshot(identity.content_id, snapshot)
        economics = service.compute_unit_economics(identity.content_id, platform_revenue=5.0)
        assert economics.content_id == identity.content_id
        identities.append(identity)

    mix_report = service.build_content_mix_report()
    assert mix_report["content_mix_counts"]["professional"] >= 1
    assert mix_report["content_mix_counts"]["traffic"] >= 1

    cooling_report = service.build_cooling_candidates(min_samples=1)
    assert isinstance(cooling_report.get("candidates"), list)

    business_report_path = service.build_business_report()
    assert business_report_path.exists()
    assert "Revenue rankings" in business_report_path.read_text(encoding="utf-8")
    text = business_report_path.read_text(encoding="utf-8")
    assert "收益优化经营报表" in text
