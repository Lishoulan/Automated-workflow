from __future__ import annotations

import logging

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.benchmark_hotspot_service import BenchmarkHotspotService


class BenchmarkMonitorAgent:
    agent_name = "BenchmarkMonitorAgent"

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.service = BenchmarkHotspotService(settings, logger)

    def run(
        self,
        *,
        platform: str | None = None,
        track: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        self.logger.info(
            "%s collecting benchmark hot topics: platform=%s track=%s limit=%s",
            self.agent_name,
            platform or self.settings.platform,
            track or "ALL",
            limit,
        )
        return self.service.collect(platform=platform, track=track, limit=limit)
