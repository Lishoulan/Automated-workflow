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

另有一个**正文级**语料模式 `--corpus runs`：扫 `output/accounts/*/runs/*/article.md`，
只有正文、没有候选标题，因此不算过闸率，只回答两件事（同样是零 LLM）：
  - 具象锚点层：每个 run 能提几个高置信度锚点 ⇒ 闸门是开着还是 UNAVAILABLE
  - 正文残句正则：**BEFORE（历史正则）/ AFTER（现正则）** 各自误判多少篇
后者是 Blocker 2 在**全量历史**上的影响面（`cache/` 只有 100 余个 run）。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
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

#: **历史**正文残句正则（2026-09-17 已从源码删除，仅保留在此用于 BEFORE 复算）。
#: 在 457 篇真实正文上命中 22.1%、精度为 0 —— 单字连接词会命中更长词的内部
#: （`订单、并网` 的 `并` 是「并网」首字）。留着它是为了让「修了多少」可被复现验证，
#: **任何生产代码都不得再引用它**。
_LEGACY_BODY_FRAGMENT_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9]{2,24}、(?:为|是|在|与|和|并|但|或)"
)


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


def evaluate_body_only(body: str) -> dict:
    """只知道正文时能回答的两件事：锚点层 + 残句层（BEFORE/AFTER）。"""
    checker = ArticleQualityChecker()
    body_integrity = checker.check_body_integrity(body)
    legacy_hits = _LEGACY_BODY_FRAGMENT_PATTERN.findall(body)
    anchors = extract_concrete_anchors(body)
    gate_anchors = high_confidence_anchors(body)
    return {
        "body_len": len(body),
        "anchor_count": len(anchors),
        "high_confidence_anchor_count": len(gate_anchors),
        "hook_gate_active": len(gate_anchors) >= GATE_MIN_ANCHORS,
        "anchors_sample": [a.text for a in gate_anchors[:5]],
        "legacy_fragment_flag": bool(legacy_hits),
        "legacy_fragment_hits": legacy_hits[:3],
        "body_integrity_passed": body_integrity.passed,
        "body_integrity_fragments": body_integrity.matched_fragments,
    }


def _iter_run_bodies(accounts_root: Path):
    """产出 (path, body) —— 扫所有账号 runs 目录下的 `article.md`。"""
    for path in sorted(accounts_root.glob("*/runs/*/article.md")):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(body.strip()) < 200:
            continue
        yield path, body


def _run_body_corpus(args) -> int:
    """正文级语料回放：全量历史 `article.md` 上的锚点层 + 残句层（BEFORE/AFTER）。"""
    accounts_root = PROJECT_ROOT / args.accounts_root
    runs: list[dict] = []
    seen_bodies: set[str] = set()
    for path, body in _iter_run_bodies(accounts_root):
        if args.limit and len(runs) >= args.limit:
            break
        stats = evaluate_body_only(body)
        stats["run"] = f"{path.parents[2].name}/{path.parents[0].name}"
        runs.append(stats)
        seen_bodies.add(body.strip())

    total = len(runs)
    if not total:
        print(f"（{accounts_root} 下没有可回放的正文）")
        return 1

    legacy = sum(1 for r in runs if r["legacy_fragment_flag"])
    after = sum(1 for r in runs if not r["body_integrity_passed"])
    gate_on = sum(1 for r in runs if r["hook_gate_active"])
    anchors = [r["high_confidence_anchor_count"] for r in runs]

    print(f"=== 正文语料: {total} 个 run（去重后 {len(seen_bodies)} 篇正文）===")
    print(
        f"  正文残句误判  BEFORE（历史正则）: {legacy}/{total}"
        f"（{legacy / total * 100:.1f}%）   AFTER（现正则）: {after}/{total}"
        f"（{after / total * 100:.1f}%）"
    )
    print(
        f"  具象锚点闸 ACTIVE（≥{GATE_MIN_ANCHORS} 个高置信锚点）: {gate_on}/{total}"
        f"（{gate_on / total * 100:.1f}%）  UNAVAILABLE: {total - gate_on}"
        f"（{(total - gate_on) / total * 100:.1f}%）"
    )
    buckets = Counter()
    for count in anchors:
        buckets["0" if count == 0 else ("1" if count == 1 else "2-3" if count <= 3 else "4+")] += 1
    print("  锚点分桶: " + "  ".join(f"{k}={buckets.get(k, 0)}" for k in ("0", "1", "2-3", "4+")))

    print("\n=== 仍被现正则标记的正文（应全部是真残句）===")
    for r in runs:
        if not r["body_integrity_passed"]:
            print(f"  {r['run']}  片段={r['body_integrity_fragments']}")

    if args.json:
        out = {
            "runs": total,
            "unique_bodies": len(seen_bodies),
            "legacy_fragment_flag_runs": legacy,
            "body_integrity_fail_runs": after,
            "hook_gate_active_runs": gate_on,
            "anchor_buckets": dict(buckets),
            "per_run": runs,
        }
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结构化结果已写入: {args.json}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="标题闸门全量离线回放（无 LLM）")
    parser.add_argument("--cache-dir", default="cache", help="运行缓存目录")
    parser.add_argument("--json", default="", help="把结构化结果写到该路径")
    parser.add_argument(
        "--limit", type=int, default=0, help="只回放前 N 个 run（0 = 全部，调试用）"
    )
    parser.add_argument(
        "--corpus",
        choices=("cache", "runs"),
        default="cache",
        help="cache=有候选池的运行缓存（能算过闸率）；runs=历史 runs 正文（只算锚点/残句层）",
    )
    parser.add_argument(
        "--accounts-root",
        default="output/accounts",
        help="--corpus runs 时的账号产物根目录",
    )
    args = parser.parse_args()

    if args.corpus == "runs":
        return _run_body_corpus(args)

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
