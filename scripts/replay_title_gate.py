"""全量历史 run 的标题闸门离线回放（**不调用 LLM、不联网**）。

用途：回答「现在这套闸门，放到历史上真实跑过的每一篇稿子上，会不会把全部候选杀光」。
这是 D2 决策与 Blocker 1/2 验收的**唯一可信依据** —— 单看两个选题的个案会误导
（2026-09-17 的教训：两个选题复现同一模式，但诱因完全不同：一个是正文残句误报，
一个是正文提不出具象元素）。

数据来源：`cache/*.json`，只挑「有 data_report、有正文、有 titles」的运行缓存。
标题候选直接取缓存里落盘的 `content.titles`（即当时真实产出的候选池），
因此回放结果**可复现、且与当时的候选池一致**。

输出（每个 run 一行 + 汇总）：
  - body_integrity        正文层自身是否有硬伤（HTML/占位/残句）
  - zero_pass             该 run 有没有**任何**候选通过全部闸门
  - 各 reason_code 的计数（缺具象钩子 / 标题与正文不一致 / 禁用词 / 超长 …）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.toutiao_content_factory.agents.title_agent import TitleAgent  # noqa: E402
from src.toutiao_content_factory.config import load_settings  # noqa: E402
from src.toutiao_content_factory.services.article_quality_checker import (  # noqa: E402
    ArticleQualityChecker,
)
from src.toutiao_content_factory.services.concrete_anchors import (  # noqa: E402
    GATE_MIN_ANCHORS,
    extract_concrete_anchors,
    high_confidence_anchors,
)
from src.toutiao_content_factory.services.openai_service import (  # noqa: E402
    OpenAIContentService,
)
from src.toutiao_content_factory.services.prompt_repository import (  # noqa: E402
    PromptRepository,
)

ACCOUNT_KEY = "robot_notes"


def _build_agent() -> TitleAgent:
    """构建一个**不会调用 LLM** 的 TitleAgent（只用来跑闸门判定）。"""
    settings = load_settings(PROJECT_ROOT, ACCOUNT_KEY)
    logger = logging.getLogger("title-gate-replay")
    return TitleAgent(
        settings,
        PromptRepository(settings.prompts_dir),
        OpenAIContentService(settings, logger),
        logger,
    )


def _iter_run_caches(cache_dir: Path):
    """产出 (path, body, titles) —— 只保留有正文且有候选标题的运行缓存。"""
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if not isinstance(content, dict):
            continue
        body = content.get("article") or content.get("polished_article") or ""
        if not isinstance(body, str) or len(body.strip()) < 200:
            continue
        titles = [
            str(t).strip()
            for t in (content.get("titles") or [])
            if str(t).strip()
        ]
        if not titles:
            continue
        yield path, body, titles


def evaluate_run(agent: TitleAgent, body: str, titles: list[str]) -> dict:
    """对一个 run 的候选池做一次闸门回放。"""
    checker = ArticleQualityChecker()
    max_length = agent._title_max_length()
    body_integrity = checker.check_body_integrity(body)

    # 具象锚点闸门是**正文级**的开关：正文锚点不够时整道闸记 UNAVAILABLE，
    # 此时它不该否决任何标题（旧实现是静默放行，我们改成如实留痕）。
    gate_anchors = high_confidence_anchors(body)
    hook_gate_active = len(gate_anchors) >= GATE_MIN_ANCHORS

    reasons: Counter[str] = Counter()
    passed: list[str] = []
    seen: set[str] = set()
    for raw in titles:
        title = agent._sanitize_title(raw)
        if not title:
            continue
        normalized = agent.normalize_title(title)
        if normalized in seen:      # 缓存里的候选可能有重复，计数会被重复放大
            continue
        seen.add(normalized)

        title_reasons: list[str] = []
        if len(title) > max_length:
            title_reasons.append("title_too_long")
        for pattern in agent.DISCOURAGED_TITLE_PATTERNS:
            if pattern in title:
                title_reasons.append(f"discouraged_pattern:{pattern}")
                break
        if any(p.search(title) for p in agent.UNSUPPORTED_FINANCIAL_LINK_PATTERNS):
            title_reasons.append("unsupported_financial_link")
        # ⚠️ 标题层只用分层后的入口：正文自身的问题不算标题的问题（Blocker 2）。
        if checker.check_title_body_consistency(title, body).issues:
            title_reasons.append("title_body_inconsistency")
        if hook_gate_active and 10 <= len(title) <= max_length:
            if not any(anchor.matches(title) for anchor in gate_anchors):
                title_reasons.append("missing_concrete_hook")

        if title_reasons:
            for code in title_reasons:
                reasons[code] += 1
        else:
            passed.append(title)

    if not hook_gate_active:
        reasons["concrete_hook_unavailable"] += len(seen)

    return {
        "body_len": len(body),
        "candidates": len(seen),
        "passed": len(passed),
        "zero_pass": not passed,
        "hook_gate_active": hook_gate_active,
        "anchor_count": len(extract_concrete_anchors(body)),
        "high_confidence_anchor_count": len(gate_anchors),
        "reason_summary": dict(reasons),
        "body_integrity_passed": body_integrity.passed,
        "body_integrity_fragments": body_integrity.matched_fragments,
        "top_passed": passed[:3],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="标题闸门全量离线回放（无 LLM）")
    parser.add_argument("--cache-dir", default="cache", help="运行缓存目录")
    parser.add_argument("--json", default="", help="把结构化结果写到该路径")
    parser.add_argument(
        "--limit", type=int, default=0, help="只回放前 N 个 run（0 = 全部，调试用）"
    )
    args = parser.parse_args()

    cache_dir = PROJECT_ROOT / args.cache_dir
    agent = _build_agent()

    runs: list[dict] = []
    for path, body, titles in _iter_run_caches(cache_dir):
        if args.limit and len(runs) >= args.limit:
            break
        stats = evaluate_run(agent, body, titles)
        stats["cache_file"] = path.name
        runs.append(stats)

    total = len(runs)
    if not total:
        print(f"（{cache_dir} 下没有可回放的 run：需要有 data_report/正文/titles）")
        return 1

    zero_pass = sum(1 for r in runs if r["zero_pass"])
    body_fail = sum(1 for r in runs if not r["body_integrity_passed"])
    hook_off = sum(1 for r in runs if not r["hook_gate_active"])
    reason_totals: Counter[str] = Counter()
    for run in runs:
        reason_totals.update(run["reason_summary"])

    print(f"=== 回放 run 数: {total} ===")
    print(f"  候选过闸 = 0 的 run : {zero_pass}/{total}（{zero_pass / total * 100:.1f}%）")
    print(f"  正文层不合格 run    : {body_fail}/{total}（{body_fail / total * 100:.1f}%）")
    print(
        f"  具象锚点闸 UNAVAILABLE run: {hook_off}/{total}"
        f"（{hook_off / total * 100:.1f}%，根因在正文太薄，不是标题）"
    )
    anchor_counts = sorted(r["high_confidence_anchor_count"] for r in runs)
    if anchor_counts:
        mid = anchor_counts[len(anchor_counts) // 2]
        print(
            f"  高置信度锚点数 median={mid} min={anchor_counts[0]} max={anchor_counts[-1]}"
        )
    print("\n=== 拒绝原因汇总（按候选计）===")
    for code, count in reason_totals.most_common():
        print(f"  {code:32s} {count}")

    # 按「高置信度锚点数」分桶，看闸门在不同正文丰度下的表现 —— 这是
    # GATE_MIN_ANCHORS 取值的依据。
    print("\n=== 按高置信度锚点数分桶 ===")
    buckets: dict[str, list[dict]] = {}
    for run in runs:
        count = run["high_confidence_anchor_count"]
        key = "0" if count == 0 else ("1" if count == 1 else "2-3" if count <= 3 else "4+")
        buckets.setdefault(key, []).append(run)
    for key in ("0", "1", "2-3", "4+"):
        group = buckets.get(key) or []
        if not group:
            continue
        z = sum(1 for r in group if r["zero_pass"])
        candidates = sum(r["candidates"] for r in group)
        passed = sum(r["passed"] for r in group)
        rate = f"{passed / candidates * 100:.1f}%" if candidates else "n/a"
        print(
            f"  锚点 {key:4s}: run={len(group):3d}  零通过={z:3d}"
            f"（{z / len(group) * 100:5.1f}%）  候选通过率={rate}"
        )

    if args.json:
        out = {
            "runs": total,
            "zero_pass_runs": zero_pass,
            "body_integrity_fail_runs": body_fail,
            "reason_totals": dict(reason_totals),
            "per_run": runs,
        }
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结构化结果已写入: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
