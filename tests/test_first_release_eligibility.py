"""平台首发资格线与内部质量篇幅目标必须保持独立。"""

from __future__ import annotations

from toutiao_content_factory.agents.polish_agent import PolishAgent
from toutiao_content_factory.agents.review_agent import ReviewAgent
from toutiao_content_factory.agents.writer_agent import WriterAgent
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker


PLATFORM_FIRST_RELEASE_MIN_CHARS = 101
NARRATIVE_QUALITY_TARGET_MIN = 1500


def test_platform_minimum_matches_current_more_than_100_rule() -> None:
    assert ArticleQualityChecker.FIRST_RELEASE_MIN_CHARS == PLATFORM_FIRST_RELEASE_MIN_CHARS


def test_generation_keeps_a_depth_target_above_platform_minimum() -> None:
    ideal = WriterAgent.NARRATIVE_ARTICLE_IDEAL_CHARS
    floor = WriterAgent.NARRATIVE_ARTICLE_PROMPT_FLOOR

    assert ideal >= NARRATIVE_QUALITY_TARGET_MIN + 100
    assert NARRATIVE_QUALITY_TARGET_MIN <= floor < ideal


def test_publish_floor_is_content_quality_floor_not_platform_qualification() -> None:
    publish_floor = WriterAgent.NARRATIVE_ARTICLE_MIN_CHARS

    assert publish_floor > PLATFORM_FIRST_RELEASE_MIN_CHARS
    assert publish_floor < NARRATIVE_QUALITY_TARGET_MIN
    assert (
        publish_floor
        == AgentWorkflowService.NARRATIVE_ARTICLE_MIN_CHARS
        == ContentFactoryAppService.NARRATIVE_ARTICLE_MIN_CHARS
    )


def test_polish_target_preserves_room_for_incentive_quality() -> None:
    assert PolishAgent.NARRATIVE_POLISH_TARGET_MIN >= NARRATIVE_QUALITY_TARGET_MIN
    assert PolishAgent.NARRATIVE_POLISH_TARGET_MAX > PolishAgent.NARRATIVE_POLISH_TARGET_MIN


def test_review_word_bounds_admit_the_generation_target() -> None:
    target = WriterAgent.NARRATIVE_ARTICLE_IDEAL_CHARS

    assert ReviewAgent.NARRATIVE_WORD_FLOOR <= NARRATIVE_QUALITY_TARGET_MIN
    assert ReviewAgent.NARRATIVE_WORD_FLOOR <= target <= ReviewAgent.NARRATIVE_WORD_CEILING


def test_automation_word_target_matches_the_writer_target() -> None:
    import inspect

    source = inspect.getsource(
        __import__(
            "toutiao_content_factory.services.automation",
            fromlist=["TopicAutomationService"],
        ).TopicAutomationService._build_word_target
    )
    assert "return max(1700, 800)" in source
