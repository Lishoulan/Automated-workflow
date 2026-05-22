from __future__ import annotations

from datetime import datetime
import logging

from toutiao_content_factory.agents import (
    DataAgent,
    PolishAgent,
    PublishReviewAgent,
    ReviewAgent,
    TitleAgent,
    TrendAgent,
    WriterAgent,
)
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import CacheContext, ExtractionResult, WorkflowResult
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


class AgentWorkflowService:
    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self.trend_agent = TrendAgent(settings, prompt_repository, openai_service, logger)
        self.data_agent = DataAgent(settings, prompt_repository, openai_service, logger)
        self.writer_agent = WriterAgent(settings, prompt_repository, openai_service, logger)
        self.polish_agent = PolishAgent(settings, prompt_repository, openai_service, logger)
        self.title_agent = TitleAgent(settings, prompt_repository, openai_service, logger)
        self.review_agent = ReviewAgent(settings, prompt_repository, openai_service, logger)
        self.publish_review_agent = PublishReviewAgent(settings, prompt_repository, openai_service, logger)
        self.openai_service = openai_service

    def run(
        self,
        extraction: ExtractionResult,
        style_key: str,
        cache_context: CacheContext,
        fast_mode: bool = False,
        skip_review_agent: bool = False,
        skip_publish_review: bool = False,
    ) -> WorkflowResult:
        style = self.settings.get_style(style_key)

        trend_report = self.trend_agent.run(extraction, style, cache_context)
        data_report = self.data_agent.run(extraction, trend_report, style)
        content = self.writer_agent.run(trend_report, data_report, style, cache_context)

        polish_pack = self.polish_agent.run(content, trend_report, data_report, style, cache_context)
        content.polished_article = str(polish_pack.get("polished_article", "")).strip()
        if content.polished_article:
            content.article = content.polished_article
        content.validation_notes = list(polish_pack.get("validation_notes", []))
        content.validation_score = int(polish_pack.get("validation_score", 0))

        title_pack = self.title_agent.run(content, trend_report, data_report, style, cache_context)
        content.titles = title_pack["titles"]
        content.recommended_titles = title_pack["recommended_titles"]

        content.cover_prompt = ""
        content.infographic_prompts = []

        if skip_review_agent:
            review_pack = self.review_agent._fallback(content, data_report, cache_context)
        else:
            review_pack = self.review_agent.run(content, data_report, style, cache_context)
        content.optimized_opening_lines = list(review_pack.get("optimized_opening_lines", []))
        content.review_notes = list(review_pack.get("review_notes", []))
        content.cache_note = cache_context.note
        content_score = review_pack["content_score"]

        if not fast_mode and self._should_run_second_polish(content, content_score):
            revision_focus = self._build_revision_focus(content, content_score)
            revised_pack = self.polish_agent.run(
                content,
                trend_report,
                data_report,
                style,
                cache_context,
                revision_focus=revision_focus,
            )
            revised_article = str(revised_pack.get("polished_article", "")).strip()
            if revised_article and revised_article != content.article:
                self.logger.info("Review-triggered polish pass executed.")
                content.polished_article = revised_article
                content.article = revised_article
                content.validation_notes = list(revised_pack.get("validation_notes", [])) or content.validation_notes
                content.validation_score = max(int(revised_pack.get("validation_score", 0)), content.validation_score)

                title_pack = self.title_agent.run(content, trend_report, data_report, style, cache_context)
                content.titles = title_pack["titles"]
                content.recommended_titles = title_pack["recommended_titles"]

                review_pack = self.review_agent.run(content, data_report, style, cache_context)
                content.optimized_opening_lines = list(review_pack.get("optimized_opening_lines", []))
                content.review_notes = list(review_pack.get("review_notes", []))
                content_score = review_pack["content_score"]

        if skip_publish_review:
            publish_review = self.publish_review_agent._fallback(
                content,
                data_report,
                content_score,
                preflight_summary={},
            )
        else:
            publish_review = self.publish_review_agent.run(
                content,
                data_report,
                style,
                content_score,
                preflight_summary={},
            )
        content_score.publish_readiness_score = int(publish_review.get("publish_readiness_score", 0))
        content_score.publish_risk_flags = list(publish_review.get("publish_risk_flags", []))
        content_score.publish_recommendation = str(publish_review.get("publish_recommendation", "manual_review"))
        content_score.publish_notes = list(publish_review.get("publish_notes", []))

        model_used = self.settings.openai_model if self.openai_service.available else "local-draft"
        return WorkflowResult(
            extraction=extraction,
            trend_report=trend_report,
            data_report=data_report,
            content=content,
            content_score=content_score,
            style_key=style.key,
            style_label=style.label,
            cache_key=cache_context.cache_key,
            cache_status=cache_context.status,
            model_used=model_used,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _should_run_second_polish(self, content, content_score) -> bool:
        return (
            content_score.pass_status == "needs_revision"
            or content_score.ai_generated_risk >= 32
            or content_score.completion_rate_potential <= 84
            or content.validation_score <= 84
            or not self._has_source_anchor(content.article)
            or self._has_long_paragraph(content.article)
        )

    def _build_revision_focus(self, content, content_score) -> list[str]:
        focus = list(content.review_notes[:3]) + list(content_score.revision_suggestions[:3])
        if not self._has_source_anchor(content.article):
            focus.append("????????????????????????????????")
        if self._has_long_paragraph(content.article):
            focus.append("??????? 2 ? 3 ????????????????")
        if content_score.ai_generated_risk >= 32:
            focus.append("????????????????????????????")

        result: list[str] = []
        seen: set[str] = set()
        for item in focus:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result[:6]

    def _has_long_paragraph(self, article: str) -> bool:
        return any(len(item.strip()) >= 180 for item in article.split("\n\n") if item.strip())

    def _has_source_anchor(self, article: str) -> bool:
        markers = ("据", "数据显示", "公开材料", "财报", "公告", "机构统计", "报告")
        return any(marker in article for marker in markers)

