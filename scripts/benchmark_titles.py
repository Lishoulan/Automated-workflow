#!/usr/bin/env python
"""离线标题形态基线（Offline title-shape baseline）。

目的：在**不调用任何 LLM、不启动 Playwright**的前提下，量化历史运行产出的
「标题形态」，为后续标题系统改造提供可比较的基线。

⚠️ 这不是 CTR 因果证明。历史样本混合了发布时间、账号阶段、选题、推荐权重、
历史时期与模型能力，只能作为**标题形态基线**使用。

用法：
    python scripts/benchmark_titles.py
    python scripts/benchmark_titles.py --md docs/TITLE_BENCHMARK_BASELINE_20260917.md
    python scripts/benchmark_titles.py --json .runtime/title_benchmark.json
    python scripts/benchmark_titles.py --source output/accounts/robot-notes/runs --limit 40

数据来源（可复选，默认全开）：
    output/accounts/<slug>/runs/      本地历史运行
    .cloud-artifacts/                 云端产物缓存
    .runtime/diag/                    诊断下载产物
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# 归一化与相似度
# ---------------------------------------------------------------------------

_PUNCT_PATTERN = re.compile(r"[\s　!-/:-@\[-`{-~ -⁯、-〿！-･]+")
_DIGIT_PATTERN = re.compile(r"\d+(?:\.\d+)?%?")

# 聚类阈值：用 9-17 真实塌缩样本校准，见 largest_cluster_ratio 的 docstring。
CLUSTER_THRESHOLD = 0.50


def normalize_title(title: str) -> str:
    """归一化：去标点/空白、数字统一为 #、英文转小写。"""
    text = _PUNCT_PATTERN.sub("", title or "")
    text = _DIGIT_PATTERN.sub("#", text)
    return text.lower()


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def normalized_title_similarity(a: str, b: str) -> float:
    """标题相似度：字符 2-gram Jaccard 与 SequenceMatcher 的平均。

    纯离线、无 embedding、不联网。之所以不用项目现有的 ``_title_skeleton()``：
    它只做「前缀截断 / 固定填充词替换」，模型换个后缀就能绕过（9-17 已验证）。
    """
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    ga, gb = _char_ngrams(na), _char_ngrams(nb)
    jaccard = len(ga & gb) / len(ga | gb) if (ga | gb) else 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    return round((jaccard + seq) / 2, 4)


def largest_cluster_ratio(titles: list[str], threshold: float = CLUSTER_THRESHOLD) -> float:
    """单链接聚类（贪心）下最大簇占比。

    ⚠️ 阈值 0.50 是**用 9-17 真实塌缩样本校准出来的**，不是拍脑袋：
    那次 8 条候选里肉眼可见 6 条是「埃斯顿出货量…机器人市场…」，两两相似度落在
    0.54–0.63；阈值取 0.72 时最大簇只有 0.125（严重低估），取 0.50 才是 0.625（与肉眼一致）。
    换阈值会显著改变数值，所以指标名里带阈值。
    """
    n = len(titles)
    if n <= 1:
        return 1.0 if n == 1 else 0.0
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if normalized_title_similarity(titles[i], titles[j]) >= threshold:
                parent[find(i)] = find(j)
    sizes: dict[int, int] = {}
    for i in range(n):
        root = find(i)
        sizes[root] = sizes.get(root, 0) + 1
    return round(max(sizes.values()) / n, 4)


# ---------------------------------------------------------------------------
# 可解释的启发式特征（不引入任何模型）
# ---------------------------------------------------------------------------

_QUESTION_PATTERN = re.compile(r"(？|\?$|吗|谁在|谁会|谁能|哪一家|哪家|为什么|凭什么|能不能|够不够|会不会|怎么办)")
_JUDGMENT_PATTERN = re.compile(
    r"(才是|不是.{1,10}而是|不再|不再是|开始|才是关键|先流向|先拿到|分化|兑现|"
    r"考验|谁在拿走|凭什么|卡在|落后于|真正的|其实|未必|反而是|决定)"
)
_CONTRAST_PATTERN = re.compile(r"(但|却|然而|虽然|反而|不只是|并非|一边.{1,8}一边|从.{1,8}到)")
_RESULT_PATTERN = re.compile(
    r"(突破|超越|首次|纪录|量产|交付|订单|落成|落地|兑现|盈亏|扭亏|增长|下滑|"
    r"受益|承压|洗牌|分化|重估|盈亏|回款|利润|营收|出货)"
)
_TREND_PATTERN = re.compile(r"(新阶段|新格局|趋势|进入|开始|加速|转向|拐点|周期|浪潮|升温|降温|拐弯)")
_INDUSTRY_PATTERN = re.compile(r"(产业链|行业|市场|赛道|格局|生态|国产|格局)")
_PROFIT_PATTERN = re.compile(r"(利润|毛利|净利|营收|收入|成本|价格|毛利率|盈利|赚钱|回款|毛利)")
_DELIVERY_PATTERN = re.compile(r"(交付|量产|产能|订单|下线|投产|出货|供货|验收|落地)")
_COMPARISON_PATTERN = re.compile(r"(对比|谁更|还是|还是.{1,6}更|谁先|pk|VS|vs|之别|差距)")
_COMPANY_PATTERN = re.compile(
    r"([一-龥A-Za-z0-9]{2,8}(?:公司|集团|科技|股份|电机|智能|机器人|汽车|电池|传媒|控股))|"
    r"(埃斯顿|优必选|宇树|智元|汇川|舜宇|宁德时代|比亚迪|特斯拉|华为|小米|英伟达)"
)


