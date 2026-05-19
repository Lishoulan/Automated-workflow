from __future__ import annotations

import json
import logging
from pathlib import Path

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.pipeline import ContentFactoryPipeline
from toutiao_content_factory.services.review_service import ReviewWorkflowService
from toutiao_content_factory.services.topic_collection import TopicCollectionService
from toutiao_content_factory.services.topic_exporter import TopicExportService
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService


class ContentFactoryAppService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.topic_automation = TopicAutomationService(settings, logger)
        self.review_service = ReviewWorkflowService(settings, logger)

    def collect_topics(self) -> dict[str, str]:
        topic_collector = TopicCollectionService(self.settings, self.logger)
        topic_exporter = TopicExportService(self.settings.output_dir, self.logger)
        topic_report = topic_collector.collect_sync()
        topic_exports = topic_exporter.export(topic_report)
        return {name: str(path) for name, path in topic_exports.items()}

    def manual_run(
        self,
        paste_text: str | None = None,
        style_override: str | None = None,
        dry_run: bool = False,
        refresh_cache: bool = False,
        include_input_dir: bool = True,
        fast_mode: bool = False,
    ) -> dict[str, str]:
        pipeline = ContentFactoryPipeline(
            settings=self.settings,
            logger=self.logger,
            force_mock=dry_run,
            style_override=style_override,
            refresh_cache=refresh_cache,
            fast_mode=fast_mode,
        )
        try:
            return pipeline.run(paste_text=paste_text, include_input_dir=include_input_dir)
        finally:
            pipeline.close()

    def auto_run(
        self,
        style_override: str | None = None,
        dry_run: bool = False,
        refresh_cache: bool = False,
        topic_rank: int | None = None,
        topic_title: str | None = None,
        collect_if_missing: bool = True,
        force_collect: bool = False,
        no_archive: bool = False,
        fast_mode: bool = False,
    ) -> dict[str, str]:
        exported_files: dict[str, str] = {}
        topics_path = self.settings.output_dir / "daily_topics.json"
        should_collect = force_collect or (collect_if_missing and not topics_path.exists())
        if should_collect:
            exported_files.update(self.collect_topics())

        try:
            selection = self.topic_automation.prepare_auto_run(
                rank=topic_rank,
                title_query=topic_title,
                allow_generated_duplicates=refresh_cache or dry_run,
            )
        except (FileNotFoundError, ValueError) as exc:
            if not self._should_retry_topic_collection(exc, already_collected=should_collect):
                raise
            self.logger.warning("Topic selection failed, recollecting topics once: %s", exc)
            exported_files.update(self.collect_topics())
            selection = self.topic_automation.prepare_auto_run(
                rank=topic_rank,
                title_query=topic_title,
                allow_generated_duplicates=refresh_cache or dry_run,
            )
        exported_files.update({name: str(path) for name, path in selection.exported_files.items()})

        content_exports = self.manual_run(
            paste_text=selection.input_text,
            style_override=style_override,
            dry_run=dry_run,
            refresh_cache=refresh_cache,
            include_input_dir=False,
            fast_mode=fast_mode,
        )
        exported_files.update(content_exports)

        if self.settings.automation.archive_outputs and not no_archive:
            archived_files = self.topic_automation.archive_run(exported_files, selected_topic=selection.topic)
            exported_files.update({name: str(path) for name, path in archived_files.items()})
            archive_root = archived_files.get("archive_root")
            if self.settings.review.auto_prepare and isinstance(archive_root, Path):
                review_exports = self.review_service.prepare_review(archive_root)
                exported_files.update({name: str(path) for name, path in review_exports.items()})

        return exported_files

    def _should_retry_topic_collection(self, exc: Exception, already_collected: bool) -> bool:
        if already_collected:
            return False
        message = str(exc)
        retry_markers = (
            "daily_topics.json not found",
            "daily_topics.json contains no available topics",
            "No unpublished topics are available for today",
        )
        return any(marker in message for marker in retry_markers)

    def daily_run(
        self,
        style_override: str | None = None,
        dry_run: bool = False,
        refresh_cache: bool = False,
        topic_rank: int | None = None,
        topic_title: str | None = None,
        no_archive: bool = False,
        fast_mode: bool = False,
    ) -> dict[str, str]:
        return self.auto_run(
            style_override=style_override,
            dry_run=dry_run,
            refresh_cache=refresh_cache,
            topic_rank=topic_rank,
            topic_title=topic_title,
            collect_if_missing=True,
            force_collect=True,
            no_archive=no_archive,
            fast_mode=fast_mode,
        )

    def prepare_review(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.prepare_review(resolved)
        return {name: str(path) for name, path in result.items()}

    def approve_run(
        self,
        run_dir: str | None = None,
        selected_title: str | None = None,
        selected_title_index: int | None = None,
        review_notes: str | None = None,
        auto_publish: bool = False,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.approve_review(
            resolved,
            selected_title=selected_title,
            title_index=selected_title_index,
            reviewer_notes=review_notes,
            auto_publish=auto_publish,
        )
        return {name: str(path) for name, path in result.items()}

    def reject_run(self, run_dir: str | None = None, review_notes: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.reject_review(resolved, reviewer_notes=review_notes)
        return {name: str(path) for name, path in result.items()}

    def publish_run(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.publish_run(resolved)
        return {name: str(path) for name, path in result.items()}

    def publish_latest_approved(self, unpublished_only: bool = True, today_only: bool = True) -> dict[str, str]:
        resolved = self.review_service.resolve_latest_approved_run(
            unpublished_only=unpublished_only,
            today_only=today_only,
        )
        result = self.review_service.publish_run(resolved)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def approve_latest(
        self,
        selected_title: str | None = None,
        selected_title_index: int | None = None,
        review_notes: str | None = None,
        auto_publish: bool = False,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_latest_pending_run()
        result = self.review_service.approve_review(
            resolved,
            selected_title=selected_title,
            title_index=selected_title_index,
            reviewer_notes=review_notes,
            auto_publish=auto_publish,
        )
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def initialize_publish_login(self) -> dict[str, str]:
        result = self.review_service.publish_service.initialize_login_session()
        return {name: str(value) for name, value in result.items()}

    def collect_publish_preflight(self) -> dict[str, str]:
        result = self.review_service.publish_service.export_preflight_summary()
        return {name: str(value) for name, value in result.items()}

    def evaluate_latest_run_publish_readiness(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        workflow = self.review_service._load_workflow(resolved)
        prompt_repository = PromptRepository(self.settings.prompts_dir)
        openai_service = OpenAIContentService(self.settings, self.logger)
        try:
            workflow_service = AgentWorkflowService(self.settings, prompt_repository, openai_service, self.logger)
            preflight_summary = self.review_service.publish_service.collect_preflight_summary()
            publish_review = workflow_service.publish_review_agent.run(
                workflow.content,
                workflow.data_report,
                self.settings.get_style(workflow.style_key),
                workflow.content_score,
                preflight_summary=preflight_summary,
            )
            workflow.content_score.publish_readiness_score = int(publish_review.get("publish_readiness_score", 0))
            workflow.content_score.publish_risk_flags = list(publish_review.get("publish_risk_flags", []))
            workflow.content_score.publish_recommendation = str(
                publish_review.get("publish_recommendation", "manual_review")
            )
            workflow.content_score.publish_notes = list(publish_review.get("publish_notes", []))
            workflow_path = resolved / "workflow_result.json"
            score_path = resolved / "content_score.json"
            workflow_path.write_text(json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            score_payload = json.loads(score_path.read_text(encoding="utf-8")) if score_path.exists() else {}
            score_payload["scores"] = {
                **dict(score_payload.get("scores", {})),
                "publish_readiness_score": workflow.content_score.publish_readiness_score,
                "validation_score": workflow.content.validation_score,
            }
            score_payload["publish_risk_flags"] = workflow.content_score.publish_risk_flags
            score_payload["publish_recommendation"] = workflow.content_score.publish_recommendation
            score_payload["publish_notes"] = workflow.content_score.publish_notes
            score_payload["preflight_summary"] = preflight_summary
            score_path.write_text(json.dumps(score_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                "workflow_result": str(workflow_path),
                "content_score": str(score_path),
            }
        finally:
            openai_service.close()

    def resolve_run_dir(self, run_dir: str | None = None) -> Path:
        return self.review_service.resolve_run_dir(run_dir)
