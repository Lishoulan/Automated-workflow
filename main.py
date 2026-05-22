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
        "--wechat-draft-latest-approved",
        action="store_true",
        help="Create a WeChat draft from the latest approved run without submitting it.",
    )
    parser.add_argument(
        "--wechat-draft-run",
        action="store_true",
        help="Create a WeChat draft from a specific approved run without submitting it.",
    )
    parser.add_argument(
        "--wechat-publish-latest-approved",
        action="store_true",
        help="Create and submit a WeChat publish draft from the latest approved run.",
    )
    parser.add_argument(
        "--wechat-publish-run",
        action="store_true",
        help="Create and submit a WeChat publish draft from a specific approved run.",
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
    parser.add_argument(
        "--init-wechat-login",
        action="store_true",
        help="Initialize the persistent WeChat Official Account browser login session.",
    )
    parser.add_argument(
        "--prepare-wechat-launch-day",
        type=int,
        default=None,
        help="Prepare a WeChat launch-week day package (1-7) with article and image prompts.",
    )
    parser.add_argument(
        "--prepare-wechat-launch-week",
        action="store_true",
        help="Prepare all 7 WeChat launch-week packages at once.",
    )
    parser.add_argument(
        "--generate-images-run",
        action="store_true",
        help="Generate or regenerate cover/body images for a specific run package.",
    )
    parser.add_argument(
        "--build-benchmark-report",
        action="store_true",
        help="Build the benchmark account reports/playbooks for Toutiao and WeChat benchmark libraries.",
    )
    parser.add_argument(
        "--collect-benchmark-hot-topics",
        action="store_true",
        help="Collect the latest hot topics from benchmark-account source feeds.",
    )
    parser.add_argument(
        "--benchmark-platform",
        type=str,
        default=None,
        help="Benchmark platform key, e.g. toutiao or wechat. Defaults to the current workflow platform.",
    )
    parser.add_argument(
        "--benchmark-track",
        type=str,
        default=None,
        help="Optional benchmark track filter, e.g. AI机器人 / 新能源储能 / 科技商业.",
    )
    parser.add_argument(
        "--benchmark-limit",
        type=int,
        default=20,
        help="Maximum number of benchmark hot topics to export.",
    )
    parser.add_argument(
        "--collect-self-benchmark-samples",
        action="store_true",
        help="Collect real high-performing self benchmark samples from the performance ledger.",
    )
    parser.add_argument(
        "--backfill-performance-ledger",
        action="store_true",
        help="Backfill the performance ledger from existing Toutiao/WeChat publish history files.",
    )
    parser.add_argument(
        "--record-performance",
        action="store_true",
        help="Record post-publish performance metrics for a published run.",
    )
    parser.add_argument(
        "--build-performance-report",
        action="store_true",
        help="Build a performance review report from the performance ledger.",
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
    parser.add_argument(
        "--performance-platform",
        type=str,
        default="wechat",
        help="Platform key for performance tracking, e.g. wechat or toutiao.",
    )
    parser.add_argument("--snapshot-date", type=str, default=None, help="Snapshot date for performance metrics.")
    parser.add_argument("--impression-count", type=int, default=None, help="Total impressions/exposures.")
    parser.add_argument("--read-count", type=int, default=None, help="Total reads.")
    parser.add_argument(
        "--recommendation-read-count",
        type=int,
        default=None,
        help="Reads that came from platform recommendations.",
    )
    parser.add_argument("--like-count", type=int, default=None, help="Likes count.")
    parser.add_argument("--wow-count", type=int, default=None, help="Wow/在看 count.")
    parser.add_argument("--share-count", type=int, default=None, help="Share/repost count.")
    parser.add_argument("--comment-count", type=int, default=None, help="Comment count.")
    parser.add_argument("--follower-gain", type=int, default=None, help="Follower gain after publish.")
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

            if args.init_wechat_login:
                init_result = app_service.initialize_wechat_publish_login()
                _print_export_summary("WeChat login session initialized.", init_result)
                return 0

            if args.prepare_wechat_launch_day is not None:
                result = app_service.prepare_wechat_launch_day(args.prepare_wechat_launch_day)
                _print_export_summary("WeChat launch day package prepared.", result)
                return 0

            if args.prepare_wechat_launch_week:
                result = app_service.prepare_wechat_launch_week()
                _print_export_summary("WeChat launch week packages prepared.", result)
                return 0

            if args.generate_images_run:
                result = app_service.generate_images_for_run(args.run_dir)
                _print_export_summary("Images generated for run.", result)
                return 0

            if args.build_benchmark_report:
                result = app_service.build_benchmark_report()
                _print_export_summary("Benchmark report built.", result)
                return 0

            if args.collect_benchmark_hot_topics:
                result = app_service.collect_benchmark_hot_topics(
                    platform=args.benchmark_platform,
                    track=args.benchmark_track,
                    limit=max(1, int(args.benchmark_limit or 20)),
                )
                _print_export_summary("Benchmark hot topics collected.", result)
                return 0

            if args.collect_self_benchmark_samples:
                result = app_service.collect_self_benchmark_samples(account_key=args.account)
                _print_export_summary("Self benchmark samples collected.", result)
                return 0

            if args.backfill_performance_ledger:
                result = app_service.backfill_performance_ledger()
                _print_export_summary("Performance ledger backfilled.", result)
                return 0

            if args.record_performance:
                result = app_service.record_performance(
                    args.run_dir,
                    platform=args.performance_platform,
                    snapshot_date=args.snapshot_date,
                    impression_count=args.impression_count,
                    read_count=args.read_count,
                    recommendation_read_count=args.recommendation_read_count,
                    like_count=args.like_count,
                    wow_count=args.wow_count,
                    share_count=args.share_count,
                    comment_count=args.comment_count,
                    follower_gain=args.follower_gain,
                )
                _print_export_summary("Performance snapshot recorded.", result)
                return 0

            if args.build_performance_report:
                result = app_service.build_performance_report()
                _print_export_summary("Performance report built.", result)
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

            if args.wechat_draft_run:
                publish_exports = app_service.publish_wechat_run(args.run_dir, submit=False)
                _print_export_summary("Run exported to a WeChat draft.", publish_exports)
                return 0

            if args.wechat_publish_run:
                publish_exports = app_service.publish_wechat_run(args.run_dir, submit=True)
                _print_export_summary("Run submitted to WeChat.", publish_exports)
                return 0

            if args.refresh_publish_readiness:
                readiness_exports = app_service.evaluate_latest_run_publish_readiness(args.run_dir)
                _print_export_summary("Publish readiness refreshed.", readiness_exports)
                return 0

            if args.publish_latest_approved:
                publish_exports = app_service.publish_latest_approved(unpublished_only=True)
                _print_export_summary("Latest approved unpublished run published.", publish_exports)
                return 0

            if args.wechat_draft_latest_approved:
                publish_exports = app_service.publish_wechat_latest_approved(unpublished_only=False, submit=False)
                _print_export_summary("Latest approved run exported to a WeChat draft.", publish_exports)
                return 0

            if args.wechat_publish_latest_approved:
                publish_exports = app_service.publish_wechat_latest_approved(unpublished_only=False, submit=True)
                _print_export_summary("Latest approved run submitted to WeChat.", publish_exports)
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

    result_status, result_message = _write_daily_run_success_result(settings, args, exported_files)
    _print_export_summary(result_message, exported_files)
    return 1 if result_status == "failed" else 0


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
            args.wechat_draft_run,
            args.wechat_publish_run,
            args.refresh_publish_readiness,
            args.publish_latest_approved,
            args.wechat_draft_latest_approved,
            args.wechat_publish_latest_approved,
            args.publish_preflight,
            args.init_publish_login,
            args.init_wechat_login,
            args.prepare_wechat_launch_day is not None,
            args.prepare_wechat_launch_week,
            args.generate_images_run,
            args.build_benchmark_report,
            args.collect_benchmark_hot_topics,
            args.collect_self_benchmark_samples,
            args.backfill_performance_ledger,
            args.record_performance,
            args.build_performance_report,
        ]
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
) -> tuple[str, str]:
    if not args.daily_run:
        return "success", "Content generation completed."

    run_summary = _build_daily_run_summary(settings, exported_files)
    workflow_status = str(run_summary.get("workflow_status", "")).strip()
    review_status = str(run_summary.get("review_status", "")).strip()
    publish_status = str(run_summary.get("publish_status", "")).strip()
    wechat_status = str(run_summary.get("wechat_status", "")).strip()

    if workflow_status == "platform_publish_failed":
        status = "failed"
        message = "Scheduled pipeline finished generation, but at least one platform publish step failed."
    elif workflow_status == "platform_partial_success":
        status = "success"
        message = "Scheduled pipeline partially succeeded. At least one platform completed, but another platform failed."
    elif publish_status or wechat_status:
        platform_parts: list[str] = []
        if publish_status:
            platform_parts.append(f"Toutiao={publish_status}")
        if wechat_status:
            platform_parts.append(f"WeChat={wechat_status}")
        status = "success"
        message = f"Scheduled pipeline succeeded. {'; '.join(platform_parts)}."
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
    return status, message


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
        "workflow_status": "",
        "workflow_errors": [],
        "publish_status": "",
        "publish_url": "",
        "publish_message": "",
        "publish_pgc_id": "",
        "wechat_status": "",
        "wechat_submit_status": "",
        "wechat_submit_requested": False,
        "wechat_publish_url": "",
        "wechat_draft_media_id": "",
    }
    run_dir = _resolve_run_dir_from_exports(exported_files)
    workflow_report_path = exported_files.get("workflow_report") or str(settings.automation_dir / "last_daily_workflow_report.json")
    workflow_report = _load_json_file(Path(workflow_report_path)) or {}
    summary["workflow_status"] = str(workflow_report.get("status", "")).strip()
    final_attempt = workflow_report.get("final_attempt", {}) if isinstance(workflow_report, dict) else {}
    if isinstance(final_attempt, dict):
        raw_errors = final_attempt.get("platform_errors", [])
        if isinstance(raw_errors, list):
            summary["workflow_errors"] = [str(item) for item in raw_errors]

    if run_dir is None:
        return summary

    summary["run_dir"] = str(run_dir)
    summary["run_name"] = run_dir.name

    review_status = _load_json_file(run_dir / "review_status.json") or {}
    publish_result = _load_json_file(run_dir / settings.review.publish_dirname / "publish_result.json") or {}
    wechat_result = _load_json_file(run_dir / settings.review.publish_dirname / "wechat" / "wechat_publish_result.json") or {}
    backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}
    wechat_status = str(wechat_result.get("status", "")).strip()
    wechat_submit_status = str(wechat_result.get("submit_status", "")).strip()
    wechat_publish_url = str(wechat_result.get("external_url", "")).strip()
    wechat_draft_media_id = str(wechat_result.get("draft_media_id", "")).strip()
    if not wechat_status:
        if wechat_submit_status or wechat_publish_url:
            wechat_status = "submitted"
        elif wechat_draft_media_id:
            wechat_status = "draft_created"

    summary["review_status"] = str(review_status.get("status", "")).strip()
    summary["selected_title"] = str(review_status.get("selected_title", "")).strip()
    summary["publish_status"] = str(publish_result.get("status", "")).strip()
    summary["publish_url"] = str(publish_result.get("external_url", "")).strip()
    summary["publish_message"] = str(backend.get("message", "")).strip()
    summary["publish_pgc_id"] = str(backend.get("pgc_id", "")).strip()
    summary["wechat_status"] = wechat_status
    summary["wechat_submit_status"] = wechat_submit_status
    summary["wechat_submit_requested"] = bool(wechat_result.get("submit_requested", False))
    summary["wechat_publish_url"] = wechat_publish_url
    summary["wechat_draft_media_id"] = wechat_draft_media_id
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
