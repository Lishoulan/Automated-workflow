from __future__ import annotations

import argparse
import json
import sys
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
        if exported_files:
            _print_export_summary(
                f"Command failed after producing partial outputs: {exc}",
                exported_files,
            )
            return 1
        print(f"Command failed: {exc}")
        return 1

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


if __name__ == "__main__":
    raise SystemExit(main())
