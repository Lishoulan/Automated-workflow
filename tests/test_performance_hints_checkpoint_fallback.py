"""闭环必须能在 primary 数据源稀疏时回退到 growth_checkpoints。

生产证据（2026-10-21 定时运行 `39525806166` 的 artifact）：

    growth_checkpoints.json   -> 31 条作品，其中 19 条有真实阅读
    performance_metrics.json  ->  1 条

`performance_hints.load_records()` 只读 performance_metrics.json，于是闭环
永远停在 `record_count: 1 / mode: insufficient`、从不注入任何参考样本，
尽管同一目录里就有 31 条带 24h/72h 观测的作品。

growth_checkpoints 的字段名与 performance_metrics 不同（`latest.reads` /
`latest.impressions` 而不是 `read_count` / `impression_count`），所以映射必须
显式做，不能直接把整条记录丢进去当范例。

本文件锁住三件事：
1. primary 源不足时能回退到 checkpoints；
2. 回退后记录被映射成统一字段，`select_examples` 能正确分档；
3. primary 源充足时**不**回退（避免两个源混用导致口径漂移）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from toutiao_content_factory.services import performance_hints


class _Settings:
    def __init__(self, root: Path) -> None:
        self.output_dir = root
        self.automation_dir = root / "automation"
        self.project_root = root.parent


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _metrics(records: list[dict]) -> dict:
    return {"records": records}


def _checkpoints(works: dict) -> dict:
    return {"works": works, "generated_at": "2026-10-21T09:16:17"}


def _work(title: str, impressions: int, reads: int, kind: str = "weitoutiao") -> dict:
    return {
        "title": title,
        "type": kind,
        "created_at": "2026-10-09T19:28:17+08:00",
        "latest": {"impressions": impressions, "reads": reads, "comments": 0,
                   "age_hours": 277.8},
        "checkpoints": {"24h": {"impressions": impressions // 2, "reads": reads // 2}},
    }


def test_falls_back_to_growth_checkpoints_when_primary_is_too_sparse(tmp_path: Path) -> None:
    """primary 只有 1 条时，必须用 checkpoints 里更完整的作品补齐。"""
    settings = _Settings(tmp_path)
    _write_json(tmp_path / "performance_metrics.json",
                _metrics([{"title": "唯一一条", "impression_count": 955, "read_count": 9}]))
    _write_json(tmp_path / "growth_checkpoints.json", _checkpoints({
        f"gid{i}": _work(f"作品{i}", 2500 + i, i) for i in range(6)
    }))

    records = performance_hints.load_records(settings)

    assert len(records) >= performance_hints.MIN_RECORDS_FOR_RELATIVE, (
        f"回退后应至少 {performance_hints.MIN_RECORDS_FOR_RELATIVE} 条，实际 {len(records)}"
    )
    # 统一字段：checkpoints 的 latest.reads 必须映射到 read_count
    sample = next(row for row in records if "作品" in str(row.get("title")))
    assert sample["read_count"] > 0 or sample["impression_count"] > 0
    assert all(str(row.get("title", "")).strip() for row in records)


def test_checkpoint_records_use_the_documented_field_mapping(tmp_path: Path) -> None:
    """latest.impressions/reads 必须映射成 impression_count/read_count。"""
    settings = _Settings(tmp_path)
    _write_json(tmp_path / "growth_checkpoints.json", _checkpoints({
        "gidA": _work("测试作品", impressions=2599, reads=42),
        **{f"gid{i}": _work(f"填充{i}", 100, 1) for i in range(5)},
    }))

    records = performance_hints.load_records(settings)
    target = next(row for row in records if row.get("title") == "测试作品")

    assert target["impression_count"] == 2599
    assert target["read_count"] == 42


def test_fallback_never_overrides_a_healthy_primary_source(tmp_path: Path) -> None:
    """primary 已够用时不得混入 checkpoints，避免两个源口径漂移。"""
    settings = _Settings(tmp_path)
    primary = [
        {"title": f"主源{i}", "impression_count": 1000, "read_count": 5}
        for i in range(6)
    ]
    _write_json(tmp_path / "performance_metrics.json", _metrics(primary))
    _write_json(tmp_path / "growth_checkpoints.json", _checkpoints({
        f"gid{i}": _work(f"快照{i}", 9000, 900) for i in range(20)
    }))

    records = performance_hints.load_records(settings)

    assert len(records) == len(primary)
    assert all(str(row.get("title", "")).startswith("主源") for row in records)


def test_fallback_is_disabled_by_default_threshold_constant(tmp_path: Path) -> None:
    """回退阈值必须就是相对档下限，不能另立一套数字。"""
    assert performance_hints.FALLBACK_MIN_PRIMARY_RECORDS == performance_hints.MIN_RECORDS_FOR_RELATIVE


def test_no_records_anywhere_still_yields_empty_without_raising(tmp_path: Path) -> None:
    settings = _Settings(tmp_path)
    assert performance_hints.load_records(settings) == []
    assert performance_hints.loop_status(settings)["mode"] == "insufficient"


def test_corrupt_checkpoints_do_not_break_the_primary_path(tmp_path: Path) -> None:
    settings = _Settings(tmp_path)
    _write_json(tmp_path / "performance_metrics.json",
                _metrics([{"title": "主源", "impression_count": 100, "read_count": 1}]))
    (tmp_path / "growth_checkpoints.json").write_text("{broken", encoding="utf-8")

    records = performance_hints.load_records(settings)
    assert [row["title"] for row in records] == ["主源"]
