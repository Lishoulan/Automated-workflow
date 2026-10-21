"""所有表现数据消费方必须解析到同一个文件。

生产证据（2026-10-21 定时运行 run 39525806166 的 artifact）：

    automation/performance_metrics.json                       -> 不存在
    output/accounts/robot-notes/performance_metrics.json      -> 存在（13846 字节）

而下面这些消费方原先各自把路径硬拼成 `automation_dir / performance_metrics.json`：

    scripts/run_feedback_loop.py            (报告里展示的 metrics_path 是错的)
    scripts/stage_gate.py                   (_works() 恒 0 条 -> 阶段判定失真)
    services/feedback_loop_service.py       (get_title_few_shot / get_writer_few_shot 恒空)

本文件锁住「只有一处解析」这件事：任何消费方都不得再自己拼路径。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from toutiao_content_factory.services import performance_hints


class _Settings:
    """最小 settings 桩：只有三个路径属性，与真实 AppSettings 同名。"""

    def __init__(self, root: Path) -> None:
        self.output_dir = root
        self.automation_dir = root / "automation"
        self.project_root = root.parent


def _write_metrics(root: Path, records: list[dict], *, where: str = "root") -> Path:
    target = root / "performance_metrics.json" if where == "root" \
        else root / "automation" / "performance_metrics.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"records": records}, ensure_ascii=False), encoding="utf-8")
    return target


def test_metrics_path_resolves_to_the_production_location(tmp_path: Path) -> None:
    """生产只写根目录时必须解析到根目录，而不是 automation/。"""
    settings = _Settings(tmp_path)
    expected = _write_metrics(tmp_path, [{"title": "真实作品", "read_count": 9}])

    assert performance_hints.metrics_path(settings) == expected


def test_metrics_path_prefers_legacy_automation_file_when_present(tmp_path: Path) -> None:
    """历史 automation/ 文件存在时不得被遮蔽（保持既有行为）。"""
    settings = _Settings(tmp_path)
    legacy = _write_metrics(tmp_path, [{"title": "旧位置", "read_count": 1}], where="automation")

    assert performance_hints.metrics_path(settings) == legacy


def test_metrics_path_falls_back_to_legacy_path_when_nothing_exists(tmp_path: Path) -> None:
    """都不存在时返回稳定的历史位置，便于报告展示；不得抛异常。"""
    settings = _Settings(tmp_path)
    resolved = performance_hints.metrics_path(settings)
    assert resolved.name == "performance_metrics.json"
    assert resolved.parent.name == "automation"


def test_stage_gate_works_reads_the_resolved_file(tmp_path: Path) -> None:
    """stage_gate._works() 必须看到真实记录，而不是 0 条。"""
    sys_path = Path(__file__).resolve().parents[1] / "scripts"
    import sys

    if str(sys_path) not in sys.path:
        sys.path.insert(0, str(sys_path))
    import stage_gate

    settings = _Settings(tmp_path)
    _write_metrics(tmp_path, [
        {"title": "作品A", "impression_count": 955, "read_count": 9},
        {"title": "作品B", "impression_count": 120, "read_count": 1},
    ])

    assert len(stage_gate._works(settings)) == 2


def test_run_feedback_loop_reports_the_resolved_path(tmp_path: Path) -> None:
    """run_feedback_loop 报告里展示的 metrics_path 必须与实际位置一致。"""
    sys_path = Path(__file__).resolve().parents[1] / "scripts"
    import sys

    if str(sys_path) not in sys.path:
        sys.path.insert(0, str(sys_path))
    import run_feedback_loop

    settings = _Settings(tmp_path)
    settings.account_key = "robot_notes"
    _write_metrics(tmp_path, [{"title": "作品A", "impression_count": 955, "read_count": 9}])

    status = run_feedback_loop.build_status(settings)
    assert Path(status["metrics_path"]) == tmp_path / "performance_metrics.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative",
    [
        "scripts/run_feedback_loop.py",
        "scripts/stage_gate.py",
        "src/toutiao_content_factory/services/feedback_loop_service.py",
        "src/toutiao_content_factory/services/performance_hints.py",
    ],
)
def test_no_consumer_hardcodes_the_automation_metrics_path(relative: str) -> None:
    """任何消费方都不得再出现 `automation_dir / "performance_metrics.json"` 的硬拼。

    唯一的例外是 performance_hints 内部的候选列表 —— 那是集中解析点本身。
    """
    path = _repo_root() / relative
    source = path.read_text(encoding="utf-8")
    hardcoded = re.findall(
        r'automation_dir\s*/\s*"performance_metrics\.json"', source
    )
    if relative.endswith("performance_hints.py"):
        # 集中解析点允许引用一次常量文件名，但不允许拼 automation_dir 字面量。
        assert not hardcoded, (
            f"{relative} 应当通过 METRICS_FILENAME 常量拼路径，不要硬编码文件名"
        )
    else:
        assert not hardcoded, (
            f"{relative} 仍在硬拼 automation_dir/performance_metrics.json；"
            "请改用 performance_hints.metrics_path(settings)"
        )
