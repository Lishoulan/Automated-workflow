from __future__ import annotations

import argparse
import faulthandler
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

faulthandler.enable()

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
    parser.add_argument(
        "--send-daily-report",
        action="store_true",
        help="Send the date-scoped notification digest and consume at most one daily report quota.",
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
    parser.add_argument(
        "--publish-wtt-run",
        action="store_true",
        help="Publish an approved run as a WeiToutiao (微头条) short post.",
    )
    parser.add_argument(
        "--publish-wtt-latest-approved",
        action="store_true",
        help="Publish the latest approved unpublished run as a WeiToutiao (微头条) short post.",
    )
    parser.add_argument(
        "--publish-wtt-standalone",
        type=int,
        default=None,
        metavar="N",
        help="Generate and publish N independent WeiToutiao (微头条) posts with their own topics (高频化).",
    )
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
        help="Run a unified platform session health preflight for Toutiao / WeChat.",
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
    parser.add_argument("--lead-content-id", type=str, default="", help="Source content_id for lead attribution.")
    parser.add_argument("--lead-cta-source-code", type=str, default="", help="CTA source code for lead attribution.")
    parser.add_argument("--lead-platform", type=str, default="", help="Source platform for CTA attribution, e.g. toutiao or wechat.")
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
    parser.add_argument(
        "--build-revenue-attribution-report",
        action="store_true",
        help="Build revenue attribution, content mix, cooling, and unit economics reports.",
    )
    parser.add_argument(
        "--register-content-identity",
        action="store_true",
        help="Register a stable content_id for a published run.",
    )
    parser.add_argument("--record-content-cost", action="store_true", help="Record production cost for a content_id.")
    parser.add_argument("--record-content-revenue", action="store_true", help="Record a revenue event for a content_id.")
    parser.add_argument("--compute-unit-economics", action="store_true", help="Compute unit economics for a content_id.")
    parser.add_argument("--content-id", type=str, default=None, help="Content ID for revenue attribution commands.")
    parser.add_argument("--api-cost", type=float, default=0.0, help="API cost for --record-content-cost.")
    parser.add_argument("--image-cost", type=float, default=0.0, help="Image generation cost for --record-content-cost.")
    parser.add_argument("--labor-minutes", type=float, default=0.0, help="Labor minutes for --record-content-cost.")
    parser.add_argument("--labor-rate-per-hour", type=float, default=0.0, help="Labor rate per hour for --record-content-cost.")
    parser.add_argument("--revenue-event-type", type=str, default="", help="Event type for --record-content-revenue.")
    parser.add_argument("--revenue-amount", type=float, default=0.0, help="Amount for --record-content-revenue.")
    parser.add_argument("--revenue-currency", type=str, default="CNY", help="Currency for --record-content-revenue.")
    parser.add_argument("--revenue-lead-id", type=str, default="", help="Lead ID for --record-content-revenue.")
    parser.add_argument("--revenue-product-key", type=str, default="", help="Product key for --record-content-revenue.")
    parser.add_argument("--revenue-channel", type=str, default="", help="Channel for --record-content-revenue.")
    parser.add_argument("--revenue-note", type=str, default="", help="Note for --record-content-revenue.")
    parser.add_argument("--revenue-event-id", type=str, default="", help="Stable event ID for idempotent revenue callbacks.")
    parser.add_argument("--replace-existing-revenue", action="store_true", help="Replace an existing revenue event with the same event ID.")
    parser.add_argument("--platform-revenue", type=float, default=0.0, help="Platform revenue for --compute-unit-economics.")
    parser.add_argument("--commercial-intent", type=str, default="medium", help="Commercial intent for --register-content-identity.")
    parser.add_argument("--content-mix", type=str, default="professional", help="Content mix for --register-content-identity.")
    parser.add_argument("--cta-variant", type=str, default="", help="CTA variant for --register-content-identity.")
    parser.add_argument("--cta-source-code", type=str, default="", help="CTA source code for --register-content-identity.")
    parser.add_argument("--title-variant", type=str, default="", help="Title variant for --register-content-identity.")
    parser.add_argument("--cover-variant", type=str, default="", help="Cover variant for --register-content-identity.")
    parser.add_argument("--platform-variant", type=str, default="", help="Platform variant for --register-content-identity.")
    parser.add_argument("--topic-bucket", type=str, default="", help="Stable topic bucket for --register-content-identity.")
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
    parser.add_argument("--snapshot-label", type=str, default=None, help="Maturity label for performance metrics, e.g. 1h/6h/24h/72h.")
    parser.add_argument("--snapshot-at", type=str, default=None, help="Actual observation timestamp for performance metrics.")
    parser.add_argument("--published-date", type=str, default=None, help="Article publication date for performance metrics.")
    parser.add_argument("--earnings-date", type=str, default=None, help="Date to which platform earnings belong (Toutiao defaults to yesterday).")
    parser.add_argument("--availability-status", type=str, default="available", choices=("available", "pending_t1", "not_available"), help="Whether the article data is eligible for analysis.")
    parser.add_argument("--revenue-status", type=str, default="unknown", choices=("available", "partial", "pending_t1", "unknown", "not_available"), help="Revenue data availability for a performance snapshot.")
    parser.add_argument("--platform-revenue-status", type=str, default="unknown", choices=("available", "pending_t1", "unknown", "not_available"), help="Platform earnings availability for a performance snapshot.")
    parser.add_argument("--platform-revenue-amount", type=float, default=None, help="Known platform earnings amount for a performance snapshot.")
    parser.add_argument("--revenue-amount-known", action="store_true", help="Mark the performance snapshot revenue amount as known, including a known zero.")
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
    parser.add_argument(
        "--deep-research",
        type=str,
        default=None,
        help="Run DeerFlow deep research on a given topic and output the report.",
    )
    parser.add_argument("--engagement", action="store_true", help="Run daily engagement agent (like, comment, follow).")
    parser.add_argument("--engagement-mine-topics", action="store_true", help="Mine comments for topic ideas.")
    parser.add_argument("--multi-publish", action="store_true", help="Publish to all enabled platforms.")
    parser.add_argument("--platforms", type=str, default=None, help="Comma-separated list of platforms for --multi-publish (e.g., bilibili,zhihu).")
    parser.add_argument("--matrix-dashboard", action="store_true", help="Show matrix operations dashboard.")
    parser.add_argument("--monetization-sync", action="store_true", help="Sync monetization data and generate revenue report.")
    parser.add_argument("--collect-performance", action="store_true", help="Collect performance data (views/likes/comments/shares) from creator dashboards.")
    parser.add_argument("--feedback-loop", action="store_true", help="Run full feedback loop: collect performance data, evolve writing rules, update prompts.")
    parser.add_argument("--monthly-review", action="store_true", help="运行 30 天数据回顾 Agent，生成回顾报告、迭代建议与演化提案。")
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

    # 提前初始化 logger，供 deep_research / engagement / monetization 等早期分支使用
    logger = configure_logging(settings.logs_dir, settings.logging_level)

    if args.resume:
        from toutiao_content_factory.services.pipeline import ContentFactoryPipeline
        pipeline = ContentFactoryPipeline(settings, configure_logging(settings.logs_dir, settings.logging_level), task_id=args.resume, fast_mode=args.fast_mode)
        try:
            exported_files = pipeline.resume(args.resume, paste_text=args.paste)
            _print_export_summary(f"Pipeline resumed from checkpoint (task: {args.resume}).", exported_files)
        finally:
            pipeline.close()
        return 0
    if args.deep_research:
        from toutiao_content_factory.services.deerflow_client import DeerFlowClient
        client = DeerFlowClient(settings.deerflow_base_url, logger)
        result = client.deep_research(args.deep_research)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("Deep research failed or DeerFlow service unavailable.")
        return 0
    if args.engagement:
        from toutiao_content_factory.services.engagement_agent import EngagementAgent
        agent = EngagementAgent(settings, logger)
        records = agent.run_daily_engagement("toutiao", args.account or "default")
        print(f"Engagement completed: {len(records)} actions")
        return 0
    if args.engagement_mine_topics:
        from toutiao_content_factory.services.engagement_agent import EngagementAgent
        agent = EngagementAgent(settings, logger)
        topics = agent.mine_comments_for_topics("toutiao", args.account or "default")
        print(json.dumps(topics, ensure_ascii=False, indent=2))
        return 0
    if args.multi_publish:
        from toutiao_content_factory.services.multi_platform_publisher import MultiPlatformPublisher
        from toutiao_content_factory.services.bilibili_publisher import BilibiliPublisher
        from toutiao_content_factory.services.zhihu_publisher import ZhihuPublisher
        from toutiao_content_factory.services.xiaohongshu_publisher import XiaohongshuPublisher
        mpp = MultiPlatformPublisher(settings, logger)
        if settings.bilibili_enabled:
            mpp.register_publisher("bilibili", BilibiliPublisher(settings, logger))
        if settings.zhihu_enabled:
            mpp.register_publisher("zhihu", ZhihuPublisher(settings, logger))
        if settings.xiaohongshu_enabled:
            mpp.register_publisher("xiaohongshu", XiaohongshuPublisher(settings, logger))
        platforms = args.platforms.split(",") if args.platforms else None
        print(f"Available platforms: {mpp.get_available_platforms()}")
        return 0
    if args.matrix_dashboard:
        from toutiao_content_factory.services.matrix_ops_manager import MatrixOpsManager
        mgr = MatrixOpsManager(settings, logger)
        data = mgr.get_dashboard_data()
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.monetization_sync:
        from toutiao_content_factory.services.monetization_engine import MonetizationEngine
        engine = MonetizationEngine(settings, logger)
        report = engine.get_revenue_report("monthly")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.collect_performance:
        from toutiao_content_factory.services.performance_service import PerformanceService
        service = PerformanceService(settings, logger)
        result = service.collect_performance_data()
        print(f"Performance data collected: {len(result.get('records', []))} records")
        if result.get("errors"):
            print(f"Errors: {result['errors']}")
        # 采集后自动运行反馈闭环：数据 → EvolutionAgent 规则 → prompt 优化
        if args.feedback_loop:
            from toutiao_content_factory.services.feedback_loop_service import FeedbackLoopService
            from toutiao_content_factory.services.openai_service import OpenAIContentService
            from toutiao_content_factory.services.prompt_repository import PromptRepository
            loop = FeedbackLoopService(
                settings,
                logger,
                prompt_repository=PromptRepository(settings.prompts_dir),
                openai_service=OpenAIContentService(settings, logger),
            )
            loop_result = loop.run_full_loop()
            print(f"Feedback loop completed: {loop_result.get('status', 'unknown')}")
        return 0
    if args.feedback_loop:
        from toutiao_content_factory.services.feedback_loop_service import FeedbackLoopService
        from toutiao_content_factory.services.openai_service import OpenAIContentService
        from toutiao_content_factory.services.prompt_repository import PromptRepository
        loop = FeedbackLoopService(
            settings,
            logger,
            prompt_repository=PromptRepository(settings.prompts_dir),
            openai_service=OpenAIContentService(settings, logger),
        )
        result = loop.run_full_loop()
        print(f"Feedback loop completed: {result.get('status', 'unknown')}")
        return 0
    if args.monthly_review:
        from toutiao_content_factory.agents.monthly_review_agent import MonthlyReviewAgent
        agent = MonthlyReviewAgent(account=args.account or "default", logger=logger)
        result = agent.run()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.send_daily_report:
        from toutiao_content_factory.services.notification_service import NotificationService

        notification_service = NotificationService(
            dict(os.environ),
            fallback_log_dir=str(settings.output_dir / "automation"),
        )
        result = notification_service.send_daily_report()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {
            "sent",
            "already_sent",
            "quota_exhausted",
            "no_channels",
            "no_events",
        } else 1

    refresh_heartbeat(settings.automation_dir / "scheduled_status.json")
    if args.daily_run:
        _mark_daily_run_running(settings)
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
                    source_content_id=args.lead_content_id,
                    cta_source_code=args.lead_cta_source_code,
                    platform=args.lead_platform,
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
                    snapshot_label=args.snapshot_label,
                    snapshot_at=args.snapshot_at,
                    published_date=args.published_date,
                    earnings_date=args.earnings_date,
                    availability_status=args.availability_status,
                    revenue_status=args.revenue_status,
                    platform_revenue_status=args.platform_revenue_status,
                    revenue_amount_known=args.revenue_amount_known,
                    platform_revenue=args.platform_revenue_amount,
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

            if args.build_revenue_attribution_report:
                result = app_service.build_revenue_attribution_report()
                _print_export_summary("Revenue attribution report built.", result)
                return 0

            if args.register_content_identity:
                resolved_run_dir = app_service.resolve_run_dir(args.run_dir)
                publish_meta_path = resolved_run_dir / app_service.settings.review.publish_dirname / "publish_meta.json"
                publish_meta_payload: dict[str, object] = {}
                if publish_meta_path.exists():
                    try:
                        publish_meta_payload = json.loads(publish_meta_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        publish_meta_payload = {}
                selected_topic_payload = publish_meta_payload.get("selected_topic") or {}
                title_text = str(
                    args.selected_title
                    or publish_meta_payload.get("selected_title", "")
                    or ""
                ).strip()
                topic_title_text = str(selected_topic_payload.get("title", "")).strip()
                published_at_text = str(publish_meta_payload.get("approved_at", "")).strip()
                result = app_service.register_content_identity(
                    platform=args.performance_platform,
                    run_dir=resolved_run_dir,
                    title=title_text,
                    topic_title=topic_title_text,
                    published_at=published_at_text,
                    commercial_intent=str(args.commercial_intent),
                    content_mix=str(args.content_mix),
                    cta_variant=str(args.cta_variant),
                    cta_source_code=str(args.cta_source_code),
                    title_variant=str(args.title_variant),
                    cover_variant=str(args.cover_variant),
                    platform_variant=str(args.platform_variant),
                    topic_bucket=str(args.topic_bucket),
                )
                _print_export_summary("Content identity registered.", result)
                return 0

            if args.record_content_cost:
                if not args.content_id:
                    print("--content-id is required for --record-content-cost.")
                    return 1
                result = app_service.record_content_cost(
                    args.content_id,
                    api_cost=args.api_cost,
                    image_cost=args.image_cost,
                    labor_minutes=args.labor_minutes,
                    labor_rate_per_hour=args.labor_rate_per_hour,
                )
                _print_export_summary("Content cost recorded.", result)
                return 0

            if args.record_content_revenue:
                if not args.content_id:
                    print("--content-id is required for --record-content-revenue.")
                    return 1
                if not args.revenue_event_type:
                    print("--revenue-event-type is required for --record-content-revenue.")
                    return 1
                result = app_service.record_content_revenue_event(
                    args.content_id,
                    event_type=args.revenue_event_type,
                    amount=args.revenue_amount,
                    currency=args.revenue_currency,
                    lead_id=args.revenue_lead_id,
                    product_key=args.revenue_product_key,
                    channel=args.revenue_channel,
                    note=args.revenue_note,
                    event_id=args.revenue_event_id,
                    replace_existing=args.replace_existing_revenue,
                )
                _print_export_summary("Content revenue event recorded.", result)
                return 0

            if args.compute_unit_economics:
                if not args.content_id:
                    print("--content-id is required for --compute-unit-economics.")
                    return 1
                result = app_service.compute_unit_economics(
                    args.content_id,
                    platform_revenue=args.platform_revenue,
                )
                _print_export_summary("Unit economics computed.", result)
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

            if args.publish_wtt_run:
                publish_exports = app_service.publish_weitoutiao_run(args.run_dir, draft_only=args.draft_only)
                _print_export_summary("WeiToutiao publish command completed.", publish_exports)
                return 0

            if args.publish_wtt_latest_approved:
                publish_exports = app_service.publish_weitoutiao_latest_approved(draft_only=args.draft_only)
                _print_export_summary("Latest approved unpublished run published to WeiToutiao.", publish_exports)
                return 0

            if args.publish_wtt_standalone is not None:
                publish_exports = app_service.publish_standalone_weitoutiao(
                    count=max(1, args.publish_wtt_standalone),
                    draft_only=args.draft_only,
                )
                _print_export_summary(
                    f"Standalone WeiToutiao batch ({args.publish_wtt_standalone} target) completed.",
                    publish_exports,
                )
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
    return 1 if result_status in {
        "failed",
        "partial_success",
        "publish_login_required",
        "publish_uncertain",
    } else 0


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
            args.publish_wtt_run,
            args.publish_wtt_latest_approved,
            args.publish_wtt_standalone is not None,
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
            args.build_revenue_attribution_report,
            args.register_content_identity,
            args.record_content_cost,
            args.record_content_revenue,
            args.compute_unit_economics,
            args.collect_performance,
            args.feedback_loop,
            args.monthly_review,
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
        "exit_code": 1,
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
    run_outcome = str(exported_files.get("status", "")).strip().lower()
    run_outcome_message = str(exported_files.get("message", "")).strip()
    workflow_status = str(run_summary.get("workflow_status", "")).strip()
    review_status = str(run_summary.get("review_status", "")).strip()
    publish_status = str(run_summary.get("publish_status", "")).strip()
    wechat_status = str(run_summary.get("wechat_status", "")).strip()
    weitoutiao_status = str(run_summary.get("weitoutiao_status", "")).strip().lower()
    weitoutiao_message = str(run_summary.get("weitoutiao_message", "")).strip()

    failure_statuses = {
        "failed",
        "error",
        "blocked",
        "blocked_by_ip",
        "publish_login_required",
        "publish_uncertain",
        "circuit_open",
        "missing",
        "invalid",
    }
    graphic_submitted = publish_status.strip().lower() in (_DAILY_PUBLISH_SUCCESS_STATUSES - {"publish_uncertain"})
    weitoutiao_needs_follow_up = weitoutiao_status in (
        failure_statuses
        | {"partial_success", "draft_only", "no_topics"}
    )
    weitoutiao_partial_message = (
        f"图文发布已完成（{publish_status or 'submitted'}），但"
        f"{'微头条补发部分成功但仍有失败项' if weitoutiao_status == 'partial_success' else '微头条补发失败'}。"
        f"{weitoutiao_message or '请查看微头条日报结果并进行后续处理。'}"
    )
    if run_outcome in {"no_topics", "already_published", "skipped"}:
        if run_outcome == "already_published" and graphic_submitted and weitoutiao_needs_follow_up:
            status = "partial_success"
            message = weitoutiao_partial_message
        else:
            status = run_outcome
            message = run_outcome_message or "Scheduled run completed without generating a new article."
    elif run_outcome == "blocked":
        status = "publish_login_required"
        message = run_outcome_message or "Scheduled pipeline was blocked by platform health checks."
    elif graphic_submitted and weitoutiao_needs_follow_up:
        status = "partial_success"
        message = weitoutiao_partial_message
    elif workflow_status == "platform_publish_failed":
        if publish_status == "publish_login_required":
            status = "publish_login_required"
        elif publish_status == "publish_uncertain":
            status = "publish_uncertain"
        else:
            status = "failed"
        message = "Scheduled pipeline generated content, but the platform publish step did not complete successfully."
    elif workflow_status == "platform_partial_success":
        status = "partial_success"
        message = "Scheduled pipeline partially succeeded. At least one platform completed, but another platform failed."
    elif publish_status in failure_statuses or wechat_status in failure_statuses:
        if publish_status == "publish_login_required":
            status = "publish_login_required"
        elif publish_status == "publish_uncertain":
            status = "publish_uncertain"
        else:
            status = "failed"
        message = "Scheduled pipeline finished, but a platform result is not publishable."
    elif publish_status == "draft_saved":
        status = "draft_only"
        message = "Scheduled pipeline saved a draft; the article was not submitted to Toutiao."
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
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_scheduled_runtime_status(
        settings,
        summary=payload,
        status=status,
        message=message,
    )


def _build_daily_run_summary(settings, exported_files: dict[str, str]) -> dict[str, Any]:
    _hydrate_already_published_exports(settings, exported_files)
    summary: dict[str, Any] = {
        "run_dir": "",
        "run_name": "",
        "content_id": "",
        "run_outcome": str(exported_files.get("status", "")).strip(),
        "run_outcome_message": str(exported_files.get("message", "")).strip(),
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
        "weitoutiao": {},
        "weitoutiao_status": "",
        "weitoutiao_message": "",
        "weitoutiao_topup_status": "",
        "weitoutiao_topup_result": "",
        "weitoutiao_result": "",
    }
    run_dir = _resolve_run_dir_from_exports(exported_files)
    workflow_report_path = exported_files.get("workflow_report") or str(settings.automation_dir / "last_daily_workflow_report.json")
    workflow_report = _load_json_file(Path(workflow_report_path)) or {}
    summary["workflow_status"] = str(workflow_report.get("status", "")).strip()
    run_outcome = str(exported_files.get("status", "")).strip().lower()
    if run_outcome in {"already_published", "no_topics", "skipped"}:
        # The previous workflow report can belong to an older run. The short
        # circuit outcome is authoritative for this invocation.
        summary["workflow_status"] = run_outcome
    final_attempt = workflow_report.get("final_attempt", {}) if isinstance(workflow_report, dict) else {}
    if isinstance(final_attempt, dict):
        raw_errors = final_attempt.get("platform_errors", [])
        if isinstance(raw_errors, list):
            summary["workflow_errors"] = [str(item) for item in raw_errors]

    workflow_platforms = final_attempt.get("platform_results", {}) if isinstance(final_attempt, dict) else {}
    workflow_weitoutiao = (
        workflow_platforms.get("weitoutiao", {})
        if isinstance(workflow_platforms, dict)
        else {}
    )
    if not isinstance(workflow_weitoutiao, dict):
        workflow_weitoutiao = {}

    daily_result_path = str(exported_files.get("weitoutiao_daily_result", "")).strip()
    if not daily_result_path and run_dir is not None:
        candidate = run_dir / settings.review.publish_dirname / "weitoutiao_daily_result.json"
        if candidate.exists():
            daily_result_path = str(candidate)
    daily_result = _load_json_file(Path(daily_result_path)) if daily_result_path else None
    daily_result = daily_result if isinstance(daily_result, dict) else {}

    topup_result_path = str(
        exported_files.get("standalone_topup_result")
        or exported_files.get("standalone_topup_latest_result")
        or ""
    ).strip()
    nested_topup = daily_result.get("topup", {}) if isinstance(daily_result, dict) else {}
    if not isinstance(nested_topup, dict):
        nested_topup = {}
    if not topup_result_path:
        topup_result_path = str(nested_topup.get("result_path", "")).strip()
    topup_result = _load_json_file(Path(topup_result_path)) if topup_result_path else None
    topup_result = topup_result if isinstance(topup_result, dict) else {}

    run_outcome = str(exported_files.get("status", "")).strip().lower()
    weitoutiao_payload = dict(daily_result)
    if not weitoutiao_payload and run_outcome not in {"already_published", "no_topics", "skipped"}:
        weitoutiao_payload = dict(workflow_weitoutiao)
    if topup_result and "topup" not in weitoutiao_payload:
        weitoutiao_payload["topup"] = topup_result
    if not weitoutiao_payload and topup_result:
        weitoutiao_payload = {
            "status": str(topup_result.get("status", "")).strip() or "not_attempted",
            "message": str(topup_result.get("message", "")).strip(),
            "topup": topup_result,
        }

    topup_status = str(
        topup_result.get("status")
        or nested_topup.get("status")
        or exported_files.get("standalone_topup_status")
        or ""
    ).strip()
    weitoutiao_status = str(
        weitoutiao_payload.get("status")
        or exported_files.get("weitoutiao_status")
        or ""
    ).strip()
    if not weitoutiao_status and topup_status:
        weitoutiao_status = topup_status
    weitoutiao_message = str(
        weitoutiao_payload.get("message")
        or exported_files.get("weitoutiao_message")
        or topup_result.get("message")
        or nested_topup.get("message")
        or exported_files.get("standalone_topup_summary")
        or ""
    ).strip()
    if not weitoutiao_payload and (weitoutiao_status or topup_status):
        weitoutiao_payload = {
            "status": weitoutiao_status or topup_status,
            "message": weitoutiao_message,
            "topup": topup_result or {
                "status": topup_status,
                "message": weitoutiao_message,
            },
        }
    if weitoutiao_payload:
        weitoutiao_payload.setdefault("status", weitoutiao_status)
        weitoutiao_payload.setdefault("message", weitoutiao_message)
        summary["weitoutiao"] = weitoutiao_payload
    summary["weitoutiao_status"] = weitoutiao_status
    summary["weitoutiao_message"] = weitoutiao_message
    summary["weitoutiao_topup_status"] = topup_status
    summary["weitoutiao_topup_result"] = topup_result_path
    summary["weitoutiao_result"] = daily_result_path

    if run_dir is None:
        return summary

    summary["run_dir"] = str(run_dir)
    summary["run_name"] = run_dir.name

    workflow_result = _load_json_file(run_dir / "workflow_result.json") or {}
    if isinstance(workflow_result, dict):
        summary["content_id"] = str(workflow_result.get("content_id", "")).strip()

    review_status = _load_json_file(run_dir / "review_status.json") or {}
    publish_result = _load_json_file(run_dir / settings.review.publish_dirname / "publish_result.json") or {}
    wechat_result = _load_json_file(run_dir / settings.review.publish_dirname / "wechat" / "wechat_publish_result.json") or {}
    publish_record = publish_result.get("record", {}) if isinstance(publish_result, dict) else {}
    if not isinstance(publish_record, dict):
        publish_record = {}
    backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}
    if not isinstance(backend, dict):
        backend = {}
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
    summary["selected_title"] = (
        str(review_status.get("selected_title", "")).strip()
        or str(publish_result.get("title", "")).strip()
        or str(publish_record.get("title", "")).strip()
        or str(exported_files.get("selected_title", "")).strip()
    )
    summary["publish_status"] = str(publish_result.get("status", "")).strip()
    if not summary["content_id"] and isinstance(publish_result, dict):
        summary["content_id"] = str(publish_result.get("content_id", "")).strip()
    if not summary["content_id"]:
        summary["content_id"] = str(exported_files.get("content_id", "")).strip()
    summary["publish_url"] = (
        str(publish_result.get("external_url", "")).strip()
        or str(exported_files.get("publish_url", "")).strip()
    )
    summary["publish_message"] = (
        str(backend.get("message", "")).strip()
        or str(publish_result.get("message", "")).strip()
        or str(exported_files.get("publish_message", "")).strip()
    )
    summary["publish_pgc_id"] = (
        str(backend.get("pgc_id", "")).strip()
        or str(publish_result.get("pgc_id", "")).strip()
        or str(exported_files.get("publish_pgc_id", "")).strip()
    )
    summary["wechat_status"] = wechat_status
    summary["wechat_submit_status"] = wechat_submit_status
    summary["wechat_submit_requested"] = bool(wechat_result.get("submit_requested", False))
    summary["wechat_publish_url"] = wechat_publish_url
    summary["wechat_draft_media_id"] = wechat_draft_media_id
    return summary


_DAILY_PUBLISH_SUCCESS_STATUSES = {
    "published",
    "submitted_for_review",
    "submitted",
    "success",
    "publish_uncertain",
}


def _hydrate_already_published_exports(settings, exported_files: dict[str, Any]) -> None:
    """Restore the latest same-day graphic run for an already-published result."""
    if str(exported_files.get("status", "")).strip().lower() != "already_published":
        return

    history_path = settings.output_dir / settings.publishing.history_filename
    if not history_path.exists():
        return
    try:
        history_payload = json.loads(history_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(history_payload, dict):
        history = history_payload.get("records", history_payload.get("history", []))
    else:
        history = history_payload
    if not isinstance(history, list):
        return

    today = datetime.now().date().isoformat()
    platform = str(settings.publishing.platform or "toutiao").strip()
    candidates: list[dict[str, Any]] = []
    for index, record in enumerate(history):
        if not isinstance(record, dict):
            continue
        if str(record.get("platform", "")).strip() != platform:
            continue
        if str(record.get("status", "")).strip().lower() not in _DAILY_PUBLISH_SUCCESS_STATUSES:
            continue
        published_at = str(
            record.get("published_at")
            or record.get("timestamp")
            or record.get("created_at")
            or ""
        ).strip()
        if not published_at.startswith(today):
            continue
        candidates.append({"index": index, "record": record, "published_at": published_at})
    if not candidates:
        return

    candidates.sort(key=lambda item: (str(item["published_at"]), int(item["index"])), reverse=True)
    selected = candidates[0]["record"]
    raw_run_dir = str(selected.get("run_dir", "")).strip()
    if not raw_run_dir:
        return
    run_dir = Path(raw_run_dir)
    if not run_dir.is_absolute():
        run_dir = settings.project_root / run_dir
    try:
        run_dir = run_dir.resolve()
    except OSError:
        return
    if not run_dir.is_dir():
        return

    def set_if_missing(key: str, value: object) -> None:
        if str(exported_files.get(key, "")).strip() or not str(value or "").strip():
            return
        exported_files[key] = str(value)

    publish_dir = run_dir / settings.review.publish_dirname
    publish_result_path = publish_dir / "publish_result.json"
    review_status_path = run_dir / "review_status.json"
    workflow_result_path = run_dir / "workflow_result.json"
    set_if_missing("resolved_run_dir", run_dir)
    set_if_missing("archive_root", run_dir)
    set_if_missing("published_run_dir", run_dir)
    if publish_result_path.exists():
        set_if_missing("publish_result", publish_result_path)
    if review_status_path.exists():
        set_if_missing("review_status", review_status_path)
    if workflow_result_path.exists():
        set_if_missing("workflow_result", workflow_result_path)

    publish_result = _load_json_file(publish_result_path) or {}
    publish_record = publish_result.get("record", {}) if isinstance(publish_result, dict) else {}
    if not isinstance(publish_record, dict):
        publish_record = {}
    backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}
    if not isinstance(backend, dict):
        backend = {}
    set_if_missing(
        "selected_title",
        selected.get("title") or publish_record.get("title") or publish_result.get("title", ""),
    )
    set_if_missing("publish_status", publish_result.get("status") or selected.get("status", ""))
    set_if_missing("publish_url", publish_result.get("external_url") or selected.get("external_url", ""))
    set_if_missing("publish_message", backend.get("message") or publish_result.get("message", ""))
    set_if_missing("publish_pgc_id", backend.get("pgc_id") or publish_result.get("pgc_id", ""))

    content_id = str(
        (publish_result.get("content_id", "") if isinstance(publish_result, dict) else "")
        or publish_record.get("content_id", "")
        or ""
    ).strip()
    if not content_id and workflow_result_path.exists():
        workflow_result = _load_json_file(workflow_result_path) or {}
        content_id = str(workflow_result.get("content_id", "")).strip()
    content_index_path = settings.output_dir / "growth_ops" / "revenue_attribution" / "content_index.json"
    if not content_id and content_index_path.exists():
        index_payload = _load_json_file(content_index_path) or {}
        if isinstance(index_payload, dict):
            try:
                target_path = os.path.normcase(str(run_dir.resolve()))
            except OSError:
                target_path = os.path.normcase(str(run_dir))
            for key, item in index_payload.items():
                if not isinstance(item, dict):
                    continue
                item_path = str(item.get("run_dir", "")).strip()
                if not item_path:
                    continue
                try:
                    item_path = str(Path(item_path).resolve())
                except OSError:
                    pass
                if os.path.normcase(item_path) == target_path:
                    content_id = str(item.get("content_id") or key).strip()
                    break
    set_if_missing("content_id", content_id)
    if content_index_path.exists():
        set_if_missing("content_index", content_index_path)


def _mark_daily_run_running(settings) -> None:
    """Mark direct ``main.py --daily-run`` execution as active."""
    path = settings.automation_dir / "scheduled_status.json"
    payload = _load_json_file(path) or {}
    now = datetime.now().isoformat(timespec="seconds")
    payload.update(
        {
            "updated_at": now,
            "last_run_time": now,
            "last_heartbeat": now,
            "pid": os.getpid(),
            "account_key": settings.account_key,
            "account_slug": settings.account_slug,
            "status": "running",
            "overall_status": "running",
            "overall_message": "Scheduled pipeline is running.",
            "message": "Scheduled pipeline is running.",
            "exit_code": 0,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _scheduled_overall_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "running":
        return "running"
    if normalized == "partial_success":
        return "partial_success"
    if normalized in {
        "failed",
        "error",
        "blocked",
        "blocked_by_ip",
        "publish_login_required",
        "publish_uncertain",
    }:
        return "failed"
    if normalized == "review_required":
        # The pipeline completed normally and left a reviewable draft. This is
        # a business state, not a scheduler/process failure.
        return "review_required"
    if normalized == "no_topics":
        return "idle"
    return "success"


def _write_scheduled_runtime_status(
    settings,
    *,
    summary: dict[str, Any],
    status: str,
    message: str,
) -> None:
    """Persist terminal daily-run state without losing the last article."""
    path = settings.automation_dir / "scheduled_status.json"
    payload = _load_json_file(path) or {}
    now = datetime.now().isoformat(timespec="seconds")
    normalized_status = str(status or "").strip().lower()
    has_new_run = bool(str(summary.get("run_dir", "")).strip())
    preserve_previous_article = normalized_status in {
        "no_topics",
        "skipped",
        "already_published",
    } and not has_new_run
    payload.update(
        {
            "updated_at": now,
            "last_run_time": now,
            "last_heartbeat": "",
            "pid": 0,
            "account_key": settings.account_key,
            "account_slug": settings.account_slug,
            "status": status,
            "overall_status": _scheduled_overall_status(status),
            "overall_message": message,
            "message": message,
            "run_outcome": str(summary.get("run_outcome", "")).strip(),
        }
    )
    # Keep persisted state aligned with the CLI contract so stale retry
    # metadata cannot make a successful scheduled run look like an error.
    payload["exit_code"] = 1 if str(status or "").strip().lower() in {
        "failed",
        "partial_success",
        "publish_login_required",
        "publish_uncertain",
    } else 0
    field_map = {
        "run_dir": "run_dir",
        "run_name": "run_name",
        "content_id": "content_id",
        "review_status": "review_status",
        "selected_title": "selected_title",
        "workflow_status": "workflow_status",
        "publish_status": "publish_status",
        "publish_url": "publish_url",
        "publish_message": "publish_message",
        "publish_pgc_id": "publish_pgc_id",
        "wechat_status": "wechat_status",
        "wechat_submit_status": "wechat_submit_status",
        "wechat_submit_requested": "wechat_submit_requested",
        "wechat_publish_url": "wechat_publish_url",
        "wechat_draft_media_id": "wechat_draft_media_id",
        "weitoutiao_status": "weitoutiao_status",
        "weitoutiao_message": "weitoutiao_message",
        "weitoutiao_topup_status": "weitoutiao_topup_status",
        "weitoutiao_topup_result": "weitoutiao_topup_result",
        "weitoutiao_result": "weitoutiao_result",
    }
    for target, source in field_map.items():
        value = summary.get(source)
        if isinstance(value, bool):
            if value or not preserve_previous_article:
                payload[target] = value
        elif str(value or "").strip():
            payload[target] = value
        elif not preserve_previous_article:
            payload[target] = ""
    if isinstance(summary.get("workflow_errors"), list):
        payload["workflow_errors"] = list(summary["workflow_errors"])
    if isinstance(summary.get("weitoutiao"), dict):
        payload["weitoutiao"] = dict(summary["weitoutiao"])

    existing_platforms = payload.get("platforms", {}) if isinstance(payload.get("platforms"), dict) else {}
    platforms: dict[str, dict[str, str]] = {}
    for key in ("toutiao", "wechat", "weitoutiao"):
        previous = existing_platforms.get(key, {}) if isinstance(existing_platforms.get(key), dict) else {}
        platforms[key] = {
            "status": (
                str(previous.get("status", "idle") or "idle")
                if preserve_previous_article
                else "idle"
            ),
            "last_run": (
                str(previous.get("last_run", "") or "")
                if preserve_previous_article
                else ""
            ),
            "message": (
                str(previous.get("message", "") or "")
                if preserve_previous_article
                else ""
            ),
        }
    publish_status = str(summary.get("publish_status", "")).strip().lower()
    if publish_status:
        platforms["toutiao"] = {
            "status": "success" if publish_status in _DAILY_PUBLISH_SUCCESS_STATUSES or publish_status in {"draft_saved", "draft_created"} else "failed",
            "last_run": now,
            "message": str(summary.get("publish_message") or message),
        }
    elif normalized_status == "review_required":
        platforms["toutiao"] = {
            "status": "review_required",
            "last_run": now,
            "message": message,
        }
    elif normalized_status in {
        "failed",
        "error",
        "blocked",
        "blocked_by_ip",
        "publish_login_required",
        "publish_uncertain",
    }:
        platforms["toutiao"] = {
            "status": "failed",
            "last_run": now,
            "message": message,
        }
    wechat_status = str(summary.get("wechat_status", "")).strip().lower()
    if wechat_status:
        platforms["wechat"] = {
            "status": "success" if wechat_status in {"submitted", "submitted_for_review", "success", "published", "draft_created", "draft_saved"} else "failed",
            "last_run": now,
            "message": str(summary.get("wechat_publish_url") or message),
        }
    weitoutiao_status = str(summary.get("weitoutiao_status", "")).strip().lower()
    if weitoutiao_status:
        if weitoutiao_status in {"success", "published", "submitted", "submitted_for_review", "target_reached"}:
            platform_status = "success"
        elif weitoutiao_status == "partial_success":
            platform_status = "partial_success"
        elif weitoutiao_status in {"not_attempted", "disabled"}:
            platform_status = "idle"
        else:
            platform_status = "failed"
        platforms["weitoutiao"] = {
            "status": platform_status,
            "last_run": now,
            "message": str(summary.get("weitoutiao_message") or message),
        }
    payload["platforms"] = platforms
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


if __name__ == "__main__":
    raise SystemExit(main())
