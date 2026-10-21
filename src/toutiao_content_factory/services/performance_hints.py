"""把真实表现数据转成生成侧的提示片段（闭环最后一段）。

## 为什么需要这个模块

`FeedbackLoopService.get_title_few_shot()` / `get_writer_few_shot()` 早已实现同样的逻辑，
但全仓 **零调用**（2026-10-21 核查）。也就是说数据采集、归一化、规则提取都在跑，
唯独「把结果喂回生成」这一步断了 —— 所谓自动优化一直是自说自话。

这里不复用 Service 实例，因为 Agent 层构造 FeedbackLoopService 会牵出
PerformanceService / EvolutionAgent / OpenAI 服务，既重又可能形成循环依赖。
改成模块级纯函数，只读一个 JSON 文件。

## 两道防污染闸

1. **绝对档**：阅读量 >= MIN_READS_FOR_FEWSHOT（100）才可当范本。
   这是 2026-09-04 那次自污染事故留下的线 —— 当时 0 阅读的模板标题被当成
   「高点击率标题」注入 prompt，反过来训出更多同类垃圾。
2. **相对档**：样本不足绝对档时，取展现量最高的几篇，但要求
   `>= MIN_RECORDS_FOR_RELATIVE`（5）条记录才启用，且在提示里显式标注
   「样本不足，仅作相对参考」。少于 5 条时直接返回空字符串 ——
   拿 1 条数据当规律，比没有规律更糟。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MIN_READS_FOR_FEWSHOT = 100
MIN_RECORDS_FOR_RELATIVE = 5
MAX_EXAMPLES = 3


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


METRICS_FILENAME = "performance_metrics.json"
CHECKPOINTS_FILENAME = "growth_checkpoints.json"

#: primary 源少于这个数时才回退到 growth_checkpoints。
#: 复用相对档下限，避免另立一套数字（两个阈值一旦分叉就会产生"够用但不回退"
#: 或"不足却不回退"的边界bug）。
FALLBACK_MIN_PRIMARY_RECORDS = MIN_RECORDS_FOR_RELATIVE


def _candidate_paths(settings: Any) -> list[Path]:
    """Every location this project has ever written the metrics file to.

    Production evidence (scheduled Daily Run artifact, 2026-10-21) puts the file
    in the account *output root*:
        output/accounts/<slug>/performance_metrics.json
    while `automation/` only carries run bookkeeping. The earlier single-path
    lookup therefore returned zero records on real cloud runs and the loop
    silently injected nothing. Order matters: keep the legacy automation/
    location first so an existing file there is never shadowed, then fall back
    to the locations actually observed in production.
    """
    automation_dir = getattr(settings, "automation_dir", None)
    output_dir = getattr(settings, "output_dir", None)
    project_root = getattr(settings, "project_root", None)
    candidates = [
        Path(automation_dir) / METRICS_FILENAME if automation_dir else None,
        Path(automation_dir) / "feedback" / METRICS_FILENAME if automation_dir else None,
        Path(output_dir) / METRICS_FILENAME if output_dir else None,
        Path(project_root) / METRICS_FILENAME if project_root else None,
    ]
    return [path for path in candidates if path is not None]


def _read_records(path: Path) -> list[dict[str, Any]] | None:
    """Return records from one file, or None when the file is unusable."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return None
    return [row for row in records if isinstance(row, dict) and str(row.get("title", "")).strip()]


def load_records(settings: Any) -> list[dict[str, Any]]:
    """读取表现记录；primary 源不足时回退到 growth_checkpoints。

    2026-10-21：生产 artifact 实测 `growth_checkpoints.json` 有 31 条作品
    （19 条有真实阅读、含 24h/72h 观测），而 `performance_metrics.json` 只有 1 条。
    闭环只读后者，于是永远 `record_count: 1 / insufficient`、从不注入任何样本 ——
    尽管同一目录里数据一直都有。

    回退规则（保守）：
    - 只在 primary **少于相对档下限**时回退；primary 够用就不混入第二个源，
      避免两份口径不同的数据混在一起导致统计漂移；
    - checkpoints 的字段名不同（`latest.reads` / `latest.impressions`），
      这里显式映射到统一的 `read_count` / `impression_count`，不直接透传。
    """
    records = _load_primary_records(settings)
    if len(records) >= FALLBACK_MIN_PRIMARY_RECORDS:
        return records
    fallback = _load_checkpoint_records(settings)
    return fallback if len(fallback) > len(records) else records


def _load_primary_records(settings: Any) -> list[dict[str, Any]]:
    for path in _candidate_paths(settings):
        records = _read_records(path)
        if records:
            return records
    return []


def _checkpoint_paths(settings: Any) -> list[Path]:
    output_dir = getattr(settings, "output_dir", None)
    project_root = getattr(settings, "project_root", None)
    candidates = [
        Path(output_dir) / CHECKPOINTS_FILENAME if output_dir else None,
        Path(project_root) / "output" / CHECKPOINTS_FILENAME if project_root else None,
    ]
    return [path for path in candidates if path is not None]


