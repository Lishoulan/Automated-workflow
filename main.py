from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.utils.logging_utils import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Toutiao content factory")
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Account key defined in config.yaml accounts section. Defaults to TOUTIAO_ACCOUNT or default.",
    )
    parser.add_argument("--paste", type=str, default=None, help="Inline text to analyze.")
    parser.add_argument("--input-dir", type=str, default=None, help="Override the default input directory.")
    parser.add_argument("--output-dir", type=str, default=None, help="Override the default output directory.")
    parser.add_argument("--style", type=str, default=None, help="Override the configured writing style.")
    parser.add_argument("--dry-run", action="store_true", help="Use local fallback logic without calling the model.")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached results and regenerate outputs.")
    parser.add_argument("--collect-topics", action="store_true", help="Collect topics before content generation.")
    parser.add_argument("--topics-only", action="store_true", help="Collect topics only and skip article generation.")
    parser.add_argument("--auto-run", action="store_true", help="Pick a topic automatically and generate content.")
    parser.add_argument(
        "--daily-run",
        action="store_true",
        help="Run the full scheduled workflow: collect, pick, generate, review, publish.",
    )
    parser.add_argument("--topic-rank", type=int, default=None, help="Ranked topic to use for auto-run.")
    parser.add_argument("--topic-title", type=str, default=None, help="Topic title keyword for auto-run.")
    parser.add_argument("--no-archive", action="store_true", help="Skip archiving the current run.")
    parser.add_argument(
        "--fast-mode",
        action="store_true",
        help="Use a faster scheduled pipeline by skipping some non-essential review/image steps.",
    )
    parser.add_argument("--prepare-review", action="store_true", help="Build a review packet for a run.")
    parser.add_argument("--approve-run", action="store_true", help="Approve a run and export publish assets.")
    parser.add_argument("--approve-latest", action="store_true", help="Approve the latest pending run.")
    parser.add_argument("--reject-run", action="store_true", help="Reject a run and save review notes.")
    parser.add_argument("--publish-run", action="store_true", help="Publish an approved run.")
    parser.add_argument(
        "--refresh-publish-readiness",
        action="store_true",
        help="Recalculate publish readiness with a live account/browser preflight.",
    )
    parser.add_argument(
        "--publish-latest-approved",
        action="store_true",
        help="Publish the latest approved run that has not been published yet.",
    )
    parser.add_argument(
        "--publish-preflight",
        action="store_true",
        help="Check whether the current persistent browser session can publish on Toutiao.",
    )
    parser.add_argument(
        "--init-publish-login",
        action="store_true",
        help="Initialize the persistent Toutiao browser login session.",
    )
    parser.add_argument("--run-dir", type=str, default=None, help="Archived run directory for review or publish.")
    parser.add_argument("--selected-title", type=str, default=None, help="Explicit final title to approve.")
    parser.add_argument(
        "--selected-title-index",
        type=int,
        default=None,
        help="1-based title index to approve from the candidate list.",
    )
    parser.add_argument("--review-notes", type=str, default=None, help="Review notes to save with the decision.")
    parser.add_argument("--auto-publish", action="store_true", help="Publish immediately after approval.")
    return parser


