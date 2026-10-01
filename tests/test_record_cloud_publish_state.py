from __future__ import annotations

import json
from pathlib import Path

from scripts.record_cloud_publish_state import latest_today_record, today_records


def test_uncertain_submission_holds_same_day_slot(tmp_path) -> None:
    history = tmp_path / "publish_history.json"
    history.write_text(json.dumps([
        {"platform": "toutiao", "published_at": "2026-09-29T01:58:02",
         "status": "publish_uncertain", "title": "可能已提交"},
    ], ensure_ascii=False), encoding="utf-8")

    record = latest_today_record(history, "toutiao", "2026-09-29")
    assert record is not None and record["status"] == "publish_uncertain"
    assert latest_today_record(history, "toutiao", "2026-09-28") is None


def test_today_records_keeps_all_successful_posts_for_daily_count(tmp_path: Path) -> None:
    history = tmp_path / "publish_history.json"
    history.write_text(
        json.dumps(
            [
                {"published_at": "2026-10-02T08:00:00", "platform": "toutiao_weitoutiao", "status": "submitted_for_review", "title": "A"},
                {"published_at": "2026-10-02T12:30:00", "platform": "toutiao_weitoutiao", "status": "success", "title": "B"},
                {"published_at": "2026-10-01T18:00:00", "platform": "toutiao_weitoutiao", "status": "success", "title": "old"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    records = today_records(history, "toutiao_weitoutiao", "2026-10-02")

    assert [item["title"] for item in records] == ["A", "B"]