def _load_checkpoint_records(settings: Any) -> list[dict[str, Any]]:
    """把 growth_checkpoints 的 works 映射成统一的记录形状。

    只取有标题、且至少有一个非零指标的作品：没有任何展现/阅读的作品当不了
    参考范本，混进来只会稀释样本。
    """
    for path in _checkpoint_paths(settings):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        works = payload.get("works") if isinstance(payload, dict) else None
        if not isinstance(works, dict):
            continue
        records: list[dict[str, Any]] = []
        for gid, sample in works.items():
            if not isinstance(sample, dict):
                continue
            title = str(sample.get("title", "")).strip()
            if not title:
                continue
            latest = sample.get("latest") if isinstance(sample.get("latest"), dict) else {}
            impressions = _as_int(latest.get("impressions"))
            reads = _as_int(latest.get("reads"))
            if impressions <= 0 and reads <= 0:
                continue
            records.append(
                {
                    "title": title,
                    "impression_count": impressions,
                    "read_count": reads,
                    "comment_count": _as_int(latest.get("comments")),
                    "platform_item_id": str(gid),
                    "content_type": sample.get("type"),
                    "published_at": sample.get("created_at"),
                    "source": "growth_checkpoints",
                }
            )
        if records:
            return records
    return []


def metrics_path(settings: Any) -> Path:
    """表现数据文件的**权威路径**，供所有消费方复用。

    2026-10-21：`run_feedback_loop.py`、`stage_gate.py` 与
    `feedback_loop_service` 原先各自拼 `automation_dir / performance_metrics.json`，
    但生产把文件写在账号输出根目录，于是它们全部读到空数据
    （实测 `stage_gate._works()` 看到 0 条，实际有记录）。

    这里统一成一处解析，避免同一个路径散落多份、再出现不一致：
    返回第一个真实存在的候选；都不存在时返回历史位置（automation/），
    以便调用方写报告时有一个稳定的路径可展示。
    """
    candidates = _candidate_paths(settings)
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0] if candidates else Path(METRICS_FILENAME)


def _reads(record: dict[str, Any]) -> int:
    return _as_int(record.get("read_count")) or _as_int(record.get("views"))


def _impressions(record: dict[str, Any]) -> int:
    return _as_int(record.get("impression_count"))


def select_examples(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """挑出可当范本的记录，返回 (记录, 模式)。

    模式取值：``absolute``（阅读过线）/ ``relative``（样本不足时取相对最优）/
    ``insufficient``（连相对档都不够，不注入）。
    """
    qualified = [row for row in records if _reads(row) >= MIN_READS_FOR_FEWSHOT]
    if qualified:
        qualified.sort(key=lambda row: (_reads(row), _impressions(row)), reverse=True)
        return qualified[:MAX_EXAMPLES], "absolute"

    if len(records) < MIN_RECORDS_FOR_RELATIVE:
        return [], "insufficient"

    ranked = sorted(records, key=lambda row: (_impressions(row), _reads(row)), reverse=True)
    # 相对档必须真的有展现差异，否则等于随机挑。
    top = ranked[0]
    if _impressions(top) <= 0:
        return [], "insufficient"
    return ranked[:MAX_EXAMPLES], "relative"


def _header(mode: str) -> str:
    if mode == "absolute":
        return (
            "\n## 参考：本账号近期阅读表现最好的标题\n"
            "（借鉴其具体程度和信息结构，不要照抄原标题）\n"
        )
    return (
        "\n## 参考：本账号近期展现相对最高的标题（样本不足，仅作相对参考）\n"
        "（这些还不是有阅读验证的成功样本，只说明平台给了相对多的推荐；借鉴时保持保守）\n"
    )


def title_hints(settings: Any) -> str:
    """生成标题侧的提示片段；无可用样本时返回空字符串。"""
    records = load_records(settings)
    examples, mode = select_examples(records)
    if not examples:
        return ""
    lines = [_header(mode)]
    for index, row in enumerate(examples, 1):
        lines.append(f"{index}. {str(row.get('title', '')).strip()}")
        impressions = _impressions(row)
        reads = _reads(row)
        if impressions or reads:
            lines.append(f"   展现 {impressions} | 阅读 {reads}")
        lines.append("")
    return "\n".join(lines)


def writer_hints(settings: Any) -> str:
    """生成正文侧的提示片段；只给出可复核的结构信息，不复述结论。"""
    records = load_records(settings)
    examples, mode = select_examples(records)
    if not examples:
        return ""
    lines = [_header(mode).replace("标题", "作品")]
    for index, row in enumerate(examples, 1):
        title = str(row.get("title", "")).strip()
        lines.append(f"### 相对高表现作品 {index}")
        lines.append(f"- 标题：{title}")
        impressions = _impressions(row)
        reads = _reads(row)
        if impressions:
            ctr = reads / impressions if impressions else 0.0
            lines.append(f"- 展现 {impressions} | 阅读 {reads} | 点击率 {ctr:.2%}")
        lines.append("")
    return "\n".join(lines)


def loop_status(settings: Any) -> dict[str, Any]:
    """闭环当前的可运行状态，供阶段判定与报告消费。"""
    records = load_records(settings)
    examples, mode = select_examples(records)
    return {
        "record_count": len(records),
        "min_reads_threshold": MIN_READS_FOR_FEWSHOT,
        "min_records_for_relative": MIN_RECORDS_FOR_RELATIVE,
        "selected_count": len(examples),
        "mode": mode,
        "active": bool(examples),
    }
