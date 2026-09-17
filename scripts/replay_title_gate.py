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

from src.toutiao_content_factory.agents.title_agent import (  # noqa: E402
    REASON_VALIDATION_CONTEXT_UNAVAILABLE,
    TitleAgent,
)
from src.toutiao_content_factory.config import load_settings  # noqa: E402
from src.toutiao_content_factory.models import (  # noqa: E402
    CacheContext,
    DataReport,
    GeneratedContent,
    TrendReport,
)
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
    """产出 (path, body, titles, content, data_report, trend_report, style_key)。"""
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        content_payload = payload.get("content")
        if not isinstance(content_payload, dict):
            continue
        body = content_payload.get("article") or content_payload.get("polished_article") or ""
        if not isinstance(body, str) or len(body.strip()) < 200:
            continue
        titles = [
            str(t).strip()
            for t in (content_payload.get("titles") or [])
            if str(t).strip()
        ]
        if not titles:
            continue
        yield (
            path,
            body,
            titles,
            GeneratedContent.from_dict(content_payload),
            DataReport.from_dict(payload.get("data_report") or {}),
            TrendReport.from_dict(payload.get("trend_report") or {}),
            str(payload.get("style_key") or ""),
        )


def _split_cached_pool(
    agent: TitleAgent,
    titles: list[str],
    content: GeneratedContent,
    trend: TrendReport,
    data_report: DataReport,
) -> tuple[list[str], list[str]]:
    """把缓存里合并落盘的候选拆回「模型池 / 兜底池」。

    缓存只存了**合并后**的 `content.titles`，没有分开存。但兜底是**纯规则生成**
    （同样的正文 + 同样的 data_report ⇒ 同样的兜底集合），所以可以离线把它重新算一遍：

        model_pool   = 合并池 − 兜底集合        （还原出模型当时给的候选）
        fallback_pool = **重新生成的完整兜底集合**

    注意救援池用的是「重新生成的完整集合」，而不是「合并池 ∩ 兜底集合」：
    后者只包含**当时活下来**的那几条，而线上在模型池全灭时是拿完整兜底集合去过的，
    用交集会把救援能力系统性低估。

    误差只有一处：若模型恰好写出一条与兜底**字面完全相同**的标题，会被算进兜底侧。
    """
    style_key = getattr(agent.settings, "default_style", "")
    style = (agent.settings.style_profiles or {}).get(style_key)
    if style is None:
        print(f"[warn] 找不到 style_profile({style_key!r})，兜底池无法还原", file=sys.stderr)
        return list(titles), []
    try:
        playbook = agent._build_title_playbook_context(data_report)
    except Exception as exc:
        print(f"[warn] playbook 上下文构建失败，按空处理: {exc!r}", file=sys.stderr)
        playbook = None
    try:
        fallback = agent._fallback_titles(
            content, trend, data_report, style, CacheContext(), playbook_context=playbook
        )
    except Exception as exc:
        # ⚠️ 这里**不能**只 `return list(titles), []` —— 第一次写这个函数时
        # `CacheContext` 忘了 import，NameError 被这个 except 静静吃掉，
        # 于是「兜底池永远为空」看起来像「兜底本来就生成不出东西」。
        # 静默的宽 except 会把编码错误伪装成业务事实。
        print(f"[warn] 兜底集合还原失败，本次按空处理: {exc!r}", file=sys.stderr)
        return list(titles), []
    fallback_set = set(fallback)
    model_pool = [t for t in titles if t not in fallback_set]
    return model_pool, list(fallback)


