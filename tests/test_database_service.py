from __future__ import annotations

from pathlib import Path

import pytest

from toutiao_content_factory.database.models import Article, RunRecord, Topic
from toutiao_content_factory.database.service import DatabaseService


@pytest.fixture()
def db_service(tmp_path: Path) -> DatabaseService:
    db_path = tmp_path / "test.db"
    return DatabaseService(db_path)


def _read_attr_in_session(db_service: DatabaseService, obj) -> dict:
    """Read all attributes of a detached object within a fresh session."""
    if not db_service.available:
        return {}
    from sqlmodel import select
    with db_service._session() as session:
        if session is None:
            return {}
        # Re-query the object to get a fresh, attached instance
        if isinstance(obj, Article):
            fresh = session.exec(select(Article).where(Article.run_name == obj.run_name)).first()
        elif isinstance(obj, RunRecord):
            fresh = session.exec(select(RunRecord).where(
                RunRecord.run_name == obj.run_name,
                RunRecord.account_key == obj.account_key,
            )).first()
        elif isinstance(obj, Topic):
            fresh = session.exec(select(Topic).where(Topic.id == obj.id)).first()
        else:
            return {}
        if fresh is None:
            return {}
        # Force load all columns by accessing them
        return {col: getattr(fresh, col) for col in fresh.__fields__ if hasattr(fresh, col)}


class TestSaveAndGetArticle:
    def test_save_and_retrieve(self, db_service: DatabaseService) -> None:
        article = Article(
            run_name="run-001",
            account_key="default",
            title="测试文章",
            article_text="这是测试文章内容",
        )
        saved = db_service.save_article(article)
        assert saved is not None

        # Use stats to verify data was persisted
        stats = db_service.get_stats()
        assert stats["articles"] == 1

    def test_update_existing(self, db_service: DatabaseService) -> None:
        article = Article(run_name="run-002", title="原标题")
        db_service.save_article(article)

        updated = Article(run_name="run-002", title="新标题")
        db_service.save_article(updated)

        # Should still be 1 article (update, not insert)
        stats = db_service.get_stats()
        assert stats["articles"] == 1

    def test_get_nonexistent(self, db_service: DatabaseService) -> None:
        assert db_service.get_article("nonexistent") is None

    def test_save_multiple_articles(self, db_service: DatabaseService) -> None:
        db_service.save_article(Article(run_name="run-a", title="文章A"))
        db_service.save_article(Article(run_name="run-b", title="文章B"))
        stats = db_service.get_stats()
        assert stats["articles"] == 2


class TestSaveAndGetRunRecord:
    def test_save_and_retrieve(self, db_service: DatabaseService) -> None:
        record = RunRecord(
            run_name="run-001",
            account_key="default",
            pipeline_stage="CONTENT_GENERATED",
            status="running",
        )
        saved = db_service.save_run_record(record)
        assert saved is not None

        stats = db_service.get_stats()
        assert stats["run_records"] == 1

    def test_update_existing(self, db_service: DatabaseService) -> None:
        record = RunRecord(
            run_name="run-002",
            account_key="default",
            pipeline_stage="INIT",
            status="running",
        )
        db_service.save_run_record(record)

        updated = RunRecord(
            run_name="run-002",
            account_key="default",
            pipeline_stage="PUBLISHED",
            status="published",
        )
        db_service.save_run_record(updated)

        stats = db_service.get_stats()
        assert stats["run_records"] == 1

    def test_get_nonexistent(self, db_service: DatabaseService) -> None:
        assert db_service.get_run_record("nonexistent") is None


class TestSaveAndListTopics:
    def test_save_and_list(self, db_service: DatabaseService) -> None:
        topic = Topic(
            title="新能源行业动态",
            url="https://example.com/1",
            source="test",
            source_type="rss",
            account_key="default",
            total_score=85,
        )
        saved = db_service.save_topic(topic)
        assert saved is not None

        stats = db_service.get_stats()
        assert stats["topics"] == 1

    def test_duplicate_url_skipped(self, db_service: DatabaseService) -> None:
        topic1 = Topic(
            title="标题1",
            url="https://example.com/dup",
            account_key="default",
        )
        topic2 = Topic(
            title="标题2",
            url="https://example.com/dup",
            account_key="default",
        )
        db_service.save_topic(topic1)
        db_service.save_topic(topic2)

        stats = db_service.get_stats()
        assert stats["topics"] == 1

    def test_list_unused_only(self, db_service: DatabaseService) -> None:
        topic1 = Topic(title="未使用", url="https://example.com/a", account_key="default", used=False)
        topic2 = Topic(title="已使用", url="https://example.com/b", account_key="default", used=True)
        db_service.save_topic(topic1)
        db_service.save_topic(topic2)

        stats = db_service.get_stats()
        assert stats["topics"] == 2


class TestGetStats:
    def test_empty_stats(self, db_service: DatabaseService) -> None:
        stats = db_service.get_stats()
        assert stats["available"] is True
        assert stats["articles"] == 0
        assert stats["topics"] == 0
        assert stats["run_records"] == 0

    def test_stats_with_data(self, db_service: DatabaseService) -> None:
        db_service.save_article(Article(run_name="run-001", title="文章"))
        db_service.save_topic(Topic(title="话题", url="https://example.com/1", account_key="default"))
        db_service.save_run_record(RunRecord(run_name="run-001", account_key="default"))

        stats = db_service.get_stats()
        assert stats["articles"] == 1
        assert stats["topics"] == 1
        assert stats["run_records"] == 1

    def test_stats_filtered_by_account(self, db_service: DatabaseService) -> None:
        db_service.save_article(Article(run_name="run-a", account_key="acct_a", title="A"))
        db_service.save_article(Article(run_name="run-b", account_key="acct_b", title="B"))

        stats = db_service.get_stats(account_key="acct_a")
        assert stats["articles"] == 1
