from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from toutiao_content_factory.services.pipeline_state_manager import (
    PipelineStage,
    PipelineStateManager,
)


@pytest.fixture()
def state_manager(tmp_path: Path) -> PipelineStateManager:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return PipelineStateManager(output_dir)


class TestSaveAndLoadState:
    def test_save_and_load(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-001", "CONTENT_GENERATED")
        loaded = state_manager.load_state("run-001")
        assert loaded is not None
        assert loaded["state"] == "CONTENT_GENERATED"
        assert loaded["run_name"] == "run-001"

    def test_load_nonexistent(self, state_manager: PipelineStateManager) -> None:
        assert state_manager.load_state("nonexistent") is None

    def test_save_with_extra_data(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state(
            "run-002", "PUBLISHED", extra_data={"article_title": "测试文章"}
        )
        loaded = state_manager.load_state("run-002")
        assert loaded is not None
        assert loaded["extra_data"]["article_title"] == "测试文章"


class TestSaveAndLoadStage:
    def test_save_and_load_stage(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_stage("run-003", PipelineStage.SIGNALS_EXTRACTED)
        # Verify via load_stage (DB is primary)
        loaded = state_manager.load_stage("run-003")
        assert loaded == "SIGNALS_EXTRACTED"

    def test_load_stage_nonexistent(self, state_manager: PipelineStateManager) -> None:
        assert state_manager.load_stage("nonexistent") is None

    def test_save_stage_string(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_stage("run-004", "REVIEWED")
        loaded = state_manager.load_stage("run-004")
        assert loaded == "REVIEWED"


class TestMarkFailed:
    def test_mark_failed(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-005", "CONTENT_GENERATED")
        state_manager.mark_failed("run-005", "writer", "写入失败")

        loaded = state_manager.load_state("run-005")
        assert loaded is not None
        assert loaded["state"] == "FAILED"
        assert loaded["extra_data"]["failed_stage"] == "writer"
        assert loaded["extra_data"]["error_message"] == "写入失败"

    def test_mark_failed_preserves_previous_extra(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-006", "REVIEWED", extra_data={"key1": "value1"})
        state_manager.mark_failed("run-006", "review", "审核错误")

        loaded = state_manager.load_state("run-006")
        assert loaded is not None
        assert loaded["extra_data"]["key1"] == "value1"
        assert loaded["extra_data"]["failed_stage"] == "review"


class TestMarkSuccess:
    def test_mark_success(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-007", "PUBLISHING")
        state_manager.mark_success("run-007", "测试文章标题")

        loaded = state_manager.load_state("run-007")
        assert loaded is not None
        assert loaded["state"] == "PUBLISHED"
        assert loaded["extra_data"]["article_title"] == "测试文章标题"
        assert loaded["extra_data"]["declared_toutiao_first_release"] is True

    def test_mark_success_not_first_release(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-008", "PUBLISHING")
        state_manager.mark_success("run-008", "测试文章", declared_first_release=False)

        loaded = state_manager.load_state("run-008")
        assert loaded is not None
        assert loaded["extra_data"]["declared_toutiao_first_release"] is False
        assert loaded["extra_data"]["toutiao_published_at"] is None


class TestCanPublishToOtherPlatforms:
    def test_no_state_allows_publish(self, state_manager: PipelineStateManager) -> None:
        assert state_manager.can_publish_to_other_platforms("nonexistent") is True

    def test_not_first_release_allows_publish(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-010", "PUBLISHED")
        state_manager.mark_success("run-010", "文章", declared_first_release=False)
        assert state_manager.can_publish_to_other_platforms("run-010") is True

    def test_first_release_within_72h_blocks(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-011", "PUBLISHED")
        state_manager.mark_success("run-011", "文章", declared_first_release=True)
        # Just marked as first release, should be within 72h
        assert state_manager.can_publish_to_other_platforms("run-011") is False

    def test_first_release_after_72h_allows(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-012", "PUBLISHED")
        state_manager.mark_success("run-012", "文章", declared_first_release=True)

        # Manually modify the state in DB to simulate 72h+ elapsed
        loaded = state_manager.load_state("run-012")
        assert loaded is not None
        past_time = (datetime.now() - timedelta(hours=73)).isoformat(timespec="seconds")
        loaded["extra_data"]["toutiao_published_at"] = past_time
        # Re-save the state with modified extra_data
        state_manager.save_state("run-012", "PUBLISHED", extra_data=loaded["extra_data"])

        assert state_manager.can_publish_to_other_platforms("run-012") is True

    def test_no_toutiao_published_at_allows(self, state_manager: PipelineStateManager) -> None:
        state_manager.save_state("run-013", "PUBLISHED", extra_data={
            "declared_toutiao_first_release": True,
            "toutiao_published_at": None,
        })
        assert state_manager.can_publish_to_other_platforms("run-013") is True
