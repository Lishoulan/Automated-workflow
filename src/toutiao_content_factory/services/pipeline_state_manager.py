"""Pipeline state manager — persistent state with SQLite + JSON fallback.

Primary backend is the SQLModel-powered DatabaseService.
When sqlmodel is not installed, falls back to file-based JSON storage
for backward compatibility.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.database.models import (
    Article,
    PipelineStage,
    RunRecord,
)
from toutiao_content_factory.database.service import DatabaseService
from toutiao_content_factory.models import WorkflowResult
from toutiao_content_factory.services.notification_service import NotificationService


logger = logging.getLogger(__name__)


class PipelineStateManager:
    """Pipeline state manager with SQLite database backend + JSON file fallback.

    Manages the lifecycle of run records:
    INIT -> DOCUMENTS_LOADED -> SIGNALS_EXTRACTED -> CONTENT_GENERATED
      -> REVIEWED -> APPROVED -> PUBLISHING -> PUBLISHED / FAILED

    DB is the primary data source. JSON files are only used as a fallback
    when sqlmodel is not installed.
    """

    def __init__(
        self,
        output_dir: Path,
        notification_service: NotificationService | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.runs_dir = self.output_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.notification_service = notification_service

        # Initialize database service (SQLite in the output directory)
        db_path = self.output_dir / "content_factory.db"
        self.db = DatabaseService(db_path)
        if self.db.available:
            logger.info("PipelineStateManager: using SQLite database at %s", db_path)
        else:
            logger.info("PipelineStateManager: sqlmodel not available, using JSON file fallback")

    # ====================================================================
    # Stage management (used by the old pipeline code)
    # ====================================================================

    def save_stage(self, task_id: str, stage: PipelineStage | str) -> None:
        """Save the current pipeline stage for a task.

        Writes to DB only. Falls back to JSON when DB is not available.
        Preserves existing state_data when updating stage on an existing record.
        """
        stage_value = stage.value if isinstance(stage, PipelineStage) else str(stage)
        run_name = task_id

        if self.db.available:
            self.db.update_article_stage(run_name, stage_value)
            # Preserve existing state_data when updating stage
            existing = self.db.get_run_record(run_name)
            existing_state_data = existing.state_data if existing else None
            self.db.save_run_record(RunRecord(
                run_name=run_name,
                account_key="default",
                pipeline_stage=stage_value,
                status="running" if stage_value != "FAILED" else "failed",
                state_data=existing_state_data or "",
            ))
        else:
            self._save_stage_json(run_name, stage_value)

        logger.debug("Pipeline state: %s -> %s", run_name, stage_value)

    def _save_stage_json(self, run_name: str, stage: str) -> Path:
        """Fallback: save stage to JSON file. Only called when DB is unavailable."""
        stage_file = self.runs_dir / f"{run_name}_stage.json"
        stage_file.write_text(
            json.dumps({
                "run_name": run_name,
                "stage": stage,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return stage_file

    def load_stage(self, run_name: str) -> str | None:
        """Load the stage for a run.

        Tries DB first. Falls back to JSON when DB is not available.
        """
        if self.db.available:
            article = self.db.get_article(run_name)
            if article:
                return article.pipeline_stage
            record = self.db.get_run_record(run_name)
            if record:
                return record.pipeline_stage
            return None

        # Fallback to JSON
        stage_file = self.runs_dir / f"{run_name}_stage.json"
        if stage_file.exists():
            try:
                return json.loads(stage_file.read_text(encoding="utf-8")).get("stage")
            except (json.JSONDecodeError, OSError):
                pass
        return None

    # ====================================================================
    # Full state save/load (backward compatible API)
    # ====================================================================

    def save_state(
        self,
        run_name: str,
        state: str,
        workflow_result: WorkflowResult | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> Path:
        """Save pipeline state (backward-compatible API).

        Writes to DB only. Falls back to JSON when DB is not available.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        state_file = self.runs_dir / f"{run_name}_state.json"

        record = {
            "run_name": run_name,
            "state": state,
            "timestamp": timestamp,
            "created_at": workflow_result.created_at if workflow_result else None,
            "workflow_result": workflow_result.to_dict() if workflow_result else None,
            "extra_data": extra_data or {},
        }

        if self.db.available:
            self.db.save_run_record(RunRecord(
                run_name=run_name,
                account_key="default",
                pipeline_stage=state,
                status="published" if state == "PUBLISHED" else "failed" if state == "FAILED" else "running",
                state_data=json.dumps(record, ensure_ascii=False),
            ))
        else:
            # Fallback to JSON
            state_file.write_text(
                json.dumps(record, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        logger.info("State saved: %s -> %s", run_name, state)
        return state_file

    def load_state(self, run_name: str) -> dict[str, Any] | None:
        """Load pipeline state (backward-compatible API).

        Tries DB first. Falls back to JSON when DB is not available.
        """
        if self.db.available:
            db_record = self.db.get_run_record(run_name)
            if db_record and db_record.state_data:
                try:
                    return json.loads(db_record.state_data)
                except (json.JSONDecodeError, TypeError):
                    pass
            return None

        # Fallback to JSON
        state_file = self.runs_dir / f"{run_name}_state.json"
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return None

    def get_latest_state(self, platform: str = "toutiao") -> dict[str, Any] | None:
        """Get the most recent run state.

        Uses DB query. Falls back to scanning JSON files when DB is not available.
        """
        if self.db.available:
            records = self.db.list_run_records(limit=1)
            if records and records[0].state_data:
                try:
                    return json.loads(records[0].state_data)
                except (json.JSONDecodeError, TypeError):
                    pass
            return None

        # Fallback to JSON file scanning
        if not self.runs_dir.exists():
            return None
        state_files = sorted(
            self.runs_dir.glob(f"*_state.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not state_files:
            return None
        try:
            return json.loads(state_files[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def get_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get run history.

        Uses DB query. Falls back to scanning JSON files when DB is not available.
        """
        if self.db.available:
            records = self.db.list_run_records(limit=limit)
            history = []
            for rec in records:
                if rec.state_data:
                    try:
                        history.append(json.loads(rec.state_data))
                    except (json.JSONDecodeError, TypeError):
                        pass
            return history

        # Fallback to JSON file scanning
        if not self.runs_dir.exists():
            return []
        state_files = sorted(
            self.runs_dir.glob("*_state.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]

        history = []
        for state_file in state_files:
            try:
                history.append(json.loads(state_file.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning("Failed to read state file %s: %s", state_file, e)
        return history

    def can_resume(self, run_name: str) -> bool:
        """Check if a run can be resumed."""
        state = self.load_state(run_name)
        if not state:
            return False
        failed_stages = ["collect", "analyze", "write", "polish", "review"]
        return (
            state["state"] == "FAILED"
            and state.get("extra_data", {}).get("failed_stage") in failed_stages
        )

    def get_workflow_result(self, run_name: str) -> WorkflowResult | None:
        """Load WorkflowResult from state."""
        state = self.load_state(run_name)
        if not state or not state.get("workflow_result"):
            return None
        try:
            return WorkflowResult.from_dict(state["workflow_result"])
        except Exception as e:
            logger.error("Failed to load WorkflowResult: %s", e)
            return None

    # ====================================================================
    # Mark helpers
    # ====================================================================

    def mark_failed(
        self,
        run_name: str,
        failed_stage: str,
        error_message: str,
        debug_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Mark a run as failed with debug info."""
        state = self.load_state(run_name)
        workflow_result = self.get_workflow_result(run_name)

        extra_data = {
            "failed_stage": failed_stage,
            "error_message": error_message,
            "debug_snapshot": debug_snapshot,
        }
        if state and state.get("extra_data"):
            extra_data.update(state["extra_data"])

        self.save_state(run_name, "FAILED", workflow_result, extra_data)
        self.save_stage(run_name, PipelineStage.FAILED)

        if self.notification_service:
            self.notification_service.notify_failure(
                platform="Content Factory",
                stage=failed_stage,
                error_msg=error_message,
                run_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
            )

    def mark_success(
        self,
        run_name: str,
        article_title: str,
        declared_first_release: bool = True,
    ) -> None:
        """Mark a run as successfully published."""
        state = self.load_state(run_name)
        workflow_result = self.get_workflow_result(run_name)

        self.save_state(run_name, "PUBLISHED", workflow_result, {
            "article_title": article_title,
            "published_at": datetime.now().isoformat(timespec="seconds"),
            "declared_toutiao_first_release": declared_first_release,
            "toutiao_published_at": datetime.now().isoformat(timespec="seconds") if declared_first_release else None,
        })
        self.save_stage(run_name, PipelineStage.PUBLISHED)

        if self.notification_service:
            self.notification_service.notify_success(
                platform="Content Factory",
                article_title=article_title,
                run_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
            )

    def can_publish_to_other_platforms(self, run_name: str, platform: str = "wechat") -> bool:
        """Check if 72-hour first-release protection allows cross-platform publish."""
        state = self.load_state(run_name)
        if not state:
            return True
        extra = state.get("extra_data", {})
        if not extra.get("declared_toutiao_first_release"):
            return True
        toutiao_published_at_str = extra.get("toutiao_published_at")
        if not toutiao_published_at_str:
            return True
        try:
            toutiao_published_at = datetime.fromisoformat(toutiao_published_at_str)
            elapsed = datetime.now() - toutiao_published_at
            if elapsed < timedelta(hours=72):
                logger.info(
                    "Article %s is in first-release protection (%.1f hours elapsed), blocking %s",
                    run_name, elapsed.total_seconds() / 3600, platform,
                )
                return False
        except (ValueError, TypeError):
            pass
        return True

    def get_db_stats(self, account_key: str | None = None) -> dict[str, Any]:
        """Get database statistics for the dashboard."""
        return self.db.get_stats(account_key=account_key)
