from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings


@dataclass
class RunSummary:
    name: str
    created_at: str
    selected_topic_title: str
    review_status: str
    recommended_action: str
    overall_score: int
    ai_risk: int
    validation_score: int
    publish_readiness_score: int
    has_publish: bool
    publish_title: str
    path: Path


@dataclass
class ScheduledTaskSummary:
    task_name: str
    state: str
    last_run_time: str
    next_run_time: str
    last_task_result: int
    platform: str = ""
    account_slug: str = ""
    slot_index: int = 0


@dataclass
class LogSummary:
    name: str
    modified_at: str
    size_kb: float
    url: str


@dataclass
class PublishStatusSummary:
    status: str
    submitted_at: str
    title: str
    run_name: str
    topic_title: str
    pgc_id: str
    message: str
    external_url: str
    has_result: bool


@dataclass
class WechatPublishStatusSummary:
    status: str
    submitted_at: str
    title: str
    run_name: str
    draft_media_id: str
    submit_status: str
    external_url: str
    inline_image_count: int
    layout_backend: str
    submit_requested: bool
    has_result: bool


@dataclass
class ScheduledWindowSummary:
    slot_label: str
    expected_time: str
    status: str
    updated_at: str
    message: str
    run_name: str
    publish_status: str
    publish_pgc_id: str
    log_url: str


