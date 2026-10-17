"""Build an operator-facing 30-day writing revenue experiment dashboard.

The dashboard combines account entitlements, settled daily income, published-work
metrics and exact per-content revenue when the creator center exposes it.  It
never allocates an account-level daily amount to individual posts by guesswork.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from toutiao_content_factory.services.content_income_service import platform_item_id
from toutiao_content_factory.services.generation_cost_service import build_cost_summary


class RevenueExperimentService:
    WINDOW_DAYS = 30
    WTT_SAMPLE_TARGET = 30
    ARTICLE_SAMPLE_TARGET = 8
    WTT_READ_RATE_TARGET = 2.0
    THOUSAND_IMPRESSION_SHARE_TARGET = 20.0

    def __init__(self, output_dir: Path, logger: logging.Logger) -> None:
        self.output_dir = Path(output_dir)
        self.logger = logger
        self.report_dir = self.output_dir / "growth_ops" / "revenue_experiment"
        self.readiness_path = self.report_dir / "revenue_readiness.json"
        self.dashboard_path = self.report_dir / "revenue_30d_dashboard.json"
        self.dashboard_markdown_path = self.report_dir / "revenue_30d_dashboard.md"

    def build_and_write(
        self,
        *,
        growth_snapshot: dict[str, Any] | None = None,
        growth_feedback: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        account_payload = self._load_json(
            self.output_dir / "growth_ops" / "performance" / "account_snapshot.json"
        )
        account_snapshot = self._latest_account_snapshot(account_payload)
        # The live feed rolls after 30 days. The existing snapshot artifact owns
        # the optional history; conflicting settled reports remain separate.
        daily_history = account_payload.get("settled_daily_history", [])
        if isinstance(daily_history, list) and daily_history:
            account_snapshot = {**account_snapshot, "settled_daily_history": daily_history}
        performance = self._load_json(self.output_dir / "performance_metrics.json")
        snapshot = growth_snapshot or self._load_json(self.output_dir / "growth_snapshot.json")
        feedback = growth_feedback or self._load_json(self.output_dir / "growth_feedback.json")

        readiness = self.build_readiness(account_snapshot)
        dashboard = self.build_dashboard(
            account_snapshot=account_snapshot,
            growth_snapshot=snapshot,
            growth_feedback=feedback,
            performance=performance,
            readiness=readiness,
        )
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(self.readiness_path, readiness)
        self._write_json(self.dashboard_path, dashboard)
        self.dashboard_markdown_path.write_text(
            self.render_markdown(dashboard), encoding="utf-8"
        )
        return {
            "readiness": self.readiness_path,
            "dashboard": self.dashboard_path,
            "dashboard_md": self.dashboard_markdown_path,
        }

    def build_readiness(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        benefits = snapshot.get("benefits") if isinstance(snapshot.get("benefits"), dict) else {}
        revenue_benefits = (
            benefits.get("revenue_benefits")
            if isinstance(benefits.get("revenue_benefits"), dict)
            else {}
        )
        quality = snapshot.get("quality") if isinstance(snapshot.get("quality"), dict) else {}
        revenue = snapshot.get("revenue") if isinstance(snapshot.get("revenue"), dict) else {}
        income = revenue.get("income") if isinstance(revenue.get("income"), dict) else {}

        article_active = self._optional_bool(revenue_benefits.get("文章创作收益"))
        weitoutiao_active = self._optional_bool(revenue_benefits.get("微头条创作收益"))
        semantics_ok = self._optional_bool(benefits.get("semantics_ok"))
        low_quality = self._optional_bool(quality.get("is_low_quality"))
        credit_score = self._optional_number(quality.get("credit_score"))
        daily_income = income.get("daily") if isinstance(income.get("daily"), list) else []

        checks = {
            "article_revenue_benefit": self._check_state(article_active),
            "weitoutiao_revenue_benefit": self._check_state(weitoutiao_active),
            "benefit_semantics": self._check_state(semantics_ok),
            "credit_score": (
                "unknown"
                if credit_score is None
                else "ready"
                if credit_score >= 100
                else "blocked"
                if credit_score < 60
                else "warning"
            ),
            "account_quality": (
                "unknown" if low_quality is None else "blocked" if low_quality else "ready"
            ),
            "income_feed": "ready" if daily_income else "unknown",
        }
        blockers: list[str] = []
        warnings: list[str] = []
        if article_active is False:
            blockers.append("文章创作收益权益未开通")
        if weitoutiao_active is False:
            blockers.append("微头条创作收益权益未开通")
        if credit_score is not None and credit_score < 60:
            blockers.append("信用分低于60，创作收益受限")
        elif credit_score is not None and credit_score < 100:
            warnings.append("信用分未达到100，不能申请新的创作权益")
        if low_quality is True:
            blockers.append("平台将账号标记为低质量")
        if semantics_ok is False:
            warnings.append("创作权益接口计数语义发生变化，需要人工复核")
        if not daily_income:
            warnings.append("30天收益明细尚未采集")
        if article_active is None:
            warnings.append("文章创作收益权益状态未知")
        if weitoutiao_active is None:
            warnings.append("微头条创作收益权益状态未知")

        critical_unknown = article_active is None or weitoutiao_active is None
        status = "blocked" if blockers else "unknown" if critical_unknown else "ready"
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "account_key": str(snapshot.get("account_key", "")),
            "checks": checks,
            "credit_score": credit_score,
            "revenue_benefits": revenue_benefits,
            "income_feed_days": len(daily_income),
            "blockers": blockers,
            "warnings": warnings,
        }

    def build_dashboard(
        self,
        *,
        account_snapshot: dict[str, Any],
        growth_snapshot: dict[str, Any],
        growth_feedback: dict[str, Any],
        performance: dict[str, Any],
        readiness: dict[str, Any],
    ) -> dict[str, Any]:
        observed_at = str(
            growth_snapshot.get("observed_at")
            or account_snapshot.get("generated_at")
            or datetime.now().isoformat(timespec="seconds")
        )
        reference_time = self._parse_datetime(observed_at) or datetime.now()
        revenue = (
            account_snapshot.get("revenue")
            if isinstance(account_snapshot.get("revenue"), dict)
            else {}
        )
        income = revenue.get("income") if isinstance(revenue.get("income"), dict) else {}
        daily_rows = [row for row in [*(income.get("daily") or []), *(account_snapshot.get("settled_daily_history") or [])]
                      if isinstance(row, dict)]
        income_summary = self._income_summary(daily_rows, reference_time)
        income_windows = {str(days): self._income_summary(daily_rows, reference_time, days=days)
                          for days in (7, 30, 90)}
        settled_dates = [stamp for row in daily_rows if row.get("pending") is False
                         and self._valid_amount(row.get("total"))
                         and (stamp := self._parse_datetime(row.get("day"))) is not None
                         and stamp.date() <= reference_time.date()]
        latest_settled = max(settled_dates, key=lambda value: value.date()) if settled_dates else None
        settled_windows = {str(days): self._income_summary(daily_rows, latest_settled, days=days)
                           for days in (7, 30, 90)} if latest_settled else {}
        format_metrics = self._format_metrics(growth_snapshot, income_summary)
        income_records = list(performance.get("records") or [])
        # The published-work API exposes micro-posts that the management DOM
        # omits. Use their platform IDs; never match earnings by fuzzy title.
        works = [w for w in growth_snapshot.get("works", []) if isinstance(w, dict) and w.get("gid") and "created_at" in w]
        work_ids = {str(w["gid"]) for w in works}
        income_records = [r for r in income_records if isinstance(r, dict) and platform_item_id(r.get("url", "")) not in work_ids]
        for work in works:
            income_records.append({
                "content_id": "toutiao_work:" + str(work["gid"]), "platform": "toutiao_weitoutiao" if work.get("type") == "weitoutiao" else "toutiao",
                "title": work.get("title", ""), "publish_date": work["created_at"][:10],
                "impression_count": work.get("impressions", 0), "read_count": work.get("reads", 0),
                "platform_revenue": work.get("platform_revenue"), "platform_revenue_status": work.get("platform_revenue_status", "unknown"),
                "revenue_amount_known": work.get("revenue_amount_known", False),
                "revenue_as_of_date": work.get("revenue_as_of_date", ""), "counters_as_of_date": observed_at[:10],
            })
        attribution = self._per_content_attribution({"records": income_records})
        costs = build_cost_summary(self.output_dir, now=reference_time)
        experiment = self._experiment_status(
            growth_snapshot=growth_snapshot,
            growth_feedback=growth_feedback,
            format_metrics=format_metrics,
            attribution=attribution,
            readiness=readiness,
        )
        return {
            "schema": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "observed_at": observed_at,
            "window_days": self.WINDOW_DAYS,
            "account_key": str(account_snapshot.get("account_key", readiness.get("account_key", ""))),
            "readiness": readiness,
            "account": account_snapshot.get("account") or {},
            "generation_costs": costs,
            "cost_windows": {str(days): costs if days == 30 else build_cost_summary(self.output_dir, now=reference_time, days=days)
                             for days in (7, 30, 90)},
            "income_windows": income_windows,
            "latest_settled_windows": settled_windows,
            "traffic_windows": self._traffic_windows(growth_snapshot, reference_time),
            "category_feedback": growth_feedback.get("revenue_feedback", {}),
            "revenue_reconciliation": {"account_key": str(account_snapshot.get("account_key", "")),
                "income_observed_at": account_snapshot.get("generated_at"),
                "income_source": "logged_in_creator_backend", "income_window_days": self.WINDOW_DAYS,
                "settled_income": income_summary.get("complete_total"),
                "observed_settled_income": income_summary.get("settled_total"),
                "cash_received": None, "settlement_scope": "creator_backend_accounted_income_not_bank_receipt",
                "actual_cost_status": costs["status"], "net_profit": None,
                "reason": "只有完整扣费、相同期间与币种及全部成本均已核实，才能计算净利润。"},
            "income": {
                "currency": str(revenue.get("currency", "CNY")),
                "total": self._optional_number(revenue.get("total")),
                "window_income": self._optional_number(income.get("window_income")),
                **income_summary,
            },
            "formats": format_metrics,
            "per_content_attribution": attribution,
            "content_income_collection": self._load_json(self.output_dir / "growth_ops" / "performance" / "content_income_collection.json"),
            "experiment": experiment,
            "feedback": {
                "sample_count": int(growth_feedback.get("sample_count", 0) or 0),
                "checkpoint_used": str(growth_feedback.get("checkpoint_used", "")),
                "winning_titles": list(
                    (growth_feedback.get("topic_feedback") or {}).get("winning_titles") or []
                )[:5],
                "stop_titles": list(
                    (growth_feedback.get("topic_feedback") or {}).get("stop_titles") or []
                )[:5],
            },
            "attribution_policy": (
                "Only exact per-content platform revenue is attributed. "
                "Account-level daily income is never distributed across posts by estimation."
            ),
        }

    def render_markdown(self, dashboard: dict[str, Any]) -> str:
        readiness = dashboard.get("readiness") or {}
        income = dashboard.get("income") or {}
        formats = dashboard.get("formats") or {}
        attribution = dashboard.get("per_content_attribution") or {}
        experiment = dashboard.get("experiment") or {}
        collection = dashboard.get("content_income_collection") or {}
        costs = dashboard.get("generation_costs") or {}
        lines = [
            "# 30天写作收益实验看板",
            "",
            f"- 账号：{dashboard.get('account_key') or '未知'}",
            f"- 收益准备状态：{readiness.get('status', 'unknown')}",
            f"- 30天完整结算收益：{self._money(income.get('complete_total'))}；已取得 {income.get('settled_days', 0)}/30 天，共 {self._money(income.get('settled_total'))}",
            f"- 最近7天收益：{self._money(income.get('latest_7d'))}",
            f"- 前7天收益：{self._money(income.get('previous_7d'))}",
            f"- 生成成本：{costs.get('status', 'untracked')}；记录 {costs.get('recorded_calls', 0)} 次调用，估算 {json.dumps(costs.get('estimated_by_currency', {}), ensure_ascii=False)}；实际扣费及净利润未知。",
            f"- 扣费凭据核对：{costs.get('verified_cost_calls', 0)}/{costs.get('recorded_calls', 0)} 次；有凭据金额 {json.dumps(costs.get('actual_verified_by_currency', {}), ensure_ascii=False)}；未回传金额不按零处理。",
            f"- 单篇精确收益覆盖：{attribution.get('exact_count', 0)}/{attribution.get('total_count', 0)}（{attribution.get('coverage_pct', 0)}%）",
            f"- 收益页采集：{collection.get('status', 'unknown')}，匹配作品 {collection.get('matched_works', 0)}，金额冲突 {collection.get('conflicting_works', 0)}；采集时间 {collection.get('observed_at', '未知')}",
            "",
            "## 分体裁表现",
            "",
            "| 体裁 | 作品 | 展现 | 阅读 | 阅读率 | 30天收益 | 千次展现收益 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for key, label in (("weitoutiao", "微头条"), ("article", "文章")):
            row = formats.get(key) or {}
            lines.append(
                f"| {label} | {row.get('works', 0)} | {row.get('impressions', 0)} | "
                f"{row.get('reads', '未知')} | {row.get('read_rate_pct') if row.get('read_rate_pct') is not None else '未知'}% | "
                f"{self._money(row.get('revenue'))} | {self._money(row.get('revenue_per_1000_impressions'))} |"
            )
        lines.extend(["", "## 实验进度"])
        for days, window in (dashboard.get("income_windows") or {}).items():
            lines.append(f"- {days}天自然窗口 {window['window_start']}～{window['window_end']}：完整收益 {self._money(window['complete_total'])}，已取得 {window['settled_days']}/{days} 天。")
        lines.append("- 展现和阅读为发布队列的累计计数，不能与自然日收益相除；来源、截止时间不齐时千次收益未知。")
        for target in experiment.get("targets", []):
            lines.append(
                f"- {target.get('label')}：{target.get('current')}/{target.get('target')}，"
                f"状态 {target.get('status')}"
            )
        lines.extend(["", "## 下一步动作"])
        for action in experiment.get("next_actions", []):
            lines.append(f"- {action}")
        blockers = readiness.get("blockers") or []
        warnings = readiness.get("warnings") or []
        if blockers or warnings:
            lines.extend(["", "## 权益与数据提醒"])
            for item in blockers:
                lines.append(f"- 阻塞：{item}")
            for item in warnings:
                lines.append(f"- 提醒：{item}")
        lines.extend(
            [
                "",
                "> 单篇收益只记录平台返回的精确金额；不会把账号日收益按阅读量猜测分摊。",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _valid_amount(value: Any) -> bool:
        try:
            return type(value) in {int, float} and math.isfinite(value) and value >= 0
        except OverflowError:
            return False

    def _income_summary(self, rows: list[dict[str, Any]], reference_time: datetime, *, days: int = 30) -> dict[str, Any]:
        current = reference_time.date()
        end = current if any(self._parse_datetime(row.get("day")) and self._parse_datetime(row["day"]).date() == current
                             and row.get("pending") is False for row in rows) else current - timedelta(days=1)
        cutoff_30 = end - timedelta(days=days - 1)
        cutoff_7 = end - timedelta(days=6)
        cutoff_14 = end - timedelta(days=13)
        unique, conflicts = {}, set()
        amount = self._valid_amount
        aggregation_errors = []
        def finite_sum(values, name):
            value = sum(values)
            if not math.isfinite(value):
                aggregation_errors.append(name)
                return None
            return round(value, 4)
        for row in rows:
            day = self._parse_datetime(row.get("day"))
            if not day or not cutoff_30 <= day.date() <= end or row.get("pending") is not False:
                continue
            if not amount(row.get("total")):
                continue
            key = day.date()
            if key in unique and any(unique[key].get(field) != row.get(field) for field in ("total", "article", "weitoutiao", "video")):
                conflicts.add(key)
            unique.setdefault(key, row)
        settled = [(day, row) for day, row in sorted(unique.items()) if day not in conflicts]
        total = finite_sum((float(row["total"]) for _, row in settled), "settled_total")
        latest_7d = finite_sum((float(row["total"]) for day, row in settled if day >= cutoff_7), "latest_7d")
        previous_7d = finite_sum((float(row["total"]) for day, row in settled if cutoff_14 <= day < cutoff_7), "previous_7d")
        by_format = {key: finite_sum((float(row[key]) for _, row in settled), "by_format." + key)
                     if settled and all(amount(row.get(key)) for _, row in settled) else None
                     for key in ("article", "weitoutiao", "video")}
        present = {day for day, _ in settled}
        missing = [end - timedelta(days=i) for i in range(days) if end - timedelta(days=i) not in present]
        complete = not missing and not aggregation_errors
        latest_complete = all(end - timedelta(days=i) in present for i in range(7))
        previous_complete = all(end - timedelta(days=i) in present for i in range(7, 14))
        return {
            "window_start": cutoff_30.isoformat(), "window_end": end.isoformat(),
            "conflicting_days": [day.isoformat() for day in sorted(conflicts)],
            "coverage_complete": complete, "complete_total": total if complete else None,
            "missing_days": [day.isoformat() for day in sorted(missing)],
            "scope": "earnings_date_settled_daily", "window_days": days,
            "aggregation_errors": aggregation_errors,
            "settled_days": len(settled),
            "settled_total": total if settled else None,
            "observed_latest_7d": latest_7d if settled else None,
            "latest_7d": latest_7d if latest_complete else None,
            "previous_7d": previous_7d if previous_complete else None,
            "change_7d": round(latest_7d - previous_7d, 4) if latest_complete and previous_complete and latest_7d is not None and previous_7d is not None else None,
            "by_format": by_format if settled else {},
        }

    def _format_metrics(
        self,
        growth_snapshot: dict[str, Any],
        income_summary: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        summary = growth_snapshot.get("summary") if isinstance(growth_snapshot.get("summary"), dict) else {}
        # Matching duration is insufficient: these counters are lifetime totals
        # for works created in the window, not traffic earned on those days.
        aligned = False
        by_format_income = income_summary.get("by_format") or {}
        out: dict[str, dict[str, Any]] = {}
        for key in ("article", "weitoutiao"):
            raw = summary.get(key) if isinstance(summary.get(key), dict) else {}
            works = int(raw.get("works", 0) or 0)
            impressions = int(raw.get("impressions", 0) or 0)
            reads = int(raw.get("reads", 0) or 0)
            revenue = by_format_income.get(key)
            rpm = None
            rpr = None
            out[key] = {
                "works": works,
                "impressions": impressions,
                "reads": reads,
                "zero_read_works": int(raw.get("zero_read_works", 0) or 0),
                "read_rate_pct": round(reads * 100 / impressions, 4) if impressions else 0.0,
                "works_above_1000_impressions": 0,
                "works_above_1000_share_pct": 0.0,
                "revenue": revenue if income_summary.get("coverage_complete") and self._valid_amount(revenue) else None,
                "observed_revenue": revenue if self._valid_amount(revenue) else None,
                "revenue_per_1000_impressions": rpm,
                "revenue_per_1000_reads": rpr,
                "window_aligned": aligned,
                "scope": "creation_cohort_cumulative_observed_inventory",
            }
            if not raw:
                for field in ("works", "impressions", "reads", "zero_read_works", "read_rate_pct"):
                    out[key][field] = None
        works = growth_snapshot.get("works") if isinstance(growth_snapshot.get("works"), list) else []
        for key in out:
            group = [row for row in works if str(row.get("type", "")) == key]
            above = sum(int(row.get("impressions", 0) or 0) >= 1000 for row in group)
            out[key]["works_above_1000_impressions"] = above
            out[key]["works_above_1000_share_pct"] = (
                round(above * 100 / len(group), 2) if group else 0.0
            )
        return out

    def _traffic_windows(self, snapshot: dict[str, Any], reference: datetime) -> dict[str, Any]:
        """Observed creation cohorts, explicitly not calendar-day traffic."""
        result = {}
        for days in (7, 30, 90):
            groups = {}
            for kind in ("article", "weitoutiao"):
                rows = []
                for work in snapshot.get("works", []):
                    created = self._parse_datetime(work.get("created_at"))
                    if (work.get("type") != kind or created is None
                            or not reference.date() - timedelta(days=days - 1) <= created.date() <= reference.date()):
                        continue
                    if all(type(work.get(k)) is int and work[k] >= 0 for k in ("impressions", "reads")):
                        rows.append(work)
                impressions = sum(w["impressions"] for w in rows)
                reads = sum(w["reads"] for w in rows)
                groups[kind] = {"observed_works": len(rows), "impressions": impressions if rows else None,
                    "reads": reads if rows else None, "comments": sum(w.get("comments", 0) for w in rows)
                        if rows and all(type(w.get("comments")) is int and w["comments"] >= 0 for w in rows) else None,
                    "read_rate_pct": round(reads * 100 / impressions, 4) if impressions else None}
            result[str(days)] = {"scope": "creation_cohort_lifetime_counters", "inventory_complete": False,
                "calendar_traffic": None, "candidate_supply": None, "formats": groups}
        return result

    def _per_content_attribution(self, performance: dict[str, Any]) -> dict[str, Any]:
        records = performance.get("records") if isinstance(performance.get("records"), list) else []
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        exact_count = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            key = "|".join(
                str(record.get(field, "")).strip()
                for field in ("content_id", "url", "title", "publish_date")
            )
            if key in seen:
                continue
            seen.add(key)
            revenue = record.get("platform_revenue")
            exact = self._valid_amount(revenue) and (
                bool(record.get("revenue_amount_known"))
                or str(record.get("platform_revenue_status", "")) in {"", "available"}
            )
            if exact:
                exact_count += 1
            impressions = int(record.get("impression_count", 0) or 0)
            reads = int(record.get("read_count", record.get("views", 0)) or 0)
            exact_revenue = float(revenue) if exact else None
            rows.append(
                {
                    "content_id": str(record.get("content_id", "")),
                    "platform": str(record.get("platform", "")),
                    "title": str(record.get("title", "")),
                    "publish_date": str(record.get("publish_date", "")),
                    "impressions": impressions,
                    "reads": reads,
                    "revenue": exact_revenue,
                    "revenue_status": "exact" if exact else str(record.get("revenue_status", "unknown")),
                    "revenue_as_of_date": record.get("revenue_as_of_date", ""),
                    "revenue_per_1000_impressions": (
                        round(exact_revenue * 1000 / impressions, 4)
                        if exact and impressions and record.get("revenue_scope") == "checkpoint_cumulative"
                        and record.get("revenue_counters_captured_at")
                        and self._parse_datetime(record.get("revenue_counters_captured_at"))
                        and record.get("revenue_counters_captured_at") == record.get("counters_captured_at")
                        else None
                    ),
                }
            )
        total = len(rows)
        return {
            "total_count": total,
            "exact_count": exact_count,
            "unknown_count": total - exact_count,
            "coverage_pct": round(exact_count * 100 / total, 2) if total else 0.0,
            "rows": rows,
        }

    def _experiment_status(
        self,
        *,
        growth_snapshot: dict[str, Any],
        growth_feedback: dict[str, Any],
        format_metrics: dict[str, dict[str, Any]],
        attribution: dict[str, Any],
        readiness: dict[str, Any],
    ) -> dict[str, Any]:
        wtt = format_metrics.get("weitoutiao") or {}
        article = format_metrics.get("article") or {}
        benchmarks = (
            growth_feedback.get("benchmarks")
            if isinstance(growth_feedback.get("benchmarks"), dict)
            else {}
        )
        wtt_benchmark = (
            benchmarks.get("weitoutiao")
            if isinstance(benchmarks.get("weitoutiao"), dict)
            else {}
        )
        calibration = (
            growth_feedback.get("creative_calibration")
            if isinstance(growth_feedback.get("creative_calibration"), dict)
            else {}
        )
        topic_feedback = growth_feedback.get("topic_feedback")
        mature_wtt_samples = int(
            topic_feedback.get("qualified_samples", 0)
            if isinstance(topic_feedback, dict)
            else wtt_benchmark.get("samples", 0) or 0
        )
        mature_article_samples = int(calibration.get("article_samples", 0) or 0)
        targets = [
            self._target("成熟微头条样本", mature_wtt_samples, self.WTT_SAMPLE_TARGET),
            self._target("有效文章样本", mature_article_samples, self.ARTICLE_SAMPLE_TARGET),
            self._target(
                "微头条阅读率(%)",
                round(float(wtt.get("read_rate_pct", 0) or 0), 2),
                self.WTT_READ_RATE_TARGET,
            ),
            self._target(
                "千展作品占比(%)",
                round(float(wtt.get("works_above_1000_share_pct", 0) or 0), 2),
                self.THOUSAND_IMPRESSION_SHARE_TARGET,
            ),
        ]
        actions: list[str] = []
        if readiness.get("status") != "ready":
            actions.append("先复核创作收益权益和信用分，再恢复真实发布。")
        if mature_wtt_samples < self.WTT_SAMPLE_TARGET:
            actions.append("统计题材调权仍需30条合格样本；题材聚焦与信息价值改进立即执行，不等待该门槛。")
        batch = growth_feedback.get("reader_value_batch") or {}
        actions.append(f"按5—7篇新作品复盘24/72小时阅读量、阅读率及展现；当前 {batch.get('status', 'not_started')}，不能只看提交成功。")
        if batch.get("next_action"):
            actions.append(str(batch["next_action"]))
        if float(wtt.get("read_rate_pct", 0) or 0) < self.WTT_READ_RATE_TARGET:
            actions.append("微头条阅读率低于首阶段2%目标；优先回答具体读者问题，并要求阅读量增加和展现不下降。")
        if int(article.get("reads", 0) or 0) == 0 and int(article.get("works", 0) or 0) > 0:
            actions.append("文章仍为零阅读，只保留高证据深度首发，每周最多2篇。")
        if float(attribution.get("coverage_pct", 0) or 0) < 80:
            actions.append("单篇收益覆盖不足80%，继续采集平台精确金额，暂不按RPM自动调权。")
        decision_ready = bool(
            all(target["status"] == "reached" for target in targets[:2])
            and float(attribution.get("coverage_pct", 0) or 0) >= 80
            and readiness.get("status") == "ready"
        )
        return {
            "status": "decision_ready" if decision_ready else "collecting",
            "snapshot_window_days": int(growth_snapshot.get("window_days", 0) or 0),
            "reader_value_batch": batch,
            "targets": targets,
            "next_actions": actions,
        }

    @staticmethod
    def _target(label: str, current: int | float, target: int | float) -> dict[str, Any]:
        return {
            "label": label,
            "current": current,
            "target": target,
            "status": "reached" if current >= target else "collecting",
        }

    @staticmethod
    def _latest_account_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else None
        return latest or payload

    @staticmethod
    def _check_state(value: bool | None) -> str:
        return "unknown" if value is None else "ready" if value else "blocked"

    @staticmethod
    def _optional_bool(value: object) -> bool | None:
        return value if isinstance(value, bool) else None

    @staticmethod
    def _optional_number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) else None
        except OverflowError:
            return None

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _money(value: object) -> str:
        return "未知" if not isinstance(value, (int, float)) else f"¥{float(value):.4f}"

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
