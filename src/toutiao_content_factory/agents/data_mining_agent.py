from __future__ import annotations

import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import (
    DataInsightReport,
    ExtractionResult,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class DataMiningAgent(BaseAgent):
    agent_name = "DataMiningAgent"

    def run(
        self,
        extraction: ExtractionResult,
        style: StyleProfile,
    ) -> DataInsightReport:
        prompt = self.render_prompt(
            "data_mining_prompt.md",
            merged_text=extraction.merged_text[:3000],
            core_data=self.json_text(extraction.core_data),
            growth_numbers=self.json_text(extraction.growth_numbers),
        )
        system_prompt = "你是 Data Mining Agent，负责挖掘数据洞察。只输出 JSON。"
        payload = self.request_json(system_prompt, prompt)
        if payload:
            return DataInsightReport.from_dict(payload)
        return self._fallback(extraction)

    def _fallback(
        self,
        extraction: ExtractionResult,
    ) -> DataInsightReport:
        return DataInsightReport(
            extracted_metrics=[],
            trends=extraction.industry_trends[:3],
            anomalies=[],
            correlations=[],
            data_conclusions=extraction.key_points[:3],
        )
