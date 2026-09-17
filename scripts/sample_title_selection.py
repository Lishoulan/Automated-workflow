"""从回放结果里随机抽样，人工可核查（Phase 1 §41）。

用法：
    .venv/Scripts/python.exe scripts/replay_title_gate.py --json .runtime/replay_phase1.json
    .venv/Scripts/python.exe scripts/sample_title_selection.py --replay .runtime/replay_phase1.json

输出两段：
  * **30 条最终 selected title** —— 带来源池、命中的具象锚点、状态；
  * **20 条 rejected title** —— 带拒绝原因，用来人工确认「有没有正常标题被误拒」。

随机用固定种子，所以同一份 replay JSON 每次抽到的是同一批，结论可复核。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED = 20260917


def main() -> int:
    parser = argparse.ArgumentParser(description="标题选择结果抽样核查（离线）")
    parser.add_argument("--replay", default=".runtime/replay_phase1.json")
    parser.add_argument("--selected", type=int, default=30)
    parser.add_argument("--rejected", type=int, default=20)
    parser.add_argument("--json", default="", help="把抽样结果写到该路径")
    args = parser.parse_args()

    payload = json.loads((PROJECT_ROOT / args.replay).read_text(encoding="utf-8"))
    runs = payload["per_run"]
    rng = random.Random(SEED)

    winners = [
        {
            "run": r["cache_file"],
            "title": r["selected_title"],
            "source": r["selected_title_source"] or "(无)",
            "anchor": r["selected_title_anchor"] or "(无)",
            "cache_pool_status": r["status"],
            "body_len": r["body_len"],
            "anchor_count": r["high_confidence_anchor_count"],
        }
        for r in runs
        if r.get("selected_title")
    ]
    rejected_pool = [
        {"run": r["cache_file"], **item}
        for r in runs
        for item in r.get("rejected_samples", [])
    ]

    picked_winners = rng.sample(winners, min(args.selected, len(winners)))
    picked_rejected = rng.sample(rejected_pool, min(args.rejected, len(rejected_pool)))

    print(f"=== 随机 {len(picked_winners)} 条最终 selected title（共 {len(winners)} 条可选）===")
    for item in picked_winners:
        print(
            f"  [缓存池:{item['cache_pool_status']:11s}] src={item['source']:12s} "
            f"anchor={item['anchor']:24s} {item['title']}"
        )
    print(f"\n=== 随机 {len(picked_rejected)} 条 rejected title（共 {len(rejected_pool)} 条可选）===")
    for item in picked_rejected:
        print(f"  {item['title']}")
        print(f"      原因: {', '.join(item.get('reason_codes', []))}")

    source_totals: dict[str, int] = {}
    for item in winners:
        source_totals[item["source"]] = source_totals.get(item["source"], 0) + 1
    print("\n=== 有可用标题的 run 的来源分布 ===")
    for source, count in sorted(source_totals.items(), key=lambda kv: -kv[1]):
        print(f"  {source:18s} {count}")
    print(
        f"\n  有可用标题的 run: {len(winners)}/{len(runs)}"
        f"；三层全灭: {payload.get('final_zero_pass_runs')}/{len(runs)}"
    )

    if args.json:
        Path(PROJECT_ROOT / args.json).write_text(
            json.dumps(
                {"selected": picked_winners, "rejected": picked_rejected, "seed": SEED},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n抽样结果已写入: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
