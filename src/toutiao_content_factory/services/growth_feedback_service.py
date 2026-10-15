from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any


TOPIC_TAGS: dict[str, tuple[str, ...]] = {
    "房产继承": ("房产", "房子", "继承", "遗产", "过户", "居住权"),
    "养老赡养": ("养老", "赡养", "子女", "老人", "退休", "养老院"),
    "借贷纠纷": ("借钱", "借条", "欠款", "亲戚", "还钱"),
    "家政照护": ("保姆", "家政", "照护", "护工"),
    "独居安全": ("独居", "摔倒", "邻居", "求助", "报警"),
    "婚恋家庭": ("相亲", "婚介", "夫妻", "婚姻", "离婚", "育儿"),
}
CHECKPOINT_TOLERANCE_HOURS = {"24h": 12, "72h": 24}
TOPIC_WEIGHT_MIN_SAMPLES = 30
ARTICLE_CALIBRATION_MIN_SAMPLES = 8


def classify_checkpoint_capture(label: str, delay_hours: float) -> str:
    tolerance = CHECKPOINT_TOLERANCE_HOURS.get(str(label), 0)
    return "on_schedule" if float(delay_hours) <= tolerance else "late"


def normalize_title(value: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(value or "")).casefold()


def infer_topic_tags(text: str) -> list[str]:
    value = str(text or "")
    return [tag for tag, markers in TOPIC_TAGS.items() if any(marker in value for marker in markers)]


def title_signals(title: str) -> list[str]:
    text = str(title or "").strip()
    signals: list[str] = []
    length = len(text)
    if 14 <= length <= 24:
        signals.append("mobile_length")
    if re.search(r"\d", text):
        signals.append("number")
    if "？" in text or "?" in text or any(
        token in text for token in ("为何", "为什么", "怎么", "如何", "哪些")
    ):
        signals.append("question_gap")
    if any(token in text for token in ("结果", "最后", "后来", "判决", "认定", "解决")):
        signals.append("outcome")
    if any(token in text for token in ("老人", "夫妻", "儿子", "女儿", "邻居", "法院", "公司")):
        signals.append("concrete_subject")
    return signals


