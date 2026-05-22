from __future__ import annotations

import logging

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


class SampleCollectorAgent(BaseAgent):
    agent_name = "SampleCollectorAgent"

    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        super().__init__(settings, prompt_repository, openai_service, logger)

    def run(
        self,
        *,
        account_key: str | None = None,
        track: str | None = None,
        top_n: int = 10,
    ) -> dict[str, object]:
        self.logger.info(
            "%s 收集真实高表现样本：account=%s track=%s top_n=%s",
            self.agent_name,
            account_key or "ALL",
            track or "ALL",
            top_n,
        )
        exports = self.growth_ops_service.collect_self_benchmark_samples(
            account_key=account_key,
            track=track,
            top_n=top_n,
        )
        exports.update(self.growth_ops_service.build_benchmark_report())
        return exports
