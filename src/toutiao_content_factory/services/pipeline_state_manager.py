"""流水线状态管理器 - 负责状态持久化、断点续传和重新发布"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.models import WorkflowResult
from toutiao_content_factory.services.notification_service import NotificationService


logger = logging.getLogger(__name__)


class PipelineStateManager:
    """
    管理流水线状态的持久化和恢复
    状态标记：DRAFTED -> APPROVED -> PUBLISHING -> PUBLISHED / FAILED
    """

    STATE_DRAFTED = "DRAFTED"
    STATE_APPROVED = "APPROVED"
    STATE_PUBLISHING = "PUBLISHING"
    STATE_PUBLISHED = "PUBLISHED"
    STATE_FAILED = "FAILED"
    STATE_REVIEW_REQUIRED = "REVIEW_REQUIRED"

    def __init__(
        self,
        output_dir: Path,
        notification_service: NotificationService | None = None
    ):
        self.output_dir = Path(output_dir)
        self.runs_dir = self.output_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.notification_service = notification_service

    def save_state(
        self,
        run_name: str,
        state: str,
        workflow_result: WorkflowResult | None = None,
        extra_data: dict[str, Any] | None = None
    ) -> Path:
        """保存流水线状态"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        state_file = self.runs_dir / f"{run_name}_state.json"

        record = {
            "run_name": run_name,
            "state": state,
            "timestamp": timestamp,
            "created_at": workflow_result.created_at if workflow_result else None,
            "workflow_result": workflow_result.to_dict() if workflow_result else None,
            "extra_data": extra_data or {}
        }

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

        logger.info(f"状态已保存: {run_name} -> {state}")
        return state_file

    def load_state(self, run_name: str) -> dict[str, Any] | None:
        """加载流水线状态"""
        state_file = self.runs_dir / f"{run_name}_state.json"
        if not state_file.exists():
            return None

        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_latest_state(self, platform: str = "toutiao") -> dict[str, Any] | None:
        """获取最新的一次运行状态"""
        if not self.runs_dir.exists():
            return None

        state_files = sorted(
            self.runs_dir.glob(f"*_{platform}_state.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if not state_files:
            return None

        with open(state_files[0], "r", encoding="utf-8") as f:
            return json.load(f)

    def get_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取运行历史"""
        if not self.runs_dir.exists():
            return []

        state_files = sorted(
            self.runs_dir.glob("*_state.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )[:limit]

        history = []
        for state_file in state_files:
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    history.append(json.load(f))
            except Exception as e:
                logger.warning(f"读取状态文件失败 {state_file}: {e}")

        return history

    def can_resume(self, run_name: str) -> bool:
        """检查是否可以继续某个运行"""
        state = self.load_state(run_name)
        if not state:
            return False

        failed_stages = ["collect", "analyze", "write", "polish", "review"]
        return (
            state["state"] == self.STATE_FAILED
            and state.get("extra_data", {}).get("failed_stage") in failed_stages
        )

    def get_workflow_result(self, run_name: str) -> WorkflowResult | None:
        """从状态文件加载WorkflowResult"""
        state = self.load_state(run_name)
        if not state or not state.get("workflow_result"):
            return None

        try:
            return WorkflowResult.from_dict(state["workflow_result"])
        except Exception as e:
            logger.error(f"加载WorkflowResult失败: {e}")
            return None

    def mark_failed(
        self,
        run_name: str,
        failed_stage: str,
        error_message: str,
        debug_snapshot: dict[str, Any] | None = None
    ) -> None:
        """标记运行失败并保存调试信息"""
        state = self.load_state(run_name)
        workflow_result = self.get_workflow_result(run_name)

        extra_data = {
            "failed_stage": failed_stage,
            "error_message": error_message,
            "debug_snapshot": debug_snapshot
        }

        if state and state.get("extra_data"):
            extra_data.update(state["extra_data"])

        self.save_state(run_name, self.STATE_FAILED, workflow_result, extra_data)

        if self.notification_service:
            self.notification_service.notify_failure(
                platform="今日头条",
                stage=failed_stage,
                error_msg=error_message,
                run_time=datetime.now().strftime("%Y-%m-%d %H:%M")
            )

    def mark_success(
        self,
        run_name: str,
        article_title: str,
        declared_first_release: bool = True,
    ) -> None:
        state = self.load_state(run_name)
        workflow_result = self.get_workflow_result(run_name)

        self.save_state(run_name, self.STATE_PUBLISHED, workflow_result, {
            "article_title": article_title,
            "published_at": datetime.now().isoformat(timespec="seconds"),
            "declared_toutiao_first_release": declared_first_release,
            "toutiao_published_at": datetime.now().isoformat(timespec="seconds") if declared_first_release else None,
        })

        if self.notification_service:
            self.notification_service.notify_success(
                platform="今日头条",
                article_title=article_title,
                run_time=datetime.now().strftime("%Y-%m-%d %H:%M")
            )

    def can_publish_to_other_platforms(self, run_name: str, platform: str = "wechat") -> bool:
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
                    "Article %s is in toutiao first-release protection period "
                    "(%.1f hours elapsed), blocking publish to %s",
                    run_name,
                    elapsed.total_seconds() / 3600,
                    platform,
                )
                return False
        except (ValueError, TypeError):
            pass
        return True
