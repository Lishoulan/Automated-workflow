#!/usr/bin/env python3
"""拉取真实的「采集(收集)标题 vs 最终发布标题」对比数据，评估二创效果。

数据来源（都是真实落盘数据）：
  1. output/publish_history.json          聚合发布历史
  2. output/accounts/*/publish_history.json 各账号发布历史
  3. output/accounts/*/wechat_publish_history.json 微信发布历史

每条发布记录天然含有:
  - topic_title: 采集到的原始新闻标题
  - title:        二创后最终发布的标题

脚本按最近 N 天（默认 7 天）汇总对比，输出:
  - output/comparison/collection_vs_publish_report.md   人类可读 Markdown 报告
  - output/comparison/collection_vs_publish_report.json 结构化数据

用法:
  python scripts/fetch_comparison_data.py            # 最近 7 天
  python scripts/fetch_comparison_data.py --days 30  # 最近 30 天
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "output" / "comparison"

# 视为「真实产出/发布」的状态（草稿也算已二创内容）
KEEP_STATUSES = {"submitted_for_review", "publish_uncertain", "draft_saved", "published", "success"}


def load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def title_similarity(a: str, b: str) -> float:
    """0~1, 越高代表发布标题与采集标题越接近(二创改造越少)。"""
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b).ratio(), 4)


def parse_ts(value: str):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
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

    # 聚合文件(可能与账号级重复，按 key 去重)
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


def build_rows(records: list[dict], days: int) -> tuple[list[dict], dict]:
    cutoff = datetime.now() - timedelta(days=days)
    rows: list[dict] = []
    for r in records:
        status = str(r.get("status", "")).strip().lower()
        if status and status not in KEEP_STATUSES:
            continue
        topic_title = str(r.get("topic_title", "")).strip()
        published = str(r.get("title", "")).strip()
        if not published:
            continue
        ts = parse_ts(r.get("published_at"))
        if ts is not None and ts < cutoff:
            continue
        rows.append({
            "published_at": r.get("published_at", ""),
            "date": ts.strftime("%Y-%m-%d") if ts else "",
            "platform": str(r.get("platform", "")).strip() or "toutiao",
            "account": r.get("account_key", ""),
            "status": status,
            "topic_title": topic_title,
            "published_title": published,
            "similarity": title_similarity(topic_title, published),
            "topic_url": str(r.get("topic_url", "") or ""),
        })

    rows.sort(key=lambda x: (x["date"], x["published_at"]))

    stats = {
        "total": len(rows),
        "with_topic_title": sum(1 for x in rows if x["topic_title"]),
        "avg_similarity": round(sum(x["similarity"] for x in rows) / len(rows), 4) if rows else 0.0,
        "sim_lt_0_2": sum(1 for x in rows if x["similarity"] < 0.2),
        "sim_0_2_0_5": sum(1 for x in rows if 0.2 <= x["similarity"] < 0.5),
        "sim_0_5_0_8": sum(1 for x in rows if 0.5 <= x["similarity"] < 0.8),
        "sim_ge_0_8": sum(1 for x in rows if x["similarity"] >= 0.8),
    }
    return rows, stats


def render_markdown(rows: list[dict], stats: dict, days: int) -> str:
    lines = [
        "# 采集 vs 发布 标题对比报告（二创效果）",
        "",
        f"> 数据窗口：最近 {days} 天 ｜ 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"> {stats['total']} 条发布记录 ｜ 平均相似度 **{stats['avg_similarity']}**（越低=二创改造越大）",
        "",
        "## 相似度分布",
        "",
        f"- 高相似(≥0.8)：{stats['sim_ge_0_8']} 条 —— 二创改造很小，可能只是临近标题",
        f"- 中相似(0.5~0.8)：{stats['sim_0_5_0_8']} 条",
        f"- 低相似(0.2~0.5)：{stats['sim_0_2_0_5']} 条",
        f"- 极低相似(<0.2)：{stats['sim_lt_0_2']} 条 —— 二创改造明显",
        "",
        "## 明细",
        "",
        "| 时间 | 平台 | 采集标题 | 发布标题 | 相似度 |",
        "|---|---|---|---|---|",
    ]
    for x in rows:
        topic = (x["topic_title"][:40] + "…") if len(x["topic_title"]) > 40 else x["topic_title"]
        pub = (x["published_title"][:40] + "…") if len(x["published_title"]) > 40 else x["published_title"]
        lines.append(f"| {x['date']} | {x['platform']} | {topic.replace('|', '/')} | {pub.replace('|', '/')} | {x['similarity']} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取采集 vs 发布标题对比数据")
    parser.add_argument("--days", type=int, default=7, help="统计最近 N 天，默认 7")
    args = parser.parse_args()

    records = collect_records()
    rows, stats = build_rows(records, args.days)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = render_markdown(rows, stats, args.days)
    md_path = OUT_DIR / "collection_vs_publish_report.md"
    json_path = OUT_DIR / "collection_vs_publish_report.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps({"days": args.days, "generated_at": datetime.now().isoformat(timespec="seconds"), "stats": stats, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"采集 vs 发布对比：最近 {args.days} 天共 {stats['total']} 条发布记录")
    print(f"平均相似度 {stats['avg_similarity']}（越低=二创改造越大）")
    print(f"  极低相似(<0.2): {stats['sim_lt_0_2']} 条 | 低相似(0.2-0.5): {stats['sim_0_2_0_5']} 条 | "
          f"中相似(0.5-0.8): {stats['sim_0_5_0_8']} 条 | 高相似(>=0.8): {stats['sim_ge_0_8']} 条")
    if rows:
        print("\n最近记录：")
        for x in rows[-5:]:
            topic = x["topic_title"][:24]
            pub = x["published_title"][:24]
            print(f"  [{x['date']}][{x['platform']}] 相似度 {x['similarity']:>6} | 采集: {topic} → 发布: {pub}")
    print(f"\n报告已保存：{md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())