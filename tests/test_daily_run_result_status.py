from __future__ import annotations

import json
import logging
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from main import (
    _build_daily_run_summary,
    _mark_daily_run_running,
    _write_daily_run_success_result,
)
from toutiao_content_factory.services.app_service import ContentFactoryAppService


def _args() -> Namespace:
    return Namespace(daily_run=True)


def _prepare_run(
    app_settings,
    tmp_path: Path,
    *,
    publish_status: str,
) -> tuple[dict[str, str], Path]:
    app_settings.output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "run"
    publish_dir = run_dir / app_settings.review.publish_dirname
    publish_dir.mkdir(parents=True)
    (run_dir / "workflow_result.json").write_text("{}", encoding="utf-8")
    (run_dir / "review_status.json").write_text(
        json.dumps({"status": "approved", "selected_title": "Test title"}),
        encoding="utf-8",
    )
    (publish_dir / "publish_result.json").write_text(
        json.dumps({"status": publish_status}),
        encoding="utf-8",
    )
    return {"archive_root": str(run_dir)}, run_dir


def test_confirmed_submission_is_success(app_settings, tmp_path: Path) -> None:
    exports, _run_dir = _prepare_run(app_settings, tmp_path, publish_status="submitted_for_review")

    status, _message = _write_daily_run_success_result(app_settings, _args(), exports)

    assert status == "success"
    result = json.loads(
        (app_settings.automation_dir / "last_daily_run_result.json").read_text(encoding="utf-8")
    )
    assert result["publish_status"] == "submitted_for_review"
    assert result["status"] == "success"


def test_draft_is_not_reported_as_public_success(app_settings, tmp_path: Path) -> None:
    exports, _run_dir = _prepare_run(app_settings, tmp_path, publish_status="draft_saved")

    status, _message = _write_daily_run_success_result(app_settings, _args(), exports)

    assert status == "draft_only"


def test_uncertain_publish_is_a_nonzero_terminal_state(app_settings, tmp_path: Path) -> None:
    exports, _run_dir = _prepare_run(app_settings, tmp_path, publish_status="publish_uncertain")

    status, _message = _write_daily_run_success_result(app_settings, _args(), exports)

    assert status == "publish_uncertain"


def test_failed_publish_is_not_masked_by_generated_artifacts(app_settings, tmp_path: Path) -> None:
    exports, _run_dir = _prepare_run(app_settings, tmp_path, publish_status="failed")

    status, _message = _write_daily_run_success_result(app_settings, _args(), exports)

    assert status == "failed"


def test_review_required_does_not_inherit_previous_publish_state(app_settings, tmp_path: Path) -> None:
    previous = {
        "publish_status": "submitted_for_review",
        "publish_url": "https://example.test/previous",
        "publish_pgc_id": "previous-pgc",
        "platforms": {
            "toutiao": {
                "status": "success",
                "last_run": "2026-08-28T08:00:00",
                "message": "previous publish",
            }
        },
    }
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.automation_dir / "scheduled_status.json").write_text(
        json.dumps(previous),
        encoding="utf-8",
    )
    exports, run_dir = _prepare_run(app_settings, tmp_path, publish_status="")
    (run_dir / "review_status.json").write_text(
        json.dumps({"status": "pending", "selected_title": "Review me"}),
        encoding="utf-8",
    )

    status, _message = _write_daily_run_success_result(app_settings, _args(), exports)

    assert status == "review_required"
    scheduled = json.loads(
        (app_settings.automation_dir / "scheduled_status.json").read_text(encoding="utf-8")
    )
    assert scheduled["overall_status"] == "review_required"
    assert scheduled["publish_status"] == ""
    assert scheduled["publish_url"] == ""
    assert scheduled["publish_pgc_id"] == ""
    assert scheduled["platforms"]["toutiao"]["status"] == "review_required"


def test_content_guard_blocks_high_score_auto_approval(app_settings) -> None:
    service = ContentFactoryAppService(app_settings, logging.getLogger("test-content-guard-gate"))
    high_score = {
        "overall_score": 90,
        "ai_generated_risk": 10,
        "validation_score": 90,
        "publish_readiness_score": 90,
        "article_char_count": 1600,
    }

    assert service._qualifies_for_auto_approval(high_score, content_guard_report={"blocked": True}) is False
    assert service._qualifies_for_auto_approval(
        high_score,
        content_guard_report={"blocked": False, "blocking_flags": []},
    ) is True


