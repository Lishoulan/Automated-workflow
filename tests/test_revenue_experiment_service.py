from __future__ import annotations

import json
import logging
from pathlib import Path

from toutiao_content_factory.services.revenue_experiment_service import (
    RevenueExperimentService,
)


def _account_snapshot() -> dict:
    daily = []
    for day in range(1, 31):
        daily.append(
            {
                "day": f"2026-09-{day:02d}",
                "pending": False,
                "total": 0.1,
                "article": 0.02,
                "weitoutiao": 0.08,
                "video": 0.0,
            }
        )
    return {
        "generated_at": "2026-09-30T22:00:00+08:00",
        "account_key": "robot_notes",
        "account": {"fans": 16},
        "quality": {"credit_score": 100, "is_low_quality": False},
        "benefits": {
            "semantics_ok": True,
            "revenue_benefits": {
                "文章创作收益": True,
                "微头条创作收益": True,
            },
        },
        "revenue": {
            "currency": "CNY",
            "total": 36.33,
            "income": {"daily": daily, "window_income": 3.0},
        },
    }


def _growth_snapshot() -> dict:
    works = [
        {
            "gid": str(index),
            "type": "weitoutiao",
            "impressions": 1200 if index < 8 else 500,
            "reads": 50,
        }
        for index in range(10)
    ]
    works.extend(
        {
            "gid": f"a{index}",
            "type": "article",
            "impressions": 1000,
            "reads": 10,
        }
        for index in range(2)
    )
    return {
        "observed_at": "2026-09-30T22:00:00+08:00",
        "window_days": 30,
        "summary": {
            "weitoutiao": {
                "works": 10,
                "impressions": 10600,
                "reads": 500,
                "zero_read_works": 0,
            },
            "article": {
                "works": 2,
                "impressions": 2000,
                "reads": 20,
                "zero_read_works": 0,
            },
        },
        "works": works,
    }


def test_readiness_reports_active_rights_and_credit() -> None:
    service = RevenueExperimentService(Path("output"), logging.getLogger("test"))

    result = service.build_readiness(_account_snapshot())

    assert result["status"] == "ready"
    assert result["checks"]["article_revenue_benefit"] == "ready"
    assert result["checks"]["weitoutiao_revenue_benefit"] == "ready"
    assert result["credit_score"] == 100
    assert result["blockers"] == []


def test_dashboard_keeps_unknown_per_content_revenue_out_of_rpm() -> None:
    service = RevenueExperimentService(Path("output"), logging.getLogger("test"))
    account = _account_snapshot()
    readiness = service.build_readiness(account)
    performance = {
        "records": [
            {
                "platform": "toutiao_weitoutiao",
                "title": "未知收益作品",
                "url": "https://www.toutiao.com/item/1",
                "publish_date": "2026-09-29",
                "impression_count": 1000,
                "read_count": 50,
                "platform_revenue": None,
                "revenue_status": "unknown",
            },
            {
                "platform": "toutiao_weitoutiao",
                "title": "精确收益作品",
                "url": "https://www.toutiao.com/item/2",
                "publish_date": "2026-09-28",
                "impression_count": 2000,
                "read_count": 100,
                "platform_revenue": 1.5,
                "platform_revenue_status": "available",
                "revenue_amount_known": True,
            },
        ]
    }

    dashboard = service.build_dashboard(
        account_snapshot=account,
        growth_snapshot=_growth_snapshot(),
        growth_feedback={
            "benchmarks": {"weitoutiao": {"samples": 3}},
            "creative_calibration": {"article_samples": 4},
        },
        performance=performance,
        readiness=readiness,
    )

    attribution = dashboard["per_content_attribution"]
    assert attribution["exact_count"] == 1
    assert attribution["unknown_count"] == 1
    assert attribution["coverage_pct"] == 50.0
    unknown, exact = attribution["rows"]
    assert unknown["revenue"] is None
    assert unknown["revenue_per_1000_impressions"] is None
    assert exact["revenue_per_1000_impressions"] == 0.75
    assert dashboard["formats"]["weitoutiao"]["revenue_per_1000_impressions"] > 0
    targets = dashboard["experiment"]["targets"]
    assert targets[0]["current"] == 3
    assert targets[1]["current"] == 4


def test_build_and_write_creates_operator_dashboard(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    account_path = output_dir / "growth_ops" / "performance" / "account_snapshot.json"
    account_path.parent.mkdir(parents=True)
    account_path.write_text(
        json.dumps({"latest": _account_snapshot()}, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "performance_metrics.json").write_text(
        json.dumps({"records": []}), encoding="utf-8"
    )
    service = RevenueExperimentService(output_dir, logging.getLogger("test"))

    outputs = service.build_and_write(
        growth_snapshot=_growth_snapshot(),
        growth_feedback={"sample_count": 12},
    )

    assert all(path.exists() for path in outputs.values())
    payload = json.loads(outputs["dashboard"].read_text(encoding="utf-8"))
    assert payload["account_key"] == "robot_notes"
    assert payload["experiment"]["status"] == "collecting"
    markdown = outputs["dashboard_md"].read_text(encoding="utf-8")
    assert "30天写作收益实验看板" in markdown
    assert "不会把账号日收益按阅读量猜测分摊" in markdown
