from __future__ import annotations

import json
from pathlib import Path

from toutiao_content_factory.services.growth_feedback_service import (
    build_growth_feedback,
    render_daily_growth_report,
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
    assert weights["房产继承"] == 0
    assert weights["借贷纠纷"] == 0
    assert feedback["topic_feedback"]["status"] == "collecting"
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
        for index in range(1, 9)
    }

    feedback = build_growth_feedback({}, {"works": works})

    calibration = feedback["creative_calibration"]
    assert calibration["status"] == "active"
    assert calibration["minimum_samples_per_signal"] == 3
    assert "mobile_length" in calibration["ready_signals"]
    assert calibration["article_samples"] == 8
    assert "mobile_length" in calibration["signal_adjustments"]
    assert calibration["evidence"]["mobile_length"]["samples"] == 8


def test_zero_read_articles_with_impressions_still_calibrate_title_signals() -> None:
    works = {
        str(index): _entry(
            str(index),
            "article",
            f"老人养老政策为何引发讨论这件事{index}",
            500 + index * 20,
            0,
        )
        for index in range(1, 9)
    }

    feedback = build_growth_feedback({}, {"works": works})

    calibration = feedback["creative_calibration"]
    assert calibration["status"] == "active"
    assert calibration["article_samples"] == 8
    assert calibration["positive_read_article_samples"] == 0
    assert calibration["signal_adjustments"]["question_gap"] < 0


def test_feedback_marks_late_samples_and_groups_duplicate_stop_titles() -> None:
    repeated = "老人借钱给亲戚没打借条"
    first = _entry("1", "weitoutiao", repeated, 100, 0)
    second = _entry("2", "weitoutiao", repeated, 80, 0)
    first["checkpoints"]["72h"]["capture_delay_hours"] = 120
    second["checkpoints"]["72h"]["capture_delay_hours"] = 121

    feedback = build_growth_feedback({}, {"works": {"1": first, "2": second}})

    assert "迟到回收 2 条" in feedback["checkpoint_used"]
    assert feedback["stop_loss"] == []
    assert len(feedback["late_observations"]) == 2
    assert feedback["topic_feedback"]["stop_titles"] == []
    assert feedback["topic_feedback"]["weights"] == {}
    assert feedback["creative_calibration"]["status"] == "collecting"


def test_topic_weights_require_thirty_qualified_samples_and_five_per_tag():
    works = {str(i): _entry(str(i), "weitoutiao", f"房产继承事实{i}" if i < 5 else f"现场记录{i}", 1000, 80 if i < 5 else 10) for i in range(30)}
    feedback = build_growth_feedback({}, {"works": works})
    assert feedback["topic_feedback"]["weights"]["房产继承"] > 0
    works["29"]["checkpoints"]["72h"]["capture_delay_hours"] = 100
    feedback = build_growth_feedback({}, {"works": works})
    assert feedback["topic_feedback"]["qualified_samples"] == 29
    assert feedback["topic_feedback"]["weights"]["房产继承"] == 0


def test_exposure_alone_cannot_recommend_the_real_low_read_work():
    weak = _entry("taishan", "weitoutiao", "泰山景区否认躲雨收费传闻", 370, 1)
    weak["checkpoints"]["24h"] = weak["checkpoints"].pop("72h")
    other = _entry("other", "weitoutiao", "其他同期作品", 30, 0)
    other["checkpoints"]["24h"] = other["checkpoints"].pop("72h")
    good = _entry("property", "weitoutiao", "业主告物业要停车费账本，法院这么判", 1600, 75)
    comparable = _entry("same-window", "weitoutiao", "同窗口另一作品", 800, 10)

    feedback = build_growth_feedback({}, {"works": {item["gid"]: item for item in (weak, other, good, comparable)}})

    assert [item["gid"] for item in feedback["winners"]] == ["property"]
    assert feedback["topic_feedback"]["status"] == "collecting"
    report = render_daily_growth_report(feedback)
    assert "72h，展现 1600，阅读 75" in report
    assert "不代表题材或写法已证明有效" in report
    assert "继续放大" not in report


def test_high_rate_with_low_read_count_or_exposure_is_not_a_recommendation():
    works = [
        _entry("tiny", "weitoutiao", "很少展现的高阅读率", 10, 3),
        _entry("large", "weitoutiao", "更大展现但阅读不足", 1000, 20),
        _entry("middle", "weitoutiao", "同窗口中位作品", 500, 30),
        _entry("strong", "weitoutiao", "阅读量和阅读率都较高", 1000, 80),
    ]
    feedback = build_growth_feedback({}, {"works": {item["gid"]: item for item in works}})
    assert [item["gid"] for item in feedback["winners"]] == ["strong"]


def test_opening_experiment_keeps_windows_separate_and_excludes_late_captures():
    works = {}
    for i in range(30):
        work = _entry(str(i), "weitoutiao", f"真实事件{i}", 100, 5)
        work["content_experiment"] = {"id": "story_opening_v1", "variant": "result_first" if i < 15 else "situation_first"}
        work["checkpoints"]["24h"] = {"impressions": 50, "reads": 1, "capture_delay_hours": 0}
        works[str(i)] = work
    feedback = build_growth_feedback({}, {"works": works})
    experiment = feedback["opening_experiment"]
    assert experiment["status"] == "comparison_ready"
    assert experiment["groups"][0]["read_rate_pct"] == 2
    assert experiment["groups"][2]["read_rate_pct"] == 5
    works["0"]["checkpoints"]["72h"]["capture_delay_hours"] = 100
    assert build_growth_feedback({}, {"works": works})["opening_experiment"]["status"] == "collecting"
