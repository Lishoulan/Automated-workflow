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
    """读取 performance_metrics.json 里的记录；任何异常都返回空列表。

    依次尝试历史与生产实际使用的多个位置，返回第一个含可用记录的来源。
    某处文件损坏时继续找下一处，而不是直接判为空。
    """
    for path in _candidate_paths(settings):
        records = _read_records(path)
        if records:
            return records
    return []


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
