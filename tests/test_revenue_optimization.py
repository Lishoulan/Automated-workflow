from __future__ import annotations

import csv
import json
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from toutiao_content_factory.models import ArticlePerformance, TopicCandidate
from toutiao_content_factory.services.feedback_loop_service import FeedbackLoopService
from toutiao_content_factory.services.lead_ops_service import LeadOpsService
from toutiao_content_factory.services.performance_service import PerformanceService
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.revenue_attribution_service import (
    CostRecord,
    PerformanceSnapshot,
    RevenueAttributionService,
    RevenueEvent,
)
from toutiao_content_factory.services.review_service import ReviewBundle, ReviewWorkflowService
from toutiao_content_factory.web.view_service import WebViewService


def _minimal_settings(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    prompts_dir = project_root / "prompts"
    config_dir = project_root / "config"
    prompts_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (config_dir / "bad_words.txt").write_text("# test\n", encoding="utf-8")
    output_dir = tmp_path / "output"
    return SimpleNamespace(
        project_root=project_root,
        prompts_dir=prompts_dir,
        output_dir=output_dir,
        automation_dir=output_dir / "automation",
        account_key="default",
        brand_name="测试品牌",
        industry_focus="科技商业",
        brand_positioning="科技分析",
        openai_api_key=None,
        openai_base_url=None,
        openai_model="gpt-4.1-mini",
        temperature=0.2,
        publishing=SimpleNamespace(history_filename="publish_history.json", title_max_length=40),
    )


def test_performance_records_expose_compatible_read_and_ctr_fields(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    service = PerformanceService(settings, logging.getLogger("test"))
    metrics = service._normalize_metric_rows(
        [
            {
                "title": "人形机器人完成9分钟演示",
                "publish_text": "",
                "text": "展现 681 阅读 14 点赞 2 评论 1 转发 1",
                "url": "https://example.test/article",
            }
        ]
    )

    assert len(metrics) == 1
    assert metrics[0].views == 14
    assert metrics[0].read_count == 14
    assert metrics[0].impression_count == 681
    assert metrics[0].ctr == 2.06

    matched = service._match_metrics_to_local_records("toutiao", metrics)
    assert matched[0]["read_count"] == 14
    assert matched[0]["ctr"] == 2.06
    assert matched[0]["like_count"] == 2
    assert matched[0]["comment_count"] == 1
    assert matched[0]["share_count"] == 1


def test_performance_recovers_archived_publish_when_history_is_missing(
    app_settings, tmp_path: Path
) -> None:
    service = PerformanceService(app_settings, logging.getLogger("test.archive-match"))
    run_dir = (
        app_settings.output_dir
        / app_settings.automation.archive_dirname
        / "20261001-090000_story"
    )
    publish_dir = run_dir / app_settings.review.publish_dirname
    publish_dir.mkdir(parents=True)
    (publish_dir / "publish_meta.json").write_text(
        json.dumps(
            {
                "approved_at": "2026-10-01T09:00:00+08:00",
                "selected_title": "交公粮和社保能否衔接，先分清建议与现行规则",
                "selected_topic": {
                    "title": "农民交公粮能否视同缴社保",
                    "url": "https://www.toutiao.com/article/1234567890123456789/",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (publish_dir / "publish_result.json").write_text(
        json.dumps(
            {
                "status": "submitted_for_review",
                "published_at": "2026-10-01T09:01:00+08:00",
                "external_url": "https://www.toutiao.com/article/1234567890123456789/",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    metric = ArticlePerformance(
        url="https://www.toutiao.com/group/1234567890123456789/?source=mp",
        title="平台展示标题可能被截断",
        publish_date="2026-10-01",
        views=10,
        interactions=1,
        roi_score=0,
        impression_count=200,
        read_count=10,
    )

    matched = service._match_metrics_to_local_records("toutiao", [metric])

    assert matched[0]["matched"] is True
    assert matched[0]["local_record"]["run_dir"] == str(run_dir)
    assert matched[0]["local_record"]["status"] == "submitted_for_review"


def test_performance_syncs_matured_revenue_snapshot(app_settings, tmp_path: Path) -> None:
    app_settings.output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "run-8h"
    publish_dir = run_dir / app_settings.review.publish_dirname
    publish_dir.mkdir(parents=True)
    published_at = datetime.now() - timedelta(hours=8)
    (publish_dir / "publish_meta.json").write_text(
        json.dumps(
            {
                "selected_title": "鏍囬",
                "selected_topic": {
                    "title": "鍟嗕笟涓婚",
                    "commercial_intent": "high",
                    "content_mix": "conversion",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (app_settings.output_dir / app_settings.publishing.history_filename).write_text(
        json.dumps(
            [
                {
                    "published_at": published_at.isoformat(timespec="seconds"),
                    "platform": "toutiao",
                    "title": "鏍囬",
                    "run_dir": str(run_dir),
                    "topic_title": "鍟嗕笟涓婚",
                    "topic_url": "",
                    "status": "published",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    attribution = RevenueAttributionService(app_settings.output_dir, logging.getLogger("test"))
    identity = attribution.build_content_identity(
        account_key=app_settings.account_key,
        platform="toutiao",
        run_dir=run_dir,
        topic_title="鍟嗕笟涓婚",
        title="鏍囬",
        published_at=published_at.isoformat(timespec="seconds"),
        commercial_intent="high",
        content_mix="conversion",
    )
    attribution.register_content(identity)
    attribution.record_revenue_event(
        identity.content_id,
        RevenueEvent(event_type="lead_value", amount=199.0, source_article_id=identity.content_id),
    )

    service = PerformanceService(app_settings, logging.getLogger("test"))
    records = [
        {
            "platform": "toutiao",
            "title": "鏍囬",
            "publish_date": published_at.date().isoformat(),
            "impression_count": 1000,
            "read_count": 80,
            "like_count": 4,
            "comment_count": 2,
            "share_count": 1,
            "local_record": {
                "platform": "toutiao",
                "title": "鏍囬",
                "published_at": published_at.isoformat(timespec="seconds"),
                "run_dir": str(run_dir),
                "topic_title": "鍟嗕笟涓婚",
            },
        }
    ]

    result = service._sync_attribution_snapshots(records, observed_at=datetime.now())

    assert result["recorded_count"] == 1
    assert result["snapshot_labels"]["6h"] == 1
    assert records[0]["content_id"] == identity.content_id
    assert records[0]["snapshot_label"] == "6h"
    assert records[0]["unit_economics"]["lead_revenue"] == 199.0
    assert attribution.get_snapshots(identity.content_id)[0].snapshot_label == "6h"


def test_snapshot_window_uses_largest_matured_label(app_settings) -> None:
    service = PerformanceService(app_settings, logging.getLogger("test"))
    published_at = datetime(2026, 8, 28, 0, 0, 0)

    assert service._resolve_snapshot_window(
        published_at.isoformat(), observed_at=published_at + timedelta(hours=5, minutes=59)
    )[0] == "1h"
    assert service._resolve_snapshot_window(
        published_at.isoformat(), observed_at=published_at + timedelta(hours=24)
    )[0] == "24h"


def test_toutiao_collection_marks_today_pending_and_yesterday_available(app_settings) -> None:
    service = PerformanceService(app_settings, logging.getLogger("test"))
    records = [
        {
            "platform": "toutiao",
            "published_at": "2026-08-28T08:00:00",
        },
        {
            "platform": "toutiao",
            "published_at": "2026-08-27T08:00:00",
        },
    ]

    service._annotate_collection_semantics(
        records,
        observed_at=datetime(2026, 8, 28, 12, 0, 0),
        earnings_date=date(2026, 8, 27),
        target_publish_date=date(2026, 8, 27),
    )

    today, yesterday = records
    assert today["collection_date"] == "2026-08-28"
    assert today["published_date"] == "2026-08-28"
    assert today["earnings_date"] == "2026-08-27"
    assert today["availability_status"] == "pending_t1"
    assert today["analysis_eligible"] is False
    assert yesterday["published_date"] == "2026-08-27"
    assert yesterday["earnings_date"] == "2026-08-27"
    assert yesterday["availability_status"] == "available"
    assert yesterday["analysis_eligible"] is True


def test_missing_platform_revenue_is_unknown_not_zero(app_settings) -> None:
    service = PerformanceService(app_settings, logging.getLogger("test"))
    records = [{"platform": "toutiao", "published_at": "2026-08-27T08:00:00"}]

    service._annotate_collection_semantics(
        records,
        observed_at=datetime(2026, 8, 28, 12, 0, 0),
        earnings_date=date(2026, 8, 27),
        target_publish_date=date(2026, 8, 27),
    )

    assert records[0]["revenue_status"] == "unknown"
    assert records[0]["revenue_amount_known"] is False
    assert "platform_revenue" not in records[0]


def test_performance_metrics_retry_preserves_known_revenue(app_settings) -> None:
    service = PerformanceService(app_settings, logging.getLogger("test"))
    old_row = {
        "content_id": "ci-known-revenue",
        "platform": "toutiao",
        "title": "已回流收益",
        "published_date": "2026-08-27",
        "availability_status": "available",
        "revenue_status": "available",
        "platform_revenue_status": "available",
        "revenue_amount_known": True,
        "platform_revenue": 12.5,
        "revenue_amount": 12.5,
        "read_count": 100,
    }
    new_row = {
        **old_row,
        "availability_status": "available",
        "revenue_status": "unknown",
        "platform_revenue_status": "unknown",
        "revenue_amount_known": False,
        "platform_revenue": None,
        "revenue_amount": 0.0,
        "read_count": 80,
    }

    service._save_metrics({"generated_at": "2026-08-28T12:00:00", "records": [old_row], "platforms": {"toutiao": [old_row]}})
    service._save_metrics({"generated_at": "2026-08-28T13:00:00", "records": [new_row], "platforms": {"toutiao": [new_row]}})
    payload = json.loads((app_settings.output_dir / "performance_metrics.json").read_text(encoding="utf-8"))

    assert payload["records"][0]["platform_revenue"] == 12.5
    assert payload["records"][0]["revenue_amount_known"] is True
    assert payload["records"][0]["platform_revenue_status"] == "available"
    assert payload["platforms"]["toutiao"][0]["platform_revenue"] == 12.5


def test_growth_ledger_records_collection_date_and_csv_value(app_settings, tmp_path: Path) -> None:
    run_dir = tmp_path / "growth-run"
    publish_dir = run_dir / app_settings.review.publish_dirname
    publish_dir.mkdir(parents=True)
    (publish_dir / "publish_meta.json").write_text(
        json.dumps({"selected_title": "收益日期测试", "selected_topic": {}}),
        encoding="utf-8",
    )
    (publish_dir / "publish_result.json").write_text(
        json.dumps({"status": "published", "published_at": "2026-08-27T08:00:00"}),
        encoding="utf-8",
    )

    from toutiao_content_factory.services.growth_ops_service import GrowthOpsService

    service = GrowthOpsService(app_settings, logging.getLogger("test"))
    service.record_performance(
        run_dir=run_dir,
        platform="toutiao",
        snapshot_date="2026-08-27",
        snapshot_label="24h",
        snapshot_at="2026-08-28T12:00:00",
        published_date="2026-08-27",
        earnings_date="2026-08-27",
        read_count=100,
        impression_count=1000,
    )

    ledger = json.loads(service.performance_ledger_path.read_text(encoding="utf-8"))
    assert ledger[0]["metrics_latest"]["collection_date"] == "2026-08-28"
    with service.performance_csv_path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["collection_date"] == "2026-08-28"


def test_dashboard_reports_only_current_t1_earnings_date(app_settings) -> None:
    performance_dir = app_settings.output_dir / "growth_ops" / "performance"
    performance_dir.mkdir(parents=True)
    rows = [
        {
            "record_id": "old",
            "title": "历史文章",
            "platform": "toutiao",
            "published_at": "2026-08-26T08:00:00",
            "updated_at": "2026-08-27T12:00:00",
            "metrics_latest": {
                "collection_date": "2026-08-27",
                "earnings_date": "2026-08-26",
                "availability_status": "available",
                "analysis_eligible": True,
                "platform_revenue_status": "available",
                "platform_revenue": 99.0,
                "read_count": 100,
                "impression_count": 1000,
            },
        },
        {
            "record_id": "current",
            "title": "昨日文章",
            "platform": "toutiao",
            "published_at": "2026-08-27T08:00:00",
            "updated_at": "2026-08-28T12:00:00",
            "metrics_latest": {
                "collection_date": "2026-08-28",
                "earnings_date": "2026-08-27",
                "availability_status": "available",
                "analysis_eligible": True,
                "platform_revenue_status": "available",
                "platform_revenue": 8.0,
                "read_count": 120,
                "impression_count": 1000,
            },
        },
        {
            "record_id": "today",
            "title": "今日文章",
            "platform": "toutiao",
            "published_at": "2026-08-28T08:00:00",
            "updated_at": "2026-08-28T12:00:00",
            "metrics_latest": {
                "collection_date": "2026-08-28",
                "earnings_date": "2026-08-27",
                "availability_status": "pending_t1",
                "analysis_eligible": False,
                "revenue_status": "pending_t1",
                "platform_revenue_status": "pending_t1",
                "platform_revenue": None,
                "read_count": 200,
                "impression_count": 1000,
            },
        },
    ]
    (performance_dir / "performance_ledger.json").write_text(
        json.dumps(rows, ensure_ascii=False),
        encoding="utf-8",
    )
    (performance_dir / "performance_report.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-28T12:00:00",
                "summary": {
                    "published_count": 3,
                    "with_metrics_count": 3,
                    "analysis_eligible_count": 1,
                    "pending_t1_count": 1,
                    "collection_date": "2026-08-28",
                    "earnings_date": "2026-08-27",
                    "averages": {},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    feedback = WebViewService(app_settings).load_performance_feedback()

    assert feedback["collection_date"] == "2026-08-28"
    assert feedback["earnings_date"] == "2026-08-27"
    assert feedback["known_platform_revenue"] == 8.0
    assert feedback["platform_revenue_known_count"] == 1
    assert feedback["pending_t1_count"] == 1
    assert "不按 0 收益处理" in feedback["summary_message"]


def test_legacy_statusless_today_row_is_pending_t1_in_feedback_and_dashboard(app_settings) -> None:
    from toutiao_content_factory.services.growth_ops_service import GrowthOpsService

    legacy_record = {
        "platform": "toutiao",
        "published_at": "2026-08-28T08:00:00",
        "metrics_latest": {
            "published_date": "2026-08-28",
            "collection_date": "2026-08-28",
            "earnings_date": "2026-08-27",
            "analysis_eligible": True,
            "platform_revenue_status": "available",
            "platform_revenue": 42.0,
        },
    }

    feedback_loop = FeedbackLoopService(app_settings, logging.getLogger("test"))
    assert feedback_loop._is_analysis_eligible(legacy_record) is False

    growth = GrowthOpsService(app_settings, logging.getLogger("test"))
    assert growth._metric_row_is_pending_t1(legacy_record) is True
    assert growth._metric_row_is_analysis_eligible(legacy_record) is False

    performance_dir = app_settings.output_dir / "growth_ops" / "performance"
    performance_dir.mkdir(parents=True)
    (performance_dir / "performance_ledger.json").write_text(
        json.dumps(
            [
                {
                    "record_id": "legacy-today",
                    "title": "legacy today",
                    "platform": "toutiao",
                    "published_at": "2026-08-28T08:00:00",
                    "metrics_latest": legacy_record["metrics_latest"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (performance_dir / "performance_report.json").write_text(
        json.dumps(
            {
                "summary": {
                    "published_count": 1,
                    "with_metrics_count": 1,
                    "collection_date": "2026-08-28",
                    "earnings_date": "2026-08-27",
                    "averages": {},
                }
            }
        ),
        encoding="utf-8",
    )

    dashboard = WebViewService(app_settings).load_performance_feedback()
    assert dashboard["analysis_eligible_count"] == 0
    assert dashboard["pending_t1_count"] == 1
    assert dashboard["known_platform_revenue"] == 0.0
    assert dashboard["latest_records"][0]["platform_revenue_known"] is False


def test_pending_t1_retry_overrides_stale_available_collection_row(app_settings) -> None:
    service = PerformanceService(app_settings, logging.getLogger("test"))
    previous = {
        "title": "same-day article",
        "platform": "toutiao",
        "published_date": "2026-08-28",
        "availability_status": "available",
        "revenue_status": "available",
        "platform_revenue_status": "available",
        "revenue_amount_known": True,
        "platform_revenue": 42.0,
        "revenue_amount": 42.0,
    }
    current = {
        **previous,
        "availability_status": "pending_t1",
        "revenue_status": "pending_t1",
        "platform_revenue_status": "pending_t1",
        "revenue_amount_known": False,
        "platform_revenue": None,
        "revenue_amount": None,
        "earnings_date": "2026-08-27",
        "collection_date": "2026-08-28",
    }

    merged = service._merge_collected_rows([previous], [current])[0]

    assert merged["availability_status"] == "pending_t1"
    assert merged["platform_revenue_status"] == "pending_t1"
    assert merged["platform_revenue"] is None
    assert merged["revenue_amount_known"] is False


def test_local_feedback_fallback_excludes_today_toutiao_article(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    runs_dir = settings.output_dir / "runs"
    today = datetime.now().date()
    for name, published_day in (("today", today), ("yesterday", today - timedelta(days=1))):
        run_dir = runs_dir / name
        publish_dir = run_dir / "publish"
        publish_dir.mkdir(parents=True)
        (run_dir / "workflow_result.json").write_text(
            json.dumps({"title": f"{name} article", "article": "正文", "overall_score": 60}),
            encoding="utf-8",
        )
        (publish_dir / "publish_result.json").write_text(
            json.dumps(
                {
                    "status": "published",
                    "title": f"{name} article",
                    "published_at": f"{published_day.isoformat()}T08:00:00",
                }
            ),
            encoding="utf-8",
        )

    service = FeedbackLoopService(settings, logging.getLogger("test"))
    payload = service._build_performance_from_local_records()
    by_title = {record["title"]: record for record in payload["records"]}

    assert by_title["today article"]["availability_status"] == "pending_t1"
    assert by_title["yesterday article"]["availability_status"] == "available"
    metrics_path = service._normalize_for_evolution(payload)
    assert metrics_path is not None
    normalized = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert [record["title"] for record in normalized] == ["yesterday article"]


def test_feedback_normalization_falls_back_from_views_to_read_count(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    service = FeedbackLoopService(settings, logging.getLogger("test"))
    metrics_path = service._normalize_for_evolution(
        {
            "records": [
                {
                    "title": "测试文章",
                    "views": 14,
                    "impression_count": 681,
                    "interactions": 2,
                }
            ]
        }
    )

    assert metrics_path is not None
    payload = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    assert payload[0]["read_count"] == 14
    assert payload[0]["ctr"] == 2.06


def test_auto_opportunity_is_not_counted_as_verified_lead(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    service = LeadOpsService(settings, logging.getLogger("test"))
    service.sync_generated_opportunities(
        [
            {
                "id": "customer_pitch::unverified",
                "type": "customer_pitch",
                "priority": "high",
                "headline": "自动生成商机",
                "recommended_offer_price": "999-2999元 / 次",
            }
        ]
    )

    lead = service.list_leads()[0]
    summary = service.load_summary()
    assert lead["contact"].startswith("auto://")
    assert lead["contact_verified"] is False
    assert lead["lead_kind"] == "auto_opportunity"
    assert summary["real_leads"] == 0
    assert summary["auto_estimated_pipeline_revenue"] == 999.0


def test_verified_lead_and_payment_sync_to_content_revenue_ledger(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    attribution = RevenueAttributionService(settings.output_dir, logging.getLogger("test"))
    identity = attribution.build_content_identity(
        account_key=settings.account_key,
        platform="toutiao",
        run_dir=tmp_path / "lead-run",
        topic_title="储能订单",
        title="储能订单标题",
        published_at=datetime.now().isoformat(timespec="seconds"),
        cta_source_code="storage_consultation",
    )
    attribution.register_content(identity)

    service = LeadOpsService(settings, logging.getLogger("test"))
    lead = service.record_lead(
        name="客户A",
        contact="customer@example.com",
        intent="咨询",
        source="wechat_keyword",
        source_content_id=identity.content_id,
        platform="toutiao",
    )
    events = attribution.get_revenue_events(identity.content_id)
    assert lead["attribution_status"] == "explicit"
    assert [event.event_type for event in events] == ["lead"]

    service.mark_payment_received(lead_id=lead["id"], amount=1999.0)
    service.mark_payment_received(lead_id=lead["id"], amount=2499.0)
    events = attribution.get_revenue_events(identity.content_id)
    assert len(events) == 2
    assert {event.event_type for event in events} == {"lead", "deal"}
    assert next(event.amount for event in events if event.event_type == "deal") == 2499.0


def test_auto_opportunity_never_writes_content_revenue_event(tmp_path: Path) -> None:
    settings = _minimal_settings(tmp_path)
    attribution = RevenueAttributionService(settings.output_dir, logging.getLogger("test"))
    identity = attribution.build_content_identity(
        account_key=settings.account_key,
        platform="toutiao",
        run_dir=tmp_path / "auto-lead-run",
        topic_title="机器人订单",
        title="机器人标题",
        published_at=datetime.now().isoformat(timespec="seconds"),
        cta_source_code="robot_consultation",
    )
    attribution.register_content(identity)

    service = LeadOpsService(settings, logging.getLogger("test"))
    lead = service.record_lead(
        name="自动商机",
        contact="auto://robot",
        intent="咨询",
        source="auto_opportunity",
        source_content_id=identity.content_id,
        estimated_value=999.0,
    )
    service.mark_payment_received(lead_id=lead["id"], amount=999.0)

    assert attribution.get_revenue_events(identity.content_id) == []


def test_topic_soft_fallback_keeps_stopped_bucket_excluded(app_settings, tmp_path: Path) -> None:
    app_settings.revenue_optimization = SimpleNamespace(
        enabled=True,
        lookback_days=30,
        min_samples=2,
        weak_rpm_threshold=0.0,
        weak_read_rate_threshold=3.0,
        cooldown_weak_ratio=0.5,
        stop_weak_ratio=0.8,
        cooldown_penalty=12,
    )
    attribution = RevenueAttributionService(app_settings.output_dir, logging.getLogger("test"))
    for index in range(2):
        identity = attribution.build_content_identity(
            account_key=app_settings.account_key,
            platform="toutiao",
            run_dir=tmp_path / f"stopped-run-{index}",
            topic_title="机器人订单",
            title=f"机器人标题{index}",
            published_at=datetime.now().isoformat(timespec="seconds"),
            topic_bucket="robotics",
        )
        attribution.register_content(identity)
        attribution.record_cost(identity.content_id, CostRecord(api_cost=1.0))
        attribution.record_snapshot(
            identity.content_id,
            PerformanceSnapshot(
                snapshot_label="24h",
                snapshot_at=datetime.now().isoformat(timespec="seconds"),
                impression_count=1000,
                read_count=10,
            ),
        )

    automation = TopicAutomationService(app_settings, logging.getLogger("test"))
    topics = [
        TopicCandidate(
            title="机器人新订单",
            url="https://example.test/robot",
            source="test",
            source_type="rss",
            topic_bucket="robotics",
        ),
        TopicCandidate(
            title="储能新订单",
            url="https://example.test/storage",
            source="test",
            source_type="rss",
            topic_bucket="energy_storage",
        ),
    ]

    strict = automation._available_topics(topics)
    soft = automation._available_topics_soft(topics)
    assert [topic.title for topic in strict] == ["储能新订单"]
    assert [topic.title for topic in soft] == ["储能新订单"]


def test_auto_title_selection_skips_rule_007_candidate(tmp_path: Path, monkeypatch) -> None:
    settings = _minimal_settings(tmp_path)
    service = ReviewWorkflowService(settings, logging.getLogger("test"))
    bundle = ReviewBundle(
        recommended_titles=[
            "197.4亿背后，机器人的利润先流向谁",
            "人形机器人9分钟演示，离进工厂还差哪些证据",
        ],
        article="人形机器人完成9分钟演示，文章将说明进工厂仍需哪些证据。",
    )
    monkeypatch.setattr(service, "_load_bundle", lambda _run_dir: (bundle, None))

    selection = service.select_auto_title(tmp_path)

    assert selection["selected_title"] == "人形机器人9分钟演示，离进工厂还差哪些证据"
    assert selection["selected_title_index"] == 2
    assert selection["evaluations"][0]["passed"] is False
    assert selection["evaluations"][1]["passed"] is True


def test_auto_title_selection_requires_manual_review_when_all_candidates_fail(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _minimal_settings(tmp_path)
    service = ReviewWorkflowService(settings, logging.getLogger("test"))
    bundle = ReviewBundle(
        recommended_titles=["197.4亿背后，机器人的利润先流向谁"],
        article="机器人行业数据分析。",
    )
    monkeypatch.setattr(service, "_load_bundle", lambda _run_dir: (bundle, None))

    selection = service.select_auto_title(tmp_path)

    assert selection["selected_title"] == ""
    assert selection["selected_title_index"] == 0
    assert selection["reason"]


def test_auto_title_selection_skips_recently_published_title(tmp_path: Path, monkeypatch) -> None:
    settings = _minimal_settings(tmp_path)
    settings.publishing.enabled = True
    settings.publishing.auto_publish = True
    settings.publishing.draft_only = False
    settings.publishing.allow_duplicate_topics = False
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    (settings.output_dir / settings.publishing.history_filename).write_text(
        json.dumps(
            [
                {
                    "status": "submitted_for_review",
                    "published_at": datetime.now().isoformat(timespec="seconds"),
                    "title": "机器人热度之外，谁在拿走真正订单",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = ReviewWorkflowService(settings, logging.getLogger("test"))
    bundle = ReviewBundle(
        recommended_titles=[
            "机器人热度之外，谁在拿走真正订单",
            "特斯拉Optimus进入工厂，交付能力成新门槛",
        ],
        article="特斯拉Optimus进入工厂后，交付能力将成为机器人商业化的新门槛。",
    )
    monkeypatch.setattr(service, "_load_bundle", lambda _run_dir: (bundle, None))

    selection = service.select_auto_title(tmp_path)

    assert selection["selected_title"] == "特斯拉Optimus进入工厂，交付能力成新门槛"
    assert selection["selected_title_index"] == 2
    assert selection["evaluations"][0]["passed"] is False
    assert "近7天已发布" in selection["evaluations"][0]["issues"][-1]


def test_article_performance_from_dict_backfills_read_count_and_ctr() -> None:
    metric = ArticlePerformance.from_dict({"views": 14, "impression_count": 681})

    assert metric.read_count == 14
    assert metric.ctr == 2.06


def test_performance_collection_defaults_to_publishing_headless(app_settings, monkeypatch, tmp_path) -> None:
    settings = replace(
        app_settings,
        publishing=replace(app_settings.publishing, headless=True),
        # 2026-09-16：微信采集改为**跟随渠道开关**（135 退役后微信一直采集失败，
        # 纯噪音）。要断言"两个平台都采"，就得把渠道打开。
        wechat=replace(app_settings.wechat, enabled=True),
    )
    service = PerformanceService(settings, logging.getLogger("test"))
    observed: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        service,
        "_collect_toutiao_metrics",
        lambda *, headless: observed.append(("toutiao", headless)) or [],
    )
    monkeypatch.setattr(
        service,
        "_collect_wechat_metrics",
        lambda *, headless: observed.append(("wechat", headless)) or [],
    )
    monkeypatch.setattr(service, "_sync_attribution_snapshots", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "_build_automatic_reports", lambda: {})
    monkeypatch.setattr(service, "_save_metrics", lambda payload: tmp_path / "performance.json")

    service.collect_performance_data()

    assert observed == [("toutiao", True), ("wechat", True)]


def test_performance_collection_skips_wechat_when_the_channel_is_disabled(
    app_settings, monkeypatch, tmp_path
) -> None:
    """微信渠道停用后不该再起浏览器去采（它每次都会失败，是纯噪音）。"""
    settings = replace(
        app_settings,
        wechat=replace(app_settings.wechat, enabled=False),
    )
    service = PerformanceService(settings, logging.getLogger("test"))
    observed: list[str] = []

    monkeypatch.setattr(service, "_collect_toutiao_metrics", lambda *, headless: observed.append("toutiao") or [])
    monkeypatch.setattr(service, "_collect_wechat_metrics", lambda *, headless: observed.append("wechat") or [])
    monkeypatch.setattr(service, "_sync_attribution_snapshots", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "_build_automatic_reports", lambda: {})
    monkeypatch.setattr(service, "_save_metrics", lambda payload: tmp_path / "performance.json")

    service.collect_performance_data()

    assert observed == ["toutiao"]
