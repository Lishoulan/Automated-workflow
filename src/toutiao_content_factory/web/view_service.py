from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
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
        candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        if limit is not None:
            candidates = candidates[:limit]
        return [self._build_run_summary(path) for path in candidates]

    def load_dashboard(self) -> dict[str, Any]:
        topics = self.load_daily_topics()
        runs = self.list_runs(limit=8)
        latest_score = self._load_json(self.output_dir / "content_score.json") or {}
        latest_selected_topic = (self._load_json(self.output_dir / "selected_topic.json") or {}).get("selected_topic", {})
        latest_workflow = self._load_json(self.output_dir / "workflow_result.json") or {}
        scheduled_status = self._load_json(self.automation_dir / "scheduled_status.json") or {}
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

        images = self._collect_run_images(run_dir, run_name, "images")
        publish_images = self._collect_run_images(
            run_dir,
            run_name,
            f"{self.publish_dirname}/images",
        )
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
            "images": images,
            "publish_images": publish_images,
            "artifacts": artifacts,
        }

    def list_scheduled_tasks(self) -> list[ScheduledTaskSummary]:
        task_name_pattern = f"ToutiaoContentFactory-{self.settings.account_slug}-*" if not self.settings.is_default_account else "ToutiaoContentFactory-*"
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$tasks = Get-ScheduledTask -TaskName '{task_name_pattern}' -ErrorAction SilentlyContinue; "
                "if (-not $tasks) { '[]'; exit 0 }; "
                "$tasks | ForEach-Object { "
                "$info = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
                "[pscustomobject]@{"
                "task_name = $_.TaskName; "
                "state = $_.State.ToString(); "
                "last_run_time = $info.LastRunTime; "
                "next_run_time = $info.NextRunTime; "
                "last_task_result = $info.LastTaskResult"
                "} "
                "} | ConvertTo-Json -Depth 4"
            ),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=8)
        except Exception:
            return []
        if result.returncode != 0 or not result.stdout.strip():
            return []
        try:
            payload = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return []
        items = payload if isinstance(payload, list) else [payload]
        return [
            ScheduledTaskSummary(
                task_name=str(item.get("task_name", "")),
                state=str(item.get("state", "")),
                last_run_time=str(item.get("last_run_time", "")),
                next_run_time=str(item.get("next_run_time", "")),
                last_task_result=int(item.get("last_task_result", 0)),
            )
            for item in items
            if isinstance(item, dict)
        ]

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
        for item in payload[:limit]:
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

    def _collect_run_images(self, run_dir: Path, run_name: str, relative_dir: str) -> dict[str, str]:
        result: dict[str, str] = {}
        base_dir = run_dir / Path(relative_dir)
        for image_name in ("cover.png", "infographic_1.png", "infographic_2.png"):
            path = base_dir / image_name
            if not path.exists():
                continue
            result[image_name.removesuffix(".png")] = self._artifact_url(run_name, f"{relative_dir}/{image_name}")
        return result

    def _account_output_artifact_url(self, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/output/{relative_path}"

    def _account_log_artifact_url(self, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/logs/{relative_path}"

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
