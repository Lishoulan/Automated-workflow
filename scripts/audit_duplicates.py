#!/usr/bin/env python3
"""盘点所有账号的真实发布记录，找出重复内容并输出完整清单。

重复定义：
  - 相同最终发布标题(title)出现多次 → 线上疑似重复发布
  - 相同采集话题(topic_title/topic_url)被用于多条 → 采集源头重复

输出：
  - output/duplicates_report.md   人类可读清单
  - output/duplicates_report.json 结构化数据

用法：
  py scripts/audit_duplicates.py
  py scripts/audit_duplicates.py --min-count 2
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "output"


def load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def collect_records() -> list[dict]:
    records: list[dict] = []
    accounts_dir = PROJECT_ROOT / "output" / "accounts"
    if accounts_dir.is_dir():
        for act_dir in accounts_dir.iterdir():
            if not act_dir.is_dir():
                continue
            for fname in ("publish_history.json", "wechat_publish_history.json"):
                data = load_json(act_dir / fname)
                if isinstance(data, list):
                    for r in data:
                        if isinstance(r, dict):
                            r.setdefault("account_key", act_dir.name)
                            r.setdefault("history_file", fname)
                            records.append(r)

    agg = load_json(PROJECT_ROOT / "output" / "publish_history.json")
    if isinstance(agg, list):
        for r in agg:
            if isinstance(r, dict):
                r.setdefault("account_key", "default")
                r.setdefault("history_file", "publish_history.json")
                records.append(r)

    seen = set()
    deduped = []
    for r in records:
        key = (str(r.get("published_at", "")), str(r.get("title", "")), str(r.get("topic_title", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser(description="盘点账号重复发布内容")
    parser.add_argument("--min-count", type=int, default=2, help="标题出现次数阈值，默认 2")
    args = parser.parse_args()

    records = collect_records()

    by_title: dict[str, list[dict]] = defaultdict(list)
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        title = str(r.get("title", "")).strip()
        if title:
            by_title[title].append(r)
        topic = str(r.get("topic_title", "")).strip()
        if topic:
            by_topic[topic].append(r)

    dup_titles = {k: v for k, v in by_title.items() if len(v) >= args.min_count}
    dup_topics = {k: v for k, v in by_topic.items() if len(v) >= args.min_count}

    def group_payload(group: dict[str, list[dict]]) -> list[dict]:
        out = []
        for label, items in sorted(group.items(), key=lambda kv: -len(kv[1])):
            entries = sorted(items, key=lambda x: str(x.get("published_at", "")))
            first = entries[0]
            out.append({
                "label": label,
                "count": len(entries),
                "first_published_at": first.get("published_at", ""),
                "first_status": str(first.get("status", "")),
                "first_run_dir": str(first.get("run_dir", "")),
                "entries": [
                    {
                        "published_at": e.get("published_at", ""),
                        "status": str(e.get("status", "")),
                        "platform": str(e.get("platform", "") or "toutiao"),
                        "account": e.get("account_key", ""),
                        "title": str(e.get("title", "")),
                        "topic_title": str(e.get("topic_title", "")),
                        "topic_url": str(e.get("topic_url", "")),
                        "run_dir": str(e.get("run_dir", "")),
                    }
                    for e in entries
                ],
            })
        return out

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_records": len(records),
        "duplicate_titles": group_payload(dup_titles),
        "duplicate_topics": group_payload(dup_topics),
        "summary": {
            "dup_title_groups": len(dup_titles),
            "dup_title_total_occurrences": sum(len(v) for v in dup_titles.values()),
            "dup_topic_groups": len(dup_topics),
            "dup_topic_total_occurrences": sum(len(v) for v in dup_topics.values()),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "duplicates_report.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown 清单
    lines = [
        "# 账号重复内容完整清单",
        "",
        f"> 记录总数：{len(records)} ｜ 生成时间：{result['generated_at']}",
        "",
        f"**重复标题分组：{len(dup_titles)} 组，累计 {result['summary']['dup_title_total_occurrences']} 次**",
        f"**重复采集话题分组：{len(dup_topics)} 组，累计 {result['summary']['dup_topic_total_occurrences']} 次**",
        "",
        "## 一、重复发布的最终标题",
        "",
    ]
    for g in result["duplicate_titles"]:
        lines.append(f"### 「{g['label']}」 共 {g['count']} 次（最早 {g['first_published_at']}，状态 {g['first_status']}）")
        lines.append("")
        lines.append("| 时间 | 平台 | 状态 | 账号 | 采集源话题 |")
        lines.append("|---|---|---|---|---|")
        for e in g["entries"]:
            topic = e["topic_title"].replace("\n", " ")[:40]
            lines.append(
                f"| {e['published_at']} | {e['platform']} | {e['status']} | {e['account']} | {topic} |"
            )
        lines.append("")

    lines.append("## 二、重复采集的话题标题")
    lines.append("")
    for g in result["duplicate_topics"]:
        lines.append(f"### 「{g['label']}」 共 {g['count']} 次")
        lines.append("")
        lines.append("| 时间 | 最终发布标题 | 状态 | 平台 |")
        lines.append("|---|---|---|---|")
        for e in g["entries"]:
            lines.append(f"| {e['published_at']} | {e['title']} | {e['status']} | {e['platform']} |")
        lines.append("")

    md_path = OUT_DIR / "duplicates_report.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"记录总数：{len(records)}")
    print(f"重复标题分组 {len(dup_titles)} 组、累计 {result['summary']['dup_title_total_occurrences']} 次；"
          f"重复采集话题 {len(dup_topics)} 组、累计 {result['summary']['dup_topic_total_occurrences']} 次\n")
    for g in result["duplicate_titles"]:
        print(f"[{g['count']}x] {g['label']}  (最早 {g['first_published_at']}, {g['first_status']})")
    print(f"\n清单已保存：{md_path}\n{json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())