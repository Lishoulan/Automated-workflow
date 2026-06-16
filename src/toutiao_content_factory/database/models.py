"""SQLModel database models for AI Content Factory.

Replaces file-based JSON/YAML storage with a lightweight SQLite database,
enabling concurrent multi-account access, efficient queries, and data integrity.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from sqlmodel import Field, SQLModel, Column, DateTime, JSON, Integer, String, Text


# ========================================================================
# Enums
# ========================================================================

class PipelineStage(str, Enum):
    INIT = "INIT"
    DOCUMENTS_LOADED = "DOCUMENTS_LOADED"
    SIGNALS_EXTRACTED = "SIGNALS_EXTRACTED"
    CONTENT_GENERATED = "CONTENT_GENERATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"

    @classmethod
    def _missing_(cls, value: Any) -> PipelineStage:
        return cls.INIT


class PublishPlatform(str, Enum):
    TOUTIAO = "toutiao"
    WECHAT = "wechat"

    @classmethod
    def _missing_(cls, value: Any) -> PublishPlatform:
        return cls.TOUTIAO


# ========================================================================
# SQLModel Tables
# ========================================================================

class Topic(SQLModel, table=True):
    """Persistent topic candidate storage."""
    __tablename__: str = "topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    url: str = Field(default="", max_length=1024)
    source: str = Field(default="", max_length=128)
    source_type: str = Field(default="", max_length=64)
    snippet: str = Field(default="", sa_type=Text)
    summary: str = Field(default="", sa_type=Text)
    heat_score: int = Field(default=0)
    total_score: int = Field(default=0)
    quality_score: int = Field(default=0)
    topic_type: str = Field(default="", max_length=64)
    matched_industries: str = Field(default="", sa_type=Text, description="JSON list")
    matched_keywords: str = Field(default="", sa_type=Text, description="JSON list")
    raw_metadata: str = Field(default="", sa_type=Text, description="JSON dict")

    account_key: str = Field(default="default", index=True, max_length=64)
    created_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime(), nullable=False))
    used: bool = Field(default=False)
    used_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(), nullable=True))


class Article(SQLModel, table=True):
    """Persistent article storage."""
    __tablename__: str = "articles"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_name: str = Field(index=True, max_length=128, unique=True)
    account_key: str = Field(default="default", index=True, max_length=64)

    title: str = Field(default="", max_length=512)
    recommended_titles: str = Field(default="", sa_type=Text, description="JSON list")
    article_text: str = Field(default="", sa_type=Text)
    polished_article: str = Field(default="", sa_type=Text)
    summary: str = Field(default="", sa_type=Text)
    tags: str = Field(default="", sa_type=Text, description="JSON list")
    topic_title: str = Field(default="", max_length=512)
    topic_url: str = Field(default="", max_length=1024)

    platform: str = Field(default="toutiao", max_length=32)
    cover_prompt: str = Field(default="", sa_type=Text)
    model_used: str = Field(default="", max_length=128)
    estimated_cost: float = Field(default=0.0)

    # Pipeline state
    pipeline_stage: str = Field(default=PipelineStage.INIT.value, max_length=32)
    review_status: str = Field(default="pending", max_length=32)
    overall_score: int = Field(default=0)
    ai_risk_score: int = Field(default=0)
    validation_score: int = Field(default=0)
    publish_readiness_score: int = Field(default=0)
    review_notes: str = Field(default="", sa_type=Text, description="JSON list")
    publish_result: str = Field(default="", sa_type=Text, description="JSON dict")
    draft_status: str = Field(default="draft", max_length=32)
    cache_note: str = Field(default="", sa_type=Text)

    created_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime(), nullable=False))
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime(), nullable=False))
    published_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(), nullable=True))


class RunRecord(SQLModel, table=True):
    """Persistent run record for the automation scheduler."""
    __tablename__: str = "run_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_name: str = Field(index=True, max_length=128)
    account_key: str = Field(default="default", index=True, max_length=64)

    pipeline_stage: str = Field(default=PipelineStage.INIT.value, max_length=32)
    status: str = Field(default="running", max_length=32)
    error_message: str = Field(default="", sa_type=Text)
    failed_stage: str = Field(default="", max_length=64)

    # Platform publish status
    toutiao_status: str = Field(default="", max_length=32)
    wechat_status: str = Field(default="", max_length=32)
    toutiao_url: str = Field(default="", max_length=1024)

    # Debug info
    debug_snapshot: str = Field(default="", sa_type=Text, description="JSON dict")

    # Full pipeline state (JSON dict with workflow_result, extra_data, etc.)
    state_data: str = Field(default="", sa_type=Text, description="JSON dict of full pipeline state")

    created_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime(), nullable=False))
    updated_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime(), nullable=False))
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(), nullable=True))
