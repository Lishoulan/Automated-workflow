from __future__ import annotations

import json
from pathlib import Path

from toutiao_content_factory.services.growth_feedback_service import (
    build_growth_feedback,
    topic_feedback_score,
    write_growth_outputs,
)


def _entry(gid: str, content_type: str, title: str, impressions: int, reads: int) -> dict:
    return {
        "gid": gid,
        "type": content_type,
        "title": title,
        "checkpoints": {
            "72h": {
                "impressions": impressions,
                "reads": reads,
                "comments": 0,
                "capture_delay_hours": 1,
            }
        },
    }


def test_feedback_boosts_winners_and_stops_repeated_low_performers(tmp_path: Path) -> None:
    checkpoints = {
        "works": {
            "1": _entry("1", "weitoutiao", "老人房产继承最后怎么处理", 1200, 60),
            "2": _entry("2", "weitoutiao", "房子过户后子女为何起争执", 1000, 45),
            "3": _entry("3", "weitoutiao", "老人借钱给亲戚没打借条", 100, 0),
            "4": _entry("4", "weitoutiao", "亲戚借钱一直不还怎么办", 80, 0),
        }
    }
    snapshot = {"summary": {"weitoutiao": {"works": 4, "impressions": 2380, "reads": 105}}}

    feedback = build_growth_feedback(snapshot, checkpoints, generated_at="2026-09-30T08:00:00+08:00")

    weights = feedback["topic_feedback"]["weights"]
    assert weights["房产继承"] > 0
    assert weights["借贷纠纷"] < 0
    stopped_score, reasons = topic_feedback_score("老人借钱给亲戚没打借条", feedback)
    assert stopped_score == -100
    assert reasons == ["72小时低表现标题停止复用"]

    outputs = write_growth_outputs(tmp_path, feedback)
    assert all(path.exists() for path in outputs.values())
    assert "report_json" not in outputs
    assert not (tmp_path / "daily_growth_report.json").exists()
    assert "自动止损" in outputs["report_md"].read_text(encoding="utf-8")
    assert json.loads(outputs["feedback"].read_text(encoding="utf-8"))["sample_count"] == 4


def test_feedback_builds_creative_calibration_only_with_enough_samples() -> None:
    works = {
        str(index): _entry(
            str(index),
            "article",
            f"老人房产纠纷最后到底怎么处理这件事{index}",
            1000 + index * 10,
            20 + index,
        )
        for index in range(1, 4)
    }

    feedback = build_growth_feedback({}, {"works": works})

    calibration = feedback["creative_calibration"]
    assert calibration["status"] == "active"
    assert calibration["minimum_samples_per_signal"] == 3
    assert "mobile_length" in calibration["ready_signals"]
    assert calibration["article_samples"] == 3
    assert "mobile_length" in calibration["signal_adjustments"]
    assert calibration["evidence"]["mobile_length"]["samples"] == 3


def test_feedback_marks_late_samples_and_groups_duplicate_stop_titles() -> None:
    repeated = "老人借钱给亲戚没打借条"
    first = _entry("1", "weitoutiao", repeated, 100, 0)
    second = _entry("2", "weitoutiao", repeated, 80, 0)
    first["checkpoints"]["72h"]["capture_delay_hours"] = 120
    second["checkpoints"]["72h"]["capture_delay_hours"] = 121

    feedback = build_growth_feedback({}, {"works": {"1": first, "2": second}})

    assert "迟到回收 2 条" in feedback["checkpoint_used"]
    assert len(feedback["stop_loss"]) == 1
    assert feedback["stop_loss"][0]["repeat_count"] == 2
    assert feedback["stop_loss"][0]["impressions"] == 180
    assert len(feedback["topic_feedback"]["stop_titles"]) == 1
    assert feedback["topic_feedback"]["weights"] == {}
    assert feedback["creative_calibration"]["status"] == "collecting"
