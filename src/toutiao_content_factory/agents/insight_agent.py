from __future__ import annotations

import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import (
    CacheContext,
    DataReport,
    ExtractionResult,
    InsightReport,
    TrendReport,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class InsightAgent(BaseAgent):
    agent_name = "InsightAgent"

    def run(
        self,
        extraction: ExtractionResult,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
    ) -> InsightReport:
        benchmark_digest = self.benchmark_strategy_digest(
            purpose="insight",
            track_hint=extraction.focus_industry or trend_report.recommended_angle,
            title_hint=extraction.topic_title,
            focus_industry=extraction.focus_industry,
            keywords=extraction.focus_keywords + extraction.supply_chain_keywords,
            angle_hint=trend_report.recommended_angle,
            extra_text=extraction.writing_brief,
        )
        prompt = self.render_prompt(
            "insight_prompt.md",
            topic_title=extraction.topic_title,
            core_data=self.json_text(data_report.core_data),
            industry_trends=self.json_text(data_report.industry_trends),
            growth_numbers=self.json_text(data_report.growth_numbers),
            key_points=self.json_text(data_report.key_points),
            watch_metrics=self.json_text(data_report.watch_metrics),
            style_digest=self.style_digest(style),
            benchmark_digest=benchmark_digest,
            best_practices=self.best_practices_instruction(),
            cache_note=self.cache_note(cache_context),
        )
        system_prompt = "你是 Insight Agent，专业生成深度观点。只输出 JSON。"
        payload = self.request_json(system_prompt, prompt)
        if payload:
            return InsightReport.from_dict(payload)
        return self._fallback(extraction, trend_report, data_report)

    def _fallback(
        self,
        extraction: ExtractionResult,
        trend_report: TrendReport,
        data_report: DataReport,
    ) -> InsightReport:
        return InsightReport(
            main_insight=extraction.suggested_angle or "基于当前信息分析",
            supporting_data=data_report.core_data[:3],
            alternative_angles=["从技术角度分析", "从商业角度分析"],
            counterarguments=data_report.risk_flags[:2],
            prediction="持续关注后续发展",
            confidence_score=0.5,
        )
