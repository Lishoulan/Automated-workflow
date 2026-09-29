from __future__ import annotations

import json

from scripts.record_cloud_publish_state import latest_today_record


def test_uncertain_submission_holds_same_day_slot(tmp_path) -> None:
    history = tmp_path / "publish_history.json"
    history.write_text(json.dumps([
        {"platform": "toutiao", "published_at": "2026-09-29T01:58:02",
         "status": "publish_uncertain", "title": "可能已提交"},
    ], ensure_ascii=False), encoding="utf-8")

    record = latest_today_record(history, "toutiao", "2026-09-29")
    assert record is not None and record["status"] == "publish_uncertain"
    assert latest_today_record(history, "toutiao", "2026-09-28") is None
