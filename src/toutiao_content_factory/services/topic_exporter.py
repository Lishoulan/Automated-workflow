from __future__ import annotations

import json
import logging
from pathlib import Path

from toutiao_content_factory.models import DailyTopicsReport
from toutiao_content_factory.services.topic_pool_service import TopicPoolService


class TopicExportService:
    def __init__(
        self,
        output_dir: Path,
        logger: logging.Logger,
        *,
        topic_pool_service: TopicPoolService | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.logger = logger
        self.topic_pool_service = topic_pool_service

    def export(self, report: DailyTopicsReport) -> dict[str, Path]:
        topics_path = self.output_dir / "daily_topics.json"
        topics_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        exported = {"daily_topics": topics_path}
        if self.topic_pool_service is not None:
            exported.update(self.topic_pool_service.ingest_report(report))
        self.logger.info("热点选题导出完成: %s", exported)
        return exported