def main() -> int:
    _configure_utf8_stdio()

    args = build_parser().parse_args()
    settings = load_settings(PROJECT_ROOT, account_key=args.account)

    if args.input_dir:
        settings.input_dir = Path(args.input_dir).resolve()
    if args.output_dir:
        settings.output_dir = Path(args.output_dir).resolve()

    settings.ensure_directories()
    logger = configure_logging(settings.logs_dir, settings.logging_level)
    app_service = ContentFactoryAppService(settings, logger)
    exported_files: dict[str, str] = {}

    try:
        if _is_review_or_publish_action(args):
            if args.init_publish_login:
                init_result = app_service.initialize_publish_login()
                _print_export_summary("Publish login session initialized.", init_result)
                return 0

            if args.publish_preflight:
                preflight_exports = app_service.collect_publish_preflight()
                _print_export_summary("Publish preflight collected.", preflight_exports)
                return 0

            if args.prepare_review:
                review_exports = app_service.prepare_review(args.run_dir)
                _print_export_summary("Review packet prepared.", review_exports)
                return 0

            if args.approve_run:
                approve_exports = app_service.approve_run(
                    args.run_dir,
                    selected_title=args.selected_title,
                    selected_title_index=args.selected_title_index,
                    review_notes=args.review_notes,
                    auto_publish=args.auto_publish,
                )
                _print_export_summary("Run approved.", approve_exports)
                return 0

            if args.approve_latest:
                approve_exports = app_service.approve_latest(
                    selected_title=args.selected_title,
                    selected_title_index=args.selected_title_index,
                    review_notes=args.review_notes,
                    auto_publish=args.auto_publish,
                )
                _print_export_summary("Latest pending run approved.", approve_exports)
                return 0

            if args.publish_run:
                publish_exports = app_service.publish_run(args.run_dir)
                _print_export_summary("Publish command completed.", publish_exports)
                return 0

            if args.refresh_publish_readiness:
                readiness_exports = app_service.evaluate_latest_run_publish_readiness(args.run_dir)
                _print_export_summary("Publish readiness refreshed.", readiness_exports)
                return 0

            if args.publish_latest_approved:
                publish_exports = app_service.publish_latest_approved(unpublished_only=True)
                _print_export_summary("Latest approved unpublished run published.", publish_exports)
                return 0

            reject_exports = app_service.reject_run(args.run_dir, review_notes=args.review_notes)
            _print_export_summary("Run rejected.", reject_exports)
            return 0

        if args.topics_only:
            exported_files = app_service.collect_topics()
            _print_export_summary("Topic collection completed.", exported_files)
            return 0

        if args.collect_topics and not args.auto_run and not args.daily_run and args.paste is None:
            exported_files = app_service.collect_topics()
            _print_export_summary("Topic collection completed.", exported_files)
            return 0

        if args.daily_run:
            exported_files = app_service.daily_run(
                style_override=args.style,
                dry_run=args.dry_run,
                refresh_cache=args.refresh_cache,
                topic_rank=args.topic_rank,
                topic_title=args.topic_title,
                no_archive=args.no_archive,
                fast_mode=args.fast_mode,
            )
        elif args.auto_run:
            exported_files = app_service.auto_run(
                style_override=args.style,
                dry_run=args.dry_run,
                refresh_cache=args.refresh_cache,
                topic_rank=args.topic_rank,
                topic_title=args.topic_title,
                collect_if_missing=True,
                force_collect=args.collect_topics,
                no_archive=args.no_archive,
                fast_mode=args.fast_mode,
            )
        else:
            if args.collect_topics:
                exported_files.update(app_service.collect_topics())
            content_exports = app_service.manual_run(
                paste_text=args.paste,
                style_override=args.style,
                dry_run=args.dry_run,
                refresh_cache=args.refresh_cache,
                include_input_dir=True,
                fast_mode=args.fast_mode,
            )
            exported_files.update(content_exports)

        if _should_auto_approve_and_publish(args, settings, exported_files):
            archive_root = exported_files.get("archive_root")
            if archive_root:
                approve_exports = app_service.approve_run(
                    archive_root,
                    selected_title_index=1,
                    review_notes="Auto-approved by the scheduled publishing pipeline.",
                    auto_publish=True,
                )
                exported_files.update(approve_exports)

    except Exception as exc:  # pragma: no cover - CLI safety net
        logger.exception("Content factory failed: %s", exc)
        handled_result = _handle_daily_run_exception(args, settings, exported_files, exc)
        if handled_result is not None:
            if exported_files:
                _print_export_summary(handled_result["message"], exported_files)
            else:
                print(handled_result["message"])
            return int(handled_result["exit_code"])
        if exported_files:
            _write_daily_run_result(
                settings,
                args,
                exported_files,
                status="failed",
                message=str(exc),
                error=str(exc),
            )
            _print_export_summary(
                f"Command failed after producing partial outputs: {exc}",
                exported_files,
            )
            return 1
        _write_daily_run_result(
            settings,
            args,
            exported_files,
            status="failed",
            message=str(exc),
            error=str(exc),
        )
        print(f"Command failed: {exc}")
        return 1

    _write_daily_run_success_result(settings, args, exported_files)
    _print_export_summary("Content generation completed.", exported_files)
    return 0


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _print_export_summary(message: str, exported_files: dict[str, Any]) -> None:
    print(message)
    for name, path in exported_files.items():
        print(f"- {name}: {path}")


def _is_review_or_publish_action(args: argparse.Namespace) -> bool:
    return any(
        [
            args.prepare_review,
            args.approve_run,
            args.approve_latest,
            args.reject_run,
            args.publish_run,
            args.refresh_publish_readiness,
            args.publish_latest_approved,
            args.publish_preflight,
            args.init_publish_login,
        ]
    )


