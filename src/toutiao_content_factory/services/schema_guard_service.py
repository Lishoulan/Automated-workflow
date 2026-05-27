from __future__ import annotations

from typing import Any

from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate


class SchemaGuardService:
    @staticmethod
    def validate_daily_topics_report(payload: dict[str, Any]) -> DailyTopicsReport:
        report = DailyTopicsReport.from_dict(payload)
        for index, topic in enumerate(report.topics, start=1):
            SchemaGuardService.validate_topic_candidate(topic, topic_index=index)
        return report

    @staticmethod
    def validate_topic_candidate(topic: TopicCandidate, *, topic_index: int = 0) -> None:
        prefix = f"Topic #{topic_index}" if topic_index else "Topic"
        if not str(topic.title or "").strip():
            raise ValueError(f"{prefix} is missing title.")
        if not str(topic.source or "").strip():
            raise ValueError(f"{prefix} is missing source.")
        if len(str(topic.title)) > 120:
            raise ValueError(f"{prefix} title is unexpectedly long.")

    @staticmethod
    def validate_publish_meta(payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("selected_title", "")).strip()
        summary = str(payload.get("summary", "")).strip()
        if not title:
            raise ValueError("publish_meta.selected_title is required.")
        if len(title) > 64:
            raise ValueError("publish_meta.selected_title is too long.")
        if not summary:
            raise ValueError("publish_meta.summary is required.")
        if not isinstance(payload.get("tags", []), list):
            raise ValueError("publish_meta.tags must be a list.")
        selected_topic = payload.get("selected_topic")
        if selected_topic is not None and not isinstance(selected_topic, dict):
            raise ValueError("publish_meta.selected_topic must be an object when provided.")
        return payload
