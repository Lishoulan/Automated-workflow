#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为历史 run 补抓素材原文，存为 source_material_full.txt。

新生成的稿子会自动留存原文（见 automation._capture_source_material），
本脚本只用于补齐此前没有原文的存量稿件，让它们也能做原创查重。

用法::

    python scripts/fetch_source_material.py --today     # 只补今天的
    python scripts/fetch_source_material.py 20260915215615  # 补指定一篇
    python scripts/fetch_source_material.py --all       # 全部（请求较多，谨慎）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.config import load_settings  # noqa: E402
from toutiao_content_factory.services.topic_collection import TopicCollectionService  # noqa: E402

RUNS_DIR = ROOT / "output" / "accounts" / "robot-notes" / "runs"


def fetch_one(collector: TopicCollectionService, run_dir: Path) -> bool:
    topic_file = run_dir / "selected_topic.json"
    if not topic_file.exists():
        return False
    try:
        payload = json.loads(topic_file.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    topic = payload.get("selected_topic") or {}
    url = str(topic.get("url") or "").strip()
    title = str(topic.get("title") or "").strip()
    if not url.startswith(("http://", "https://")):
        return False

    target = run_dir / "source_material_full.txt"
    if target.exists():
        return False

    fallback = str(topic.get("summary") or topic.get("snippet") or "").strip()
    body = ""
    try:
        html_text = collector._fetch_text_sync(url)
        if html_text:
            body = collector._extract_article_context(html_text, title)
    except Exception as exc:
        print(f"  ! {run_dir.name[:16]} 抓取失败: {type(exc).__name__}")

    content = (body or "").strip() or fallback
    if not content:
        return False
    header = (f"素材来源：{topic.get('source') or '未知'}\n"
              f"原文标题：{title}\n原文链接：{url}\n\n")
    target.write_text(header + content, encoding="utf-8")
    print(f"  + {run_dir.name[:16]} 已存入 {len(content)} 字")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="补抓素材原文")
    parser.add_argument("run_id", nargs="?", default="", help="指定 run 的 ID（如 20260915215615）")
    parser.add_argument("--today", action="store_true", help="只处理今天的")
    parser.add_argument("--all", action="store_true", help="处理全部（请求较多）")
    parser.add_argument("--delay", type=float, default=1.0, help="每次请求间隔秒数")
    args = parser.parse_args()

    settings = load_settings(ROOT)
    collector = TopicCollectionService(settings, logging.getLogger("fetch_source"))

    if args.run_id:
        matches = [d for d in RUNS_DIR.iterdir() if d.is_dir()
                   and d.name.split("_", 1)[0].replace("-", "") == args.run_id]
    else:
        prefix = time.strftime("%Y%m%d") if (args.today or not args.all) else ""
        matches = sorted(
            (d for d in RUNS_DIR.iterdir() if d.is_dir() and d.name.startswith(prefix)),
            reverse=True,
        )

    if not matches:
        print("未找到匹配的 run")
        return 1

    done = 0
    for run_dir in matches:
        if fetch_one(collector, run_dir):
            done += 1
            if args.delay > 0:
                time.sleep(args.delay)
    print(f"\n完成：补充 {done} 篇素材原文")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