def test_mark_daily_run_running_handles_existing_status(app_settings) -> None:
    app_settings.automation_dir.mkdir(parents=True, exist_ok=True)
    (app_settings.automation_dir / "scheduled_status.json").write_text(
        json.dumps(
            {
                "status": "review_required",
                "overall_status": "review_required",
                "publish_status": "submitted_for_review",
                "publish_pgc_id": "old-pgc",
            }
        ),
        encoding="utf-8",
    )

    _mark_daily_run_running(app_settings)

    scheduled = json.loads(
        (app_settings.automation_dir / "scheduled_status.json").read_text(encoding="utf-8")
    )
    assert scheduled["status"] == "running"
    assert scheduled["overall_status"] == "running"
    assert scheduled["exit_code"] == 0



def test_already_published_restores_same_day_article_summary(app_settings, tmp_path: Path) -> None:
    exports, run_dir = _prepare_run(app_settings, tmp_path, publish_status="published")
    (run_dir / "workflow_result.json").write_text(
        json.dumps({"content_id": "cid-restored"}),
        encoding="utf-8",
    )
    (run_dir / app_settings.review.publish_dirname / "publish_result.json").write_text(
        json.dumps(
            {
                "status": "published",
                "title": "Recovered title",
                "external_url": "https://example.test/article",
            }
        ),
        encoding="utf-8",
    )
    today = datetime.now().date().isoformat()
    (app_settings.output_dir / app_settings.publishing.history_filename).write_text(
        json.dumps(
            {
                "records": [
                    {
                        "platform": "toutiao",
                        "status": "published",
                        "published_at": f"{today}T08:00:00",
                        "run_dir": str(run_dir),
                        "title": "Recovered title",
                        "external_url": "https://example.test/article",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    exports = {
        "status": "already_published",
        "message": "Already published today.",
    }

    summary = _build_daily_run_summary(app_settings, exports)

    assert summary["run_dir"] == str(run_dir.resolve())
    assert summary["content_id"] == "cid-restored"
    assert summary["publish_status"] == "published"
    assert summary["publish_url"] == "https://example.test/article"


def test_already_published_keeps_weitoutiao_topup_status(app_settings, tmp_path: Path) -> None:
    exports, run_dir = _prepare_run(app_settings, tmp_path, publish_status="published")
    today = datetime.now().date().isoformat()
    (app_settings.output_dir / app_settings.publishing.history_filename).write_text(
        json.dumps(
            {
                "records": [
                    {
                        "platform": "toutiao",
                        "status": "published",
                        "published_at": f"{today}T08:00:00",
                        "run_dir": str(run_dir),
                        "title": "Recovered title",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    topup_path = tmp_path / "topup_result.json"
    topup_path.write_text(
        json.dumps(
            {
                "status": "success",
                "message": "WeiToutiao top-up completed.",
                "accepted": 1,
                "today_count_after": 3,
            }
        ),
        encoding="utf-8",
    )
    exports.update(
        {
            "standalone_topup_result": str(topup_path),
            "standalone_topup_status": "success",
        }
    )

    status, _message = _write_daily_run_success_result(
        app_settings,
        _args(),
        {
            **exports,
            "status": "already_published",
            "message": "Daily publish limit reached; skipped generation for this scheduled slot.",
        },
    )

    assert status == "already_published"
    result = json.loads(
        (app_settings.automation_dir / "last_daily_run_result.json").read_text(encoding="utf-8")
    )
    assert result["weitoutiao_status"] == "success"
    assert result["weitoutiao_topup_status"] == "success"
    scheduled_status = json.loads(
        (app_settings.automation_dir / "scheduled_status.json").read_text(encoding="utf-8")
    )
    assert scheduled_status["exit_code"] == 0
    assert scheduled_status["platforms"]["weitoutiao"]["status"] == "success"


def test_no_topics_keeps_last_article_in_scheduled_status(app_settings, tmp_path: Path) -> None:
    exports, run_dir = _prepare_run(app_settings, tmp_path, publish_status="submitted_for_review")

    status, _message = _write_daily_run_success_result(app_settings, _args(), exports)
    assert status == "success"

    no_topic_exports = {
        "status": "no_topics",
        "message": "No eligible topics.",
    }
    status, _message = _write_daily_run_success_result(app_settings, _args(), no_topic_exports)

    scheduled_status = json.loads(
        (app_settings.automation_dir / "scheduled_status.json").read_text(encoding="utf-8")
    )
    assert status == "no_topics"
    assert scheduled_status["status"] == "no_topics"
    assert scheduled_status["overall_status"] == "idle"
    assert scheduled_status["run_dir"] == str(run_dir.resolve())
    assert scheduled_status["selected_title"] == "Test title"


def test_graphic_submission_with_weitoutiao_topup_failure_is_partial_success(
    app_settings,
    tmp_path: Path,
) -> None:
    exports, run_dir = _prepare_run(
        app_settings,
        tmp_path,
        publish_status="submitted_for_review",
    )
    weitoutiao_result_path = run_dir / app_settings.review.publish_dirname / "weitoutiao_daily_result.json"
    weitoutiao_result_path.write_text(
        json.dumps(
            {
                "status": "partial_success",
                "message": "WeiToutiao workflow partially succeeded; top-up failed.",
                "companion": {"status": "submitted_for_review"},
                "topup": {
                    "status": "failed",
                    "message": "502 Bad Gateway",
                    "error": "502 Bad Gateway",
                },
            }
        ),
        encoding="utf-8",
    )
    exports["weitoutiao_daily_result"] = str(weitoutiao_result_path)

    status, message = _write_daily_run_success_result(app_settings, _args(), exports)

    assert status == "partial_success"
    assert "图文" in message
    assert "微头条" in message
    result = json.loads(
        (app_settings.automation_dir / "last_daily_run_result.json").read_text(encoding="utf-8")
    )
    assert result["publish_status"] == "submitted_for_review"
    assert result["weitoutiao_status"] == "partial_success"
    assert result["weitoutiao_topup_status"] == "failed"
    assert result["weitoutiao"]["topup"]["error"] == "502 Bad Gateway"
    scheduled_status = json.loads(
        (app_settings.automation_dir / "scheduled_status.json").read_text(encoding="utf-8")
    )
    assert scheduled_status["status"] == "partial_success"
    assert scheduled_status["weitoutiao_status"] == "partial_success"
    assert scheduled_status["platforms"]["weitoutiao"]["status"] == "partial_success"


def test_platform_automation_exports_weitoutiao_failures(app_settings, tmp_path: Path) -> None:
    object.__setattr__(app_settings.publishing, "enabled", True)
    object.__setattr__(app_settings.publishing, "auto_publish", True)
    object.__setattr__(app_settings.publishing, "weitoutiao_enabled", True)
    object.__setattr__(app_settings.publishing, "weitoutiao_standalone_enabled", True)
    app_settings.skip_image_generation = True

    app = ContentFactoryAppService(app_settings, logging.getLogger("test.daily.wtt"))
    run_dir = tmp_path / "automation-run"
    publish_dir = run_dir / app_settings.review.publish_dirname
    publish_dir.mkdir(parents=True)
    (run_dir / "article.md").write_text("A test article.", encoding="utf-8")

    app.publish_run = MagicMock(return_value={})
    app._load_toutiao_publish_snapshot = MagicMock(
        return_value={"status": "submitted_for_review", "external_url": "https://example.test/article"}
    )
    app.publish_weitoutiao_run = MagicMock(side_effect=RuntimeError("companion browser unavailable"))
    topup_result_path = tmp_path / "topup_result.json"
    topup_result_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "message": "502 Bad Gateway",
                "error": "502 Bad Gateway",
            }
        ),
        encoding="utf-8",
    )
    app._top_up_standalone_weitoutiao = MagicMock(
        return_value={
            "standalone_topup_result": str(topup_result_path),
            "standalone_topup_status": "failed",
        }
    )

    exports, platform_results, platform_errors = app._run_daily_platform_automation(
        run_dir,
        dry_run=False,
        skip_wait=True,
    )

    daily_result_path = Path(exports["weitoutiao_daily_result"])
    daily_result = json.loads(daily_result_path.read_text(encoding="utf-8"))
    assert daily_result["status"] == "failed"
    assert daily_result["companion"]["status"] == "failed"
    assert daily_result["topup"]["status"] == "failed"
    assert platform_results["weitoutiao"]["status"] == "failed"
    assert any("companion" in error for error in platform_errors)
    assert any("top-up" in error for error in platform_errors)
