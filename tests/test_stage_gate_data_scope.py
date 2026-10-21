"""阶段判定必须看到与闭环相同的数据范围。

生产实测（2026-10-21 artifact）：

    performance_metrics.json   ->  1 条记录
    growth_checkpoints.json    -> 31 条作品（19 条有真实阅读）

`stage_gate._works()` 只读 `performance_metrics.json`，于是：

    STAGE2_SAMPLE = 10（需要 10 篇有效展现样本）
    _works() 只返回 1 条
    -> _stage_replicable() 永远返回 BLOCKED「有效展现样本 1 篇，不足 10 篇」

而同一份数据在闭环侧（`performance_hints.load_records`，已修）能看到 30 条。
阶段判定因此长期低估实际数据量，把「样本足」误判成「样本不足」。

本文件锁住：`_works()` 与闭环使用同一数据范围，且保留它需要的字段语义。
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]


def _load_stage_gate():
    scripts = REPO / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    if str(REPO / "src") not in sys.path:
        sys.path.insert(0, str(REPO / "src"))
    import stage_gate

    return stage_gate


class _Settings:
    def __init__(self, root: Path) -> None:
        self.output_dir = root
        self.automation_dir = root / "automation"
        self.project_root = root.parent
        self.account_key = "robot_notes"


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _works_fixture(root: Path, count: int) -> None:
    _write(root / "growth_checkpoints.json", {
        "works": {
            f"gid{i}": {
                "title": f"作品{i}",
                "type": "weitoutiao",
                "created_at": "2026-10-09T19:28:17+08:00",
                "latest": {"impressions": 6000 + i * 100, "reads": 60, "comments": 0},
            }
            for i in range(count)
        }
    })


def test_works_sees_checkpoint_records_when_primary_is_thin(tmp_path: Path) -> None:
    """primary 只有 1 条时，_works 必须能看到 checkpoints 的 30 条。"""
    stage_gate = _load_stage_gate()
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", {
        "records": [{"title": "唯一一条", "impression_count": 955, "read_count": 9}]
    })
    _works_fixture(tmp_path, 30)

    works = stage_gate._works(settings)

    assert len(works) >= 10, (
        f"_works() 必须看到 checkpoints 的数据（期望 >=10 条，实际 {len(works)}）"
    )


def test_stage_replicable_is_no_longer_blocked_by_a_blind_spot(tmp_path: Path) -> None:
    """有 30 条有效展现样本时，阶段二不应再报「样本不足 10 篇」。"""
    stage_gate = _load_stage_gate()
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", {
        "records": [{"title": "唯一一条", "impression_count": 955, "read_count": 9}]
    })
    _works_fixture(tmp_path, 30)

    result = stage_gate._stage_replicable(settings)

    assert result["status"] != "BLOCKED", (
        f"30 条有效样本不应判 BLOCKED，实际: {result}"
    )
    assert result["evidence"]["sample"] >= 10, result["evidence"]


def test_works_still_returns_plain_records_with_expected_fields(tmp_path: Path) -> None:
    """_works 的返回形状必须仍能被 _impressions() 消费。"""
    stage_gate = _load_stage_gate()
    settings = _Settings(tmp_path)
    _works_fixture(tmp_path, 3)

    works = stage_gate._works(settings)
    assert works, "应返回记录"
    impressions = [stage_gate._impressions(row) for row in works]
    assert all(value > 0 for value in impressions), impressions


def test_works_prefers_primary_source_when_it_is_healthy(tmp_path: Path) -> None:
    """primary 记录充足时不应混入 checkpoints（与闭环约定一致）。"""
    stage_gate = _load_stage_gate()
    settings = _Settings(tmp_path)
    _write(tmp_path / "performance_metrics.json", {
        "records": [
            {"title": f"主源{i}", "impression_count": 900, "read_count": 20}
            for i in range(12)
        ]
    })
    _works_fixture(tmp_path, 30)

    works = stage_gate._works(settings)
    titles = [str(row.get("title", "")) for row in works]
    assert all(title.startswith("主源") for title in titles), titles[:3]
