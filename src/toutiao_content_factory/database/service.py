"""Database service — CRUD operations and session management.

Provides a thread-safe database service that wraps SQLModel/SQLAlchemy
for persistent storage. Falls back gracefully if sqlmodel is not installed.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# Try to import sqlmodel; define fallback if not available
try:
    from sqlmodel import Session, SQLModel, create_engine, select
    HAS_SQLMODEL = True
except ModuleNotFoundError:
    HAS_SQLMODEL = False
    Session = None
    create_engine = None

from toutiao_content_factory.database.models import (
    Article,
    PipelineStage,
    PublishPlatform,
    RunRecord,
    Topic,
)


class DatabaseService:
    """Thread-safe database service using SQLModel + SQLite.

    Provides CRUD operations for articles, topics, and run records.
    Falls back to no-op if sqlmodel is not installed, allowing
    the application to continue functioning with file-based storage.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        echo: bool = False,
        auto_create_tables: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = None

        if not HAS_SQLMODEL:
            logger.warning(
                "sqlmodel is not installed. DatabaseService will be a no-op. "
                "Install with: pip install sqlmodel"
            )
            return

        db_url = f"sqlite:///{self.db_path.resolve()}"
        self._engine = create_engine(
            db_url,
            echo=echo,
            connect_args={"check_same_thread": False},  # Allow multi-thread access
        )

        if auto_create_tables:
            SQLModel.metadata.create_all(self._engine)
            logger.info("Database initialized: %s (tables: topics, articles, run_records)", self.db_path)

    @property
    def available(self) -> bool:
        """Check if the database backend is available."""
        return HAS_SQLMODEL and self._engine is not None

    # ====================================================================
    # Session management
    # ====================================================================

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        """Yield a database session with auto-commit/rollback.

        After commit, all returned SQLModel objects are expunged from
        the session so their attributes remain accessible after close.
        """
        if not self.available:
            logger.debug("Database session requested but sqlmodel not available.")
            yield None  # type: ignore
            return

        session = Session(self._engine, expire_on_commit=False)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ====================================================================
    # Article CRUD
    # ====================================================================

    def save_article(self, article: Article) -> Optional[Article]:
        """Insert or update an article record."""
        if not self.available:
            return None
        with self._session() as session:
            if session is None:
                return None
            existing = session.exec(
                select(Article).where(Article.run_name == article.run_name)
            ).first()
            if existing:
                article.id = existing.id
                article.updated_at = datetime.now()
                session.merge(article)
            else:
                session.add(article)
            session.flush()
            return article

    def get_article(self, run_name: str) -> Optional[Article]:
        """Get an article by run_name."""
        if not self.available:
            return None
        with self._session() as session:
            if session is None:
                return None
            article = session.exec(
                select(Article).where(Article.run_name == run_name)
            ).first()
            return article

    def list_articles(
        self,
        account_key: str | None = None,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[Article]:
        """List articles with optional filters."""
        if not self.available:
            return []
        with self._session() as session:
            if session is None:
                return []
            query = select(Article)
            if account_key:
                query = query.where(Article.account_key == account_key)
            if status:
                query = query.where(Article.pipeline_stage == status)
            query = query.order_by(Article.created_at.desc()).offset(offset).limit(limit)
            return list(session.exec(query).all())

    def update_article_stage(
        self,
        run_name: str,
        stage: PipelineStage | str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Update the pipeline stage of an article."""
        if not self.available:
            return
        stage_value = stage.value if isinstance(stage, PipelineStage) else str(stage)
        with self._session() as session:
            if session is None:
                return
            article = session.exec(
                select(Article).where(Article.run_name == run_name)
            ).first()
            if article:
                article.pipeline_stage = stage_value
                article.updated_at = datetime.now()
                if extra:
                    for key, value in extra.items():
                        if hasattr(article, key):
                            setattr(article, key, value)

    # ====================================================================
    # Run Record CRUD
    # ====================================================================

    def save_run_record(self, record: RunRecord) -> Optional[RunRecord]:
        """Insert or update a run record."""
        if not self.available:
            return None
        with self._session() as session:
            if session is None:
                return None
            existing = session.exec(
                select(RunRecord).where(
                    RunRecord.run_name == record.run_name,
                    RunRecord.account_key == record.account_key,
                )
            ).first()
            if existing:
                record.id = existing.id
                record.updated_at = datetime.now()
                session.merge(record)
            else:
                session.add(record)
            session.flush()
            return record

    def get_run_record(self, run_name: str, account_key: str = "default") -> Optional[RunRecord]:
        """Get a run record."""
        if not self.available:
            return None
        with self._session() as session:
            if session is None:
                return None
            record = session.exec(
                select(RunRecord).where(
                    RunRecord.run_name == run_name,
                    RunRecord.account_key == account_key,
                )
            ).first()
            return record

    def list_run_records(
        self,
        account_key: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[RunRecord]:
        """List run records."""
        if not self.available:
            return []
        with self._session() as session:
            if session is None:
                return []
            query = select(RunRecord)
            if account_key:
                query = query.where(RunRecord.account_key == account_key)
            query = query.order_by(RunRecord.created_at.desc()).offset(offset).limit(limit)
            records = list(session.exec(query).all())
            return records

    def mark_run_completed(
        self,
        run_name: str,
        account_key: str = "default",
        status: str = "published",
    ) -> None:
        """Mark a run as completed."""
        if not self.available:
            return
        with self._session() as session:
            if session is None:
                return
            record = session.exec(
                select(RunRecord).where(
                    RunRecord.run_name == run_name,
                    RunRecord.account_key == account_key,
                )
            ).first()
            if record:
                record.status = status
                record.pipeline_stage = PipelineStage.PUBLISHED.value
                record.completed_at = datetime.now()
                record.updated_at = datetime.now()

    # ====================================================================
    # Topic CRUD
    # ====================================================================

    TITLE_SIMILARITY_THRESHOLD = 0.7

    def save_topic(self, topic: Topic) -> Optional[Topic]:
        """Insert or update a topic."""
        if not self.available:
            return None
        with self._session() as session:
            if session is None:
                return None
            # Check duplicate by URL
            if topic.url:
                existing = session.exec(
                    select(Topic).where(
                        Topic.url == topic.url,
                        Topic.account_key == topic.account_key,
                    )
                ).first()
                if existing:
                    return existing
            # Check duplicate by title similarity
            if topic.title and topic.account_key:
                similar = self._find_similar_topic(session, topic.title, topic.account_key)
                if similar:
                    return similar
            session.add(topic)
            session.flush()
            return topic

    def _find_similar_topic(self, session, title: str, account_key: str) -> Optional[Topic]:
        candidates = session.exec(
            select(Topic).where(Topic.account_key == account_key)
        ).all()
        for candidate in candidates:
            if not candidate.title:
                continue
            ratio = SequenceMatcher(None, title, candidate.title).ratio()
            if ratio >= self.TITLE_SIMILARITY_THRESHOLD:
                return candidate
        return None

    def list_topics(
        self,
        account_key: str | None = None,
        unused_only: bool = False,
        limit: int = 50,
    ) -> list[Topic]:
        """List topics."""
        if not self.available:
            return []
        with self._session() as session:
            if session is None:
                return []
            query = select(Topic)
            if account_key:
                query = query.where(Topic.account_key == account_key)
            if unused_only:
                query = query.where(Topic.used == False)
            query = query.order_by(Topic.total_score.desc()).limit(limit)
            return list(session.exec(query).all())

    # ====================================================================
    # Health / Stats
    # ====================================================================

    def get_stats(self, account_key: str | None = None) -> dict[str, Any]:
        """Get database statistics."""
        stats: dict[str, Any] = {
            "available": self.available,
            "db_path": str(self.db_path),
            "articles": 0,
            "topics": 0,
            "run_records": 0,
        }
        if not self.available:
            return stats
        with self._session() as session:
            if session is None:
                return stats
            query = select(Article)
            if account_key:
                query = query.where(Article.account_key == account_key)
            stats["articles"] = len(list(session.exec(query).all()))

            query = select(Topic)
            if account_key:
                query = query.where(Topic.account_key == account_key)
            stats["topics"] = len(list(session.exec(query).all()))

            query = select(RunRecord)
            if account_key:
                query = query.where(RunRecord.account_key == account_key)
            stats["run_records"] = len(list(session.exec(query).all()))

        return stats
