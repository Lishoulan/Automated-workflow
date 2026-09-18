"""Phase 2C–2H：内容表现统一数据表 + 标题/选题分类 + CTR 基线（**分析层，不碰生产**）。

本脚本只读生产产物，产出两套分析文件：
    data/analytics/content_performance.jsonl   每条内容一行
    data/analytics/ctr_baseline.json           分组统计（含样本量）

设计原则（照 Phase 2 spec 执行，违反即是 bug）：
1. **缺失一律 null，绝不写 0。** 平台把未结算写成 "0"、抽取器抽不到也返回 0，
   这两类都与「真实为 0」不是一回事，本脚本靠 `*_missing` 与 `revenue_status` 保留差异。
2. **样本量必随统计一起输出。** 任何分组都必须带 n；n<5 的分组只作假设、不作结论。
3. **分类必须可重复、可解释。** 全用规则，不用 LLM；规则与优先级写死在下面，改了要改这里。
4. 不改任何生产文件，不触发发布/采集，零 LLM 调用（零成本可反复重跑）。

用法：
    .venv/Scripts/python.exe scripts/build_ctr_baseline.py [--account robot_notes]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------- 标题分类规则
# 顺序即优先级：命中前面的就不再往后判。为什么这个顺序：
# - 疑问/反直觉是读者第一眼感知到的"结构"，比语义更稳定；
# - 数字几乎与所有类型共存（"23亿押注"既是数字也是事件），所以放最后，
#   只有当"除了数字没有别的特征"时才算 NUMBER。
TITLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "QUESTION",
        ("?", "？", "谁在", "谁能", "会是谁", "为什么", "凭什么", "几何", "吗",
         "如何", "怎么", "是不是", "能否", "几分", "多少", "谁拿", "卖给谁"),
    ),
    (
        "CONTRARIAN",
        ("其实", "并非", "并不是", "反而是", "真正的", "被低估", "别只", "误区",
         "错了", "真相", "没那么", "不只是", "不只是", "以为的", "恰恰"),
    ),
    (
        "CONFLICT",
        ("争夺", "博弈", "对峙", "反击", "叫板", "硬刚", "争议", "叫停", "围剿",
         "抢", "厮杀", "分食", "暗战"),
    ),
    (
        "RISK",
        ("风险", "隐患", "承压", "泡沫", "亏损", "降温", "警惕", "难关", "困境",
         "难题", "隐忧", "代价"),
    ),
    (
        "BENEFIT",
        ("受益", "红利", "机会", "赚钱", "利润", "蛋糕", "分一杯", "谁受益", "利好"),
    ),
    (
        "JUDGMENT",
        ("才是", "关键", "意味着", "说明", "考验", "信号", "难在", "稳了",
         "不看", "看的是", "真正的", "下一步", "拐点", "分水岭", "才是真", "开始了",
         "进入", "转向", "才是主菜", "才是底牌", "改写", "重排", "分化", "决定",
         "重估", "洗牌", "座次", "比", "更"),
    ),
    (
        "EVENT",
        ("发布", "拿下", "中标", "出货", "签约", "突破", "落地", "开工", "量产",
         "交付", "投产", "扩产", "获", "进", "上市", "涨价", "降价", "裁员", "融资"),
    ),
)

NUMBER_RE = re.compile(r"\d")

# ---------------------------------------------------------------- 选题分类规则
TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("机器人", ("机器人", "具身智能", "人形", "Optimus", "optimus", "宇树", "优必选", "智元")),
    ("新能源", ("光伏", "储能", "电池", "新能源", "电网", "装机", "锂", "氢")),
    ("智能装备", ("丝杠", "减速器", "传感器", "零部件", "产线", "工厂", "机床", "装备", "叉车")),
    ("公司业绩", ("中报", "财报", "业绩", "营收", "净利", "订单", "出货量", "同比", "亏损")),
    ("政策", ("工信部", "政策", "补贴", "规划", "标准", "新规", "监管", "发改委")),
    ("技术", ("大模型", "算法", "芯片", "算力", "技术", "专利", "研发", "模型")),
    ("行业趋势", ("市场", "份额", "需求", "趋势", "规模", "增速", "赛道", "格局")),
)

# ---------------------------------------------------------------- 收益状态机
# 与 spec 一致，另加 NOT_SUPPORTED：有实测证据表明「该平台不提供单篇维度」。
# ⚠️ UNKNOWN 与 ZERO 是两回事：前者=没查到，后者=明确为 0。任何 unknown→0 都是造假。
REVENUE_STATUS_FROM_PLATFORM = {
    "unknown": "UNKNOWN",
    "pending_t1": "PENDING",
    "available": "SETTLED",
    "not_available": "UNKNOWN",
}


def content_id_for(account_key: str, platform: str, run_dir: str) -> str:
    """与生产 `RevenueAttributionService.build_content_identity` 同一算法，保证可复现。"""
    digest_input = f"{account_key.strip().lower()}|{platform.strip().lower()}|{Path(run_dir).resolve()}"
    return "ci_" + hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:16]


def classify_title(title: str) -> str:
    text = str(title or "").strip()
    if not text:
        return "OTHER"
    for label, keywords in TITLE_RULES:
        if any(keyword in text for keyword in keywords):
            return label
    if NUMBER_RE.search(text):
        return "NUMBER"
    return "FACT"


def classify_topic(topic: str, title: str) -> str:
    text = f"{topic or ''} {title or ''}"
    for label, keywords in TOPIC_RULES:
        if any(keyword in text for keyword in keywords):
            return label
    return "其他"


def title_length_bucket(title: str) -> str:
    """Experiment D 需要的分桶：≤18 / 19–24 / 25–30 / >30 字。空标题归 UNKNOWN。"""
    length = len(str(title or "").strip())
    if length == 0:
        return "UNKNOWN"
    if length <= 18:
        return "<=18"
    if length <= 24:
        return "19-24"
    if length <= 30:
        return "25-30"
    return ">30"


def _as_number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def raw_ctr(reads: int | None, impressions: int | None) -> float | None:
    """CTR = 阅读 / 展现 × 100（百分比）。展现缺失或为 0 时返回 None（**不是 0**）。"""
    if reads is None or impressions is None or impressions <= 0:
        return None
    return round(reads * 100.0 / impressions, 4)


def describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "min": None, "max": None}
    ordered = sorted(values)
    n = len(ordered)
    out = {
        "n": n,
        "mean": round(statistics.fmean(ordered), 4),
        "median": round(statistics.median(ordered), 4),
        "p25": None,
        "p75": None,
        "min": round(ordered[0], 4),
        "max": round(ordered[-1], 4),
    }
    if n >= 4:
        quantiles = statistics.quantiles(ordered, n=4, method="inclusive")
        out["p25"] = round(quantiles[0], 4)
        out["p75"] = round(quantiles[2], 4)
    # n<5 的分组不作结论 —— 让调用方必须看到这个标记。
    out["insufficient_sample"] = n < 5
    return out


def group_stats(rows: list[dict[str, Any]], key: str, metric_keys: Iterable[str]) -> dict[str, Any]:
    buckets: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        bucket = str(row.get(key) or "UNKNOWN")
        entry = buckets.setdefault(bucket, {k: [] for k in metric_keys})
        for metric in metric_keys:
            value = row.get(metric)
            if isinstance(value, (int, float)) and value is not None:
                entry[metric].append(float(value))
    return {
        bucket: {metric: describe(values) for metric, values in metrics.items()}
        for bucket, metrics in sorted(buckets.items())
    }


def build_rows(
    *,
    account_key: str,
    metrics_path: Path,
    content_index_path: Path,
    snapshot_ledger_path: Path,
) -> list[dict[str, Any]]:
    payload = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
    records = payload.get("records") if isinstance(payload, dict) else None
    records = records if isinstance(records, list) else []

    index = json.loads(content_index_path.read_text(encoding="utf-8-sig")) if content_index_path.exists() else {}
    ledger = json.loads(snapshot_ledger_path.read_text(encoding="utf-8-sig")) if snapshot_ledger_path.exists() else {}

    rows: list[dict[str, Any]] = []
    for item in records:
        local = item.get("local_record") if isinstance(item.get("local_record"), dict) else {}
        run_dir = str(local.get("run_dir") or "").strip()
        platform = str(item.get("platform") or local.get("platform") or "toutiao").strip().lower()
        content_id = content_id_for(account_key, platform, run_dir) if run_dir else None

        title = str(item.get("title") or "").strip()
        topic = str(local.get("topic_title") or "").strip()
        indexed = index.get(content_id) if isinstance(index.get(content_id), dict) else {}
        if not topic:
            topic = str(indexed.get("topic_title") or "").strip()

        impressions = _as_number(item.get("impression_count"))
        reads = _as_number(item.get("read_count"))
        if reads is None:
            reads = _as_number(item.get("views"))
        # 生产抽取器抽不到时返回 0；这里无法回溯原始文本，因此如实标注「可能是缺失」。
        impressions_missing = impressions is None or impressions == 0
        reads_missing = reads is None

        platform_status = str(item.get("revenue_status") or "").strip().lower()
        availability = str(item.get("availability_status") or "").strip().lower()
        if not platform_status and not availability:
            revenue_status = "NOT_QUERIED"
        else:
            revenue_status = REVENUE_STATUS_FROM_PLATFORM.get(platform_status, "UNKNOWN")

        snapshots = ledger.get(content_id) if isinstance(ledger.get(content_id), list) else []

        rows.append(
            {
                "content_id": content_id,
                "content_id_reliable": bool(content_id) and False,
                "platform_item_id": _item_id_from_url(str(item.get("url") or "")),
                "article_url": str(item.get("url") or "").strip() or None,
                "account": account_key,
                "platform": platform,
                "content_type": "微头条" if "weitoutiao" in platform else "头条",
                "title": title or None,
                "topic": topic or None,
                "publish_time": str(local.get("published_at") or item.get("publish_date") or "").strip() or None,
                "publish_date": str(item.get("publish_date") or "").strip() or None,
                "run_dir": run_dir or None,
                "matched_local_record": bool(item.get("matched")),
                "impressions": impressions,
                "reads": reads,
                "ctr_platform": (lambda v: float(v) if isinstance(v, (int, float)) else None)(item.get("ctr")),
                "ctr_raw": raw_ctr(reads, impressions),
                "ctr_definition": "reads / impressions * 100 (percent)",
                "impressions_missing": impressions_missing,
                "reads_missing": reads_missing,
                "likes": _as_number(item.get("like_count")),
                "comments": _as_number(item.get("comment_count")),
                "shares": _as_number(item.get("share_count")),
                "revenue": None,
                "revenue_status": revenue_status,
                "revenue_updated_at": str(item.get("observed_at") or "").strip() or None,
                "revenue_attribution_support": "not_supported_by_api",
                "title_type": classify_title(title),
                "title_length": len(title) if title else None,
                "title_length_bucket": title_length_bucket(title),
                "topic_bucket": classify_topic(topic, title),
                "snapshot_count": len(snapshots),
                "source": "performance_metrics.json",
            }
        )
    return rows


def _item_id_from_url(url: str) -> str | None:
    """从文章链接里取平台侧的文章 id —— 这才是平台的唯一键。"""
    match = re.search(r"/item/(\d+)", str(url or ""))
    if match:
        return match.group(1)
    match = re.search(r"[?&](?:item_id|group_id)=(\d+)", str(url or ""))
    return match.group(1) if match else None


def mark_representative(rows: list[dict[str, Any]]) -> int:
    """标出每条内容的「代表观测」，去重只去掉**采集层重复**。

    ⚠️ 2026-09-18 修正：第一版按 `content_id` 去重，把**同一标题的多次真实发布**
    误当成重复观测删掉了（41 行被压成 20 行）。回原始数据核对后发现不对：

        「机器人热度之外，谁在拿走真正订单」在同一份数据里有 11 行，
        publish_date 分别是 08-28 / 08-31 / 09-02 / 09-10 / 09-11，
        且 11 个 url 互不相同 ⇒ 那是 **11 次真实发布**，不是一次发布被抓 11 遍。

    正确口径：
    - 平台侧唯一键 = 文章 url（item_id）。只有 url 完全相同才是采集重复（实测仅 2 条）。
    - **content_id 反而不可靠**：`_match_metrics_to_local_records` 用标题相似度
      匹配本地记录，多篇同名文章会被指到同一个 run_dir ⇒ content_id 多对一。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row["platform_item_id"] or f"__no_url__{row['title']}|{row['publish_date']}"
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        best = max(group, key=lambda item: (item["impressions"] or -1, item["reads"] or -1))
        for row in group:
            row["is_representative_observation"] = row is best
            row["observation_count_for_content"] = len(group)
    return len(groups)


