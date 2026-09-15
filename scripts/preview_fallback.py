#!/usr/bin/env python
"""预演「兜底发布」会怎么判——不开浏览器、不调模型、零成本。

背景
----
内容流程跑在 GitHub Actions 上，没有人可以现场看一眼再决定。当所有候选都没
过严格门槛时，`_try_fallback_publish` 会按「就绪度 → 总分」排序逐个尝试，
放行第一篇通过**确定性**安全检查的稿子。这个脚本把同一套判定逻辑在本地跑
一遍，让你在上班前就能知道：今天到底会不会发出去、发的是哪一篇、其余的
为什么被挡。

用法
----
    python scripts/preview_fallback.py --account robot_notes
    python scripts/preview_fallback.py --account robot_notes --date 20260915

输出的 PASS / BLOCK 与线上一致；被挡原因即 `_fallback_safety_check` 给出的
硬伤（字数不足、内部标签、序列化残留、提示词元叙述、抄录率超标、原创性
无法核验、AI 生成风险超标、没有可用标题）。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 与 .github/workflows/daily-run.yml 里的定时任务保持一致，否则阈值会算错。
os.environ.setdefault("TOUTIAO_PUBLISH_ENABLED", "1")
os.environ.setdefault("TOUTIAO_AUTO_PUBLISH", "1")
os.environ.setdefault("TOUTIAO_DRAFT_ONLY", "0")

from toutiao_content_factory.config import load_settings  # noqa: E402
from toutiao_content_factory.services.app_service import (  # noqa: E402
    ContentFactoryAppService,
)


def _score_snapshot(run_dir: Path) -> dict:
    score_file = run_dir / "content_score.json"
    if not score_file.exists():
        return {}
    try:
        payload = json.loads(score_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    scores = payload.get("scores", payload) if isinstance(payload, dict) else {}
    return scores if isinstance(scores, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="预演兜底发布判定（只读、零成本）")
    parser.add_argument("--account", default="robot_notes", help="账号键，如 robot_notes")
    parser.add_argument("--date", default="", help="运行目录前缀，如 20260915；留空取最近 40 个")
    parser.add_argument("--runs-root", default="", help="自定义运行目录根路径")
    args = parser.parse_args()

    settings = load_settings(ROOT, args.account)
    service = ContentFactoryAppService.__new__(ContentFactoryAppService)
    service.settings = settings
    service.logger = logging.getLogger("preview_fallback")

    runs_root = Path(args.runs_root) if args.runs_root else (
        ROOT / "output" / "accounts" / args.account.replace("_", "-") / "runs"
    )
    if not runs_root.exists():
        runs_root = ROOT / "output" / "runs"
    if not runs_root.exists():
        print(f"[ERROR] 找不到运行目录：{runs_root}")
        return 1

    pattern = f"{args.date}*" if args.date else "*"
    runs = sorted(p for p in runs_root.glob(pattern) if p.is_dir())[-40:]
    if not runs:
        print(f"[ERROR] {runs_root} 下没有匹配 {pattern} 的运行")
        return 1

    publishing = settings.publishing
    print(
        f"账号={args.account} 自动发布模式="
        f"{bool(publishing.enabled and publishing.auto_publish and not publishing.draft_only)}"
        f" | AI 风险上限 普通={settings.review.max_auto_publish_ai_risk}"
        f" 热点={settings.review.hot_topic_max_auto_publish_ai_risk}"
    )

    rows = []
    for run in runs:
        scores = _score_snapshot(run)
        safe, reasons, notes = service._fallback_safety_check(run)
        rows.append(
            (
                run.name,
                safe,
                reasons,
                notes,
                int(scores.get("publish_readiness_score", 0) or 0),
                int(scores.get("overall_score", 0) or 0),
                int(scores.get("ai_generated_risk", 0) or 0),
            )
        )

    passed = [row for row in rows if row[1]]
    print(f"\n=== 候选 {len(rows)} 篇：可发 {len(passed)} / 被挡 {len(rows) - len(passed)} ===")
    for name, safe, reasons, notes, readiness, overall, ai_risk in rows:
        print(
            f"[{'PASS ' if safe else 'BLOCK'}] 就绪={readiness:>3} 总分={overall:>3} "
            f"AI风险={ai_risk:>3}  {name[:56]}"
        )
        for reason in reasons:
            print(f"          - {reason}")

    if not passed:
        print(
            "\n>>> 今天一篇都发不出去（全部候选都有确定性硬伤）。"
            "这是刻意设计：宁可少发一天，也不发会被判不原创/疑似AI的稿子。"
        )
        return 0

    chosen = max(passed, key=lambda row: (row[4], row[5]))
    print(f"\n>>> 兜底会发布：{chosen[0]}")
    print(f"    就绪度={chosen[4]} 总分={chosen[5]} AI风险={chosen[6]}")
    print(f"    说明：{'; '.join(chosen[3]) or '（无）'}")
    if chosen[0] != max(rows, key=lambda row: (row[4], row[5]))[0]:
        print("    注：按就绪度排序的首选被安全检查挡下，故顺延到本篇。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
