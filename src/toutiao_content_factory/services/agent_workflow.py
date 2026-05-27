from __future__ import annotations

from datetime import datetime
import logging

from toutiao_content_factory.agents import (
    DataAgent,
    EvolutionAgent,
    PolishAgent,
    PublishReviewAgent,
    ReviewAgent,
    TitleAgent,
    TrendAgent,
    WriterAgent,
)
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import LowQualityAbortException
from toutiao_content_factory.models import CacheContext, ExtractionResult, WorkflowResult
from toutiao_content_factory.services.content_guard_service import ContentGuardService
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


class AgentWorkflowService:
    LOW_QUALITY_THRESHOLD = 60

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
        self.evolution_agent = EvolutionAgent(settings, prompt_repository, openai_service, logger)
        self.writer_agent = WriterAgent(settings, prompt_repository, openai_service, logger)
        self.polish_agent = PolishAgent(settings, prompt_repository, openai_service, logger)
        self.title_agent = TitleAgent(settings, prompt_repository, openai_service, logger)
        self.review_agent = ReviewAgent(settings, prompt_repository, openai_service, logger)
        self.publish_review_agent = PublishReviewAgent(settings, prompt_repository, openai_service, logger)
        self.content_guard_service = ContentGuardService(settings, logger)
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

        try:
            self.evolution_agent.run()
        except Exception as exc:
            self.logger.warning("EvolutionAgent failed, continuing with existing best practices rules: %s", exc)

        trend_report = self.trend_agent.run(extraction, style, cache_context)
        data_report = self.data_agent.run(extraction, trend_report, style)
        try:
            self._abort_if_low_quality(extraction, data_report)
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
            self._apply_bad_words_gate(content, content_score)
        except LowQualityAbortException as exc:
            self.logger.info("Skipping low-quality topic: %s", exc)
            raise

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
            estimated_cost=self.openai_service.estimated_cost,
        )

    def _abort_if_low_quality(self, extraction: ExtractionResult, data_report) -> None:
        score = self._resolve_quality_score(extraction, data_report)
        reasons = self._collect_low_quality_reasons(extraction, data_report, score)
        if reasons:
            title = data_report.topic_title.strip() or extraction.topic_title.strip() or "untitled-topic"
            raise LowQualityAbortException(
                f"{title} | score={score} | reasons={', '.join(reasons)}"
            )

    def _resolve_quality_score(self, extraction: ExtractionResult, data_report) -> int:
        if data_report.topic_quality_score > 0:
            return int(data_report.topic_quality_score)
        if extraction.topic_quality_score > 0:
            return int(extraction.topic_quality_score)
        signal_count = len(data_report.core_data) + len(data_report.growth_numbers) + len(data_report.evidence_notes)
        company_bonus = min(12, len(data_report.companies) * 4)
        return min(100, signal_count * 10 + company_bonus)

    def _collect_low_quality_reasons(self, extraction: ExtractionResult, data_report, score: int) -> list[str]:
        reasons: list[str] = []
        if score < self.LOW_QUALITY_THRESHOLD:
            reasons.append(f"quality_score_below_{self.LOW_QUALITY_THRESHOLD}")
        if not (data_report.core_data or data_report.key_points):
            reasons.append("missing_core_points")
        if not (data_report.growth_numbers or data_report.evidence_notes):
            reasons.append("missing_evidence")
        if not (data_report.companies or extraction.companies or data_report.focus_industry.strip()):
            reasons.append("missing_subject_anchor")
        return reasons

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

    def _apply_bad_words_gate(self, content, content_score) -> None:
        title = content.recommended_titles[0] if content.recommended_titles else ""
        text = "\n".join(
            part
            for part in (title, content.article, content.summary, " ".join(content.tags))
            if str(part).strip()
        )
        scan = self.content_guard_service.scan_text(text)
        if not scan.get("blocked"):
            return

        matched_words = list(scan.get("matched_words", []))
        warning = f"命中本地敏感词/违禁词，已禁止自动发布：{', '.join(matched_words[:10])}"
        content_score.publish_readiness_score = 0
        content_score.publish_recommendation = "revise_before_publish"
        content_score.publish_risk_flags = self._unique([warning, *content_score.publish_risk_flags])[:6]
        content_score.publish_notes = self._unique(
            [*list(scan.get("warnings", [])), *content_score.publish_notes]
        )[:6]

    def _unique(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