def annotate_title_and_id_reuse(rows: list[dict[str, Any]]) -> dict[str, int]:
    """标出「同一标题被发了多少次」以及 content_id 是否可信。

    content_id 由 `account|platform|run_dir` 哈希而来，而 local_record 是靠标题相似度
    匹配的 —— 多篇同名文章会撞到同一条 run_dir ⇒ 同一个 content_id 下挂着多篇真实文章。
    这时 content_id **不能当唯一键**，必须标出来。
    """
    by_title: dict[str, int] = {}
    for row in rows:
        title = row.get("title") or ""
        by_title[title] = by_title.get(title, 0) + 1
    by_cid: dict[str, set[str]] = {}
    for row in rows:
        cid = row.get("content_id")
        item_id = row.get("platform_item_id")
        if cid and item_id:
            by_cid.setdefault(cid, set()).add(item_id)
    for row in rows:
        title = row.get("title") or ""
        row["title_publish_count"] = by_title.get(title, 0)
        cid = row.get("content_id")
        row["content_id_reliable"] = bool(cid) and len(by_cid.get(cid, set())) == 1
    return by_title


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="robot_notes")
    args = parser.parse_args()

    account_key = args.account
    output_dir = ROOT / "output" / "accounts" / account_key.replace("_", "-")
    metrics_path = output_dir / "performance_metrics.json"
    if not metrics_path.exists():
        print(f"[error] 找不到 {metrics_path}")
        return 1
    attribution_dir = output_dir / "growth_ops" / "revenue_attribution"

    rows = build_rows(
        account_key=account_key,
        metrics_path=metrics_path,
        content_index_path=attribution_dir / "content_index.json",
        snapshot_ledger_path=attribution_dir / "snapshot_ledger.json",
    )

    unique_contents = mark_representative(rows)
    title_counts = annotate_title_and_id_reuse(rows)
    analysis_rows = [row for row in rows if row["is_representative_observation"]]

    if rows:
        worst_title, worst_count = max(title_counts.items(), key=lambda kv: kv[1])
        print(f"[!] 标题重复发布最多：{worst_count} 次 —— {worst_title[:46]}")
        unreliable = sum(1 for r in rows if r["content_id"] and not r["content_id_reliable"])
        print(f"[!] content_id 不可靠（一个 cid 对应多篇真实文章）的行数：{unreliable}")

    analytics_dir = ROOT / "data" / "analytics"
    analytics_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = analytics_dir / "content_performance.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    # CTR 口径核对：平台给的 ctr 与自算 ctr_raw 差多少。
    ctr_check = []
    for row in rows:
        if row["ctr_platform"] is not None and row["ctr_raw"] is not None:
            ctr_check.append(round(row["ctr_platform"] - row["ctr_raw"], 6))
    ctr_check_mismatch = [delta for delta in ctr_check if abs(delta) > 0.01]

    baseline = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "account": account_key,
        "source_file": str(metrics_path.relative_to(ROOT)),
        "sample_size": len(analysis_rows),
        "raw_observation_count": len(rows),
        "unique_content_count": unique_contents,
        "deduplication_note": (
            "sample_size 是去重后的独立内容数；raw_observation_count 是采集层原始行数。"
            "统计一律基于 sample_size，重复观测只在 jsonl 里保留（is_representative_observation=false）。"
        ),
        "fields_completeness": {
            field: round(
                100.0
                * sum(1 for row in analysis_rows if row.get(field) not in (None, "", [], {}))
                / max(len(analysis_rows), 1),
                1,
            )
            for field in (
                "content_id",
                "title",
                "topic",
                "publish_time",
                "impressions",
                "reads",
                "ctr_raw",
                "likes",
                "comments",
                "shares",
                "revenue",
                "title_type",
                "topic_bucket",
            )
        },
        "ctr_definition_check": {
            "compared": len(ctr_check),
            "mismatch_gt_0_01": len(ctr_check_mismatch),
            "max_abs_delta": round(max((abs(d) for d in ctr_check), default=0.0), 6),
            "conclusion": (
                "platform ctr == reads/impressions*100"
                if not ctr_check_mismatch
                else "存在偏差，见 mismatch_gt_0_01"
            ),
        },
        "overall": {
            "impressions": describe([float(r["impressions"]) for r in analysis_rows if r["impressions"] is not None]),
            "reads": describe([float(r["reads"]) for r in analysis_rows if r["reads"] is not None]),
            "ctr_raw": describe([float(r["ctr_raw"]) for r in analysis_rows if r["ctr_raw"] is not None]),
        },
        "by_title_type": group_stats(analysis_rows, "title_type", ("impressions", "reads", "ctr_raw", "title_length")),
        "by_topic_bucket": group_stats(analysis_rows, "topic_bucket", ("impressions", "reads", "ctr_raw")),
        "by_content_type": group_stats(analysis_rows, "content_type", ("impressions", "reads", "ctr_raw")),
        "by_title_length_bucket": group_stats(analysis_rows, "title_length_bucket", ("impressions", "reads", "ctr_raw")),
        "revenue_status_counts": {
            status: sum(1 for r in analysis_rows if r["revenue_status"] == status)
            for status in sorted({r["revenue_status"] for r in analysis_rows})
        },
        "data_quality": {
            "rows_without_content_id": sum(1 for r in analysis_rows if not r["content_id"]),
            "rows_unmatched_to_local_record": sum(1 for r in analysis_rows if not r["matched_local_record"]),
            "rows_impressions_missing": sum(1 for r in analysis_rows if r["impressions_missing"]),
            "rows_reads_missing": sum(1 for r in analysis_rows if r["reads_missing"]),
            "rows_zero_reads": sum(1 for r in analysis_rows if r["reads"] == 0),
            "duplicate_observations_dropped": len(rows) - len(analysis_rows),
            "contents_with_multiple_observations": sum(
                1 for r in analysis_rows if (r.get("observation_count_for_content") or 1) > 1
            ),
        },
    }

    baseline_path = analytics_dir / "ctr_baseline.json"
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] {len(rows)} 行 -> {jsonl_path.relative_to(ROOT)}")
    print(f"[ok] 基线 -> {baseline_path.relative_to(ROOT)}")
    print("\n=== 字段完整率 ===")
    for field, pct in baseline["fields_completeness"].items():
        print(f"  {field:22s} {pct}%")
    print("\n=== 整体（CTR 为百分比） ===")
    for metric in ("impressions", "reads", "ctr_raw"):
        stat = baseline["overall"][metric]
        print(f"  {metric:12s} n={stat['n']:3d} 中位={stat['median']} 均值={stat['mean']} p25={stat['p25']} p75={stat['p75']}")
    print("\n=== 按标题类型（CTR） ===")
    for bucket, metrics in baseline["by_title_type"].items():
        stat = metrics["ctr_raw"]
        flag = " (样本不足)" if stat.get("insufficient_sample") else ""
        print(f"  {bucket:12s} n={stat['n']:3d} 中位={stat['median']} 均值={stat['mean']}{flag}")
    print("\n=== 按标题长度（CTR） ===")
    for bucket, metrics in baseline["by_title_length_bucket"].items():
        stat = metrics["ctr_raw"]
        flag = " (样本不足)" if stat.get("insufficient_sample") else ""
        print(f"  {bucket:12s} n={stat['n']:3d} 中位={stat['median']} 均值={stat['mean']}{flag}")
    print("\n=== 按选题类型（CTR） ===")
    for bucket, metrics in baseline["by_topic_bucket"].items():
        stat = metrics["ctr_raw"]
        flag = " (样本不足)" if stat.get("insufficient_sample") else ""
        print(f"  {bucket:12s} n={stat['n']:3d} 中位={stat['median']} 均值={stat['mean']}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
