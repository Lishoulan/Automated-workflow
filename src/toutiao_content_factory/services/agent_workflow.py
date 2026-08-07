from __future__ import annotations

from datetime import datetime
import logging

from toutiao_content_factory.agents import (
    DataAgent,
    DataMiningAgent,
    EvolutionAgent,
    InsightAgent,
    PolishAgent,
    PublishReviewAgent,
    ReviewAgent,
    TitleAgent,
    TrendAgent,
    WriterAgent,
)
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import LowQualityAbortException
from toutiao_content_factory.models import CacheContext, ExtractionResult, InsightReport, WorkflowResult
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.content_guard_service import ContentGuardService
from toutiao_content_factory.services.letta_client import LettaClient
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


class AgentWorkflowService:
    LOW_QUALITY_THRESHOLD = 60
    ARTICLE_MIN_CHARS = 1500
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
        self.insight_agent = InsightAgent(settings, prompt_repository, openai_service, logger)
        self.data_mining_agent = DataMiningAgent(settings, prompt_repository, openai_service, logger)
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
            if self.settings.enhanced_insights_enabled:
                try:
                    memory_context = self._get_memory_context(extraction.topic_title)
                    if memory_context:
                        self.logger.info("Letta memory context loaded: %d chars", len(memory_context))
                    self.logger.info("Running InsightAgent for enhanced opinions...")
                    insight_report = self.insight_agent.run(
                        extraction, trend_report, data_report, style, cache_context
                    )
                    self.logger.info(f"Insight generated: {insight_report.main_insight[:80]}...")
                except Exception as exc:
                    self.logger.warning(f"InsightAgent failed, falling back: %s", exc)
                    insight_report = None

            if insight_report:
                content = self.writer_agent.run_with_insights(
                    trend_report, data_report, insight_report, style, cache_context
                )
            else:
                content = self.writer_agent.run(trend_report, data_report, style, cache_context)

            polish_pack = self.polish_agent.run(content, trend_report, data_report, style, cache_context)
            self._apply_polish_pack(content, polish_pack, data_report)

            title_pack = self.title_agent.run(content, trend_report, data_report, style, cache_context)
            self._apply_title_pack(content, title_pack)
            self._filter_titles_by_quality(content)

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

                    title_pack = self.title_agent.run(content, trend_report, data_report, style, cache_context)
                    self._apply_title_pack(content, title_pack)
                    self._filter_titles_by_quality(content)

                    review_pack = self.review_agent.run(content, data_report, style, cache_context)
                    self._apply_review_pack(content, review_pack, cache_context)
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

        self._save_memory_observation(
            f"Run completed: topic selected, article generated and published"
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

    def _ensure_article_min_chars(self, article: str, data_report, min_chars: int = None) -> str:
        min_chars = min_chars if min_chars is not None else self.ARTICLE_MIN_CHARS
        current = len(article.replace("\n", "").replace(" ", ""))
        if current >= min_chars:
            return article
        self.logger.warning(
            "Article has only %d chars (minimum %d). Appending supplementary paragraphs.",
            current,
            min_chars,
        )
        paragraphs = article.split("\n\n")
        supplements = self._build_supplementary_paragraphs(data_report)
        for sup in supplements:
            paragraphs.insert(-1, sup)
            total = len("\n\n".join(paragraphs).replace("\n", "").replace(" ", ""))
            if total >= min_chars:
                break
        result = "\n\n".join(paragraphs)
        result_chars = len(result.replace("\n", "").replace(" ", ""))
        if result_chars < min_chars and self.openai_service.available:
            self.logger.info("Template supplements brought article to %d chars, still below %d. Attempting LLM expansion.", result_chars, min_chars)
            result = self._expand_article_via_llm(result, data_report, min_chars)
        return result

    def _expand_article_via_llm(self, article: str, data_report, min_chars: int) -> str:
        current_chars = len(article.replace("\n", "").replace(" ", ""))
        deficit = min_chars - current_chars
        if deficit <= 0:
            return article
        data_parts = []
        if hasattr(data_report, "watch_metrics") and data_report.watch_metrics:
            data_parts.append("跟踪变量：" + "、".join(str(m) for m in data_report.watch_metrics[:5]))
        if hasattr(data_report, "risk_flags") and data_report.risk_flags:
            data_parts.append("风险因素：" + "、".join(str(r) for r in data_report.risk_flags[:3]))
        data_summary = "\n".join(data_parts) if data_parts else "无额外数据"
        system_prompt = (
            "你是文章扩展助手。在现有文章基础上补充深度内容，禁止使用套话。"
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
                new_paragraphs = [str(p).strip() for p in payload["paragraphs"] if str(p).strip()]
                if new_paragraphs:
                    existing = article.split("\n\n")
                    for p in new_paragraphs:
                        existing.insert(-1, p)
                    result = "\n\n".join(existing)
                    self.logger.info("LLM expansion added %d paragraphs, article now %d chars.", len(new_paragraphs), len(result.replace("\n", "").replace(" ", "")))
                    return result
        except Exception as exc:
            self.logger.warning("LLM expansion failed: %s", exc)
        return article

    @staticmethod
    def _build_supplementary_paragraphs(data_report) -> list[str]:
        paragraphs = []
        if hasattr(data_report, "watch_metrics") and data_report.watch_metrics:
            items = data_report.watch_metrics[:5]
            if items:
                text = "接下来该盯什么？" + "、".join(str(m) for m in items) + "——这几个指标的变化，基本决定了产业链各环节的盈利兑现节奏。"
                paragraphs.append(text)
        if hasattr(data_report, "risk_flags") and data_report.risk_flags:
            items = data_report.risk_flags[:3]
            if items:
                text = "但风险也在积累：" + "；".join(str(r) for r in items) + "。任何一个环节的延迟，都可能拖慢整条链的兑现节奏。"
                paragraphs.append(text)
        if hasattr(data_report, "evidence_notes") and data_report.evidence_notes:
            items = data_report.evidence_notes[:3]
            if items:
                text = "目前能确认的数据有限，" + "；".join(str(e) for e in items) + "。口径以公开材料为准，等最新财报出来再更新判断。"
                paragraphs.append(text)
        if hasattr(data_report, "growth_numbers") and data_report.growth_numbers and len(data_report.growth_numbers) >= 2:
            text = f"两个核心数字：{data_report.growth_numbers[0]}和{data_report.growth_numbers[1]}。后续如果这两个指标出现明显变化，利润分配逻辑可能要重新算。"
            paragraphs.append(text)
        if len(paragraphs) < 3:
            paragraphs.append(
                "从产业链角度看，核心环节的利润兑现节奏取决于下游需求能否持续放量。"
                "目前行业仍处于从订单验证到批量交付的过渡期，部分企业已率先在零部件环节实现收入确认，"
                "但整体产能利用率仍有提升空间。后续需要关注头部企业的季度出货数据变化，"
                "以及政策端对相关赛道的支持力度是否出现边际调整。"
            )
        if len(paragraphs) < 4:
            paragraphs.append(
                "产业链利润分配并非静态格局。"
                "随着技术路线迭代和客户结构变化，不同环节的议价能力会发生此消彼长的变化。"
                "每一轮产业升级都会重塑利润分配曲线，关键在于识别哪些环节能率先完成从量到价的切换。"
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

    def _ensure_article_min_chars(self, article: str, data_report, min_chars: int = None) -> str:
        min_chars = min_chars if min_chars is not None else self.ARTICLE_MIN_CHARS
        if not self._has_grounded_evidence(data_report):
            min_chars = min(min_chars, max(950, int((getattr(data_report, "word_target", 0) or 1200) * 0.7)))
        current = len(article.replace("\n", "").replace(" ", ""))
        if current >= min_chars:
            return article
        self.logger.warning(
            "Article has only %d chars (minimum %d). Appending grounded supplementary paragraphs.",
            current,
            min_chars,
        )
        paragraphs = article.split("\n\n")
        supplements = self._build_supplementary_paragraphs(data_report)
        for sup in supplements:
            paragraphs.insert(-1, sup)
            total = len("\n\n".join(paragraphs).replace("\n", "").replace(" ", ""))
            if total >= min_chars:
                break
        result = "\n\n".join(paragraphs)
        result_chars = len(result.replace("\n", "").replace(" ", ""))
        if result_chars < min_chars and self.openai_service.available and self._has_grounded_evidence(data_report):
            self.logger.info(
                "Supplementary paragraphs brought article to %d chars, still below %d. Attempting grounded LLM expansion.",
                result_chars,
                min_chars,
            )
            result = self._expand_article_via_llm(result, data_report, min_chars)
        return result

    def _expand_article_via_llm(self, article: str, data_report, min_chars: int) -> str:
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
                new_paragraphs = [str(p).strip() for p in payload["paragraphs"] if str(p).strip()]
                if new_paragraphs:
                    existing = article.split("\n\n")
                    for p in new_paragraphs:
                        if any(char.isdigit() for char in p) and not getattr(data_report, "growth_numbers", []):
                            continue
                        existing.insert(-1, p)
                    result = "\n\n".join(existing)
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

    def _apply_polish_pack(self, content, polish_pack, data_report, update_validation: bool = True) -> None:
        content.polished_article = str(polish_pack.get("polished_article", "")).strip()
        if content.polished_article:
            content.article = content.polished_article
        content.article = self._ensure_article_min_chars(
            content.article, data_report, min_chars=self.ARTICLE_MIN_CHARS
        )
        content.polished_article = content.article
        if update_validation:
            content.validation_notes = list(polish_pack.get("validation_notes", []))
            content.validation_score = int(polish_pack.get("validation_score", 0))

    def _apply_title_pack(self, content, title_pack) -> None:
        content.titles = title_pack["titles"]
        content.recommended_titles = title_pack["recommended_titles"]

    def _filter_titles_by_quality(self, content) -> None:
        """标题质量硬过滤（RULE_006/007 落地）

        在 TitleAgent 生成候选后，剔除模板滥用、纯抽象、标题-正文不一致的标题。
        若全部被过滤，保留原列表并记录 warning，避免空列表导致后续流程崩溃。
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
                rejected.append(f"{title} -> {'; '.join(issues)}")
            else:
                kept_titles.append(title)

        if kept_titles:
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
        else:
            # 全部被过滤，保留原列表避免空标题，但记录 warning 供人工介入
            self.logger.warning(
                "标题质量硬过滤：全部 %d 个候选标题均未通过 RULE_006/007 校验，保留原列表。明细：%s",
                len(original_titles),
                " | ".join(rejected),
            )

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

