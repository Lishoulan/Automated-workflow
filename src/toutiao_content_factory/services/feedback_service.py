from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings


class FeedbackService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.feedback_dir = settings.automation_dir / "feedback"
        self.feedback_dir.mkdir(parents=True, exist_ok=True)

    def collect_run_metrics(self, run_dir: Path) -> dict[str, Any]:
        metrics: dict[str, Any] = {
            "run_dir": str(run_dir),
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "title": "",
            "status": "unknown",
            "read_count": 0,
            "impression_count": 0,
            "completion_rate": 0.0,
            "revenue": 0.0,
            "has_violation": False,
            "violation_type": "",
            "overall_score": 0,
            "ai_risk_score": 0.0,
            "content_format": "",
            "content_mode": "",
        }
        review_status_path = run_dir / "review_status.json"
        if review_status_path.exists():
            try:
                status = json.loads(review_status_path.read_text(encoding="utf-8"))
                metrics["status"] = str(status.get("status", "unknown"))
                metrics["overall_score"] = int(status.get("overall_score", 0) or 0)
            except Exception:
                pass

        title_path = run_dir / self.settings.review.publish_dirname / "title.txt"
        if title_path.exists():
            try:
                metrics["title"] = title_path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                pass

        content_guard_path = run_dir / "content_guard_report.json"
        if content_guard_path.exists():
            try:
                guard = json.loads(content_guard_path.read_text(encoding="utf-8"))
                metrics["ai_risk_score"] = float(guard.get("ai_risk_score", 0.0) or 0.0)
            except Exception:
                pass

        workflow_path = run_dir / "workflow.json"
        if workflow_path.exists():
            try:
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
                content = workflow.get("generated_content", {})
                metrics["content_format"] = str(content.get("content_format", ""))
                metrics["content_mode"] = str(content.get("content_format_label", ""))
            except Exception:
                pass

        return metrics

    def classify_sample(self, metrics: dict[str, Any]) -> str:
        if metrics.get("has_violation"):
            return "failure"
        read_count = int(metrics.get("read_count", 0))
        overall_score = int(metrics.get("overall_score", 0))
        if read_count >= 1000 or overall_score >= 85:
            return "success"
        if read_count >= 100 or overall_score >= 70:
            return "neutral"
        return "failure"

    def save_feedback(self, metrics: dict[str, Any]) -> Path:
        sample_type = self.classify_sample(metrics)
        metrics["sample_type"] = sample_type
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"feedback_{date_str}_{sample_type}.json"
        filepath = self.feedback_dir / filename
        existing: list[dict[str, Any]] = []
        if filepath.exists():
            try:
                existing = json.loads(filepath.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        existing.append(metrics)
        filepath.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return filepath

    def get_success_patterns(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = datetime.now() - timedelta(days=days)
        patterns: list[dict[str, Any]] = []
        if not self.feedback_dir.exists():
            return patterns
        for filepath in self.feedback_dir.glob("feedback_*_success.json"):
            try:
                items = json.loads(filepath.read_text(encoding="utf-8"))
                for item in items if isinstance(items, list) else [items]:
                    collected = item.get("collected_at", "")
                    if collected:
                        try:
                            collected_dt = datetime.fromisoformat(collected)
                            if collected_dt < cutoff:
                                continue
                        except Exception:
                            pass
                    patterns.append(item)
            except Exception:
                continue
        return patterns

    def get_failure_patterns(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff = datetime.now() - timedelta(days=days)
        patterns: list[dict[str, Any]] = []
        if not self.feedback_dir.exists():
            return patterns
        for filepath in self.feedback_dir.glob("feedback_*_failure.json"):
            try:
                items = json.loads(filepath.read_text(encoding="utf-8"))
                for item in items if isinstance(items, list) else [items]:
                    collected = item.get("collected_at", "")
                    if collected:
                        try:
                            collected_dt = datetime.fromisoformat(collected)
                            if collected_dt < cutoff:
                                continue
                        except Exception:
                            pass
                    patterns.append(item)
            except Exception:
                continue
        return patterns

    def generate_strategy_report(self, days: int = 30) -> dict[str, Any]:
        success = self.get_success_patterns(days)
        failure = self.get_failure_patterns(days)
        success_formats: dict[str, int] = {}
        for item in success:
            fmt = str(item.get("content_format", "unknown"))
            success_formats[fmt] = success_formats.get(fmt, 0) + 1
        failure_formats: dict[str, int] = {}
        for item in failure:
            fmt = str(item.get("content_format", "unknown"))
            failure_formats[fmt] = failure_formats.get(fmt, 0) + 1
        avg_success_score = 0.0
        if success:
            avg_success_score = sum(int(s.get("overall_score", 0)) for s in success) / len(success)
        avg_failure_score = 0.0
        if failure:
            avg_failure_score = sum(int(f.get("overall_score", 0)) for f in failure) / len(failure)
        return {
            "period_days": days,
            "total_success": len(success),
            "total_failure": len(failure),
            "success_rate": round(len(success) / max(len(success) + len(failure), 1), 4),
            "avg_success_score": round(avg_success_score, 1),
            "avg_failure_score": round(avg_failure_score, 1),
            "best_formats": sorted(success_formats.items(), key=lambda x: x[1], reverse=True)[:5],
            "worst_formats": sorted(failure_formats.items(), key=lambda x: x[1], reverse=True)[:5],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
