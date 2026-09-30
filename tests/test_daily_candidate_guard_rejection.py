from __future__ import annotations

from unittest.mock import MagicMock

from toutiao_content_factory.services.app_service import ContentFactoryAppService

BAD_WORD_REJECTION = (
    "Selected topic was rejected by the local bad-word guard. "
    "Matched bad words: 押注 (bad_words_blocked=True)."
)


def _prepare_app(app_settings, logger) -> ContentFactoryAppService:
    app = ContentFactoryAppService(app_settings, logger)
    app._cleanup_stale_runs = MagicMock(return_value=None)
    app.collect_topics = MagicMock(return_value={})
    app._heartbeat_daily_run_lock = MagicMock(return_value=None)
    app.platform_health_service.ensure_publish_ready = MagicMock(
        return_value={"blocking_platforms": []}
    )
    app._build_daily_candidate_specs = MagicMock(
        return_value=[
            {
                "rank": 1,
                "title": "拟斥资23个亿！市值百亿叉车龙头，押注机器人",
                "url": "https://example.test/bad-word-topic",
            },
            {
                "rank": 2,
                "title": "国产撕开缺口，工业机器人四大老钱家族的壁垒生变",
                "url": "https://example.test/clean-topic",
            },
        ]
    )
    return app


def test_bad_word_rejection_of_one_candidate_does_not_abort_the_day(
    app_settings,
    logger,
) -> None:
    """一个候选被本地敏感词闸门否决，不等于「今天不发」，必须继续试下一个候选。

    回归 2026-09-17 12:30 云端运行：选中「…百亿叉车龙头，押注机器人」，仅因标题含
    「押注」（config/bad_words.txt 赌博词段）就抛 ValueError 冒泡到 main.py，
    整条流水线 4 分钟失败、当天 0 产出。候选列表本来就有多条，不应该被一题拖死。
    """
    app = _prepare_app(app_settings, logger)
    app.auto_run = MagicMock(side_effect=ValueError(BAD_WORD_REJECTION))

    result = app._daily_run_impl(dry_run=True)

    assert app.auto_run.call_count == 2, (
        "第一个候选被否决后必须继续尝试下一个候选，而不是直接冒泡"
    )
    assert result["status"] == "no_topics"
    assert "source-material and safety gates" in result["message"]


def test_all_candidates_rejected_reports_no_runnable_candidates(
    app_settings,
    logger,
) -> None:
    """全部候选都被否决时，仍然要走到「没有可用候选」这条原有出口。"""
    app = _prepare_app(app_settings, logger)
    app._build_daily_candidate_specs = MagicMock(
        return_value=[
            {"rank": 1, "title": "押注机器人", "url": "https://example.test/a"},
            {"rank": 2, "title": "押注算力", "url": "https://example.test/b"},
            {"rank": 3, "title": "押注大模型", "url": "https://example.test/c"},
        ]
    )
    app.auto_run = MagicMock(side_effect=ValueError(BAD_WORD_REJECTION))

    result = app._daily_run_impl(dry_run=True)

    assert app.auto_run.call_count == 3
    assert result["status"] == "no_topics"
