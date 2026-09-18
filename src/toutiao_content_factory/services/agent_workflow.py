from __future__ import annotations

from datetime import datetime
import logging

from toutiao_content_factory.agents import (
    DataAgent,
    DataMiningAgent,
    EditorialCouncilAgent,
    EvolutionAgent,
    InsightAgent,
    PolishAgent,
    PublishReviewAgent,
    QualityReviewCouncilAgent,
    ReviewAgent,
    TitleAgent,
    TrendAgent,
    WriterAgent,
)
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import LowQualityAbortException
from toutiao_content_factory.models import (
    CacheContext,
    EditorialCouncilReport,
    ExtractionResult,
    InsightReport,
    QualityReviewReport,
    WorkflowResult,
)
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.content_guard_service import ContentGuardService
from toutiao_content_factory.services.letta_client import LettaClient
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.originality_service import (
    REVIEW_THRESHOLD,
    SOURCE_MATERIAL_MIN_CHARS,
    source_copy_ratio,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository


class AgentWorkflowService:
    LOW_QUALITY_THRESHOLD = 60
    ARTICLE_MIN_CHARS = 1500
    # Leave room for publish-time sanitization and CTA removal.  The Toutiao
    # quality gate evaluates the public article, not the generated draft.
    AUTO_PUBLISH_ARTICLE_MIN_CHARS = 1750
    # 整个 workflow 生命周期内 LLM expansion 最大调用次数
    # DeepSeek-V4-Flash 单次 expansion 耗时 5-7 分钟，多次累计会导致云端 30 分钟超时
    MAX_LLM_EXPANSION_CALLS = 1

    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.logger = logger
        # 基于发布数据观察的迭代调整项，传递给各 Agent 覆盖默认配置
        iteration_overrides = settings.iteration_overrides
        self.trend_agent = TrendAgent(settings, prompt_repository, openai_service, logger)
        self.data_agent = DataAgent(settings, prompt_repository, openai_service, logger)
        self.evolution_agent = EvolutionAgent(settings, prompt_repository, openai_service, logger)
        self.writer_agent = WriterAgent(settings, prompt_repository, openai_service, logger, iteration_overrides=iteration_overrides)
        self.polish_agent = PolishAgent(settings, prompt_repository, openai_service, logger, iteration_overrides=iteration_overrides)
        self.title_agent = TitleAgent(settings, prompt_repository, openai_service, logger, iteration_overrides=iteration_overrides)
        self.review_agent = ReviewAgent(settings, prompt_repository, openai_service, logger)
        self.publish_review_agent = PublishReviewAgent(settings, prompt_repository, openai_service, logger)
        self.quality_review_council_agent = QualityReviewCouncilAgent(
            settings, prompt_repository, openai_service, logger
        )
        self.insight_agent = InsightAgent(settings, prompt_repository, openai_service, logger)
        self.data_mining_agent = DataMiningAgent(settings, prompt_repository, openai_service, logger)
        self.editorial_council_agent = EditorialCouncilAgent(settings, prompt_repository, openai_service, logger)
        self.content_guard_service = ContentGuardService(settings, logger)
        self.quality_checker = ArticleQualityChecker()
        self.openai_service = openai_service
        # LLM expansion 调用计数器，跨 writer/polish/second_polish 阶段共享
        self._llm_expansion_call_count = 0

        # One-Person Company AI Services
        self._letta_client: LettaClient | None = None
        if settings.letta_enabled and settings.letta_base_url:
            self._letta_client = LettaClient(
                base_url=settings.letta_base_url,
                agent_id=settings.letta_agent_id,
                logger=logger,
            )

    def _get_memory_context(self, topic: str = "") -> str:
        """Retrieve memory context from Letta if available."""
        if not self._letta_client:
            return ""
        try:
            return self._letta_client.get_memory_context(topic)
        except Exception as exc:
            self.logger.warning("Letta memory retrieval failed (non-blocking): %s", exc)
            return ""

    def _save_memory_observation(self, observation: str) -> None:
        """Save an observation to Letta memory if available."""
        if not self._letta_client:
            return
        try:
            self._letta_client.save_observation(observation)
        except Exception as exc:
            self.logger.warning("Letta memory save failed (non-blocking): %s", exc)

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
        try:
            data_report = self.data_agent.run(extraction, trend_report, style)
        except Exception as exc:
            self.logger.error("DataAgent failed, using fallback data report: %s", exc)
            data_report = self.data_agent._fallback(extraction, trend_report)
        try:
            self._abort_if_low_quality(extraction, data_report)
            insight_report: InsightReport | None = None
            editorial_council_report: EditorialCouncilReport | None = None
            quality_review_council_report: QualityReviewReport | None = None
            memory_context = ""
            if self.settings.expert_council_enabled or self.settings.enhanced_insights_enabled:
                memory_context = self._get_memory_context(extraction.topic_title)
                if memory_context:
                    self.logger.info("Letta memory context loaded: %d chars", len(memory_context))

            if self.settings.expert_council_enabled:
                try:
                    data_mining_report = None
                    if self.settings.enhanced_insights_use_data_mining:
                        self.logger.info("Running DataMiningAgent for council evidence...")
                        data_mining_report = self.data_mining_agent.run(extraction, style)
                    self.logger.info(
                        "Running EditorialCouncilAgent with roles: %s",
                        ", ".join(self.settings.expert_council_roles),
                    )
                    editorial_council_report = self.editorial_council_agent.run(
                        extraction,
                        trend_report,
                        data_report,
                        style,
                        cache_context,
                        memory_context=memory_context,
                        data_mining_report=data_mining_report,
                    )
                    insight_report = editorial_council_report.to_insight_report()
                    self.logger.info(
                        "Editorial council thesis: %s",
                        insight_report.main_insight[:120],
                    )
                except Exception as exc:
                    self.logger.warning("EditorialCouncilAgent failed, falling back to InsightAgent: %s", exc)
                    editorial_council_report = None

            if insight_report is None and self.settings.enhanced_insights_enabled:
                try:
                    self.logger.info("Running InsightAgent for enhanced opinions...")
                    insight_report = self.insight_agent.run(
                        extraction, trend_report, data_report, style, cache_context
                    )
                    self.logger.info(f"Insight generated: {insight_report.main_insight[:80]}...")
                except Exception as exc:
                    self.logger.warning("InsightAgent failed, falling back: %s", exc)
                    insight_report = None

            if insight_report:
                content = self.writer_agent.run_with_insights(
                    trend_report, data_report, insight_report, style, cache_context
                )
            else:
                content = self.writer_agent.run(trend_report, data_report, style, cache_context)

            polish_pack = self.polish_agent.run(content, trend_report, data_report, style, cache_context)
            self._apply_polish_pack(content, polish_pack, data_report)
            self._reduce_source_overlap(content, extraction, data_report)

            # The quality council audits the public title as well as the body.
            # Generate the title before the first audit so it never evaluates an
            # otherwise valid article with an empty title.
            self._refresh_titles(content, trend_report, data_report, style, cache_context)
            quality_reviewed_state = self._quality_review_state(content)
            quality_review_council_report = self._run_quality_review_council(
                content=content,
                trend_report=trend_report,
                data_report=data_report,
                style=style,
                cache_context=cache_context,
                editorial_council=editorial_council_report,
                fast_mode=fast_mode,
            )
            if self._quality_review_article(content) != quality_reviewed_state[0]:
                self.logger.info(
                    "Quality review revision changed the article; regenerating the title before final audit."
                )
                self._refresh_titles(content, trend_report, data_report, style, cache_context)

            content.cover_prompt = ""
            content.infographic_prompts = []

            if skip_review_agent:
                review_pack = self.review_agent._fallback(content, data_report, cache_context)
            else:
                review_pack = self.review_agent.run(content, data_report, style, cache_context)
            self._apply_review_pack(content, review_pack, cache_context)
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
                    self._apply_polish_pack(content, revised_pack, data_report, update_validation=False)
                    content.validation_notes = list(revised_pack.get("validation_notes", [])) or content.validation_notes
                    content.validation_score = max(int(revised_pack.get("validation_score", 0)), content.validation_score)

                    self._refresh_titles(content, trend_report, data_report, style, cache_context)

                    review_pack = self.review_agent.run(content, data_report, style, cache_context)
                    self._apply_review_pack(content, review_pack, cache_context)
                    content_score = review_pack["content_score"]

            # Any later body or title change invalidates the first audit. The
            # final pass is audit-only so it cannot start another revision loop.
            if (
                quality_review_council_report is not None
                and self._quality_review_state(content) != quality_reviewed_state
            ):
                self.logger.info(
                    "Article or title changed after the initial quality audit; running final quality audit before publish review."
                )
                quality_review_council_report = self._run_quality_review_council(
                    content=content,
                    trend_report=trend_report,
                    data_report=data_report,
                    style=style,
                    cache_context=cache_context,
                    editorial_council=editorial_council_report,
                    fast_mode=fast_mode,
                    article_version="post_review",
                    allow_auto_revise=False,
                )

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
            self._apply_quality_review_gate(content_score, quality_review_council_report)
            self._apply_bad_words_gate(content, content_score)
        except LowQualityAbortException as exc:
            self.logger.info("Skipping low-quality topic: %s", exc)
            raise

        model_used = self.settings.openai_model if self.openai_service.available else "local-draft"

        self._save_memory_observation(
            "Run completed: topic selected, article generated and published"
        )

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
            editorial_council=editorial_council_report,
            quality_review_council=quality_review_council_report,
        )

    def _run_quality_review_council(
        self,
        *,
        content,
        trend_report,
        data_report,
        style,
        cache_context,
        editorial_council: EditorialCouncilReport | None,
        fast_mode: bool,
        article_version: str = "post_polish",
        allow_auto_revise: bool = True,
    ) -> QualityReviewReport | None:
        quality_settings = getattr(self.settings, "quality_review", None)
        if not bool(getattr(quality_settings, "enabled", True)):
            self.logger.info("Quality review council is disabled by configuration.")
            return None

        try:
            report = self.quality_review_council_agent.run(
                content,
                data_report,
                style,
                cache_context,
                editorial_council=editorial_council,
                revision_round=0,
            )
        except Exception as exc:
            self.logger.warning("Quality review council failed; using local fallback: %s", exc)
            report = self.quality_review_council_agent._fallback(
                content,
                data_report,
                revision_round=0,
            )
        report.article_version = article_version

        max_revisions = int(getattr(quality_settings, "max_auto_revisions", 1))
        can_auto_revise = bool(
            report.needs_revision
            and getattr(quality_settings, "auto_revise", True)
            and allow_auto_revise
            and max_revisions > 0
            and not fast_mode
            and self.openai_service.available
        )
        revision_count = 0
        while can_auto_revise and revision_count < max_revisions:
            revision_focus = self._build_quality_review_revision_focus(report)
            try:
                revised_pack = self.polish_agent.run(
                    content,
                    trend_report,
                    data_report,
                    style,
                    cache_context,
                    revision_focus=revision_focus,
                )
            except Exception as exc:
                self.logger.warning("Quality review revision pass failed; retaining the audited article: %s", exc)
                break
            revised_article = str(revised_pack.get("polished_article", "")).strip()
            if not revised_article or revised_article == content.article:
                self.logger.info("Quality review council requested revision, but the polish pass produced no new article.")
                break

            revision_count += 1
            self.logger.info(
                "Quality review council triggered automatic revision pass %d/%d.",
                revision_count,
                max_revisions,
            )
            self._apply_polish_pack(content, revised_pack, data_report)
            report = self.quality_review_council_agent.run(
                content,
                data_report,
                style,
                cache_context,
                editorial_council=editorial_council,
                revision_round=revision_count,
            )
            report.article_version = article_version
            if not report.needs_revision:
                break

        if report.needs_revision:
            mode = "audit-only" if fast_mode or revision_count == 0 else "after-revision"
            self.logger.warning(
                "Quality review council verdict=%s score=%d (%s); auto publish will be blocked.",
                report.verdict,
                report.overall_score,
                mode,
            )
        return report

    @staticmethod
    def _quality_review_article(content) -> str:
        return str(getattr(content, "polished_article", "") or getattr(content, "article", "") or "").strip()

    @classmethod
    def _quality_review_state(cls, content) -> tuple[str, str]:
        titles = list(getattr(content, "recommended_titles", []) or [])
        title = str(titles[0]).strip() if titles else ""
        return cls._quality_review_article(content), title

    def _build_quality_review_revision_focus(self, report: QualityReviewReport) -> list[str]:
        focus = [
            *report.hard_blockers,
            *report.required_revisions,
            *report.fact_issues,
            *report.logic_issues,
            *report.structure_issues,
            *report.originality_issues,
            *report.compliance_issues,
        ]
        result: list[str] = []
        seen: set[str] = set()
        for item in focus:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result[:8]

    def _apply_quality_review_gate(
        self,
        content_score,
        report: QualityReviewReport | None,
    ) -> None:
        if report is None or not report.needs_revision:
            return

        findings = self._unique(
            [
                *report.hard_blockers,
                *report.required_revisions,
                *report.fact_issues,
                *report.logic_issues,
                *report.structure_issues,
                *report.originality_issues,
                *report.compliance_issues,
            ]
        )
        prefix = "质量审核专家团"
        if report.hard_blockers or report.verdict == "blocked":
            content_score.publish_readiness_score = 0
            content_score.publish_risk_flags = self._unique(
                [f"{prefix}阻断发布：{item}" for item in findings[:5]]
                + list(content_score.publish_risk_flags)
            )[:8]
        else:
            # 非阻断问题按严重度折算就绪度，而不是一律归零。
            # 历史问题：两个分支都把 readiness 置 0，导致只要审核团跑过，
            # 就绪度恒为 0，而自动发布要求 ≥60 —— 全自动发布在设计上就
            # 不可能通过（2026-09-15 实测：23 篇全被拦，发布 0 条）。
            # 这里改用「问题条数」折算：模型在 needs_revision 时经常直接
            # 返回 0 分，不能拿它的总分当依据。
            if report.verdict == "approved" or not findings:
                readiness = 92
            else:
                readiness = max(60, 92 - len(findings) * 6)
            content_score.publish_readiness_score = readiness
            content_score.publish_risk_flags = self._unique(
                [f"{prefix}要求返工：{item}" for item in findings[:5]]
                + list(content_score.publish_risk_flags)
            )[:8]
            # 不阻断时不该继续标成「必须返工」——那与已折算的就绪度自相矛盾，
            # 也会让无人值守流程误以为稿件不可用。只有完全干净的稿子才直接
            # 标可发布，其余交给下游按就绪度分数判断。
            content_score.publish_recommendation = (
                "ready_to_publish"
                if (report.verdict == "approved" and not findings)
                else "manual_review"
            )
        if content_score.publish_readiness_score == 0:
            content_score.publish_recommendation = "revise_before_publish"
        content_score.revision_suggestions = self._unique(
            list(content_score.revision_suggestions) + findings[:6]
        )[:8]

    def _abort_if_low_quality(self, extraction: ExtractionResult, data_report) -> None:
        # 实时热榜二创：标题型热点没有完整原文/证据链，是正常形态，
        # 不以"缺证据/缺公司/质量分低"拦截（热点时效和观点密度才是价值）。
        is_hot_topic = bool(getattr(extraction, "is_hot_topic", False)) or bool(
            getattr(data_report, "is_hot_topic", False)
        )
        quality_flags = {
            str(flag).strip()
            for flag in (
                getattr(data_report, "topic_quality_flags", None)
                or getattr(extraction, "topic_quality_flags", None)
                or []
            )
            if str(flag).strip()
        }
        is_title_only = "title_only" in quality_flags
        publishing = getattr(self.settings, "publishing", None)
        toutiao_auto_publish = bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )
        # 证据门槛豁免。
        #
        # 既有语义（由 tests/test_agent_workflow.py 的两条断言共同定义，是**有意策略**，
        # 不是笔误，改动前务必先读它们）：
        #   - 未开自动发布 + 热榜/标题型热点 -> 放行（留人工复核）；
        #   - 开了自动发布                    -> **照拦**（无人值守时不冒险发证据不足的稿）；
        #   - 非热点且非标题型                -> 照拦。
        #
        # ⚠️ 2026-09-18：这条「开了自动发布就拦」的策略对社会故事类账号走不通。
        # 热榜类社会新闻（如「男子高空作业绳子被2楼老人剪断」）在采集时**只有标题、
        # 没有正文**，永远满足不了「≥6 条事实且 ≥320 字」，而这些账号恰恰是开着
        # 自动发布的，于是每个候选都被拦，日更直接报
        # 「Daily workflow did not produce any runnable candidates」。实测 4 个候选全被跳过。
        #
        # 因此加了一个**强力覆盖开关** `hot_topic_evidence_exempt`：
        #   - 默认 False -> 上面三条既有语义**一字不变**，其他账号完全不受影响；
        #   - 置 True   -> 热榜/标题型热点直接豁免，不受 auto_publish 影响。
        # 由账号配置显式打开（社会故事类账号已打开），而不是放宽全局默认值。
        evidence_exempt = bool(
            getattr(self.settings, "hot_topic_evidence_exempt", False)
            and (is_hot_topic or is_title_only)
        )
        if (
            "insufficient_source_facts" in quality_flags
            and not evidence_exempt
            and (toutiao_auto_publish or not (is_hot_topic or is_title_only))
        ):
            title = data_report.topic_title.strip() or extraction.topic_title.strip() or "untitled-topic"
            raise LowQualityAbortException(
                f"{title} | reasons=insufficient_source_facts"
            )
        if is_hot_topic or is_title_only:
            self.logger.info(
                "Hot/title-only mode: skipping evidence-based quality gate for %s",
                extraction.topic_title.strip() or data_report.topic_title.strip(),
            )
            return
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

    def _ensure_article_min_chars(self, article: str, data_report, min_chars: int = None) -> str:
        min_chars = min_chars if min_chars is not None else self.ARTICLE_MIN_CHARS
        current = len(article.replace("\n", "").replace(" ", ""))
        if current >= min_chars:
            return article
        self.logger.warning(
            "Article has only %d chars (minimum %d). Reusing WriterAgent evidence-aware expansion.",
            current,
            min_chars,
        )
        result = self.writer_agent.ensure_article_min_chars(
            article,
            data_report,
            min_chars=min_chars,
        )
        result_chars = len(result.replace("\n", "").replace(" ", ""))
        if result_chars < min_chars:
            self.logger.warning(
                "WriterAgent expansion still produced %d chars (minimum %d); quality gates will keep the article blocked.",
                result_chars,
                min_chars,
            )
        return result or article

    def _expand_article_via_llm(self, article: str, data_report, min_chars: int) -> str:
        current_chars = len(article.replace("\n", "").replace(" ", ""))
        deficit = min_chars - current_chars
        if deficit <= 0:
            return article
        data_parts = [
            f"主题：{str(getattr(data_report, 'topic_title', '') or '').strip()}",
        ]
        if getattr(data_report, "core_data", None):
            data_parts.append("核心数据：" + "；".join(str(item) for item in data_report.core_data[:6]))
        if getattr(data_report, "industry_trends", None):
            data_parts.append("行业变化：" + "；".join(str(item) for item in data_report.industry_trends[:4]))
        if getattr(data_report, "key_points", None):
            data_parts.append("关键判断：" + "；".join(str(item) for item in data_report.key_points[:4]))
        if getattr(data_report, "growth_numbers", None):
            data_parts.append("可引用数字：" + "、".join(str(item) for item in data_report.growth_numbers[:8]))
        if getattr(data_report, "companies", None):
            data_parts.append("已确认主体：" + "、".join(str(item) for item in data_report.companies[:6]))
        if hasattr(data_report, "watch_metrics") and data_report.watch_metrics:
            data_parts.append("跟踪变量：" + "、".join(str(m) for m in data_report.watch_metrics[:5]))
        if hasattr(data_report, "risk_flags") and data_report.risk_flags:
            data_parts.append("风险因素：" + "、".join(str(r) for r in data_report.risk_flags[:3]))
        data_summary = "\n".join(data_parts) if data_parts else "无额外数据"
        system_prompt = (
            "你是文章扩展助手。在现有文章基础上补充深度内容，禁止使用套话。"
            "只能使用提供的主题、核心数据、关键判断和跟踪变量，不得补造公司、订单、收入、时间、比例或案例。"
            "禁止重复现有文章中已经出现过的数据、观点或措辞。"
            "输出JSON格式：{\"paragraphs\": [\"段落1\", \"段落2\", ...]}"
        )
        user_prompt = (
            f"当前文章已有{current_chars}字，需要再补充约{deficit}字。\n\n"
            f"现有文章：\n{article[:2000]}\n\n"
            f"可用数据：\n{data_summary}\n\n"
            f"请输出JSON格式的补充段落，每个段落不少于150字。"
        )
        try:
            payload = self.openai_service.create_json_completion(system_prompt, user_prompt)
            if payload and isinstance(payload.get("paragraphs"), list):
                existing = [item.strip() for item in article.split("\n\n") if item.strip()]
                new_paragraphs: list[str] = []
                for raw in payload["paragraphs"]:
                    cleaned = clean_article_text(str(raw or ""), min_word_count=0).strip()
                    if not cleaned:
                        continue
                    for paragraph in cleaned.split("\n\n"):
                        paragraph = paragraph.strip()
                        if not paragraph or paragraph in existing or paragraph in new_paragraphs:
                            continue
                        if any(token in paragraph for token in ("manual_paste", "体裁要求", "固定写法", "推荐主结论")):
                            continue
                        new_paragraphs.append(paragraph)
                if new_paragraphs:
                    for p in new_paragraphs:
                        existing.insert(-1, p)
                    result = "\n\n".join(existing)
                    result = clean_article_text(result, min_word_count=0)
                    self.logger.info("LLM expansion added %d paragraphs, article now %d chars.", len(new_paragraphs), len(result.replace("\n", "").replace(" ", "")))
                    return result
        except Exception as exc:
            self.logger.warning("LLM expansion failed: %s", exc)
        return article

    @staticmethod
    def _build_supplementary_paragraphs(data_report, existing_paragraphs: list[str] | None = None) -> list[str]:
        """Build only evidence-bearing supplements; never pad with generic claims."""
        paragraphs: list[str] = []
        existing_text = "\n".join(existing_paragraphs or [])

        def items(values: list[str], limit: int) -> list[str]:
            cleaned: list[str] = []
            for value in values[:limit]:
                text = clean_article_text(str(value), min_word_count=0).strip()
                if text and text not in cleaned:
                    cleaned.append(text)
            return cleaned

        def append_if_new(source_items: list[str], prefix: str, suffix: str) -> None:
            fresh_items = [item for item in source_items if item not in existing_text]
            if fresh_items:
                paragraphs.append(prefix + "、".join(fresh_items) + suffix)

        metrics = items(getattr(data_report, "watch_metrics", []) or [], 4)
        if metrics:
            append_if_new(
                metrics,
                "后续核验先看",
                "。这些项目是后续复盘时可以核对的信号，不能单独证明订单或收入已经兑现。",
            )

        risks = items(getattr(data_report, "risk_flags", []) or [], 2)
        if risks:
            append_if_new(
                risks,
                "限制条件包括：",
                "。如果关键环节持续延迟，前面的判断就需要重新核对。",
            )

        notes = items(getattr(data_report, "evidence_notes", []) or [], 2)
        if notes:
            append_if_new(
                notes,
                "目前材料明确到：",
                "。其余订单、收入或规模判断，应等待新的公告或项目进展。",
            )

        numbers = items(getattr(data_report, "growth_numbers", []) or [], 2)
        if len(numbers) >= 2:
            append_if_new(
                numbers,
                "可以回指的数字是",
                "。它们说明已经发生的变化，不能单独证明后续利润或规模一定兑现。",
            )
        return paragraphs

    def _has_grounded_evidence(self, data_report) -> bool:
        companies = [str(item).strip() for item in getattr(data_report, "companies", []) if str(item).strip()]
        growth_numbers = [str(item).strip() for item in getattr(data_report, "growth_numbers", []) if str(item).strip()]
        evidence_notes = [str(item).strip() for item in getattr(data_report, "evidence_notes", []) if str(item).strip()]
        return bool(companies or growth_numbers or len(evidence_notes) >= 2 or int(getattr(data_report, "evidence_strength", 0) or 0) >= 70)

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

        quality_flags = {
            str(flag).strip()
            for flag in (getattr(data_report, "topic_quality_flags", None) or getattr(extraction, "topic_quality_flags", None) or [])
            if str(flag).strip()
        }
        if "evergreen_fallback" in quality_flags and not self._has_grounded_evidence(data_report):
            reasons.append("fallback_topic_without_hard_evidence")
        elif not self._has_grounded_evidence(data_report) and int(getattr(data_report, "topic_quality_score", 0) or 0) < 78:
            reasons.append("insufficient_hard_evidence")
        return reasons

    def _legacy_ensure_article_min_chars(self, article: str, data_report, min_chars: int = None) -> str:
        min_chars = min_chars if min_chars is not None else self.ARTICLE_MIN_CHARS
        article = clean_article_text(article, min_word_count=0)
        # Keep the explicit auto-publish target intact even when a hot-topic
        # source has limited evidence.  Otherwise the fallback word-target
        # clamp can silently push the public package below Toutiao's gate.
        if min_chars <= self.ARTICLE_MIN_CHARS and not self._has_grounded_evidence(data_report):
            min_chars = min(min_chars, max(950, int((getattr(data_report, "word_target", 0) or 1200) * 0.7)))
        current = len(article.replace("\n", "").replace(" ", ""))
        if current >= min_chars:
            return article
        self.logger.warning(
            "Article has only %d chars (minimum %d). Appending grounded supplementary paragraphs.",
            current,
            min_chars,
        )
        paragraphs = [item.strip() for item in article.split("\n\n") if item.strip()]
        supplements = self.writer_agent._build_supplementary_paragraphs(
            data_report,
            existing_paragraphs=paragraphs,
        )
        for sup in supplements:
            paragraphs.insert(-1, sup)
            total = len("\n\n".join(paragraphs).replace("\n", "").replace(" ", ""))
            if total >= min_chars:
                break
        result = clean_article_text("\n\n".join(paragraphs), min_word_count=0)
        result_chars = len(result.replace("\n", "").replace(" ", ""))
        if result_chars < min_chars and self.openai_service.available and self._has_grounded_evidence(data_report):
            self.logger.info(
                "Supplementary paragraphs brought article to %d chars, still below %d. Attempting grounded LLM expansion.",
                result_chars,
                min_chars,
            )
            result = self._expand_article_via_llm(result, data_report, min_chars)
        return clean_article_text(result, min_word_count=0)

    def _legacy_expand_article_via_llm(self, article: str, data_report, min_chars: int) -> str:
        current_chars = len(article.replace("\n", "").replace(" ", ""))
        deficit = min_chars - current_chars
        if deficit <= 0 or not self._has_grounded_evidence(data_report):
            return article
        # 限制整个 workflow 生命周期内的 LLM expansion 调用次数
        # 避免 writer/polish/second_polish 阶段反复触发导致云端 30 分钟超时
        if self._llm_expansion_call_count >= self.MAX_LLM_EXPANSION_CALLS:
            self.logger.warning(
                "LLM expansion 已达上限（%d/%d），跳过本次调用，当前文章 %d 字符（目标 %d）",
                self._llm_expansion_call_count,
                self.MAX_LLM_EXPANSION_CALLS,
                current_chars,
                min_chars,
            )
            return article
        self._llm_expansion_call_count += 1
        data_parts = []
        if hasattr(data_report, "watch_metrics") and data_report.watch_metrics:
            data_parts.append("Follow-up metrics: " + " / ".join(str(m) for m in data_report.watch_metrics[:5]))
        if hasattr(data_report, "risk_flags") and data_report.risk_flags:
            data_parts.append("Risk factors: " + " / ".join(str(r) for r in data_report.risk_flags[:3]))
        if hasattr(data_report, "growth_numbers") and data_report.growth_numbers:
            data_parts.append("Grounded numbers: " + " / ".join(str(g) for g in data_report.growth_numbers[:3]))
        if hasattr(data_report, "evidence_notes") and data_report.evidence_notes:
            data_parts.append("Evidence notes: " + " / ".join(str(e) for e in data_report.evidence_notes[:3]))
        data_summary = "\n".join(data_parts) if data_parts else "No extra evidence."
        system_prompt = (
            "You are an article expansion assistant. Extend the Chinese article using only the provided evidence. "
            "Never invent companies, numbers, time points, case studies, margins, or market share. "
            'Return strict JSON: {"paragraphs": ["...", "..."]}.'
        )
        user_prompt = (
            f"Current article length: {current_chars}. Need about {deficit} more Chinese characters.\n\n"
            f"Article:\n{article[:2400]}\n\n"
            f"Evidence:\n{data_summary}\n\n"
            "Add 1-3 analytical paragraphs only. They must stay in the same industry context and add no unsupported facts."
        )
        try:
            payload = self.openai_service.create_json_completion(system_prompt, user_prompt)
            if payload and isinstance(payload.get("paragraphs"), list):
                existing = [item.strip() for item in article.split("\n\n") if item.strip()]
                new_paragraphs: list[str] = []
                for raw in payload["paragraphs"]:
                    if not isinstance(raw, str) or not raw.strip():
                        continue
                    sanitized = clean_article_text(raw, min_word_count=0)
                    for paragraph in sanitized.split("\n\n"):
                        paragraph = paragraph.strip()
                        if not paragraph:
                            continue
                        if self.writer_agent._is_duplicate_paragraph(
                            paragraph,
                            [*existing, *new_paragraphs],
                        ):
                            continue
                        new_paragraphs.append(paragraph)
                if new_paragraphs:
                    existing.extend(new_paragraphs)
                    result = clean_article_text("\n\n".join(existing), min_word_count=0)
                    result = "\n\n".join(
                        self.writer_agent._trim_low_confidence_paragraphs(
                            result.split("\n\n"), data_report
                        )
                    )
                    self.logger.info("Grounded LLM expansion added %d paragraphs.", len(new_paragraphs))
                    return result
        except Exception as exc:
            self.logger.warning("LLM expansion failed: %s", exc)
        return article

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
            focus.append("补充数据来源标记，增加具体事实锚点")
        if self._has_long_paragraph(content.article):
            focus.append("拆分长段落，每段控制在2-3句")
        if content_score.ai_generated_risk >= 32:
            focus.append("调整表达风格，减少AI痕迹")

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

    def _reduce_source_overlap(self, content, extraction, data_report) -> None:
        """初稿若与素材原文连续重合越线，做一次**有界**的去重合改写。

        为什么放在评分之前：素材原文的逐字句子会经 ``data_report.source_facts``
        进入 writer 提示词（最多 40 句、每句最长 300 字），模型复述这些句子就会
        推高抄录率。提示词层面的约束管不住模型，所以补一道可复测的兜底：只改写
        重合片段，改写后复测确有下降才采纳，且整条链路只改写一次。

        比对基准用 ``extraction.merged_text``（本次运行的素材正文），而不是发布时
        那份 ``source_material_full.txt``——后者要到归档阶段才落盘。两者同源，
        这里量出的重合度只会更严，不会更松。
        """
        article = str(getattr(content, "article", "") or "")
        source_text = str(getattr(extraction, "merged_text", "") or "").strip()
        if not article or len(source_text) < SOURCE_MATERIAL_MIN_CHARS:
            return

        before = source_copy_ratio(article, source_text, strip_file_header=False)
        ratio = before.get("ratio")
        if ratio is None:
            return
        ratio = float(ratio)
        if ratio < REVIEW_THRESHOLD:
            self.logger.info(
                "素材连续抄录 %.1f%%，低于 %.0f%% 红线，无需改写", ratio, REVIEW_THRESHOLD
            )
            return

        spans = [str(item) for item in (before.get("samples") or [])]
        self.logger.warning(
            "素材连续抄录 %.1f%%（≥%.0f%% 红线），触发去重合改写，重合片段 %d 处",
            ratio,
            REVIEW_THRESHOLD,
            len(spans),
        )
        rewritten = self.polish_agent.dedup_rewrite(article, spans)
        if not rewritten:
            self.logger.warning("去重合改写未返回可用正文，保留原稿")
            return

        after = source_copy_ratio(rewritten, source_text, strip_file_header=False)
        after_ratio = after.get("ratio")
        if after_ratio is None or float(after_ratio) >= ratio:
            self.logger.warning(
                "去重合改写未降低抄录率（%.1f%% -> %s），保留原稿",
                ratio,
                after_ratio,
            )
            return

        # 走润色回写通道，顺带把自动发布模式的字数下限再兜一次；
        # update_validation=False 避免清掉已有的校验分。
        self._apply_polish_pack(
            content, {"polished_article": rewritten}, data_report, update_validation=False
        )
        content.validation_notes = [
            *list(getattr(content, "validation_notes", []) or []),
            f"已执行去重合改写：与素材连续抄录 {ratio}% -> {after_ratio}%",
        ][:10]
        self.logger.info("去重合改写生效：连续抄录 %.1f%% -> %.1f%%", ratio, after_ratio)

    def _apply_polish_pack(self, content, polish_pack, data_report, update_validation: bool = True) -> None:
        content.polished_article = str(polish_pack.get("polished_article", "")).strip()
        if content.polished_article:
            content.article = content.polished_article
        is_hot_topic = bool(getattr(data_report, "is_hot_topic", False))
        publishing = getattr(self.settings, "publishing", None)
        toutiao_auto_publish = bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )
        if toutiao_auto_publish:
            # Concise formats can stay short in draft/manual mode, but every
            # auto-published article must leave room for publish-time cleanup
            # and still clear Toutiao's public 1500-char gate.
            effective_min_chars = self.AUTO_PUBLISH_ARTICLE_MIN_CHARS
        else:
            effective_min_chars = 850 if is_hot_topic else self.ARTICLE_MIN_CHARS
        content.article = self._ensure_article_min_chars(
            content.article, data_report, min_chars=effective_min_chars
        )
        content.polished_article = content.article
        if update_validation:
            content.validation_notes = list(polish_pack.get("validation_notes", []))
            content.validation_score = int(polish_pack.get("validation_score", 0))

    def _refresh_titles(self, content, trend_report, data_report, style, cache_context) -> None:
        title_pack = self.title_agent.run(content, trend_report, data_report, style, cache_context)
        self._apply_title_pack(content, title_pack)
        self._filter_titles_by_quality(content)

    def _apply_title_pack(self, content, title_pack) -> None:
        content.titles = title_pack["titles"]
        content.recommended_titles = title_pack["recommended_titles"]
        # 2026-09-17（D1/D2）：来源与校验结果是**选择的一部分**，必须跟着候选一起落到
        # content 上，否则事后无法回答「这篇用的到底是模型标题还是兜底标题」，
        # 也无法回答「一个标题都没有时，是因为什么」。
        if "selected_title_source" in title_pack:
            content.selected_title_source = str(title_pack.get("selected_title_source") or "")
        if "title_validation" in title_pack:
            content.title_validation = dict(title_pack.get("title_validation") or {})

    def _filter_titles_by_quality(self, content) -> None:
        """标题质量二次校验（RULE_006/007，兜住历史 run 与旁路调用）。

        ⚠️ 2026-09-17（D2）：这里以前在**全部候选都不合格**时，会把「问题最少」的那一条
        塞回 `content.titles` 当作保底。那是本链路最实质的一处**静默绕过**：
        闸门的定义就是「不合格的不许流出」，而该分支恰恰保证了「总有一条不合格的流出」，
        于是下游任何地方都观察不到「这次没有一个标题合格」——闸门失效却无人知晓。
        实测样例 `其他智能12.7亿：机器人如何实测` 正是代码里另一处注释点名的反例
        （数字让一个模糊主体看起来很具体），却被这条保底逻辑一路送进了发布链路。

        现在改成：全灭就是全灭 —— 清空候选、把 status 与原因写进 `validation_notes`，
        由 topic attempt loop 换下一个选题。绝不把未经校验的标题放行。

        注意：`TitleAgent.run()` 已把同一套校验（含本函数的 RULE_006/007/009 闸门）
        前置到生成侧，所以正常路径下这里应当出现 0 个拒绝 —— 它现在的职责是
        **兜住旁路调用与历史产物**，而不是决定发什么。
        """
        article_text = content.article or ""
        original_titles = list(content.titles or [])
        original_recommended = list(content.recommended_titles or [])
        if not original_titles:
            return

        kept_titles: list[str] = []
        rejected: list[str] = []
        for title in original_titles:
            result = self.quality_checker._check_title(title, article_text)
            issues = result.get("issues", []) if isinstance(result, dict) else []
            # 只拦截 RULE_006/007/一致性问题（issues 中的硬性问题），suggestions 不拦截
            if issues:
                rejected.append(f"{title} -> {'; '.join(str(item) for item in issues)}")
            else:
                kept_titles.append(title)

        if kept_titles:
            kept_titles = self._cap_title_skeleton_families(kept_titles)
            content.titles = kept_titles
            # recommended_titles 也需同步过滤，只保留通过校验的
            kept_set = set(kept_titles)
            filtered_recommended = [t for t in original_recommended if t in kept_set]
            if filtered_recommended:
                content.recommended_titles = filtered_recommended
            else:
                content.recommended_titles = kept_titles[: self.settings.recommended_title_count]
            if rejected:
                self.logger.info(
                    "标题质量硬过滤：剔除 %d 个不合格标题：%s",
                    len(rejected),
                    " | ".join(rejected),
                )
            return

        # 全部不合格 —— **清空**，不保底。
        content.titles = []
        content.recommended_titles = []
        reason_summary: dict[str, int] = {}
        for item in rejected:
            code = "rule_gate_rejected"
            for token in ("RULE_007", "RULE_006", "RULE_009"):
                if token in item:
                    code = token.lower()
                    break
            reason_summary[code] = reason_summary.get(code, 0) + 1
        validation = dict(content.title_validation or {})
        validation.update(
            {
                "status": "all_rejected",
                "source": content.selected_title_source or validation.get("source", ""),
                "candidate_count": len(original_titles),
                "pass_count": 0,
                "reject_count": len(rejected),
                "top_reasons": sorted(reason_summary.items(), key=lambda kv: -kv[1])[:5],
            }
        )
        content.title_validation = validation
        content.validation_notes = self._unique(
            [
                *content.validation_notes,
                "标题质量硬过滤：全部候选均未通过校验，已清空候选并交由下一个选题重试"
                "（不再保底放行未校验标题）。",
                *[f"标题质量硬过滤：{item}" for item in rejected[:5]],
            ]
        )[:8]
        self.logger.warning(
            "标题质量硬过滤全灭：%d 个候选标题均未通过校验，已清空候选"
            "（status=all_rejected）。明细：%s",
            len(original_titles),
            " | ".join(rejected)[:300],
        )

    MAX_TITLES_PER_SKELETON = 3

    def _cap_title_skeleton_families(self, titles: list[str]) -> list[str]:
        """限制同一「开头骨架」的候选数量，避免候选列表被一个句式垄断。

        背景（2026-09-16）：提示词第 19 条已禁止填空式模板，模型仍然产出
        「灵巧手产业：…」11 条（13 个候选里）。第 19 条不管用，所以这里加硬约束。
        只保留每族**点击吸引力最高**的 N 条（同分保持原顺序），被砍掉的记日志。
        注意这是**去重**不是**删优**：每族最优的一条一定活着。
        """
        if len(titles) <= self.MAX_TITLES_PER_SKELETON:
            return titles
        checker = self.quality_checker
        families: dict[str, list[str]] = {}
        for title in titles:
            families.setdefault(checker.title_skeleton(title), []).append(title)

        kept: list[str] = []
        dropped: list[str] = []
        for family in families.values():
            if len(family) <= self.MAX_TITLES_PER_SKELETON:
                kept.extend(family)
                continue
            ranked = sorted(
                family,
                key=lambda item: (-checker.title_click_preference(item), titles.index(item)),
            )
            kept.extend(ranked[: self.MAX_TITLES_PER_SKELETON])
            dropped.extend(ranked[self.MAX_TITLES_PER_SKELETON :])
        if dropped:
            self.logger.warning(
                "Title skeleton collapse capped: dropped %d/%d candidates sharing a leading skeleton "
                "(kept at most %d per skeleton). Dropped: %s",
                len(dropped),
                len(titles),
                self.MAX_TITLES_PER_SKELETON,
                " | ".join(dropped)[:240],
            )
        # 保持原顺序，便于人读候选列表
        order = {title: index for index, title in enumerate(titles)}
        return sorted(kept, key=lambda item: order.get(item, 0))

    def _apply_review_pack(self, content, review_pack, cache_context) -> None:
        content.optimized_opening_lines = list(review_pack.get("optimized_opening_lines", []))
        content.review_notes = list(review_pack.get("review_notes", []))
        content.cache_note = cache_context.note

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