class WebViewService:
    SCHEDULED_TASK_PREFIXES = {
        "toutiao": "ToutiaoContentFactory",
        "wechat": "WechatContentFactory",
    }
    BENIGN_TASK_RESULTS = {0, 267009, 267011}
    _TASK_CATALOG_CACHE: list[ScheduledTaskSummary] = []
    _TASK_CATALOG_CACHE_AT: float = 0.0
    _TASK_CATALOG_TTL_SECONDS = 20.0

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.output_dir = settings.output_dir
        self.runs_dir = settings.runs_dir
        self.logs_dir = settings.logs_dir
        self.scheduled_logs_dir = settings.scheduled_logs_dir
        self.automation_dir = settings.automation_dir
        self.publish_dirname = settings.review.publish_dirname

    def list_styles(self) -> list[dict[str, str]]:
        return [{"key": key, "label": profile.label} for key, profile in self.settings.style_profiles.items()]

    @property
    def account_key(self) -> str:
        return self.settings.account_key

    def load_daily_topics(self) -> dict[str, Any] | None:
        return self._load_json(self.output_dir / "daily_topics.json")

    def list_runs(self, limit: int | None = None) -> list[RunSummary]:
        if not self.runs_dir.exists():
            return []
        candidates = [item for item in self.runs_dir.iterdir() if item.is_dir()]
        candidates.sort(key=self._run_sort_key, reverse=True)
        if limit is not None:
            candidates = candidates[:limit]
        return [self._build_run_summary(path) for path in candidates]

    def load_dashboard(self) -> dict[str, Any]:
        topics = self.load_daily_topics()
        runs = self.list_runs(limit=8)
        latest_score = self._load_json(self.output_dir / "content_score.json") or {}
        latest_selected_topic = (self._load_json(self.output_dir / "selected_topic.json") or {}).get("selected_topic", {})
        latest_workflow = self._load_json(self.output_dir / "workflow_result.json") or {}
        scheduled_status = self._normalize_dashboard_scheduled_status(
            self._load_json(self.automation_dir / "scheduled_status.json") or {}
        )
        latest_publish = self.load_latest_publish_status()
        publish_preflight = self.load_publish_preflight()
        return {
            "daily_topics": topics,
            "runs": runs,
            "latest_score": latest_score,
            "latest_selected_topic": latest_selected_topic,
            "latest_workflow": latest_workflow,
            "latest_publish": latest_publish,
            "publish_preflight": publish_preflight,
            "scheduled_tasks": self.list_scheduled_tasks(),
            "scheduled_logs": self.list_scheduled_logs(limit=6),
            "scheduled_status": scheduled_status,
            "scheduled_windows": self.list_scheduled_windows(),
            "notifications": self.list_notifications(limit=8),
            "publish_history": self.list_publish_history(limit=8),
        }

    def _normalize_dashboard_scheduled_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or not payload:
            return payload
        normalized = dict(payload)
        status_value = str(normalized.get("status", "")).strip().lower()
        workflow_status = str(normalized.get("workflow_status", "")).strip().lower()
        publish_status = str(normalized.get("publish_status", "")).strip()
        workflow_errors = normalized.get("workflow_errors", [])
        first_error = ""
        if isinstance(workflow_errors, list) and workflow_errors:
            first_error = str(workflow_errors[0]).strip().lower()
        if (
            status_value == "failed"
            and workflow_status in {"platform_publish_failed", "platform_partial_success"}
            and publish_status
        ):
            normalized["status"] = publish_status
            if "wechat publish failed" in first_error or "cover image not found" in first_error:
                normalized["message"] = "头条已正常提交，公众号发布失败：缺少封面图或公众号素材图。"
            else:
                normalized["message"] = "头条已正常提交，但至少有一个其他平台发布失败。"
        return normalized

    def build_account_snapshot(
        self,
        *,
        task_catalog: list[ScheduledTaskSummary] | None = None,
        is_current: bool = False,
    ) -> dict[str, Any]:
        run_count, latest_run = self.load_runs_overview()
        scheduled_status = self.load_scheduled_status()
        workflow_report = self.load_latest_workflow_report()
        latest_publish = self.load_latest_publish_status()
        latest_wechat = self.load_latest_wechat_status()
        publish_preflight = self.load_publish_preflight()
        wechat_preflight = self.load_wechat_publish_preflight()
        benchmark_merge = self.load_benchmark_topic_merge_summary()
        notifications = self.list_notifications(limit=3)
        latest_log = next(iter(self.list_scheduled_logs(limit=1)), None)

        toutiao_tasks = self._filter_task_catalog(task_catalog, platform="toutiao")
        wechat_tasks = self._filter_task_catalog(task_catalog, platform="wechat")

        workflow_attempt = workflow_report.get("final_attempt", {}) if isinstance(workflow_report, dict) else {}
        workflow_scores = workflow_attempt.get("score_snapshot", {}) if isinstance(workflow_attempt, dict) else {}
        if not workflow_scores and latest_run is not None:
            workflow_scores = {
                "overall_score": latest_run.overall_score,
                "validation_score": latest_run.validation_score,
                "publish_readiness_score": latest_run.publish_readiness_score,
                "ai_generated_risk": latest_run.ai_risk,
            }

        toutiao_platform = self._build_toutiao_platform_snapshot(
            latest_run=latest_run,
            scheduled_status=scheduled_status,
            latest_publish=latest_publish,
            publish_preflight=publish_preflight,
            latest_log=latest_log,
            tasks=toutiao_tasks,
            workflow_scores=workflow_scores if isinstance(workflow_scores, dict) else {},
            benchmark_merge=benchmark_merge,
        )
        wechat_platform = self._build_wechat_platform_snapshot(
            latest_wechat=latest_wechat,
            wechat_preflight=wechat_preflight,
            latest_log=latest_log,
            tasks=wechat_tasks,
            benchmark_merge=benchmark_merge,
        )

        attention_items = self._build_account_attention_items(
            latest_run=latest_run,
            notifications=notifications,
            toutiao_platform=toutiao_platform,
            wechat_platform=wechat_platform,
        )
        overall_meta = self._derive_overall_meta(toutiao_platform, wechat_platform, attention_items)

        return {
            "key": self.settings.account_key,
            "slug": self.settings.account_slug,
            "label": self.settings.account_label,
            "brand_name": self.settings.brand_name,
            "focus": self.settings.industry_focus,
            "audience": self.settings.audience,
            "is_current": is_current,
            "search_text": " ".join(
                part
                for part in (
                    self.settings.account_key,
                    self.settings.account_label,
                    self.settings.brand_name,
                    self.settings.industry_focus,
                    self.settings.audience,
                )
                if part
            ).lower(),
            "run_count": run_count,
            "toutiao_publish_count": self.count_publish_history(),
            "wechat_publish_count": self.count_wechat_history(),
            "latest_run_name": latest_run.name if latest_run is not None else "",
            "latest_topic_title": latest_run.selected_topic_title if latest_run is not None else "",
            "latest_review_status": latest_run.review_status if latest_run is not None else "",
            "latest_notification": notifications[0] if notifications else {},
            "has_pending_review": bool(latest_run and latest_run.review_status == "pending"),
            "overall_label": overall_meta["label"],
            "overall_tone": overall_meta["tone"],
            "attention_items": attention_items,
            "quick_links": {
                "runs": f"/runs?account={self.account_key}",
                "published": f"/published?account={self.account_key}",
                "topics": f"/topics?account={self.account_key}",
                "latest_run": f"/runs/{latest_run.name}?account={self.account_key}" if latest_run is not None else "",
            },
            "toutiao": toutiao_platform,
            "wechat": wechat_platform,
        }

    def load_run_detail(self, run_name: str) -> dict[str, Any]:
        run_dir = (self.runs_dir / run_name).resolve()
        if not run_dir.exists() or run_dir.parent != self.runs_dir.resolve():
            raise FileNotFoundError(f"未找到批次：{run_name}")

        review_status = self._load_json(run_dir / "review_status.json") or {}
        selected_topic_payload = self._load_json(run_dir / "selected_topic.json") or {}
        publish_meta = self._load_json(run_dir / self.settings.review.publish_dirname / "publish_meta.json") or {}
        content_score = self._load_json(run_dir / "content_score.json") or {}
        workflow_result = self._load_json(run_dir / "workflow_result.json") or {}
        publish_result = self._load_json(run_dir / self.settings.review.publish_dirname / "publish_result.json") or {}
        publish_status = self._build_publish_status_summary(run_dir.name, publish_result, publish_meta)
        titles_payload = self._parse_titles_file(run_dir / "titles.txt")
        article_body = self._read_text(run_dir / "article.md")
        editorial_packet = self._read_text(run_dir / "editorial_packet.md")
        review_packet = self._read_text(run_dir / "review_packet.md")
        publish_package = self._read_text(run_dir / self.settings.review.publish_dirname / "toutiao_publish_package.md")
        selected_topic = selected_topic_payload.get("selected_topic", {})
        preflight_summary = content_score.get("preflight_summary", {}) if isinstance(content_score, dict) else {}

        artifacts = {
            "review_packet": self._artifact_url(run_name, "review_packet.md"),
            "review_status": self._artifact_url(run_name, "review_status.json"),
            "run_manifest": self._artifact_url(run_name, "run_manifest.json"),
            "article": self._artifact_url(run_name, "article.md"),
            "titles": self._artifact_url(run_name, "titles.txt"),
            "content_score": self._artifact_url(run_name, "content_score.json"),
            "workflow_result": self._artifact_url(run_name, "workflow_result.json"),
        }
        if publish_meta:
            artifacts["publish_package"] = self._artifact_url(
                run_name, f"{self.settings.review.publish_dirname}/toutiao_publish_package.md"
            )
        if publish_result:
            artifacts["publish_result"] = self._artifact_url(
                run_name, f"{self.settings.review.publish_dirname}/publish_result.json"
            )

        return {
            "run": self._build_run_summary(run_dir),
            "review_status": review_status,
            "selected_topic": selected_topic,
            "publish_meta": publish_meta,
            "publish_result": publish_result,
            "publish_status": publish_status,
            "content_score": content_score,
            "workflow_result": workflow_result,
            "preflight_summary": preflight_summary,
            "titles": titles_payload,
            "article_body": article_body,
            "editorial_packet": editorial_packet,
            "review_packet": review_packet,
            "publish_package": publish_package,
            "artifacts": artifacts,
        }

    def list_scheduled_tasks(self) -> list[ScheduledTaskSummary]:
        return self._filter_task_catalog(None, platform="toutiao")

    def list_wechat_scheduled_tasks(self) -> list[ScheduledTaskSummary]:
        return self._filter_task_catalog(None, platform="wechat")

    def list_scheduled_logs(self, limit: int = 6) -> list[LogSummary]:
        if not self.scheduled_logs_dir.exists():
            return []
        files = [item for item in self.scheduled_logs_dir.iterdir() if item.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return [
            LogSummary(
                name=path.name,
                modified_at=self._format_timestamp(path.stat().st_mtime),
                size_kb=round(path.stat().st_size / 1024, 1),
                url=self._account_log_artifact_url(f"scheduled/{path.name}"),
            )
            for path in files[:limit]
        ]

    def list_notifications(self, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._load_json(self.automation_dir / "notifications.json")
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []
        return [item for item in payload[:limit] if isinstance(item, dict)]

    def list_publish_history(self, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        if not isinstance(payload, list):
            return []
        result: list[dict[str, Any]] = []
        for item in islice(reversed(payload), limit):
            if not isinstance(item, dict):
                continue
            run_name = Path(str(item.get("run_dir", ""))).name if item.get("run_dir") else ""
            result.append(
                {
                    "published_at": str(item.get("published_at", "")),
                    "title": str(item.get("title", "")),
                    "topic_title": str(item.get("topic_title", "")),
                    "status": str(item.get("status", "")),
                    "external_url": str(item.get("external_url", "")),
                    "run_name": run_name,
                }
            )
        return result

    def list_published_articles(self, limit: int = 10) -> list[dict[str, Any]]:
        payload = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        if not isinstance(payload, list):
            return []

        result: list[dict[str, Any]] = []
        for item in islice(reversed(payload), limit):
            if not isinstance(item, dict):
                continue
            run_dir_value = str(item.get("run_dir", "")).strip()
            run_name = Path(run_dir_value).name if run_dir_value else ""
            if not run_name:
                continue

            run_dir = (self.runs_dir / run_name).resolve()
            if not run_dir.exists() or run_dir.parent != self.runs_dir.resolve():
                continue

            publish_dir = run_dir / self.publish_dirname
            title_path = publish_dir / "title.txt"
            article_path = publish_dir / "article.md"
            publish_result_path = publish_dir / "publish_result.json"

            title = self._read_text(title_path).strip() or str(item.get("title", "")).strip()
            article_body = self._read_text(article_path).strip() or self._read_text(run_dir / "article.md").strip()
            article_paragraphs = self._split_article_paragraphs(article_body)

            publish_result = self._load_json(publish_result_path) or {}
            backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}
            notes = item.get("notes", []) if isinstance(item.get("notes"), list) else []

            result.append(
                {
                    "title": title,
                    "published_at": str(item.get("published_at", "")),
                    "status": str(item.get("status", "")),
                    "topic_title": str(item.get("topic_title", "")),
                    "topic_url": str(item.get("topic_url", "")),
                    "run_name": run_name,
                    "external_url": str(item.get("external_url", "")),
                    "pgc_id": str(backend.get("pgc_id", "")),
                    "message": str(backend.get("message", publish_result.get("message", "")))
                    if isinstance(publish_result, dict)
                    else "",
                    "article_body": article_body,
                    "article_paragraphs": article_paragraphs,
                    "excerpt": "\n\n".join(article_paragraphs[:3]),
                    "notes": [str(note) for note in notes if str(note).strip()],
                    "artifacts": {
                        "title": self._artifact_url(run_name, f"{self.publish_dirname}/title.txt"),
                        "article": self._artifact_url(run_name, f"{self.publish_dirname}/article.md"),
                        "publish_result": self._artifact_url(run_name, f"{self.publish_dirname}/publish_result.json"),
                        "run_detail": f"/runs/{run_name}?account={self.account_key}",
                    },
                }
            )
        return result

    def load_latest_publish_status(self) -> PublishStatusSummary:
        history = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        if not isinstance(history, list) or not history:
            return PublishStatusSummary(
                status="not_published",
                submitted_at="",
                title="",
                run_name="",
                topic_title="",
                pgc_id="",
                message="",
                external_url="",
                has_result=False,
            )

        latest = history[-1] if isinstance(history[-1], dict) else {}
        run_name = Path(str(latest.get("run_dir", ""))).name if latest.get("run_dir") else ""
        publish_result = {}
        if run_name:
            publish_result = (
                self._load_json(self.runs_dir / run_name / self.settings.review.publish_dirname / "publish_result.json")
                or {}
            )
        return self._build_publish_status_summary(run_name, publish_result, latest)

    def load_latest_wechat_status(self) -> WechatPublishStatusSummary:
        history = self._load_json(self.output_dir / self.settings.wechat.history_filename)
        if not isinstance(history, list) or not history:
            snapshot = self._load_json(self.automation_dir / "last_wechat_publish_result.json")
            if not isinstance(snapshot, dict):
                return WechatPublishStatusSummary(
                    status="not_published",
                    submitted_at="",
                    title="",
                    run_name="",
                    draft_media_id="",
                    submit_status="",
                    external_url="",
                    inline_image_count=0,
                    layout_backend="",
                    submit_requested=False,
                    has_result=False,
                )
            return self._build_wechat_status_summary(snapshot, snapshot)

        latest = history[-1] if isinstance(history[-1], dict) else {}
        run_name = Path(str(latest.get("run_dir", ""))).name if latest.get("run_dir") else ""
        publish_result = {}
        if run_name:
            publish_result = (
                self._load_json(self.runs_dir / run_name / self.settings.review.publish_dirname / "wechat" / "wechat_publish_result.json")
                or {}
            )
        return self._build_wechat_status_summary(publish_result, latest)

    def load_scheduled_status(self) -> dict[str, Any]:
        payload = self._load_json(self.automation_dir / "scheduled_status.json")
        return payload if isinstance(payload, dict) else {}

    def load_latest_workflow_report(self) -> dict[str, Any]:
        payload = self._load_json(self.automation_dir / "last_daily_workflow_report.json")
        return payload if isinstance(payload, dict) else {}

    def load_benchmark_topic_merge_summary(self) -> dict[str, Any]:
        path = self.automation_dir / "benchmark_topic_merge.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {}

        benchmark_accounts = payload.get("benchmark_accounts", [])
        if not isinstance(benchmark_accounts, list):
            benchmark_accounts = []
        top_titles = payload.get("top_titles", [])
        if not isinstance(top_titles, list):
            top_titles = []

        return {
            "generated_at": str(payload.get("generated_at", "")).strip(),
            "account_key": str(payload.get("account_key", "")).strip(),
            "account_label": str(payload.get("account_label", "")).strip(),
            "platform": str(payload.get("platform", "")).strip().lower(),
            "track": str(payload.get("track", "")).strip(),
            "base_topic_count": int(payload.get("base_topic_count", 0) or 0),
            "benchmark_topic_count": int(payload.get("benchmark_topic_count", 0) or 0),
            "merged_topic_count": int(payload.get("merged_topic_count", 0) or 0),
            "benchmark_source_count": int(payload.get("benchmark_source_count", 0) or 0),
            "benchmark_accounts": [item for item in benchmark_accounts if isinstance(item, dict)],
            "top_titles": [item for item in top_titles if isinstance(item, dict)],
            "artifact_url": self._account_output_artifact_url("automation/benchmark_topic_merge.json"),
        }

    def load_wechat_publish_preflight(self) -> dict[str, Any]:
        path = self.automation_dir / "wechat_publish_preflight.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {"updated_at": "", "state": "", "current_url": ""}
        return {
            "updated_at": self._format_timestamp(path.stat().st_mtime) if path.exists() else "",
            "state": str(payload.get("state", "")),
            "current_url": str(payload.get("current_url", "")),
            "profile_dir": str(payload.get("profile_dir", "")),
        }

    def list_scheduled_windows(self) -> list[ScheduledWindowSummary]:
        notifications = self._load_json(self.automation_dir / "notifications.json")
        items = notifications if isinstance(notifications, list) else []
        slot_map: dict[str, ScheduledWindowSummary] = {}
        latest_logs_by_slot = self._latest_scheduled_logs_by_slot()

        for item in items:
            if not isinstance(item, dict):
                continue
            created_at = str(item.get("created_at", ""))
            if not created_at:
                continue
            slot = self._resolve_schedule_slot(created_at)
            if not slot:
                continue
            data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
            log_url = ""
            log_file = str(data.get("log_file", ""))
            if log_file:
                log_name = Path(log_file).name
                log_url = self._account_log_artifact_url(f"scheduled/{log_name}")
            elif slot in latest_logs_by_slot:
                log_url = latest_logs_by_slot[slot]
            slot_map.setdefault(
                slot,
                ScheduledWindowSummary(
                    slot_label=slot,
                    expected_time=self._slot_to_expected_time(slot),
                    status=str(item.get("level", "info")),
                    updated_at=created_at,
                    message=str(item.get("message", "")),
                    run_name=str(data.get("run_name", "")),
                    publish_status=str(data.get("publish_status", "")),
                    publish_pgc_id=str(data.get("publish_pgc_id", "")),
                    log_url=log_url,
                ),
            )

        result = []
        for slot in ("morning", "noon", "evening", "night"):
            if slot in slot_map:
                result.append(slot_map[slot])
            else:
                result.append(
                    ScheduledWindowSummary(
                        slot_label=slot,
                        expected_time=self._slot_to_expected_time(slot),
                        status="unknown",
                        updated_at="",
                        message="当前还没有这个时段的执行记录。",
                        run_name="",
                        publish_status="",
                        publish_pgc_id="",
                        log_url=latest_logs_by_slot.get(slot, ""),
                    )
                )
        return result

    def _build_run_summary(self, path: Path) -> RunSummary:
        review_status = self._load_json(path / "review_status.json") or {}
        publish_meta = self._load_json(path / self.settings.review.publish_dirname / "publish_meta.json") or {}
        selected_topic_payload = self._load_json(path / "selected_topic.json") or {}
        score_payload = self._load_json(path / "content_score.json") or {}
        score_snapshot = review_status.get("score_snapshot") or score_payload.get("scores") or {}
        created_at = (
            review_status.get("updated_at")
            or review_status.get("created_at")
            or (self._load_json(path / "run_manifest.json") or {}).get("archived_at")
            or ""
        )
        selected_topic = selected_topic_payload.get("selected_topic", {})
        publish_title = str(publish_meta.get("selected_title", ""))
        return RunSummary(
            name=path.name,
            created_at=str(created_at),
            selected_topic_title=str(
                selected_topic.get("title")
                or review_status.get("selected_topic_title")
                or publish_meta.get("selected_topic", {}).get("title", "")
            ),
            review_status=str(review_status.get("status", "pending")),
            recommended_action=str(review_status.get("recommended_action", "manual_review")),
            overall_score=int(score_snapshot.get("overall_score", 0)),
            ai_risk=int(score_snapshot.get("ai_generated_risk", 0)),
            validation_score=int(score_snapshot.get("validation_score", 0)),
            publish_readiness_score=int(score_snapshot.get("publish_readiness_score", 0)),
            has_publish=(path / self.settings.review.publish_dirname).exists(),
            publish_title=publish_title,
            path=path,
        )

    def _build_publish_status_summary(
        self,
        run_name: str,
        publish_result: dict[str, Any],
        fallback_payload: dict[str, Any],
    ) -> PublishStatusSummary:
        backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}
        record = publish_result.get("record", {}) if isinstance(publish_result, dict) else {}
        source = publish_result if isinstance(publish_result, dict) and publish_result else fallback_payload
        return PublishStatusSummary(
            status=str(source.get("status", fallback_payload.get("status", "not_published"))),
            submitted_at=str(source.get("published_at", fallback_payload.get("published_at", ""))),
            title=str(record.get("title", fallback_payload.get("title", ""))),
            run_name=run_name,
            topic_title=str(record.get("topic_title", fallback_payload.get("topic_title", ""))),
            pgc_id=str(backend.get("pgc_id", "")),
            message=str(backend.get("message", publish_result.get("message", ""))),
            external_url=str(source.get("external_url", fallback_payload.get("external_url", ""))),
            has_result=bool(publish_result or fallback_payload),
        )

    def _build_wechat_status_summary(
        self,
        publish_result: dict[str, Any],
        fallback_payload: dict[str, Any],
    ) -> WechatPublishStatusSummary:
        source = publish_result if isinstance(publish_result, dict) and publish_result else fallback_payload
        run_name = Path(str(source.get("run_dir", fallback_payload.get("run_dir", "")))).name
        inline_images = source.get("inline_image_urls", [])
        status = str(source.get("status", "")).strip()
        draft_media_id = str(source.get("draft_media_id", fallback_payload.get("draft_media_id", ""))).strip()
        submit_status = str(source.get("submit_status", fallback_payload.get("submit_status", ""))).strip()
        external_url = str(source.get("external_url", fallback_payload.get("external_url", ""))).strip()
        if not status:
            if submit_status or external_url:
                status = "submitted"
            elif draft_media_id:
                status = "draft_created"
            else:
                status = "not_published"
        return WechatPublishStatusSummary(
            status=status,
            submitted_at=str(source.get("published_at", fallback_payload.get("published_at", ""))),
            title=str(source.get("title", fallback_payload.get("title", ""))),
            run_name=run_name,
            draft_media_id=draft_media_id,
            submit_status=submit_status,
            external_url=external_url,
            inline_image_count=len(inline_images) if isinstance(inline_images, list) else 0,
            layout_backend=str(source.get("layout_backend", fallback_payload.get("layout_backend", ""))),
            submit_requested=bool(source.get("submit_requested", fallback_payload.get("submit_requested", False))),
            has_result=bool(publish_result or fallback_payload),
        )

    def load_publish_preflight(self) -> dict[str, Any]:
        path = self.automation_dir / "publish_preflight.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {
                "updated_at": "",
                "blocking_flags": [],
                "warnings": [],
                "diagnostics": {},
            }
        return {
            "updated_at": self._format_timestamp(path.stat().st_mtime) if path.exists() else "",
            "current_url": str(payload.get("current_url", "")),
            "blocking_flags": list(payload.get("blocking_flags", []))
            if isinstance(payload.get("blocking_flags"), list)
            else [],
            "warnings": list(payload.get("warnings", [])) if isinstance(payload.get("warnings"), list) else [],
            "diagnostics": payload.get("diagnostics", {}) if isinstance(payload.get("diagnostics"), dict) else {},
        }

    def _load_json(self, path: Path) -> Any | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _parse_titles_file(self, path: Path) -> dict[str, list[str]]:
        text = self._read_text(path)
        if not text:
            return {"all_titles": [], "recommended_titles": []}
        recommended_marker = "推荐标题："
        if recommended_marker in text:
            all_part, recommended_part = text.split(recommended_marker, 1)
        else:
            all_part, recommended_part = text, ""
        all_part = all_part.replace("全部标题：", "", 1).strip()
        return {
            "all_titles": self._parse_numbered_lines(all_part),
            "recommended_titles": self._parse_numbered_lines(recommended_part),
        }

    def _parse_numbered_lines(self, text: str) -> list[str]:
        result: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\d+\.\s*(.+)$", line.strip())
            if match:
                result.append(match.group(1).strip())
        return result

    def _format_timestamp(self, raw_timestamp: float) -> str:
        return datetime.fromtimestamp(raw_timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def _artifact_url(self, run_name: str, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/runs/{run_name}/{relative_path}"

    def _account_output_artifact_url(self, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/output/{relative_path}"

    def _account_log_artifact_url(self, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/logs/{relative_path}"

    def count_runs(self) -> int:
        count, _ = self.load_runs_overview()
        return count

    def load_runs_overview(self) -> tuple[int, RunSummary | None]:
        if not self.runs_dir.exists():
            return 0, None
        candidates = [item for item in self.runs_dir.iterdir() if item.is_dir()]
        count = len(candidates)
        latest_path = max(candidates, key=self._run_sort_key, default=None)
        return count, self._build_run_summary(latest_path) if latest_path is not None else None

    def _run_sort_key(self, path: Path) -> tuple[float, float, str]:
        scheduled_at = self._run_name_timestamp(path.name)
        modified_at = path.stat().st_mtime
        return (scheduled_at or modified_at, modified_at, path.name)

    def _run_name_timestamp(self, run_name: str) -> float:
        prefix = str(run_name).split("_", 1)[0].strip()
        try:
            return datetime.strptime(prefix, "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            return 0.0

    def count_publish_history(self) -> int:
        payload = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        return len(payload) if isinstance(payload, list) else 0

    def count_wechat_history(self) -> int:
        payload = self._load_json(self.output_dir / self.settings.wechat.history_filename)
        return len(payload) if isinstance(payload, list) else 0

    @classmethod
    def load_scheduled_task_catalog(cls) -> list[ScheduledTaskSummary]:
        now = time.time()
        if cls._TASK_CATALOG_CACHE and (now - cls._TASK_CATALOG_CACHE_AT) < cls._TASK_CATALOG_TTL_SECONDS:
            return list(cls._TASK_CATALOG_CACHE)
        filters = " -or ".join(
            f"$_.TaskName -like '{prefix}-*'" for prefix in cls.SCHEDULED_TASK_PREFIXES.values()
        )
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{ {filters} }}; "
                "if (-not $tasks) { '[]'; exit 0 }; "
                "$tasks | ForEach-Object { "
                "$info = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
                "[pscustomobject]@{"
                "task_name = $_.TaskName; "
                "state = $_.State.ToString(); "
                "last_run_time = if ($info.LastRunTime -and $info.LastRunTime -gt [datetime]::MinValue) { $info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }; "
                "next_run_time = if ($info.NextRunTime -and $info.NextRunTime -gt [datetime]::MinValue) { $info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }; "
                "last_task_result = [int64]$info.LastTaskResult"
                "} "
                "} | ConvertTo-Json -Depth 4"
            ),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                timeout=20,
            )
        except Exception:
            return []
        if result.returncode != 0 or not result.stdout.strip():
            return []
        try:
            payload = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return []
        items = payload if isinstance(payload, list) else [payload]
        result_items: list[ScheduledTaskSummary] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            task_name = str(item.get("task_name", ""))
            platform, account_slug, slot_index = cls._parse_task_name(task_name)
            try:
                last_task_result = int(item.get("last_task_result", 0))
            except (TypeError, ValueError):
                last_task_result = 0
            result_items.append(
                ScheduledTaskSummary(
                    task_name=task_name,
                    state=str(item.get("state", "")),
                    last_run_time=str(item.get("last_run_time", "")),
                    next_run_time=str(item.get("next_run_time", "")),
                    last_task_result=last_task_result,
                    platform=platform,
                    account_slug=account_slug,
                    slot_index=slot_index,
                )
            )
        cls._TASK_CATALOG_CACHE = list(result_items)
        cls._TASK_CATALOG_CACHE_AT = now
        return result_items

    @classmethod
    def _parse_task_name(cls, task_name: str) -> tuple[str, str, int]:
        for platform, prefix in cls.SCHEDULED_TASK_PREFIXES.items():
            marker = f"{prefix}-"
            if not task_name.startswith(marker):
                continue
            remainder = task_name[len(marker) :]
            match = re.match(r"^(?P<account>.+)-(?P<slot>\d+)$", remainder)
            if not match:
                return platform, remainder, 0
            return platform, match.group("account"), int(match.group("slot"))
        return "", "", 0

    def _filter_task_catalog(
        self,
        task_catalog: list[ScheduledTaskSummary] | None,
        *,
        platform: str,
    ) -> list[ScheduledTaskSummary]:
        items = task_catalog if task_catalog is not None else self.load_scheduled_task_catalog()
        filtered = [
            item
            for item in items
            if item.platform == platform and item.account_slug == self.settings.account_slug
        ]
        filtered.sort(key=lambda item: (item.slot_index or 999, item.task_name))
        return filtered

    def _summarize_task_group(self, tasks: list[ScheduledTaskSummary]) -> dict[str, Any]:
        if not tasks:
            return {
                "configured": False,
                "ready": False,
                "label": "未配置",
                "tone": "neutral",
                "state": "",
                "task_count": 0,
                "next_run_time": "",
                "last_run_time": "",
                "last_task_result": 0,
            }

        states = sorted({task.state for task in tasks if task.state})
        next_run_time = min((task.next_run_time for task in tasks if task.next_run_time), default="")
        last_run_time = max((task.last_run_time for task in tasks if task.last_run_time), default="")
        ready_states = {"Ready", "Queued", "Running"}
        has_state_issue = any(state not in ready_states for state in states)
        has_result_issue = any(
            task.last_task_result not in self.BENIGN_TASK_RESULTS for task in tasks
        )
        tone = "warn" if has_state_issue or has_result_issue else "success"
        label = "定时正常" if tone == "success" else "调度待关注"
        last_task_result = 0
        for task in tasks:
            if task.last_task_result not in self.BENIGN_TASK_RESULTS:
                last_task_result = task.last_task_result
                break
            if last_task_result == 0 and task.last_task_result != 0:
                last_task_result = task.last_task_result
        return {
            "configured": True,
            "ready": tone == "success",
            "label": label,
            "tone": tone,
            "state": " / ".join(states),
            "task_count": len(tasks),
            "next_run_time": next_run_time,
            "last_run_time": last_run_time,
            "last_task_result": last_task_result,
        }

    def _build_toutiao_platform_snapshot(
        self,
        *,
        latest_run: RunSummary | None,
        scheduled_status: dict[str, Any],
        latest_publish: PublishStatusSummary,
        publish_preflight: dict[str, Any],
        latest_log: LogSummary | None,
        tasks: list[ScheduledTaskSummary],
        workflow_scores: dict[str, Any],
        benchmark_merge: dict[str, Any],
    ) -> dict[str, Any]:
        schedule_meta = self._summarize_task_group(tasks)
        scheduled_status_value = str(scheduled_status.get("status", "")).strip()
        scheduled_workflow_status = str(scheduled_status.get("workflow_status", "")).strip().lower()
        scheduled_publish_status = str(scheduled_status.get("publish_status", "")).strip()
        if (
            scheduled_status_value.lower() == "failed"
            and scheduled_workflow_status in {"platform_publish_failed", "platform_partial_success"}
            and scheduled_publish_status
        ):
            status_value = scheduled_publish_status
        else:
            status_value = (
                scheduled_status_value
                or str(latest_publish.status).strip()
                or (latest_run.review_status if latest_run is not None else "")
                or "idle"
            )
        status_meta = self._describe_status(status_value)
        blocking_flags = publish_preflight.get("blocking_flags", []) if isinstance(publish_preflight, dict) else []
        warnings = publish_preflight.get("warnings", []) if isinstance(publish_preflight, dict) else []
        creator_name = (
            ((publish_preflight.get("diagnostics", {}) or {}).get("summary", {}) or {}).get("creator_name", "")
            if isinstance(publish_preflight, dict)
            else ""
        )
        preflight_state = "blocked" if blocking_flags else ("ready" if publish_preflight.get("current_url") else "unknown")
        preflight_meta = self._describe_status(preflight_state)
        latest_title = (
            latest_publish.title
            or str(scheduled_status.get("selected_title", "")).strip()
            or (latest_run.publish_title if latest_run is not None else "")
            or (latest_run.selected_topic_title if latest_run is not None else "")
        )
        latest_run_name = (
            str(scheduled_status.get("run_name", "")).strip()
            or latest_publish.run_name
            or (latest_run.name if latest_run is not None else "")
        )
        latest_log_url = self.resolve_log_url(str(scheduled_status.get("log_file", ""))) or (latest_log.url if latest_log else "")
        latest_log_name = Path(str(scheduled_status.get("log_file", ""))).name if scheduled_status.get("log_file") else (latest_log.name if latest_log else "")
        benchmark_context = self._build_platform_benchmark_context(benchmark_merge, platform="toutiao")
        return {
            "key": "toutiao",
            "label": "今日头条",
            "enabled": True,
            "schedule": schedule_meta,
            "latest_status": status_value,
            "latest_status_label": status_meta["label"],
            "latest_status_tone": status_meta["tone"],
            "latest_title": latest_title,
            "latest_run_name": latest_run_name,
            "latest_updated_at": str(scheduled_status.get("updated_at", "")).strip()
            or latest_publish.submitted_at
            or (latest_run.created_at if latest_run is not None else ""),
            "review_status": str(scheduled_status.get("review_status", "")).strip()
            or (latest_run.review_status if latest_run is not None else ""),
            "auto_publish_enabled": bool(scheduled_status.get("publish_enabled", False))
            or bool(scheduled_status.get("auto_publish", False)),
            "latest_detail_url": f"/runs/{latest_run_name}?account={self.account_key}" if latest_run_name else "",
            "history_url": f"/published?account={self.account_key}",
            "external_url": latest_publish.external_url or str(scheduled_status.get("publish_url", "")).strip(),
            "publish_message": latest_publish.message or str(scheduled_status.get("publish_message", "")).strip(),
            "publish_pgc_id": latest_publish.pgc_id or str(scheduled_status.get("publish_pgc_id", "")).strip(),
            "preflight_label": preflight_meta["label"],
            "preflight_tone": preflight_meta["tone"],
            "preflight_message": str(blocking_flags[0]) if blocking_flags else (creator_name or (warnings[0] if warnings else "未刷新头条发布预检")),
            "preflight_warning_count": len(warnings),
            "latest_log_url": latest_log_url,
            "latest_log_name": latest_log_name,
            "benchmark_context": benchmark_context,
            "score_snapshot": {
                "overall_score": int(workflow_scores.get("overall_score", 0) or 0),
                "validation_score": int(workflow_scores.get("validation_score", 0) or 0),
                "publish_readiness_score": int(workflow_scores.get("publish_readiness_score", 0) or 0),
                "ai_generated_risk": int(workflow_scores.get("ai_generated_risk", 0) or 0),
            },
        }

    def _build_wechat_platform_snapshot(
        self,
        *,
        latest_wechat: WechatPublishStatusSummary,
        wechat_preflight: dict[str, Any],
        latest_log: LogSummary | None,
        tasks: list[ScheduledTaskSummary],
        benchmark_merge: dict[str, Any],
    ) -> dict[str, Any]:
        schedule_meta = self._summarize_task_group(tasks)
        status_value = "disabled" if not self.settings.wechat.enabled else (latest_wechat.status or "idle")
        status_meta = self._describe_status(status_value)
        preflight_state = "disabled"
        if self.settings.wechat.enabled:
            preflight_state = str(wechat_preflight.get("state", "")).strip()
            if not preflight_state:
                preflight_state = "not_initialized"
        preflight_meta = self._describe_status(preflight_state)
        latest_log_url = latest_log.url if latest_log is not None else ""
        latest_log_name = latest_log.name if latest_log is not None else ""
        benchmark_context = self._build_platform_benchmark_context(benchmark_merge, platform="wechat")
        return {
            "key": "wechat",
            "label": "公众号",
            "enabled": self.settings.wechat.enabled,
            "schedule": schedule_meta,
            "latest_status": status_value,
            "latest_status_label": status_meta["label"],
            "latest_status_tone": status_meta["tone"],
            "latest_title": latest_wechat.title,
            "latest_run_name": latest_wechat.run_name,
            "latest_updated_at": latest_wechat.submitted_at,
            "auto_publish_enabled": bool(self.settings.wechat.auto_publish or latest_wechat.submit_requested),
            "latest_detail_url": f"/runs/{latest_wechat.run_name}?account={self.account_key}" if latest_wechat.run_name else "",
            "history_url": "",
            "external_url": latest_wechat.external_url,
            "draft_media_id": latest_wechat.draft_media_id,
            "submit_status": latest_wechat.submit_status,
            "inline_image_count": latest_wechat.inline_image_count,
            "layout_backend": latest_wechat.layout_backend,
            "preflight_label": preflight_meta["label"],
            "preflight_tone": preflight_meta["tone"],
            "preflight_message": str(wechat_preflight.get("current_url", "")).strip()
            or ("公众号未启用" if not self.settings.wechat.enabled else "未初始化公众号登录态"),
            "latest_log_url": latest_log_url,
            "latest_log_name": latest_log_name,
            "benchmark_context": benchmark_context,
        }

    def _build_platform_benchmark_context(
        self,
        benchmark_merge: dict[str, Any],
        *,
        platform: str,
    ) -> dict[str, Any]:
        if not benchmark_merge or str(benchmark_merge.get("platform", "")).strip().lower() != platform:
            return {
                "available": False,
                "platform": platform,
                "track": "",
                "generated_at": "",
                "account_names": [],
                "headline_titles": [],
                "benchmark_topic_count": 0,
                "merged_topic_count": 0,
                "artifact_url": "",
            }

        account_names: list[str] = []
        for item in benchmark_merge.get("benchmark_accounts", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if name and name not in account_names:
                account_names.append(name)

        headline_titles: list[dict[str, str]] = []
        for item in benchmark_merge.get("top_titles", [])[:5]:
            if not isinstance(item, dict):
                continue
            headline_titles.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "source": str(item.get("source", "")).strip(),
                    "track": str(item.get("track", "")).strip(),
                }
            )

        return {
            "available": True,
            "platform": platform,
            "track": str(benchmark_merge.get("track", "")).strip(),
            "generated_at": str(benchmark_merge.get("generated_at", "")).strip(),
            "account_names": account_names[:6],
            "headline_titles": headline_titles,
            "benchmark_topic_count": int(benchmark_merge.get("benchmark_topic_count", 0) or 0),
            "merged_topic_count": int(benchmark_merge.get("merged_topic_count", 0) or 0),
            "artifact_url": str(benchmark_merge.get("artifact_url", "")).strip(),
        }

    def _build_account_attention_items(
        self,
        *,
        latest_run: RunSummary | None,
        notifications: list[dict[str, Any]],
        toutiao_platform: dict[str, Any],
        wechat_platform: dict[str, Any],
    ) -> list[str]:
        items: list[str] = []
        latest_status = str(toutiao_platform.get("latest_status", "")).strip().lower()
        latest_review_status = str(toutiao_platform.get("review_status", "")).strip().lower()
        latest_scheduled_run_name = str(toutiao_platform.get("latest_run_name", "")).strip()
        latest_scheduled_run_ts = self._run_name_timestamp(latest_scheduled_run_name)
        latest_run_ts = self._run_name_timestamp(latest_run.name) if latest_run is not None else 0.0

        if not toutiao_platform["schedule"]["configured"]:
            items.append("头条定时任务未配置。")
        elif not toutiao_platform["schedule"]["ready"]:
            items.append("头条定时任务状态需要检查。")

        if latest_status in {"review_required", "pending"} or latest_review_status == "pending" or (
            latest_run is not None
            and latest_run.review_status == "pending"
            and (
                not latest_scheduled_run_name
                or latest_run.name == latest_scheduled_run_name
                or (latest_run_ts and latest_scheduled_run_ts and latest_run_ts > latest_scheduled_run_ts)
            )
        ):
            items.append("最新头条稿件仍在待审核队列。")

        if toutiao_platform["preflight_tone"] == "error":
            items.append("头条发布预检存在阻塞项。")

        if wechat_platform["enabled"] and not wechat_platform["schedule"]["configured"]:
            items.append("公众号已启用，但没有检测到 07:00 定时任务。")

        for notification in notifications[:3]:
            if not isinstance(notification, dict):
                continue
            level = str(notification.get("level", "")).strip()
            message = self._normalize_notification_message(notification)
            if level in {"warn", "error"} and message:
                items.append(message)

        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped[:4]

    def _derive_overall_meta(
        self,
        toutiao_platform: dict[str, Any],
        wechat_platform: dict[str, Any],
        attention_items: list[str],
    ) -> dict[str, str]:
        latest_status = str(toutiao_platform.get("latest_status", "")).strip().lower()
        tones = {
            str(toutiao_platform.get("latest_status_tone", "")),
            str(toutiao_platform.get("preflight_tone", "")),
            str(toutiao_platform.get("schedule", {}).get("tone", "")),
            str(wechat_platform.get("latest_status_tone", "")),
            str(wechat_platform.get("schedule", {}).get("tone", "")),
        }
        if "error" in tones:
            return {"label": "存在阻塞", "tone": "error"}
        if latest_status == "no_topics" and (not attention_items or all(self._is_soft_attention_item(item) for item in attention_items)):
            return {"label": "候选不足", "tone": "neutral"}
        if attention_items or "warn" in tones:
            return {"label": "需要关注", "tone": "warn"}
        if toutiao_platform.get("schedule", {}).get("configured"):
            return {"label": "运行正常", "tone": "success"}
        return {"label": "等待配置", "tone": "neutral"}

    def _normalize_notification_message(self, latest_notification: dict[str, Any]) -> str:
        raw_message = str(latest_notification.get("message", "")).strip()
        notification_type = str(latest_notification.get("type", "")).strip().lower()
        title = str(latest_notification.get("title", "")).strip().lower()
        payload = latest_notification.get("data", {}) if isinstance(latest_notification, dict) else {}
        status = str(payload.get("status", "")).strip().lower() if isinstance(payload, dict) else ""
        error_text = str(payload.get("error", "")).strip().lower() if isinstance(payload, dict) else ""
        workflow_errors = payload.get("workflow_errors", []) if isinstance(payload, dict) else []
        workflow_error_text = " ".join(str(item).strip().lower() for item in workflow_errors) if isinstance(workflow_errors, list) else ""
        raw_lower = raw_message.lower()
        if notification_type == "image_generation_fallback" or title == "image2 fallback triggered":
            requested_backend = str(payload.get("requested_backend", "")).strip() if isinstance(payload, dict) else ""
            affected_modes = payload.get("affected_modes", []) if isinstance(payload, dict) else []
            mode_text = ""
            if isinstance(affected_modes, list):
                normalized_modes = [str(item).strip() for item in affected_modes if str(item).strip()]
                if normalized_modes:
                    mode_text = f"（影响环节：{', '.join(normalized_modes)}）"
            backend_label = requested_backend or "image2"
            return f"{backend_label} 出图失败，已自动降级为本地 renderer{mode_text}。请检查图片接口、中转站配置或额度。"

        if (
            status == "no_topics"
            or "no eligible topics were available after collection" in raw_lower
            or "no unpublished topics are available for today" in error_text
        ):
            return "本轮已完成热点抓取，但当前赛道没有新的合格选题，系统已自动跳过发布。"
        if raw_message == "Scheduled pipeline succeeded, but this run still requires manual review.":
            return "最新生成稿件仍需人工审核后才能发布。"
        if raw_message == "Scheduled pipeline finished generation, but at least one platform publish step failed.":
            if "wechat publish failed" in error_text or "cover image not found" in error_text:
                return "头条已正常提交，公众号发布失败：缺少封面图或公众号素材图。"
            return "本轮内容已生成，但至少有一个平台发布失败，需要检查日志。"
        if raw_message.startswith("Scheduled pipeline failed after ") and (
            "platform_publish_failed" == status
            or "wechat publish failed" in error_text
            or "cover image not found" in error_text
            or "wechat publish failed" in workflow_error_text
            or "cover image not found" in workflow_error_text
        ):
            if (
                "wechat publish failed" in error_text
                or "cover image not found" in error_text
                or "wechat publish failed" in workflow_error_text
                or "cover image not found" in workflow_error_text
            ):
                return "头条已正常提交，公众号发布失败：缺少封面图或公众号素材图。"
            return "本轮内容已生成，但至少有一个平台发布失败。"
        return raw_message

    def _is_soft_attention_item(self, item: str) -> bool:
        normalized = str(item).strip()
        soft_items = {
            "本轮已完成热点抓取，但当前赛道没有新的合格选题，系统已自动跳过发布。",
        }
        return normalized in soft_items

    def resolve_log_url(self, log_file: str) -> str:
        if not log_file:
            return ""
        candidate = Path(log_file)
        if not candidate.is_absolute():
            candidate = (self.logs_dir / log_file).resolve()
        else:
            candidate = candidate.resolve()
        root = self.logs_dir.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return ""
        if not candidate.exists():
            return ""
        return self._account_log_artifact_url(relative.as_posix())

    def _describe_status(self, status: str) -> dict[str, str]:
        normalized = (status or "").strip().lower()
        success_states = {
            "success": "运行正常",
            "published": "已发布",
            "submitted": "已提交",
            "submitted_for_review": "已提交审核",
            "draft_created": "已进草稿箱",
            "approved": "已通过审核",
            "ready": "登录态正常",
        }
        warn_states = {
            "review_required": "待人工审核",
            "pending": "待处理",
            "manual_review": "待人工审核",
            "running": "执行中",
            "queued": "排队中",
        }
        error_states = {
            "failed": "执行失败",
            "error": "执行失败",
            "blocked": "存在阻塞",
            "missing": "缺少结果",
        }
        neutral_states = {
            "unknown": "未知",
            "idle": "暂无结果",
            "disabled": "未启用",
            "not_published": "暂无记录",
            "not_initialized": "未初始化登录态",
            "no_topics": "暂无合格选题",
        }
        if normalized in success_states:
            return {"label": success_states[normalized], "tone": "success"}
        if normalized in warn_states:
            return {"label": warn_states[normalized], "tone": "warn"}
        if normalized in error_states:
            return {"label": error_states[normalized], "tone": "error"}
        if normalized in neutral_states:
            return {"label": neutral_states[normalized], "tone": "neutral"}
        return {"label": status or "未知", "tone": "neutral"}

    def _resolve_schedule_slot(self, created_at: str) -> str:
        try:
            hour = datetime.fromisoformat(created_at).hour
        except ValueError:
            return ""
        if 7 <= hour < 11:
            return "morning"
        if 11 <= hour < 15:
            return "noon"
        if 17 <= hour < 20:
            return "evening"
        if 21 <= hour < 23:
            return "night"
        return ""

    def _slot_to_expected_time(self, slot: str) -> str:
        mapping = {
            "morning": "08:00",
            "noon": "12:00",
            "evening": "18:30",
            "night": "22:00",
        }
        return mapping.get(slot, "--:--")

    def _latest_scheduled_logs_by_slot(self) -> dict[str, str]:
        if not self.scheduled_logs_dir.exists():
            return {}
        files = [item for item in self.scheduled_logs_dir.iterdir() if item.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        result: dict[str, str] = {}
        for path in files:
            slot = self._resolve_schedule_slot(self._format_timestamp(path.stat().st_mtime).replace(" ", "T"))
            if slot and slot not in result:
                result[slot] = self._account_log_artifact_url(f"scheduled/{path.name}")
        return result

    def _split_article_paragraphs(self, article_body: str) -> list[str]:
        if not article_body:
            return []
        normalized = article_body.replace("\r\n", "\n").replace("\r", "\n")
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n+", normalized) if chunk.strip()]
        paragraphs: list[str] = []
        for chunk in chunks:
            if chunk == "---":
                continue
            paragraphs.append(chunk)
        return paragraphs
