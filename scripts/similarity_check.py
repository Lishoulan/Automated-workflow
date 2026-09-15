#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""量化生成稿与素材原文的重合度（洗稿风险检测）——命令行入口。

算法本体在 `toutiao_content_factory.services.originality_service`，本脚本只是
一层薄封装，保证命令行和自动流程（content_guard）用的是同一份实现，避免
两处阈值/口径漂移。

头条原创库是全网比对：素材原文若已被其他账号先发（媒体多平台分发很常见），
改写稿与原文重合度过高就会被判「不原创」并扣分（首发激励计划 -5 分/次）。
2026-09-15 实测案例：run 20260908-093307 因与「蛇眼财经」9-07 抢发的
ofweek 原文几乎逐字相同被扣分，而本地查重原先只对自家历史，查不出这种风险。

用法：
    python scripts/similarity_check.py <生成稿.md> <素材原文.txt>
    python scripts/similarity_check.py --run 20260915215615
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.services.originality_service import (  # noqa: E402
    BLOCK_THRESHOLD,
    REVIEW_THRESHOLD,
    source_copy_ratio,
)

RUNS_DIR = ROOT / "output" / "accounts" / "robot-notes" / "runs"


def _report(article: str, source: str) -> int:
    result = source_copy_ratio(article, source)
    ratio = result["ratio"]
    if ratio is None:
        print(f"无法判断：{result['reason']}")
        return 1
    level = ("高风险" if ratio >= BLOCK_THRESHOLD
             else ("观察" if ratio >= REVIEW_THRESHOLD else "安全"))
    print(f"连续抄录：约 {result['copied_chars']} 字 / 正文 {result['article_chars']} 字"
          f" = {ratio}%（{level}）")
    print(f"素材原文：{result['source_chars']} 字")
    print(f"阈值：≥{REVIEW_THRESHOLD}% 需复核，≥{BLOCK_THRESHOLD}% 拦截")
    samples = result["samples"]
    if samples:
        print("\n最长的连续相同片段（前5段）:")
        for i, span in enumerate(samples, 1):
            show = span if len(span) <= 80 else span[:80] + "…"
            print(f"  {i}. [{len(span)}字] {show}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="素材重合度检测")
    parser.add_argument("article", nargs="?", help="生成稿路径")
    parser.add_argument("source", nargs="?", help="素材原文路径")
    parser.add_argument("--run", default="", help="按 run ID 自动取正文与素材原文")
    args = parser.parse_args()

    if args.run:
        matches = [d for d in RUNS_DIR.iterdir() if d.is_dir()
                   and d.name.split("_", 1)[0].replace("-", "") == args.run]
        if not matches:
            print(f"未找到 run: {args.run}")
            return 1
        run_dir = matches[0]
        article_file = run_dir / "article.md"
        source_file = run_dir / "source_material_full.txt"
        if not article_file.exists():
            print(f"缺正文: {article_file}")
            return 1
        if not source_file.exists():
            print(f"缺素材原文: {source_file}（可用 scripts/fetch_source_material.py 补抓）")
            return 1
        print(f"run: {run_dir.name}\n")
        return _report(article_file.read_text(encoding="utf-8"),
                       source_file.read_text(encoding="utf-8"))

    if not (args.article and args.source):
        parser.print_help()
        return 1
    return _report(Path(args.article).read_text(encoding="utf-8"),
                   Path(args.source).read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