def evaluate_run(
    agent: TitleAgent,
    body: str,
    titles: list[str],
    content: GeneratedContent | None = None,
    data_report: DataReport | None = None,
    style_key: str = "",
    trend: TrendReport | None = None,
) -> dict:
    """对一个 run 做一次**完整的三层池**回放（零 LLM）。

    与线上 `TitleAgent.run()` 同序：模型池 → （模型池 0 合格才）兜底池 →
    （前两层都 0 合格才）应急池；三池都不混池排名，且都过同一个校验入口。

    同时给出 BEFORE（Phase 1B 口径：只有标题闸门）用于对照 —— 两组判定吃同一份
    正文、同一份候选，差异只来自「规则闸门 + 全灭即失败」这两处改动。
    """
    report = data_report if data_report is not None else DataReport()
    trend_report = trend if trend is not None else TrendReport()
    checker = ArticleQualityChecker()
    body_integrity = checker.check_body_integrity(body)

    gate_anchors = high_confidence_anchors(body)
    hook_gate_active = len(gate_anchors) >= GATE_MIN_ANCHORS

    reasons: Counter[str] = Counter()
    before_reasons: Counter[str] = Counter()
    passed: list[str] = []
    before_passed: list[str] = []
    unavailable: list[str] = []
    rejected_outcomes: list = []
    seen: set[str] = set()
    for raw in titles:
        title = agent._sanitize_title(raw)
        if not title:
            continue
        normalized = agent.normalize_title(title)
        if normalized in seen:      # 缓存里的候选可能有重复，计数会被重复放大
            continue
        seen.add(normalized)

        # ── BEFORE（Phase 1B）：仅标题闸门 ──────────────────────────────────
        before_codes, _hook = agent._title_gate_reasons(title, body)
        if before_codes:
            for code in before_codes:
                before_reasons[code] += 1
        else:
            before_passed.append(title)

        # ── AFTER（Phase 1）：标题闸门 ∪ 规则闸门（与线上同一个入口）──────
        outcome = agent.validate_title_candidate(title, body)
        substantive = tuple(
            code for code in outcome.reason_codes if code != REASON_VALIDATION_CONTEXT_UNAVAILABLE
        )
        if outcome.passed:
            passed.append(title)
        elif substantive:
            for code in substantive:
                reasons[code] += 1
            rejected_outcomes.append(outcome)
        else:
            unavailable.append(title)

    if not hook_gate_active:
        reasons["concrete_hook_unavailable"] += len(seen)
        before_reasons["concrete_hook_unavailable"] += len(seen)
    if unavailable:
        reasons[REASON_VALIDATION_CONTEXT_UNAVAILABLE] += len(unavailable)

    # ── 三层池（还原缓存里看不出来的模型/兜底划分）─────────────────────────
    model_pool_titles: list[str] = list(seen)
    fallback_pool_titles: list[str] = []
    if content is not None and data_report is not None:
        model_pool_titles, fallback_pool_titles = _split_cached_pool(
            agent, list(seen), content, trend_report, report
        )

    model_pool = agent._build_title_pool("model", model_pool_titles, body, report, [])
    fallback_pool = None
    emergency_pool = None
    if not model_pool.valid_titles:
        fallback_pool = agent._build_title_pool(
            "fallback", fallback_pool_titles, body, report, []
        )
    if not model_pool.valid_titles and not (fallback_pool and fallback_pool.valid_titles):
        emergency_titles: list[str] = []
        if content is not None and data_report is not None:
            try:
                emergency_titles = agent._emergency_titles(content, report)
            except Exception:
                emergency_titles = []
        emergency_pool = agent._build_title_pool("emergency", emergency_titles, body, report, [])

    chosen = (
        model_pool
        if model_pool.valid_titles
        else fallback_pool
        if fallback_pool and fallback_pool.valid_titles
        else emergency_pool
    )
    selected = ""
    selected_source = ""
    if chosen and chosen.valid_titles:
        selected = max(
            chosen.valid_titles,
            key=lambda t: ArticleQualityChecker().title_click_preference(t),
        )
        selected_source = chosen.source

    selected_anchor = ""
    if selected:
        for anchor in gate_anchors:
            if anchor.matches(selected):
                selected_anchor = f"{anchor.kind}:{anchor.text}"
                break

    return {
        "body_len": len(body),
        "style_key": style_key,
        "candidates": len(seen),
        "passed": len(passed),
        "before_passed": len(before_passed),
        "rejected": len(seen) - len(passed) - len(unavailable),
        "unavailable": len(unavailable),
        "zero_pass": not passed,
        "before_zero_pass": not before_passed,
        "final_zero_pass": not selected,
        "status": (
            "passed" if passed and not reasons
            else "partial" if passed
            else "all_rejected"
        ),
        # ── §38 要求的逐层统计 ─────────────────────────────────────────────
        "model_generated": model_pool.to_dict()["generated_count"],
        "model_valid": model_pool.to_dict()["passed_count"],
        "model_rejected": model_pool.to_dict()["reject_count"],
        "fallback_generated": (fallback_pool.to_dict()["generated_count"] if fallback_pool else 0),
        "fallback_valid": (fallback_pool.to_dict()["passed_count"] if fallback_pool else 0),
        "fallback_rejected": (fallback_pool.to_dict()["reject_count"] if fallback_pool else 0),
        "emergency_generated": (emergency_pool.to_dict()["generated_count"] if emergency_pool else 0),
        "emergency_valid": (emergency_pool.to_dict()["passed_count"] if emergency_pool else 0),
        "selected_title": selected,
        "selected_title_source": selected_source,
        "selected_title_anchor": selected_anchor,
        "all_rejected_count": (chosen.reject_count if chosen else 0),
        "unavailable_count": (chosen.unavailable_count if chosen else len(unavailable)),
        "rejected_samples": [
            {"title": item.title, "reason_codes": list(item.reason_codes)}
            for item in rejected_outcomes[:5]
        ],
        "hook_gate_active": hook_gate_active,
        "anchor_count": len(extract_concrete_anchors(body)),
        "high_confidence_anchor_count": len(gate_anchors),
        "reason_summary": dict(reasons),
        "before_reason_summary": dict(before_reasons),
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
    # 只跑应急池（`_emergency_titles`）与三个池的校验，不生成兜底句式，
    # 所以不需要把 style 切来切去 —— 也就不去动 agent.settings。
    for path, body, titles, content, data_report, trend, style_key in _iter_run_caches(cache_dir):
        if args.limit and len(runs) >= args.limit:
            break
        stats = evaluate_run(agent, body, titles, content, data_report, style_key, trend)
        stats["cache_file"] = path.name
        runs.append(stats)

    total = len(runs)
    if not total:
        print(f"（{cache_dir} 下没有可回放的 run：需要有 data_report/正文/titles）")
        return 1

    zero_pass = sum(1 for r in runs if r["zero_pass"])
    before_zero_pass = sum(1 for r in runs if r["before_zero_pass"])
    final_zero_pass = sum(1 for r in runs if r["final_zero_pass"])
    selected_from: Counter[str] = Counter(
        r["selected_title_source"] or "(无)" for r in runs
    )
    totals: Counter[str] = Counter()
    for run in runs:
        for key in (
            "model_generated", "model_valid", "model_rejected",
            "fallback_generated", "fallback_valid", "fallback_rejected",
            "emergency_generated", "emergency_valid",
            "all_rejected_count", "unavailable_count",
        ):
            totals[key] += int(run.get(key) or 0)
    body_fail = sum(1 for r in runs if not r["body_integrity_passed"])
    hook_off = sum(1 for r in runs if not r["hook_gate_active"])
    reason_totals: Counter[str] = Counter()
    before_reason_totals: Counter[str] = Counter()
    for run in runs:
        reason_totals.update(run["reason_summary"])
        before_reason_totals.update(run["before_reason_summary"])

    total_candidates = sum(r["candidates"] for r in runs)
    total_passed = sum(r["passed"] for r in runs)
    total_before_passed = sum(r["before_passed"] for r in runs)
    total_rejected = sum(r["rejected"] for r in runs)
    total_unavailable = sum(r["unavailable"] for r in runs)
    status_totals: Counter[str] = Counter(r["status"] for r in runs)

    print(f"=== 回放 run 数: {total} ===")
    print(f"  候选总数            : {total_candidates}")
    print(
        f"  候选通过  BEFORE={total_before_passed}"
        f"（{total_before_passed / max(total_candidates, 1) * 100:.1f}%）"
        f"  →  AFTER={total_passed}"
        f"（{total_passed / max(total_candidates, 1) * 100:.1f}%）"
    )
    print(f"  候选被拒 AFTER      : {total_rejected}")
    print(f"  候选无正文 AFTER    : {total_unavailable}")
    print(
        f"  候选过闸 = 0 的 run : BEFORE={before_zero_pass}/{total}"
        f"（{before_zero_pass / total * 100:.1f}%）"
        f"  →  AFTER={zero_pass}/{total}（{zero_pass / total * 100:.1f}%）"
    )
    print(
        "  run 状态分布        : "
        + "  ".join(f"{k}={v}" for k, v in sorted(status_totals.items()))
    )
    print("\n=== 三层池逐层统计（§38）===")
    print(
        f"  模型池   生成 {totals['model_generated']:5d}  合格 {totals['model_valid']:5d}"
        f"  拒绝 {totals['model_rejected']:5d}"
    )
    print(
        f"  兜底池   生成 {totals['fallback_generated']:5d}  合格 {totals['fallback_valid']:5d}"
        f"  拒绝 {totals['fallback_rejected']:5d}"
        f"    （仅在模型池 0 合格时才构建：{sum(1 for r in runs if r['fallback_generated'])} 个 run）"
    )
    print(
        f"  应急池   生成 {totals['emergency_generated']:5d}  合格 {totals['emergency_valid']:5d}"
        f"    （仅在前两层都 0 合格时才构建：{sum(1 for r in runs if r['emergency_generated'])} 个 run）"
    )
    print(
        "  最终标题来源        : "
        + "  ".join(f"{k}={v}" for k, v in sorted(selected_from.items(), key=lambda kv: -kv[1]))
    )
    print(f"  all_rejected 计数    : {totals['all_rejected_count']}")
    print(f"  unavailable 计数     : {totals['unavailable_count']}")
    print(
        f"  ★ 三层全灭（真正发不出去）: {final_zero_pass}/{total}"
        f"（{final_zero_pass / total * 100:.1f}%）"
    )
    print(
        f"  正文层不合格 run    : {body_fail}/{total}（{body_fail / total * 100:.1f}%）"
    )
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
    print("\n=== 拒绝原因汇总（AFTER，按候选计）===")
    for code, count in reason_totals.most_common():
        print(f"  {code:36s} {count}")
    print("\n=== 拒绝原因汇总（BEFORE / Phase 1B 口径）===")
    for code, count in before_reason_totals.most_common():
        print(f"  {code:36s} {count}")

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
            "before_zero_pass_runs": before_zero_pass,
            "final_zero_pass_runs": final_zero_pass,
            "selected_from": dict(selected_from),
            "layer_totals": dict(totals),
            "candidate_total": total_candidates,
            "candidate_passed": total_passed,
            "candidate_before_passed": total_before_passed,
            "candidate_rejected": total_rejected,
            "candidate_unavailable": total_unavailable,
            "status_totals": dict(status_totals),
            "body_integrity_fail_runs": body_fail,
            "reason_totals": dict(reason_totals),
            "before_reason_totals": dict(before_reason_totals),
            "per_run": runs,
        }
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结构化结果已写入: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
