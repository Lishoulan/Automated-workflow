"""头条首发声明由当前平台门槛与五项质量评估共同决定。"""

from __future__ import annotations

import inspect

from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.publish_service import PublishService


def _qualified_content(char_count: int) -> str:
    seed = "今日根据公开数据显示，我的判断是这件事背后的原因会产生影响，也提醒读者如何处理。"
    assert len(seed) <= char_count
    return seed + "甲" * (char_count - len(seed))


def test_current_platform_boundary_is_more_than_100_chars() -> None:
    checker = ArticleQualityChecker()

    below = checker.assess_first_release(_qualified_content(100))
    boundary = checker.assess_first_release(_qualified_content(101))

    assert checker.FIRST_RELEASE_MIN_CHARS == 101
    assert below.eligible is False
    assert any("超过100字" in reason for reason in below.reasons)
    assert boundary.eligible is True


def test_char_count_ignores_all_whitespace() -> None:
    checker = ArticleQualityChecker()
    content = _qualified_content(101)

    assessment = checker.assess_first_release(content[:50] + " \n\t" + content[50:])

    assert assessment.char_count == 101
    assert assessment.eligible is True


def test_long_but_shallow_content_does_not_enable_first_release() -> None:
    assessment = ArticleQualityChecker().assess_first_release("普通叙述。" * 300)

    assert assessment.char_count > 100
    assert assessment.eligible is False
    assert assessment.standards_covered < assessment.minimum_standards
    assert "原创深度" in assessment.missing_standards


def test_internal_source_marker_blocks_first_release() -> None:
    assessment = ArticleQualityChecker().assess_first_release(
        _qualified_content(101) + " benchmark_hotspot"
    )

    assert assessment.eligible is False
    assert "正文仍含内部来源标记" in assessment.reasons


def test_publish_service_uses_quality_assessment_before_checkbox() -> None:
    source = inspect.getsource(PublishService._publish_run_locked)
    class_source = inspect.getsource(PublishService)

    assert 'first_release_assessment.get("eligible") is not True' in source
    assert "declare_first_release = False" in source
    assert "declare_first_release=declare_first_release" in source
    assert "label.byte-checkbox:has-text('头条首发')" in class_source


def test_publish_result_records_first_release_decision() -> None:
    source = inspect.getsource(PublishService._publish_run_locked)

    assert 'result_payload["first_release_requested"]' in source
    assert 'result_payload["first_release_option_enabled"]' in source
    assert 'result_payload["first_release_assessment"]' in source
