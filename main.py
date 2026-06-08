from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toutiao_content_factory.utils.runtime_status import refresh_heartbeat
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.utils.logging_utils import configure_logging
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.services.pipeline_state_manager import PipelineStage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Toutiao content factory")
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        help="Account key defined in config.yaml accounts section. Defaults to TOUTIAO_ACCOUNT or default.",
    )
    parser.add_argument("--reset-account", action="store_true", help="Reset automation state files for the selected account.")
    parser.add_argument("--audit-account-artifacts", action="store_true", help="Audit stale account artifact directories on disk.")
    parser.add_argument("--cleanup-stale-account-artifacts", action="store_true", help="Move stale account artifact directories into a local cleanup trash folder.")
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
    parser.add_argument(
        "--skip-wait",
        action="store_true",
        help="Skip or shorten the delay between WeChat and Toutiao publishing (for debugging).",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Resume a failed pipeline run by task_id and continue from the last checkpoint.",
    )
    parser.add_argument("--prepare-review", action="store_true", help="Build a review packet for a run.")
    parser.add_argument("--approve-run", action="store_true", help="Approve a run and export publish assets.")
    parser.add_argument("--approve-latest", action="store_true", help="Approve the latest pending run.")
    parser.add_argument("--reject-run", action="store_true", help="Reject a run and save review notes.")
    parser.add_argument("--publish-run", action="store_true", help="Publish an approved run.")
    parser.add_argument("--draft-only", action="store_true", help="Fill Toutiao publish form and save as draft instead of publishing.")
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
        "--health-preflight",
        action="store_true",
        help="Run a unified platform session health preflight for Toutiao / WeChat / Douyin.",
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
        "--init-135editor-login",
        action="store_true",
        help="Initialize the persistent 135 Editor browser login session for WeChat publishing.",
    )
    parser.add_argument(
        "--init-douyin-login",
        action="store_true",
        help="Initialize the persistent Douyin creator browser login session.",
    )
    parser.add_argument(
        "--prepare-douyin-run",
        action="store_true",
        help="Prepare Douyin title/caption/image payloads from an approved run.",
    )
    parser.add_argument(
        "--publish-douyin-run",
        action="store_true",
        help="Publish a prepared approved run to Douyin using the saved browser session.",
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
        "--build-company-os",
        action="store_true",
        help="Build a one-person-company operating system blueprint, backlog, and scorecard from the current repo.",
    )
    parser.add_argument(
        "--build-revenue-engine",
        action="store_true",
        help="Build proactive customer prospecting, sponsor prospecting, outreach, and newsletter monetization assets.",
    )
    parser.add_argument(
        "--run-profit-loop",
        action="store_true",
        help="Run one full profit loop: refresh revenue assets, agent dispatch queue, and founder profit brief.",
    )
    parser.add_argument(
        "--execute-profit-actions",
        action="store_true",
        help="Execute profit actions and write results back to lead tracking where supported.",
    )
    parser.add_argument(
        "--profit-action-types",
        type=str,
        default="",
        help="Comma-separated profit action types to execute, e.g. send_referral_intro,send_expansion_proposal.",
    )
    parser.add_argument(
        "--profit-action-limit",
        type=int,
        default=0,
        help="Optional max number of profit actions to execute.",
    )
    parser.add_argument(
        "--update-sponsor-outreach",
        action="store_true",
        help="Update the status of a sponsor outreach action in the sponsor pipeline.",
    )
    parser.add_argument(
        "--update-newsletter-subscriber",
        action="store_true",
        help="Update the status of a newsletter subscriber or paid member in the letter pipeline.",
    )
    parser.add_argument(
        "--sponsor-action-key",
        type=str,
        default="",
        help="Action key for sponsor outreach updates.",
    )
    parser.add_argument(
        "--sponsor-status",
        type=str,
        default="",
        help="Sponsor outreach status such as sent / interested / call_booked / won / lost.",
    )
    parser.add_argument(
        "--subscriber-key",
        type=str,
        default="",
        help="Subscriber key for newsletter pipeline updates.",
    )
    parser.add_argument(
        "--subscriber-status",
        type=str,
        default="",
        help="Subscriber status such as new_subscriber / free_sample_sent / member_interested / member_won / renewal_due / renewed.",
    )
    parser.add_argument(
        "--ensure-lead-assets",
        action="store_true",
        help="Generate lead capture assets such as keyword replies, follow-up queue, and lead summary.",
    )
    parser.add_argument("--record-lead", action="store_true", help="Record a new lead into the local lead ledger.")
    parser.add_argument("--update-lead", action="store_true", help="Update an existing lead in the local lead ledger.")
    parser.add_argument("--generate-lead-proposal", action="store_true", help="Generate a proposal markdown for a specific lead.")
    parser.add_argument("--mark-lead-outreach", action="store_true", help="Mark outreach as sent for a specific lead.")
    parser.add_argument("--triage-lead-reply", action="store_true", help="Classify a lead reply and suggest the next move.")
    parser.add_argument("--mark-lead-proposal-sent", action="store_true", help="Mark a prepared proposal as sent.")
    parser.add_argument("--mark-lead-call-booked", action="store_true", help="Mark a lead call as booked.")
    parser.add_argument("--mark-lead-payment-received", action="store_true", help="Mark payment as received for a lead.")
    parser.add_argument("--mark-lead-delivery-started", action="store_true", help="Mark delivery as started for a won lead.")
    parser.add_argument("--mark-lead-delivery-completed", action="store_true", help="Mark delivery as completed for a won lead.")
    parser.add_argument("--request-lead-testimonial", action="store_true", help="Record a testimonial request for a customer.")
    parser.add_argument("--mark-lead-expansion", action="store_true", help="Record an upsell, renewal, or referral opportunity.")
    parser.add_argument("--convert-expansion-to-lead", action="store_true", help="Convert a referral or expansion signal into a new lead.")
    parser.add_argument("--generate-lead-expansion-proposal", action="store_true", help="Generate a renewal or upsell proposal for a lead.")
    parser.add_argument("--lead-id", type=str, default=None, help="Lead ID for --update-lead.")
    parser.add_argument("--lead-name", type=str, default=None, help="Lead name for --record-lead.")
    parser.add_argument("--lead-contact", type=str, default=None, help="Lead contact for --record-lead.")
    parser.add_argument("--lead-intent", type=str, default=None, help="Lead intent such as 周报 / 诊断 / 咨询.")
    parser.add_argument("--lead-source", type=str, default="direct_message", help="Lead source key.")
    parser.add_argument("--lead-notes", type=str, default="", help="Lead notes.")
    parser.add_argument("--lead-status", type=str, default=None, help="Lead status.")
    parser.add_argument("--lead-estimated-value", type=float, default=0.0, help="Estimated deal value for a lead.")
    parser.add_argument("--lead-actual-value", type=float, default=None, help="Actual won revenue for a lead update.")
    parser.add_argument("--lead-next-follow-up", type=str, default="", help="Next follow-up datetime or note.")
    parser.add_argument("--proposal-offer-name", type=str, default="", help="Offer name for --generate-lead-proposal.")
    parser.add_argument("--proposal-offer-key", type=str, default="", help="ASCII offer key for --generate-lead-proposal.")
    parser.add_argument("--proposal-price", type=str, default="", help="Custom price for --generate-lead-proposal.")
    parser.add_argument("--proposal-scope", type=str, default="", help="Custom scope text for --generate-lead-proposal.")
    parser.add_argument("--outreach-channel", type=str, default="", help="Outreach channel for --mark-lead-outreach.")
    parser.add_argument("--outreach-message", type=str, default="", help="Outreach message text for --mark-lead-outreach.")
    parser.add_argument("--reply-text", type=str, default="", help="Reply text for --triage-lead-reply.")
    parser.add_argument("--call-scheduled-for", type=str, default="", help="Scheduled time for --mark-lead-call-booked.")
    parser.add_argument("--payment-received-at", type=str, default="", help="Received time for --mark-lead-payment-received.")
    parser.add_argument("--expansion-type", type=str, default="", help="Expansion type such as renewal / upsell / referral.")
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

    if args.resume:
        from toutiao_content_factory.services.pipeline import ContentFactoryPipeline
        pipeline = ContentFactoryPipeline(settings, configure_logging(settings.logs_dir, settings.logging_level), task_id=args.resume, fast_mode=args.fast_mode)
        try:
            exported_files = pipeline.resume(args.resume, paste_text=args.paste)
            _print_export_summary(f"Pipeline resumed from checkpoint (task: {args.resume}).", exported_files)
        finally:
            pipeline.close()
        return 0
    refresh_heartbeat(settings.automation_dir / "scheduled_status.json")
    logger = configure_logging(settings.logs_dir, settings.logging_level)
    app_service = ContentFactoryAppService(settings, logger)
    exported_files: dict[str, str] = {}

    try:
        if args.daily_run or args.publish_latest_approved:
            try:
                profile_dir = Path(settings.publishing.user_data_dir)
                if not profile_dir.is_absolute():
                    profile_dir = PROJECT_ROOT / profile_dir
                guard_summary = prepare_browser_profile(
                    profile_dir=profile_dir,
                    project_root=PROJECT_ROOT,
                    logger=logger,
                    label="main_entry_precheck",
                )
                logger.info("Browser launch guard pre-check: %s", json.dumps(guard_summary, ensure_ascii=False))
            except Exception as exc:
                logger.warning("Browser launch guard pre-check failed (non-fatal): %s", exc)

        if args.reset_account:
            result = app_service.reset_account_state()
            _print_export_summary("Account automation state reset.", result)
            return 0

        if args.audit_account_artifacts:
            result = app_service.audit_account_artifacts()
            _print_export_summary("Account artifact audit completed.", result)
            return 0

        if args.cleanup_stale_account_artifacts:
            result = app_service.cleanup_stale_account_artifacts()
            _print_export_summary("Stale account artifacts moved to cleanup trash.", result)
            return 0

        if _is_review_or_publish_action(args):
            if args.init_publish_login:
                init_result = app_service.initialize_publish_login()
                _print_export_summary("Publish login session initialized.", init_result)
                return 0

            if args.init_wechat_login:
                init_result = app_service.initialize_wechat_publish_login()
                _print_export_summary("WeChat login session initialized.", init_result)
                return 0

            if args.init_135editor_login:
                init_result = app_service.initialize_135editor_login()
                _print_export_summary("135 Editor login session initialized.", init_result)
                return 0

            if args.init_douyin_login:
                init_result = app_service.initialize_douyin_publish_login()
                _print_export_summary("Douyin login session initialized.", init_result)
                return 0

            if args.prepare_douyin_run:
                result = app_service.prepare_douyin_run(args.run_dir)
                _print_export_summary("Douyin publish package prepared.", result)
                return 0

            if args.publish_douyin_run:
                result = app_service.publish_douyin_run(args.run_dir)
                _print_export_summary("Run submitted to Douyin.", result)
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

            if args.build_company_os:
                result = app_service.build_company_os()
                _print_export_summary("Company operating system blueprint built.", result)
                return 0

            if args.build_revenue_engine:
                result = app_service.build_revenue_engine()
                _print_export_summary("Revenue engine assets built.", result)
                return 0

            if args.run_profit_loop:
                result = app_service.run_profit_loop()
                _print_export_summary("Profit loop assets built.", result)
                return 0

            if args.execute_profit_actions:
                action_types = tuple(
                    item.strip()
                    for item in str(args.profit_action_types or "").split(",")
                    if item.strip()
                )
                result = app_service.execute_profit_actions(
                    action_types=action_types,
                    limit=max(0, int(args.profit_action_limit or 0)),
                )
                _print_export_summary("Profit actions executed.", result)
                return 0

            if args.update_sponsor_outreach:
                if not (args.sponsor_action_key and args.sponsor_status):
                    raise ValueError("--update-sponsor-outreach requires --sponsor-action-key and --sponsor-status.")
                result = app_service.update_sponsor_outreach_status(
                    action_key=args.sponsor_action_key,
                    status=args.sponsor_status,
                    notes=args.lead_notes,
                    next_follow_up_at=args.lead_next_follow_up,
                    deal_value=args.lead_actual_value,
                )
                _print_export_summary("Sponsor outreach status updated.", result)
                return 0

            if args.update_newsletter_subscriber:
                if not (args.subscriber_key and args.subscriber_status):
                    raise ValueError("--update-newsletter-subscriber requires --subscriber-key and --subscriber-status.")
                result = app_service.update_newsletter_subscriber_status(
                    subscriber_key=args.subscriber_key,
                    status=args.subscriber_status,
                    notes=args.lead_notes,
                    next_follow_up_at=args.lead_next_follow_up,
                    member_revenue=args.lead_actual_value,
                )
                _print_export_summary("Newsletter subscriber status updated.", result)
                return 0

            if args.ensure_lead_assets:
                result = app_service.ensure_lead_assets()
                _print_export_summary("Lead capture assets built.", result)
                return 0

            if args.record_lead:
                if not (args.lead_name and args.lead_contact and args.lead_intent):
                    raise ValueError("--record-lead requires --lead-name, --lead-contact, and --lead-intent.")
                result = app_service.record_lead(
                    name=args.lead_name,
                    contact=args.lead_contact,
                    intent=args.lead_intent,
                    source=args.lead_source,
                    notes=args.lead_notes,
                    status=args.lead_status or "new",
                    estimated_value=args.lead_estimated_value,
                    next_follow_up_at=args.lead_next_follow_up,
                )
                _print_export_summary("Lead recorded.", result)
                return 0

            if args.update_lead:
                if not args.lead_id:
                    raise ValueError("--update-lead requires --lead-id.")
                result = app_service.update_lead(
                    lead_id=args.lead_id,
                    status=args.lead_status or None,
                    notes=args.lead_notes or None,
                    estimated_value=args.lead_estimated_value if args.lead_estimated_value else None,
                    actual_value=args.lead_actual_value,
                    next_follow_up_at=args.lead_next_follow_up or None,
                )
                _print_export_summary("Lead updated.", result)
                return 0

            if args.generate_lead_proposal:
                if not (args.lead_id and (args.proposal_offer_key or args.proposal_offer_name)):
                    raise ValueError("--generate-lead-proposal requires --lead-id and either --proposal-offer-key or --proposal-offer-name.")
                result = app_service.generate_lead_proposal(
                    lead_id=args.lead_id,
                    offer_name=args.proposal_offer_name,
                    offer_key=args.proposal_offer_key,
                    custom_price=args.proposal_price,
                    custom_scope=args.proposal_scope,
                    next_follow_up_at=args.lead_next_follow_up,
                )
                _print_export_summary("Lead proposal generated.", result)
                return 0

            if args.mark_lead_outreach:
                if not args.lead_id:
                    raise ValueError("--mark-lead-outreach requires --lead-id.")
                result = app_service.mark_lead_outreach_sent(
                    lead_id=args.lead_id,
                    channel=args.outreach_channel,
                    message=args.outreach_message,
                    next_follow_up_at=args.lead_next_follow_up,
                )
                _print_export_summary("Lead outreach marked as sent.", result)
                return 0

            if args.triage_lead_reply:
                if not (args.lead_id and args.reply_text):
                    raise ValueError("--triage-lead-reply requires --lead-id and --reply-text.")
                result = app_service.triage_lead_reply(
                    lead_id=args.lead_id,
                    reply_text=args.reply_text,
                    next_follow_up_at=args.lead_next_follow_up,
                )
                _print_export_summary("Lead reply triaged.", result)
                return 0

            if args.mark_lead_proposal_sent:
                if not args.lead_id:
                    raise ValueError("--mark-lead-proposal-sent requires --lead-id.")
                result = app_service.mark_lead_proposal_sent(
                    lead_id=args.lead_id,
                    channel=args.outreach_channel,
                    message=args.outreach_message,
                    next_follow_up_at=args.lead_next_follow_up,
                )
                _print_export_summary("Lead proposal marked as sent.", result)
                return 0

            if args.mark_lead_call_booked:
                if not (args.lead_id and args.call_scheduled_for):
                    raise ValueError("--mark-lead-call-booked requires --lead-id and --call-scheduled-for.")
                result = app_service.mark_lead_call_booked(
                    lead_id=args.lead_id,
                    call_scheduled_for=args.call_scheduled_for,
                    notes=args.lead_notes,
                )
                _print_export_summary("Lead call marked as booked.", result)
                return 0

            if args.mark_lead_payment_received:
                if not (args.lead_id and args.lead_actual_value):
                    raise ValueError("--mark-lead-payment-received requires --lead-id and --lead-actual-value.")
                result = app_service.mark_lead_payment_received(
                    lead_id=args.lead_id,
                    amount=args.lead_actual_value,
                    received_at=args.payment_received_at,
                    notes=args.lead_notes,
                )
                _print_export_summary("Lead payment marked as received.", result)
                return 0

            if args.mark_lead_delivery_started:
                if not args.lead_id:
                    raise ValueError("--mark-lead-delivery-started requires --lead-id.")
                result = app_service.mark_lead_delivery_started(
                    lead_id=args.lead_id,
                    delivery_notes=args.lead_notes,
                )
                _print_export_summary("Lead delivery marked as started.", result)
                return 0

            if args.mark_lead_delivery_completed:
                if not args.lead_id:
                    raise ValueError("--mark-lead-delivery-completed requires --lead-id.")
                result = app_service.mark_lead_delivery_completed(
                    lead_id=args.lead_id,
                    delivery_notes=args.lead_notes,
                )
                _print_export_summary("Lead delivery marked as completed.", result)
                return 0

            if args.request_lead_testimonial:
                if not args.lead_id:
                    raise ValueError("--request-lead-testimonial requires --lead-id.")
                result = app_service.request_lead_testimonial(
                    lead_id=args.lead_id,
                    message=args.lead_notes,
                )
                _print_export_summary("Lead testimonial request recorded.", result)
                return 0

            if args.mark_lead_expansion:
                if not (args.lead_id and args.expansion_type):
                    raise ValueError("--mark-lead-expansion requires --lead-id and --expansion-type.")
                result = app_service.mark_lead_expansion_opportunity(
                    lead_id=args.lead_id,
                    expansion_type=args.expansion_type,
                    notes=args.lead_notes,
                )
                _print_export_summary("Lead expansion opportunity recorded.", result)
                return 0

            if args.convert_expansion_to_lead:
                if not (args.lead_id and args.lead_name and args.lead_contact):
                    raise ValueError("--convert-expansion-to-lead requires --lead-id, --lead-name, and --lead-contact.")
                result = app_service.convert_expansion_to_lead(
                    source_lead_id=args.lead_id,
                    referral_name=args.lead_name,
                    referral_contact=args.lead_contact,
                    referral_intent=args.lead_intent or "咨询",
                    notes=args.lead_notes,
                )
                _print_export_summary("Expansion signal converted into a new lead.", result)
                return 0

            if args.generate_lead_expansion_proposal:
                if not (args.lead_id and args.expansion_type):
                    raise ValueError("--generate-lead-expansion-proposal requires --lead-id and --expansion-type.")
                result = app_service.generate_lead_expansion_proposal(
                    lead_id=args.lead_id,
                    expansion_type=args.expansion_type,
                    next_follow_up_at=args.lead_next_follow_up,
                )
                _print_export_summary("Expansion proposal generated.", result)
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

            if args.health_preflight:
                health_exports = app_service.collect_platform_health()
                _print_export_summary("Platform health preflight collected.", health_exports)
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
                publish_exports = app_service.publish_run(args.run_dir, draft_only=args.draft_only)
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
                publish_exports = app_service.publish_latest_approved(unpublished_only=True, draft_only=args.draft_only)
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
                skip_wait=args.skip_wait,
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
    os.environ.setdefault("PYTHONUTF8", "1")
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
            args.health_preflight,
            args.init_publish_login,
            args.init_wechat_login,
            args.init_135editor_login,
            args.init_douyin_login,
            args.prepare_douyin_run,
            args.publish_douyin_run,
            args.prepare_wechat_launch_day is not None,
            args.prepare_wechat_launch_week,
            args.generate_images_run,
            args.build_company_os,
            args.build_revenue_engine,
            args.run_profit_loop,
            args.execute_profit_actions,
            args.update_sponsor_outreach,
            args.update_newsletter_subscriber,
            args.ensure_lead_assets,
            args.record_lead,
            args.update_lead,
            args.generate_lead_proposal,
            args.mark_lead_outreach,
            args.triage_lead_reply,
            args.mark_lead_proposal_sent,
            args.mark_lead_call_booked,
            args.mark_lead_payment_received,
            args.mark_lead_delivery_started,
            args.mark_lead_delivery_completed,
            args.request_lead_testimonial,
            args.mark_lead_expansion,
            args.convert_expansion_to_lead,
            args.generate_lead_expansion_proposal,
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
        status = "success"
        message = "Scheduled pipeline finished generation, but one or more platform publish steps failed. Treating as success since content was generated."
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
