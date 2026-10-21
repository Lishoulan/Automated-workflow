"""The closed loop must find production performance data wherever it is written.

Production evidence (artifact of scheduled Daily Run on 2026-10-21) shows
`performance_metrics.json` sitting in the account output root
(`output/accounts/<slug>/performance_metrics.json`), while only
`automation/` holds run bookkeeping (last_daily_run_result.json, …).
`performance_hints.load_records()` used to look only inside `automation/`, so the
loop reported `record_count: 0` even though real read-bearing records existed —
"automatic optimisation" was silently fed nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

from toutiao_content_factory.services import performance_hints


class _Settings:
    def __init__(self, root: Path) -> None:
        self.output_dir = root
        self.automation_dir = root / "automation"


def _write(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"generated_at": "2026-10-21T09:16:39", "records": records},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def test_records_are_found_in_the_account_output_root(tmp_path: Path) -> None:
    """The production location must be read, not only automation/."""
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", [
        {"title": "真实作品一", "impression_count": 1200, "read_count": 40},
        {"title": "真实作品二", "impression_count": 900, "read_count": 12},
    ])

    records = performance_hints.load_records(settings)

    assert [row["title"] for row in records] == ["真实作品一", "真实作品二"]
    assert performance_hints.loop_status(settings)["record_count"] == 2


def test_automation_location_still_wins_when_it_holds_records(tmp_path: Path) -> None:
    """A real legacy file in automation/ must not be shadowed."""
    settings = _Settings(tmp_path)
    _write(tmp_path / "automation" / "performance_metrics.json", [
        {"title": "旧位置作品", "impression_count": 500, "read_count": 7},
    ])
    _write(tmp_path / "performance_metrics.json", [
        {"title": "根目录作品", "impression_count": 900, "read_count": 30},
    ])

    records = performance_hints.load_records(settings)

    assert [row["title"] for row in records] == ["旧位置作品"]


def test_missing_records_yield_empty_list_without_raising(tmp_path: Path) -> None:
    settings = _Settings(tmp_path)
    assert performance_hints.load_records(settings) == []
    assert performance_hints.title_hints(settings) == ""
    assert performance_hints.writer_hints(settings) == ""
    assert performance_hints.loop_status(settings)["mode"] == "insufficient"


def test_malformed_json_is_ignored_rather_than_crashing(tmp_path: Path) -> None:
    settings = _Settings(tmp_path)
    (tmp_path / "performance_metrics.json").write_text("{not json", encoding="utf-8")
    assert performance_hints.load_records(settings) == []


def test_records_without_a_title_are_dropped(tmp_path: Path) -> None:
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", [
        {"title": "有标题", "impression_count": 600, "read_count": 5},
        {"title": "   ", "impression_count": 600, "read_count": 5},
        {"impression_count": 600, "read_count": 5},
    ])
    assert [row["title"] for row in performance_hints.load_records(settings)] == ["有标题"]


def test_absolute_tier_still_requires_100_reads(tmp_path: Path) -> None:
    """The anti-pollution gate must survive the path fix."""
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", [
        {"title": f"低表现{i}", "impression_count": 1000, "read_count": 3}
        for i in range(6)
    ])
    status = performance_hints.loop_status(settings)
    # 6 records but none reaches 100 reads -> relative tier, never "absolute".
    assert status["mode"] == "relative"
    assert status["active"] is True


def test_no_hint_is_injected_below_the_relative_minimum(tmp_path: Path) -> None:
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", [
        {"title": "单条样例", "impression_count": 800, "read_count": 2},
    ])
    assert performance_hints.title_hints(settings) == ""
    assert performance_hints.loop_status(settings)["mode"] == "insufficient"