def classify_archetype(title: str) -> str:
    """简单、可解释的离线 archetype 启发式分类器。

    只用于基线统计与多样性观察，**不参与任何生产决策**。
    顺序即优先级（先匹配者胜）。
    """
    if _QUESTION_PATTERN.search(title):
        return "question_with_premise"
    if _CONTRAST_PATTERN.search(title) and _JUDGMENT_PATTERN.search(title):
        return "counter_intuitive"
    if _COMPARISON_PATTERN.search(title):
        return "comparison"
    if _DELIVERY_PATTERN.search(title) and (_RESULT_PATTERN.search(title) or len(title) <= 22):
        return "delivery_validation"
    if _PROFIT_PATTERN.search(title):
        return "profit_chain"
    if _JUDGMENT_PATTERN.search(title):
        return "judgment"
    if _RESULT_PATTERN.search(title):
        return "result"
    if _TREND_PATTERN.search(title):
        return "trend_change"
    if _INDUSTRY_PATTERN.search(title):
        return "industry_change"
    if len(title) <= 16:
        return "short_news"
    return "other"


# ---------------------------------------------------------------------------
# 产物扫描
# ---------------------------------------------------------------------------

_ALL_HEADER = "全部标题："
_REC_HEADER = "推荐标题："


def parse_titles_file(path: Path) -> tuple[list[str], list[str]]:
    """解析 titles.txt，返回 (全部候选, 推荐标题)。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return [], []
    all_titles: list[str] = []
    recommended: list[str] = []
    bucket: list[str] | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_ALL_HEADER):
            bucket = all_titles
            continue
        if stripped.startswith(_REC_HEADER):
            bucket = recommended
            continue
        if bucket is None:
            continue
        cleaned = re.sub(r"^\d{1,2}[\.、]\s*", "", stripped)
        if cleaned:
            bucket.append(cleaned)
    return all_titles, recommended


def _iter_run_dirs(sources: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for source in sources:
        if not source.exists():
            continue
        for titles_file in source.rglob("titles.txt"):
            run_dir = titles_file.parent
            key = str(run_dir.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(run_dir)
    return found


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


@dataclass
class RunSample:
    run_dir: str
    topic: str
    candidates: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    final_title: str = ""
    guard_blocked: bool | None = None
    published: bool | None = None


@dataclass
class BenchmarkResult:
    samples: list[RunSample]
    sources: list[str]
    data_gaps: list[str]

    def metric_rows(self) -> list[tuple[str, str]]:
        """返回 [(指标名, 值字符串)]。"""
        return _build_metric_rows(self.samples, self.data_gaps)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * pct
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 2)


def _build_metric_rows(samples: list[RunSample], gaps: list[str]) -> list[tuple[str, str]]:
    all_titles = [t for s in samples for t in s.candidates]
    counts = [len(s.candidates) for s in samples if s.candidates]
    lengths = [len(t) for t in all_titles]
    sims = [
        normalized_title_similarity(a, b)
        for s in samples
        for i, a in enumerate(s.candidates)
        for b in s.candidates[i + 1 :]
    ]
    cluster_ratios = [largest_cluster_ratio(s.candidates) for s in samples if s.candidates]

    def rate(pred) -> str:
        if not all_titles:
            return "n/a"
        return f"{sum(1 for t in all_titles if pred(t)) / len(all_titles):.1%}"

    rows: list[tuple[str, str]] = [
        ("sample_count", str(len(samples))),
        ("candidate_total", str(len(all_titles))),
    ]
    if counts:
        rows += [
            ("candidate_count mean", f"{statistics.mean(counts):.2f}"),
            ("candidate_count median", f"{statistics.median(counts):.1f}"),
            ("candidate_count min", str(min(counts))),
            ("candidate_count max", str(max(counts))),
        ]
    rows += [
        ("unique_title_count", str(len(set(all_titles)))),
        ("unique_skeleton_count", str(len({normalize_title(t) for t in all_titles}))),
        (
            "skeleton_duplicate_rate",
            f"{1 - len({normalize_title(t) for t in all_titles}) / len(all_titles):.1%}"
            if all_titles
            else "n/a",
        ),
    ]
    if sims:
        rows += [
            ("similarity mean", f"{statistics.mean(sims):.3f}"),
            ("similarity median", f"{statistics.median(sims):.3f}"),
            ("similarity max", f"{max(sims):.3f}"),
        ]
    if cluster_ratios:
        tag = f"largest_cluster_ratio@{CLUSTER_THRESHOLD}"
        rows.append((f"{tag} mean", f"{statistics.mean(cluster_ratios):.3f}"))
        rows.append((f"{tag} max", f"{max(cluster_ratios):.3f}"))
    if lengths:
        rows += [
            ("title_length mean", f"{statistics.mean(lengths):.1f}"),
            ("title_length median", f"{statistics.median(lengths):.1f}"),
            ("title_length p25", f"{_percentile(lengths, 0.25):.1f}"),
            ("title_length p75", f"{_percentile(lengths, 0.75):.1f}"),
        ]
    rows += [
        ("contains_number_rate", rate(lambda t: bool(re.search(r"\d", t)))),
        ("question_rate", rate(lambda t: bool(_QUESTION_PATTERN.search(t)))),
        ("judgment_rate", rate(lambda t: bool(_JUDGMENT_PATTERN.search(t)))),
        ("contrast_rate", rate(lambda t: bool(_CONTRAST_PATTERN.search(t)))),
        ("company_name_rate", rate(lambda t: bool(_COMPANY_PATTERN.search(t)))),
        ("archetype_count", str(len({classify_archetype(t) for t in all_titles}))),
    ]
    blocked = [s for s in samples if s.guard_blocked is not None]
    if blocked:
        rows.append(
            (
                "run_guard_blocked_rate",
                f"{sum(1 for s in blocked if s.guard_blocked) / len(blocked):.1%}"
                f" ({len(blocked)} 个样本有报告)",
            )
        )
    else:
        rows.append(("run_guard_blocked_rate", "数据不可获得"))
    return rows


def collect(sources: list[Path], limit: int = 0) -> BenchmarkResult:
    run_dirs = _iter_run_dirs(sources)
    run_dirs.sort(key=lambda p: str(p))
    if limit:
        run_dirs = run_dirs[:limit]

    samples: list[RunSample] = []
    gaps: list[str] = []
    has_guard = False
    for run_dir in run_dirs:
        candidates, recommended = parse_titles_file(run_dir / "titles.txt")
        if not candidates:
            continue
        guard = _read_json(run_dir / "content_guard_report.json")
        review = _read_json(run_dir / "review_status.json")
        publish = _read_json(run_dir / "publish" / "publish_result.json")
        final = str(review.get("selected_title") or "") or str(publish.get("title") or "")
        blocked = guard.get("blocked") if "blocked" in guard else None
        if blocked is not None:
            has_guard = True
        published = publish.get("status") if publish else None
        samples.append(
            RunSample(
                run_dir=str(run_dir),
                topic=run_dir.name[:60],
                candidates=candidates,
                recommended=recommended,
                final_title=final,
                guard_blocked=blocked,
                published=(published == "submitted_for_review" if published else None),
            )
        )

    if not has_guard:
        gaps.append("没有样本带 content_guard_report.json → run 级 blocked 率不可得")
    gaps.append(
        "**合规过滤后的候选列表没有落盘**：titles.txt 只保留「全部标题」与「推荐标题」，"
        "逐条候选的合规判定结果（哪条被 RULE_006/007/009 拦下）**数据不可获得**，"
        "因此无法计算标题级 blocked_rate。现值仅为 run 级（文章级）代理指标。"
    )
    gaps.append(
        "**去重前的原始模型输出没有落盘**：titles.txt 里的「全部标题」已经是"
        "模型输出 ∪ 兜底标题经去重后的结果，生成侧真实候选数**数据不可获得**。"
    )
    return BenchmarkResult(samples=samples, sources=[str(s) for s in sources], data_gaps=gaps)


def render_markdown(result: BenchmarkResult, focus: BenchmarkResult | None = None) -> str:
    rows = result.metric_rows()
    archetypes: dict[str, int] = {}
    for s in result.samples:
        for t in s.candidates:
            archetypes[classify_archetype(t)] = archetypes.get(classify_archetype(t), 0) + 1

    lines = [
        "# 标题形态基线（离线）",
        "",
        "> 本文件由 `scripts/benchmark_titles.py` 生成。",
        "> **完全离线**：不调用 LLM、不启动 Playwright、不联网。",
        "",
        "## 样本范围与数据来源",
        "",
    ]
    lines += [f"- `{s}`" for s in result.sources]
    lines += ["", f"- 有效样本数：**{len(result.samples)}**", ""]
    lines += ["## 数据缺失（重要）", ""]
    lines += [f"- {g}" for g in result.data_gaps]
    lines += ["", "## 指标", "", "| 指标 | 值 |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", "## archetype 分布（启发式，仅供参考）", "", "| archetype | 条数 |", "|---|---:|"]
    for name, count in sorted(archetypes.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {name} | {count} |")
    lines += [
        "",
        "## 限制（必须随结论一起读）",
        "",
        "- **这不是 CTR 因果证明。** 历史样本混合了发布时间、账号阶段、选题、",
        "  推荐权重、历史时期与模型能力，混杂因素未控制。",
        "- `archetype` 与 question/judgment/contrast 等特征均为**离线启发式正则**，",
        "  只用于观察形态分布，不代表平台真实语义。",
        "- `similarity` 使用字符 2-gram Jaccard + SequenceMatcher 平均，",
        "  无 embedding、无语义理解，近义改写会被低估。",
        f"- `largest_cluster_ratio` 阈值固定 {CLUSTER_THRESHOLD}（用 9-17 真实塌缩样本校准），"
        "换阈值会显著改变数值，所以指标名里带阈值。",
        "",
    ]
    if focus is not None:
        rows_focus = focus.metric_rows()
        lines += [
            "## 专项：对照实验的比较目标",
            "",
            f"- 来源：`{', '.join(focus.sources)}`",
            f"- 有效样本数：**{len(focus.samples)}**",
            "",
            "| 指标 | 值 |",
            "|---|---:|",
        ]
        lines += [f"| {k} | {v} |" for k, v in rows_focus]
        lines += ["", _focus_observation(rows_focus), ""]
    return "\n".join(lines)


def _focus_observation(rows: list[tuple[str, str]]) -> str:
    """给专项小节生成一句可解释的观察（阈值写死，避免误读）。"""
    values = dict(rows)
    return (
        f"**观察**：候选数 {values.get('candidate_count min', '?')}–"
        f"{values.get('candidate_count max', '?')}；"
        f"`question_rate` = {values.get('question_rate', '?')}、"
        f"`contrast_rate` = {values.get('contrast_rate', '?')}；"
        f"`archetype_count` = {values.get('archetype_count', '?')}。"
        "与全局基线相比形态更单一时，说明该次运行的标题多样性不足。"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线标题形态基线（不调用 LLM / 不启动 Playwright）")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="扫描根目录，可重复。默认扫描本地 runs / .cloud-artifacts / .runtime/diag",
    )
    parser.add_argument("--account", default="robot-notes", help="账号 slug（用于默认扫描路径）")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 个 run 目录（调试用）")
    parser.add_argument("--focus", default="", help="额外扫描的根目录，在报告中单列「对照实验比较目标」小节")
    parser.add_argument("--md", default="", help="把 Markdown 报告写到该路径")
    parser.add_argument("--json", dest="json_path", default="", help="把结构化结果写到该路径")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    if args.source:
        sources = [Path(s) for s in args.source]
    else:
        sources = [
            root / "output" / "accounts" / args.account / "runs",
            root / ".cloud-artifacts",
            root / ".runtime" / "diag",
        ]

    result = collect(sources, limit=args.limit)
    if not result.samples:
        print("没有找到任何含 titles.txt 的运行目录。检查 --source。", file=sys.stderr)
        return 1

    for name, value in result.metric_rows():
        print(f"{name:32s} {value}")

    focus_result = collect([Path(args.focus)]) if args.focus else None

    if args.md:
        out = Path(args.md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(result, focus=focus_result), encoding="utf-8")
        print(f"\nMarkdown 报告已写入: {out}")
    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sources": result.sources,
            "data_gaps": result.data_gaps,
            "metrics": {k: v for k, v in result.metric_rows()},
            "samples": [
                {
                    "run_dir": s.run_dir,
                    "topic": s.topic,
                    "candidate_count": len(s.candidates),
                    "candidates": s.candidates,
                    "recommended": s.recommended,
                    "final_title": s.final_title,
                    "guard_blocked": s.guard_blocked,
                    "published": s.published,
                }
                for s in result.samples
            ],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