def _checkpoint_rows(checkpoints: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    works = checkpoints.get("works") if isinstance(checkpoints, dict) else {}
    if not isinstance(works, dict):
        return rows
    for entry in works.values():
        if not isinstance(entry, dict):
            continue
        samples = entry.get("checkpoints")
        label = "72h" if isinstance(samples, dict) and isinstance(samples.get("72h"), dict) else "24h"
        sample = samples.get(label) if isinstance(samples, dict) else None
        if not isinstance(sample, dict):
            continue
        impressions = int(sample.get("impressions") or 0)
        reads = int(sample.get("reads") or 0)
        delay = float(sample.get("capture_delay_hours") or 0)
        capture_quality = str(sample.get("capture_quality") or "").strip()
        if capture_quality not in {"on_schedule", "late"}:
            capture_quality = classify_checkpoint_capture(label, delay)
        rows.append(
            {
                "gid": str(entry.get("gid") or ""),
                "type": str(entry.get("type") or "unknown"),
                "title": str(entry.get("title") or "").strip(),
                "impressions": impressions,
                "reads": reads,
                "comments": int(sample.get("comments") or 0),
                "read_rate_pct": round(reads * 100 / impressions, 3) if impressions else 0.0,
                "capture_delay_hours": delay,
                "capture_quality": capture_quality,
                "checkpoint": label,
            }
        )
    return rows


def _format_benchmarks(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for content_type in sorted({row["type"] for row in rows}):
        group = [row for row in rows if row["type"] == content_type]
        result[content_type] = {
            "samples": len(group),
            "median_impressions": round(float(median(row["impressions"] for row in group)), 2),
            "median_reads": round(float(median(row["reads"] for row in group)), 2),
            "median_read_rate_pct": round(float(median(row["read_rate_pct"] for row in group)), 3),
        }
    return result


def _performance_index(row: dict[str, Any], benchmark: dict[str, Any]) -> float:
    impression_base = max(1.0, float(benchmark.get("median_impressions") or 0))
    rate_base = max(0.25, float(benchmark.get("median_read_rate_pct") or 0))
    impression_index = min(3.0, float(row["impressions"]) / impression_base)
    rate_index = min(3.0, float(row["read_rate_pct"]) / rate_base)
    return round(impression_index * 0.6 + rate_index * 0.4, 3)


def build_growth_feedback(
    snapshot: dict[str, Any],
    checkpoints: dict[str, Any],
    *,
    generated_at: str | None = None,
    reader_value_batch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _checkpoint_rows(checkpoints)
    on_schedule_count = sum(row["capture_quality"] == "on_schedule" for row in rows)
    late_count = len(rows) - on_schedule_count
    checkpoint_used = (
        f"72h优先，缺失时使用24h；准时 {on_schedule_count} 条，迟到回收 {late_count} 条"
    )
    on_schedule_rows = [row for row in rows if row["capture_quality"] == "on_schedule"]
    # Late captures remain visible in the report, but they are not comparable
    # enough to tune tomorrow's title and topic weights automatically.
    benchmarks = _format_benchmarks(on_schedule_rows or rows)
    comparable_benchmarks = {
        label: _format_benchmarks([row for row in on_schedule_rows if row["checkpoint"] == label])
        for label in ("24h", "72h")
    }
    qualified_wtt_count = sum(row["type"] == "weitoutiao" and row["impressions"] > 0 for row in on_schedule_rows)
    qualified_article_count = sum(row["type"] == "article" and row["impressions"] > 0 for row in on_schedule_rows)

    evaluated: list[dict[str, Any]] = []
    tag_scores: dict[str, list[float]] = defaultdict(list)
    signal_scores: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        benchmark = comparable_benchmarks.get(row["checkpoint"], {}).get(row["type"], {}) or benchmarks.get(row["type"], {})
        index = _performance_index(row, benchmark)
        tags = infer_topic_tags(row["title"])
        signals = title_signals(row["title"])
        status = "watch"
        if (
            row["reads"] > 0
            and index >= 1.25
            and row["impressions"] >= float(benchmark.get("median_impressions") or 0)
            and row["reads"] >= float(benchmark.get("median_reads") or 0)
            and row["read_rate_pct"] >= max(2.0, float(benchmark.get("median_read_rate_pct") or 0))
        ):
            status = "winner"
        elif row["checkpoint"] == "72h" and (row["reads"] == 0 or index <= 0.45):
            status = "stop"
        item = {**row, "performance_index": index, "status": status, "topic_tags": tags, "title_signals": signals}
        evaluated.append(item)
        if row["capture_quality"] == "on_schedule" and row["type"] == "weitoutiao" and row["impressions"] > 0:
            for tag in tags:
                tag_scores[tag].append(index)
        if (
            row["capture_quality"] == "on_schedule"
            and row["type"] == "article"
            and row["impressions"] > 0
        ):
            for signal in signals:
                signal_scores[signal].append(index)

    topic_weights: dict[str, int] = {}
    topic_evidence: dict[str, dict[str, Any]] = {}
    for tag, scores in sorted(tag_scores.items()):
        average = sum(scores) / len(scores)
        weight = max(-30, min(20, round((average - 1.0) * 20))) if qualified_wtt_count >= TOPIC_WEIGHT_MIN_SAMPLES and len(scores) >= 5 else 0
        topic_weights[tag] = weight
        topic_evidence[tag] = {"samples": len(scores), "average_performance_index": round(average, 3)}

    signal_adjustments: dict[str, int] = {}
    signal_evidence: dict[str, dict[str, Any]] = {}
    for signal, scores in sorted(signal_scores.items()):
        average = sum(scores) / len(scores)
        adjustment = max(-6, min(6, round((average - 1.0) * 5))) if qualified_article_count >= ARTICLE_CALIBRATION_MIN_SAMPLES and len(scores) >= 3 else 0
        signal_adjustments[signal] = adjustment
        signal_evidence[signal] = {"samples": len(scores), "average_performance_index": round(average, 3)}

    winners = sorted(
        (item for item in evaluated if item["status"] == "winner" and item["capture_quality"] == "on_schedule"),
        key=lambda item: item["performance_index"],
        reverse=True,
    )
    stopped = sorted(
        (item for item in evaluated if item["status"] == "stop" and item["capture_quality"] == "on_schedule"),
        key=lambda item: item["performance_index"],
    )
    stop_titles = list(
        dict.fromkeys(
            normalize_title(item["title"])
            for item in stopped
            if item["type"] == "weitoutiao" and item["title"]
        )
    )
    stopped_for_report: list[dict[str, Any]] = []
    stopped_by_title: dict[tuple[str, str], dict[str, Any]] = {}
    for item in stopped:
        key = (item["type"], normalize_title(item["title"]) or item["gid"])
        existing = stopped_by_title.get(key)
        if existing is None:
            grouped = dict(item)
            grouped["repeat_count"] = 1
            grouped["gids"] = [item["gid"]]
            stopped_by_title[key] = grouped
            stopped_for_report.append(grouped)
            continue
        existing["repeat_count"] += 1
        existing["gids"].append(item["gid"])
        existing["impressions"] += item["impressions"]
        existing["reads"] += item["reads"]
        existing["comments"] += item["comments"]
        existing["read_rate_pct"] = round(
            existing["reads"] * 100 / existing["impressions"], 3
        ) if existing["impressions"] else 0.0
    return {
        "schema": 1,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "checkpoint_used": checkpoint_used,
        "sample_count": len(evaluated),
        "benchmarks": benchmarks,
        "topic_feedback": {
            "status": "active" if qualified_wtt_count >= TOPIC_WEIGHT_MIN_SAMPLES else "collecting",
            "qualified_samples": qualified_wtt_count,
            "minimum_samples": TOPIC_WEIGHT_MIN_SAMPLES,
            "minimum_samples_per_tag": 5,
            "weights": topic_weights,
            "evidence": topic_evidence,
            "stop_titles": stop_titles,
            "winning_titles": [
                normalize_title(item["title"])
                for item in winners
                if item["type"] == "weitoutiao" and item["title"]
            ],
        },
        "creative_calibration": {
            "status": "active" if qualified_article_count >= ARTICLE_CALIBRATION_MIN_SAMPLES and any(len(scores) >= 3 for scores in signal_scores.values()) else "collecting",
            "minimum_article_samples": ARTICLE_CALIBRATION_MIN_SAMPLES,
            "minimum_samples_per_signal": 3,
            "ready_signals": sorted(signal for signal, scores in signal_scores.items() if qualified_article_count >= ARTICLE_CALIBRATION_MIN_SAMPLES and len(scores) >= 3),
            "signal_adjustments": signal_adjustments,
            "evidence": signal_evidence,
            "article_samples": sum(
                item["type"] == "article"
                and item["capture_quality"] == "on_schedule"
                and item["impressions"] > 0
                for item in evaluated
            ),
            "positive_read_article_samples": sum(
                item["type"] == "article"
                and item["capture_quality"] == "on_schedule"
                and item["reads"] > 0
                for item in evaluated
            ),
            "mature_article_samples": sum(item["type"] == "article" for item in evaluated),
        },
        "winners": winners[:10],
        "late_observations": [item for item in evaluated if item["capture_quality"] == "late"],
        "stop_loss": stopped_for_report[:20],
        "current_summary": snapshot.get("summary", {}) if isinstance(snapshot, dict) else {},
        "opening_experiment": opening_experiment_summary(checkpoints),
        "reader_value_batch": reader_value_batch or {},
    }


STORY_OPENING_PROTOCOL_VERSION = 3


def opening_experiment_summary(checkpoints: dict[str, Any]) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    works = checkpoints.get("works", {})
    experiment_works = [work for work in works.values() if isinstance(work, dict)
        and work.get("type") == "weitoutiao" and isinstance(work.get("content_experiment"), dict)
        and work["content_experiment"].get("id") == "story_opening_v1"] if isinstance(works, dict) else []
    versions = [work["content_experiment"].get("protocol_version", 1) for work in experiment_works]
    protocol = max((version for version in versions if type(version) is int
                    and 1 <= version <= STORY_OPENING_PROTOCOL_VERSION), default=1)
    excluded = sum(work["content_experiment"].get("protocol_version", 1) != protocol for work in experiment_works)
    for label in ("24h", "72h"):
        for variant in ("result_first", "situation_first"):
            samples = []
            for work in works.values() if isinstance(works, dict) else []:
                if not isinstance(work, dict) or work.get("type") != "weitoutiao":
                    continue
                experiment = work.get("content_experiment", {})
                if not isinstance(experiment, dict) or experiment.get("id") != "story_opening_v1" or experiment.get("variant") != variant:
                    continue
                if experiment.get("protocol_version", 1) != protocol:
                    continue
                sample = (work.get("checkpoints") or {}).get(label)
                if isinstance(sample, dict) and classify_checkpoint_capture(label, float(sample.get("capture_delay_hours", 0))) == "on_schedule" and int(sample.get("impressions", 0)) > 0:
                    samples.append(sample)
            impressions = sum(int(s.get("impressions", 0)) for s in samples)
            reads = sum(int(s.get("reads", 0)) for s in samples)
            groups.append({"checkpoint": label, "variant": variant, "samples": len(samples), "impressions": impressions, "reads": reads,
                           "read_rate_pct": round(reads * 100 / impressions, 3) if impressions else None,
                           "median_reads": median([int(s.get("reads", 0)) for s in samples]) if samples else None,
                           "median_impressions": median([int(s.get("impressions", 0)) for s in samples]) if samples else None})
    ready = any(all(g["samples"] >= 15 and g["impressions"] >= 1000 for g in groups if g["checkpoint"] == label) for label in ("24h", "72h"))
    comparisons = []
    for label in ("24h", "72h"):
        pair = [g for g in groups if g["checkpoint"] == label]
        if not all(g["samples"] >= 15 and g["impressions"] >= 1000 for g in pair):
            continue
        ordered = sorted(pair, key=lambda g: g["read_rate_pct"], reverse=True)
        higher, lower = ordered
        preferred = (higher["read_rate_pct"] > lower["read_rate_pct"]
                     and higher["median_reads"] > lower["median_reads"]
                     and higher["median_impressions"] >= lower["median_impressions"])
        comparisons.append({"checkpoint": label, "preferred_variant": higher["variant"] if preferred else None,
            "status": "directional_preference" if preferred else "no_consistent_improvement",
            "read_rate_target_pct": 2.0,
            "target_met": preferred and higher["read_rate_pct"] >= 2.0,
            "automatic_switch": False})
    return {"id": "story_opening_v1", "variable": "opening_order", "protocol_version": protocol,
            "legacy_works_excluded": excluded, "status": "comparison_ready" if ready else "collecting", "groups": groups,
            "comparisons": comparisons, "read_rate_target_pct": 2.0,
            "note": "不同真实作品的描述性比较；24h和72h分开统计，迟到采样排除，不据此自动推断因果或收益。"}


def topic_feedback_score(title: str, feedback: dict[str, Any]) -> tuple[int, list[str]]:
    section = feedback.get("topic_feedback") if isinstance(feedback, dict) else {}
    section = section if isinstance(section, dict) else {}
    normalized = normalize_title(title)
    if normalized and normalized in set(section.get("stop_titles") or []):
        return -100, ["72小时低表现标题停止复用"]
    weights = section.get("weights") if isinstance(section.get("weights"), dict) else {}
    reasons: list[str] = []
    score = 0
    for tag in infer_topic_tags(title):
        weight = int(weights.get(tag) or 0)
        score += weight
        if weight:
            reasons.append(f"{tag}{weight:+d}")
    return max(-100, min(40, score)), reasons


def render_daily_growth_report(feedback: dict[str, Any]) -> str:
    lines = [
        "# 今日头条增长运营日报",
        "",
        f"生成时间：{feedback.get('generated_at', '')}",
        f"使用数据：{feedback.get('checkpoint_used', '')} 检查点，共 {feedback.get('sample_count', 0)} 条成熟样本",
        "",
        "## 当前表现",
        "",
    ]
    summary = feedback.get("current_summary") or {}
    for content_type, metrics in summary.items():
        lines.append(
            f"- {content_type}：{metrics.get('works', 0)} 条，{metrics.get('impressions', 0)} 展现，"
            f"{metrics.get('reads', 0)} 阅读，零阅读 {metrics.get('zero_read_works', 0)} 条。"
        )
    lines.extend(["", "## 值得借鉴的作品（观察线索）", "",
                  "阅读率至少2%，且阅读量和展现量不低于同类型、同窗口中位数；不代表题材或写法已证明有效。借鉴前需重新核对原文，不直接复用标题或正文。", ""])
    winners = feedback.get("winners") or []
    if winners:
        for item in winners[:5]:
            lines.append(
                f"- {item.get('title', '')}（{item.get('type', '')}，{item.get('checkpoint', '')}，"
                f"展现 {item.get('impressions', 0)}，阅读 {item.get('reads', 0)}，"
                f"阅读率 {item.get('read_rate_pct', 0)}%）"
            )
    else:
        lines.append("- 暂无同时达到阅读量、阅读率和展现量要求的成熟样本；选题和写作质量优化照常进行。")
    lines.extend(["", "## 自动止损", ""])
    stopped = feedback.get("stop_loss") or []
    if stopped:
        for item in stopped[:5]:
            repeat_note = f"，重复发布 {item.get('repeat_count')} 次" if item.get("repeat_count", 1) > 1 else ""
            lines.append(
                f"- 暂停复用：{item.get('title', '')}（展现 {item.get('impressions', 0)}，"
                f"阅读 {item.get('reads', 0)}{repeat_note}）"
            )
    else:
        lines.append("- 暂无触发止损阈值的成熟样本。")
    lines.extend(["", "## 明日动作", ""])
    weights = (feedback.get("topic_feedback") or {}).get("weights") or {}
    positive = sorted(weights.items(), key=lambda item: item[1], reverse=True)
    negative = sorted(weights.items(), key=lambda item: item[1])
    if positive and positive[0][1] > 0:
        lines.append(f"- 微头条优先测试“{positive[0][0]}”相关选题。")
    if negative and negative[0][1] < 0:
        lines.append(f"- 降低“{negative[0][0]}”相关选题频次，等待新角度或新事实。")
    lines.append("- 新作品继续在 24 小时和 72 小时回收展现、阅读与评论数据。")
    batch = feedback.get("reader_value_batch") or {}
    if batch:
        lines.extend(["", "## 信息价值5—7篇复盘", "",
            f"状态：{batch.get('status', 'unknown')}；真实新作品 {batch.get('cohort_size', 0)}/7；5篇提示复盘。",
            "首阶段阅读率目标2%，同时要求单篇阅读中位数增加、展现中位数不下降。",
            "| 窗口 | 有效篇数 | 阅读率 | 阅读中位数 | 展现中位数 | 判断 |",
            "|---|---:|---:|---:|---:|---|"])
        for label, row in batch.get("windows", {}).items():
            lines.append(f"| {label} | {row['samples']} | {row['read_rate_pct']} | {row['median_reads']} | {row['median_impressions']} | {row['verdict']} |")
        lines.append(str(batch.get("note", "基线未齐或状态待核时，不判断增长。")))
        if batch.get("next_action"):
            lines.append(str(batch["next_action"]))
    topic_feedback = feedback.get("topic_feedback", {})
    lines.append(f"- 题材调权：{topic_feedback.get('status', 'collecting')}，合格样本 {topic_feedback.get('qualified_samples', 0)}/{TOPIC_WEIGHT_MIN_SAMPLES}；单题材至少5条才调权。")
    experiment = feedback.get("opening_experiment", {})
    lines.extend(["", "## 开头顺序实验", "", f"状态：{experiment.get('status', 'collecting')}（同一窗口每组至少15条、1000展现后可比较）", "", "| 窗口 | 开头 | 合格样本 | 展现 | 阅读 | 阅读率 | 单篇阅读中位数 | 单篇展现中位数 |", "|---|---|---:|---:|---:|---:|---:|---:|"])
    for group in experiment.get("groups", []):
        rate = group["read_rate_pct"]
        lines.append(f"| {group['checkpoint']} | {group['variant']} | {group['samples']} | {group['impressions']} | {group['reads']} | {str(rate) + '%' if rate is not None else '未知'} | {group.get('median_reads', '未知')} | {group.get('median_impressions', '未知')} |")
    lines.append(str(experiment.get("note", "")))
    for comparison in experiment.get("comparisons", []):
        lines.append(f"- {comparison['checkpoint']}：{comparison['status']}；观察方向 {comparison['preferred_variant'] or '暂无'}，阅读率目标2%，达标 {comparison['target_met']}。不自动切换实验。")
    late = feedback.get("late_observations", [])
    if late:
        lines.extend(["", "## 迟到样本（仅展示，不用于推荐）", ""])
        for item in late:
            lines.append(f"- {item['gid']}：{item['checkpoint']}，{item['impressions']}展现，{item['reads']}阅读，迟到{item['capture_delay_hours']}小时。")
    return "\n".join(lines).rstrip() + "\n"


def write_growth_outputs(output_dir: Path, feedback: dict[str, Any]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    feedback_path = output_dir / "growth_feedback.json"
    calibration_path = output_dir / "creative_score_calibration.json"
    report_md_path = output_dir / "daily_growth_report.md"
    feedback_path.write_text(json.dumps(feedback, ensure_ascii=False, indent=2), encoding="utf-8")
    calibration_path.write_text(
        json.dumps(feedback.get("creative_calibration") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    duplicate_report_path = output_dir / "daily_growth_report.json"
    if duplicate_report_path.exists():
        duplicate_report_path.unlink()
    report_md_path.write_text(render_daily_growth_report(feedback), encoding="utf-8")
    return {
        "feedback": feedback_path,
        "calibration": calibration_path,
        "report_md": report_md_path,
    }