def _should_auto_approve_and_publish(
    args: argparse.Namespace,
    settings,
    exported_files: dict[str, str],
) -> bool:
    if not args.daily_run:
        return False
    if not settings.publishing.enabled or not settings.publishing.auto_publish:
        return False
    if args.dry_run or args.no_archive:
        return False
    archive_root = exported_files.get("archive_root")
    if not archive_root:
        return False
    score_path = Path(archive_root) / "content_score.json"
    if not score_path.exists():
        return False
    payload = json.loads(score_path.read_text(encoding="utf-8"))
    scores = payload.get("scores", {})
    overall_score = int(scores.get("overall_score", 0))
    ai_risk = int(scores.get("ai_generated_risk", 100))
    validation_score = int(scores.get("validation_score", 0))
    publish_readiness = int(scores.get("publish_readiness_score", 0))
    return settings.review.qualifies_for_auto_publish(
        overall_score=overall_score,
        ai_risk=ai_risk,
        validation_score=validation_score,
        publish_readiness_score=publish_readiness,
    )


def _handle_daily_run_exception(
    args: argparse.Namespace,
    settings,
    exported_files: dict[str, str],
    exc: Exception,
) -> dict[str, Any] | None:
    if not args.daily_run:
        return None

    error_message = str(exc)
    status = ""
    message = ""
    if any(
        marker in error_message
        for marker in (
            "daily_topics.json contains no available topics",
            "No unpublished topics are available for today",
        )
    ):
        status = "no_topics"
        message = "Scheduled run skipped because no eligible topics were available after collection."
    elif "Publish preflight blocked:" in error_message:
        status = "publish_login_required"
        message = "Scheduled run generated content, but the Toutiao publish session needs manual login or page initialization."

    if not status:
        return None

    _write_daily_run_result(
        settings,
        args,
        exported_files,
        status=status,
        message=message,
        error=error_message,
    )
    return {
        "exit_code": 0,
        "message": message,
    }


def _write_daily_run_success_result(
    settings,
    args: argparse.Namespace,
    exported_files: dict[str, str],
) -> None:
    if not args.daily_run:
        return

    run_summary = _build_daily_run_summary(settings, exported_files)
    review_status = str(run_summary.get("review_status", "")).strip()
    publish_status = str(run_summary.get("publish_status", "")).strip()

    if publish_status:
        status = "success"
        message = f"Scheduled pipeline succeeded. Publish status: {publish_status}."
    elif review_status and review_status != "approved":
        status = "review_required"
        message = "Scheduled pipeline succeeded, but this run still requires manual review."
    else:
        status = "success"
        message = "Scheduled pipeline succeeded."

    _write_daily_run_result(
        settings,
        args,
        exported_files,
        status=status,
        message=message,
    )


def _write_daily_run_result(
    settings,
    args: argparse.Namespace,
    exported_files: dict[str, str],
    status: str,
    message: str,
    error: str = "",
) -> None:
    if not args.daily_run:
        return

    result_path = settings.automation_dir / "last_daily_run_result.json"
    payload = _build_daily_run_summary(settings, exported_files)
    payload.update(
        {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": settings.account_key,
            "account_slug": settings.account_slug,
            "status": status,
            "message": message,
            "error": error,
            "exported_files": {name: str(path) for name, path in exported_files.items()},
        }
    )
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_daily_run_summary(settings, exported_files: dict[str, str]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "run_dir": "",
        "run_name": "",
        "review_status": "",
        "selected_title": "",
        "publish_status": "",
        "publish_url": "",
        "publish_message": "",
        "publish_pgc_id": "",
    }
    run_dir = _resolve_run_dir_from_exports(exported_files)
    if run_dir is None:
        return summary

    summary["run_dir"] = str(run_dir)
    summary["run_name"] = run_dir.name

    review_status = _load_json_file(run_dir / "review_status.json") or {}
    publish_result = _load_json_file(run_dir / settings.review.publish_dirname / "publish_result.json") or {}
    backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}

    summary["review_status"] = str(review_status.get("status", "")).strip()
    summary["selected_title"] = str(review_status.get("selected_title", "")).strip()
    summary["publish_status"] = str(publish_result.get("status", "")).strip()
    summary["publish_url"] = str(publish_result.get("external_url", "")).strip()
    summary["publish_message"] = str(backend.get("message", "")).strip()
    summary["publish_pgc_id"] = str(backend.get("pgc_id", "")).strip()
    return summary


def _resolve_run_dir_from_exports(exported_files: dict[str, str]) -> Path | None:
    run_dir_candidates = [
        exported_files.get("archive_root"),
        exported_files.get("resolved_run_dir"),
    ]
    review_status_path = exported_files.get("review_status")
    if review_status_path:
        run_dir_candidates.append(str(Path(review_status_path).resolve().parent))

    publish_result_path = exported_files.get("publish_result")
    if publish_result_path:
        publish_dir = Path(publish_result_path).resolve().parent
        run_dir_candidates.append(str(publish_dir.parent))

    for raw_path in run_dir_candidates:
        if not raw_path:
            continue
        candidate = Path(raw_path).resolve()
        if candidate.is_dir():
            return candidate
    return None


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


if __name__ == "__main__":
    raise SystemExit(main())
