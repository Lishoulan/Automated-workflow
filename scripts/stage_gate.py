"""阶段判定器：读真实产物，回答「现在走到第几阶段、下一个门槛是什么」。

## 为什么要它

五个阶段如果只写在文档里，没人会知道自己到哪了。这个脚本把每个门槛变成
可计算的事实，并且**缺证据时输出 BLOCKED 而不是 0 或 False** —— 后者会让
「没采到数据」看起来像「业务失败」，正是过去把发布率误读成 0.0% 的同一个坑。

## 阶段与门槛

0 通电        连续 3 天每天有 >=1 次作品提交
1 单篇突破    存在 1 篇展现 > 10,000
2 可复制      最近 10 篇展现中位数 > 5,000
3 单位经济    net_profit > 0 且成本凭据覆盖率 >= 80%
4 放大        阶段 3 达成后才允许开第二个账号

退出码：0 = 已输出（无论阶段）；1 = 执行出错。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.config import load_settings  # noqa: E402

DAYS_POWER_ON = 3
STAGE1_MIN_IMPRESSIONS = 10_000
STAGE2_MEDIAN_IMPRESSIONS = 5_000
STAGE2_SAMPLE = 10
STAGE3_MIN_VERIFIED_RATIO = 0.8


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("records") or payload.get("works") or []
    else:
        return []
    return [row for row in rows if isinstance(row, dict)]


def _submit_dates(settings) -> list[str]:
    rows = _records(_read_json(Path(settings.output_dir) / "publish_history.json"))
    dates = []
    for row in rows:
        stamp = str(row.get("published_at") or row.get("publish_date") or "").strip()
        if stamp:
            dates.append(stamp[:10])
    return dates


def _works(settings) -> list[dict[str, Any]]:
    """平台侧作品记录（体现/展现来自创作者中心，不是我们自己写的状态）。"""
    return _records(_read_json(Path(settings.automation_dir) / "performance_metrics.json"))


def _impressions(row: dict[str, Any]) -> int:
    try:
        return int(row.get("impression_count") or 0)
    except (TypeError, ValueError):
        return 0


def _dashboard(settings) -> dict[str, Any]:
    payload = _read_json(
        Path(settings.output_dir) / "growth_ops" / "revenue_experiment" / "revenue_30d_dashboard.json"
    )
    return payload if isinstance(payload, dict) else {}


def _stage_power_on(settings) -> dict[str, Any]:
    dates = _submit_dates(settings)
    if not dates:
        return {
            "status": "BLOCKED",
            "detail": "publish_history.json 无提交记录，无法判断是否每天有产出。",
            "evidence": {"submit_dates": []},
        }
    today = datetime.now(timezone.utc).astimezone().date()
    window = [(today - timedelta(days=offset)).isoformat() for offset in range(DAYS_POWER_ON - 1, -1, -1)]
    counts = {day: dates.count(day) for day in window}
    met = all(count >= 1 for count in counts.values())
    return {
        "status": "ACHIEVED" if met else "PENDING",
        "detail": (
            f"最近 {DAYS_POWER_ON} 天提交数 {counts}（口径为本地提交记录，未经平台回查）。"
        ),
        "evidence": {"window": window, "counts": counts},
    }


def _stage_single_breakthrough(settings) -> dict[str, Any]:
    works = _works(settings)
    if not works:
        return {
            "status": "BLOCKED",
            "detail": "performance_metrics.json 无平台作品记录，无法判断单篇展现。",
            "evidence": {"work_count": 0},
        }
    best = max(_impressions(row) for row in works)
    return {
        "status": "ACHIEVED" if best > STAGE1_MIN_IMPRESSIONS else "PENDING",
        "detail": f"最高单篇展现 {best}，门槛 >{STAGE1_MIN_IMPRESSIONS}。",
        "evidence": {"max_impressions": best, "work_count": len(works)},
    }


def _stage_replicable(settings) -> dict[str, Any]:
    works = _works(settings)
    values = [_impressions(row) for row in works if _impressions(row) > 0]
    if len(values) < STAGE2_SAMPLE:
        return {
            "status": "BLOCKED",
            "detail": f"有效展现样本 {len(values)} 篇，不足 {STAGE2_SAMPLE} 篇，中位数无统计意义。",
            "evidence": {"sample": len(values)},
        }
    median = statistics.median(sorted(values)[-STAGE2_SAMPLE:])
    return {
        "status": "ACHIEVED" if median > STAGE2_MEDIAN_IMPRESSIONS else "PENDING",
        "detail": f"最近 {STAGE2_SAMPLE} 篇展现中位数 {median:g}，门槛 >{STAGE2_MEDIAN_IMPRESSIONS}。",
        "evidence": {"median": median, "sample": len(values)},
    }


def _stage_unit_economics(settings) -> dict[str, Any]:
    dashboard = _dashboard(settings)
    if not dashboard:
        return {
            "status": "BLOCKED",
            "detail": "revenue_30d_dashboard.json 缺失，收益与成本都无法核算。",
            "evidence": {},
        }
    costs = dashboard.get("generation_costs") or {}
    income = dashboard.get("income") or {}
    recorded = int(costs.get("recorded_calls") or 0)
    verified = int(costs.get("verified_cost_calls") or 0)
    ratio = (verified / recorded) if recorded else 0.0
    net = costs.get("net_profit")
    coverage = bool(income.get("coverage_complete"))
    if net is None or not coverage:
        return {
            "status": "BLOCKED",
            "detail": (
                f"净值 unknown（cost net_profit={net}, income coverage_complete={coverage}）。"
                f"成本凭据 {verified}/{recorded}。证据不足，不按 0 处理。"
            ),
            "evidence": {
                "recorded_calls": recorded,
                "verified_calls": verified,
                "verified_ratio": ratio,
                "window_income": income.get("window_income"),
                "net_profit": net,
                "coverage_complete": coverage,
            },
        }
    met = float(net) > 0 and ratio >= STAGE3_MIN_VERIFIED_RATIO
    return {
        "status": "ACHIEVED" if met else "PENDING",
        "detail": (
            f"净值 {net}，成本凭据覆盖 {ratio:.0%}（要求 >={STAGE3_MIN_VERIFIED_RATIO:.0%}）。"
        ),
        "evidence": {
            "net_profit": net,
            "verified_ratio": ratio,
            "window_income": income.get("window_income"),
        },
    }


STAGES = [
    (0, "通电：每天有真实产出", _stage_power_on),
    (1, "单篇突破：证明能被推荐", _stage_single_breakthrough),
    (2, "可复制：把偶然变公式", _stage_replicable),
    (3, "单位经济转正", _stage_unit_economics),
]


def evaluate(settings) -> dict[str, Any]:
    results = []
    current_stage = 0
    advanced = True
    for stage_id, name, fn in STAGES:
        outcome = fn(settings)
        outcome.update({"id": stage_id, "name": name})
        results.append(outcome)
        if advanced and outcome["status"] == "ACHIEVED":
            current_stage = stage_id + 1
        else:
            advanced = False

    blocking = [row for row in results if row["status"] == "BLOCKED"]
    pending = [row for row in results if row["status"] == "PENDING"]
    first_unmet = next((row for row in results if row["status"] != "ACHIEVED"), None)
    if first_unmet is None:
        next_action = "四阶段全部达成，可开第二个账号。"
    else:
        prefix = "补齐证据" if first_unmet["status"] == "BLOCKED" else "推进门槛"
        next_action = f"{prefix}：{first_unmet['name']} — {first_unmet['detail']}"

    return {
        "account_key": settings.account_key,
        "evaluated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "current_stage": min(current_stage, 4),
        "stages": results,
        "blocked_count": len(blocking),
        "pending_count": len(pending),
        "next_action": next_action,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判定内容增长当前所处阶段与下一门槛")
    parser.add_argument("--account", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true", help="写入 automation/stage_status.json")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(ROOT, args.account)
        report = evaluate(settings)
        if args.write:
            target = Path(settings.automation_dir) / "stage_status.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"当前阶段：{report['current_stage']}")
            for row in report["stages"]:
                print(f"  [{row['status']}] {row['name']} — {row['detail']}")
            print(f"下一步：{report['next_action']}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"阶段判定失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
