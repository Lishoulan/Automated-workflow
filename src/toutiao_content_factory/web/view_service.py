from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any

import yaml

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.utils.runtime_status import recover_stale_running_status
from toutiao_content_factory.services.t1_policy import infer_availability_status


@dataclass
class RunSummary:
    name: str
    created_at: str
    selected_topic_title: str
    review_status: str
    recommended_action: str
    overall_score: int
    ai_risk: int
    validation_score: int
    publish_readiness_score: int
    has_publish: bool
    publish_title: str
    can_retry: bool
    retry_platforms: list[dict[str, str]]
    latest_platform_status: dict[str, dict[str, str]]
    path: Path


@dataclass
class ScheduledTaskSummary:
    task_name: str
    state: str
    last_run_time: str
    next_run_time: str
    last_task_result: int
    platform: str = ""
    account_slug: str = ""
    slot_index: int = 0


@dataclass
class LogSummary:
    name: str
    modified_at: str
    size_kb: float
    url: str


@dataclass
class PublishStatusSummary:
    status: str
    submitted_at: str
    title: str
    run_name: str
    topic_title: str
    pgc_id: str
    message: str
    external_url: str
    screenshot_url: str
    dom_url: str
    debug_dir: str
    has_result: bool


@dataclass
class WechatPublishStatusSummary:
    status: str
    submitted_at: str
    title: str
    run_name: str
    draft_media_id: str
    submit_status: str
    external_url: str
    inline_image_count: int
    layout_backend: str
    submit_requested: bool
    has_result: bool


@dataclass
class ScheduledWindowSummary:
    slot_label: str
    expected_time: str
    status: str
    updated_at: str
    message: str
    run_name: str
    publish_status: str
    publish_pgc_id: str
    log_url: str


class WebViewService:
    SCHEDULED_TASK_PREFIXES = {
        "toutiao": "ToutiaoContentFactory",
        "wechat": "WechatContentFactory",
    }
    BENIGN_TASK_RESULTS = {0, 267009, 267011}
    _TASK_CATALOG_CACHE: list[ScheduledTaskSummary] = []
    _TASK_CATALOG_CACHE_AT: float = 0.0
    _TASK_CATALOG_TTL_SECONDS = 20.0

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.output_dir = settings.output_dir
        self.runs_dir = settings.runs_dir
        self.logs_dir = settings.logs_dir
        self.scheduled_logs_dir = settings.scheduled_logs_dir
        self.automation_dir = settings.automation_dir
        self.publish_dirname = settings.review.publish_dirname

    def list_styles(self) -> list[dict[str, str]]:
        return [{"key": key, "label": profile.label} for key, profile in self.settings.style_profiles.items()]

    @property
    def account_key(self) -> str:
        return self.settings.account_key

    def load_daily_topics(self) -> dict[str, Any] | None:
        return self._load_json(self.output_dir / "daily_topics.json")

    def list_runs(self, limit: int | None = None) -> list[RunSummary]:
        if not self.runs_dir.exists():
            return []
        candidates = [item for item in self.runs_dir.iterdir() if item.is_dir()]
        candidates.sort(key=self._run_sort_key, reverse=True)
        if limit is not None:
            candidates = candidates[:limit]
        return [self._build_run_summary(path) for path in candidates]

    def load_dashboard(self) -> dict[str, Any]:
        topics = self.load_daily_topics()
        runs = self.list_runs(limit=8)
        latest_score = self._load_json(self.output_dir / "content_score.json") or {}
        latest_selected_topic = (self._load_json(self.output_dir / "selected_topic.json") or {}).get("selected_topic", {})
        latest_workflow = self._load_json(self.output_dir / "workflow_result.json") or {}
        cost_summary = self._build_cost_summary()
        latest_workflow_report = self.load_latest_workflow_report()
        scheduled_status = self._normalize_dashboard_scheduled_status(self.load_scheduled_status() or {})
        schedule_runtime = self._build_schedule_runtime_summary(scheduled_status)
        latest_publish = self.load_latest_publish_status()
        latest_wechat = self.load_latest_wechat_status()
        publish_preflight = self.load_publish_preflight()
        platform_health = self.load_platform_health()
        topic_pool = self.load_topic_pool_summary()
        content_guard = self.load_content_guard_summary()
        performance_feedback = self.load_performance_feedback()
        health_panel = self.build_health_panel(platform_health)
        notifications = self.list_notifications(limit=8)
        lead_summary = self.load_lead_summary()
        recent_leads = self.list_recent_leads(limit=8)
        lead_assets = self.load_lead_assets()
        lead_config = self.load_lead_config()
        offer_choices = self.load_offer_choices()
        revenue_engine_assets = self.load_revenue_engine_assets()
        opportunity_sync_report = self.load_opportunity_sync_report()
        scheduled_logs = self.list_scheduled_logs(limit=6)
        snapshot = self.build_account_snapshot()
        pipeline_timeline = self._build_pipeline_timeline(
            scheduled_status=scheduled_status,
            workflow_report=latest_workflow_report,
            notifications=notifications,
            latest_publish=latest_publish,
            latest_wechat=latest_wechat,
        )
        actionable_alerts = self._build_actionable_alerts(
            platform_health=platform_health,
            workflow_report=latest_workflow_report,
            notifications=notifications,
            scheduled_status=scheduled_status,
        )
        log_console = self._build_log_console_summary(scheduled_logs)
        retry_runs = [run for run in runs if getattr(run, "can_retry", False)]
        today_stats = self._build_today_stats()
        return {
            "daily_topics": topics,
            "runs": runs,
            "retry_runs": retry_runs,
            "today_stats": today_stats,
            "latest_score": latest_score,
            "latest_selected_topic": latest_selected_topic,
            "latest_workflow": latest_workflow,
            "cost_summary": cost_summary,
            "latest_workflow_report": latest_workflow_report,
            "latest_publish": latest_publish,
            "latest_wechat": latest_wechat,
            "publish_preflight": publish_preflight,
            "scheduled_tasks": self.list_scheduled_tasks(),
            "scheduled_logs": scheduled_logs,
            "scheduled_status": scheduled_status,
            "schedule_runtime": schedule_runtime,
            "scheduled_windows": self.list_scheduled_windows(),
            "notifications": notifications,
            "lead_summary": lead_summary,
            "recent_leads": recent_leads,
            "lead_assets": lead_assets,
            "lead_config": lead_config,
            "offer_choices": offer_choices,
            "revenue_engine_assets": revenue_engine_assets,
            "opportunity_sync_report": opportunity_sync_report,
            "publish_history": self.list_publish_history(limit=8),
            "platform_health": platform_health,
            "topic_pool": topic_pool,
            "content_guard": content_guard,
            "performance_feedback": performance_feedback,
            "health_panel": health_panel,
            "account_snapshot": snapshot,
            "account_overviews": [],
            "pipeline_timeline": pipeline_timeline,
            "actionable_alerts": actionable_alerts,
            "log_console": log_console,
        }

    def load_lead_summary(self) -> dict[str, Any]:
        payload = self._load_json(self.automation_dir / "lead_ops" / "lead_summary.json")
        return payload if isinstance(payload, dict) else {}

    def list_recent_leads(self, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._load_json(self.automation_dir / "lead_ops" / "lead_ledger.json")
        if not isinstance(payload, list):
            return []
        result: list[dict[str, Any]] = []
        for item in payload[:limit]:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            proposal_path = str(enriched.get("proposal_path", "")).strip()
            enriched["proposal_url"] = self._output_url_from_absolute_path(proposal_path)
            enriched["is_auto_proposal_ready"] = bool(enriched.get("proposal_auto_generated")) and bool(proposal_path)
            enriched["recommended_next_action"] = str(enriched.get("recommended_next_action", "")).strip()
            enriched["reply_temperature"] = str(enriched.get("reply_temperature", "")).strip()
            enriched["outreach_sent_at"] = str(enriched.get("outreach_sent_at", "")).strip()
            result.append(enriched)
        return result

    def load_lead_assets(self) -> dict[str, str]:
        lead_root = self.automation_dir / "lead_ops"
        mapping = {
            "reply_manual": "keyword_reply_manual.md",
            "followup_queue": "followup_queue.md",
            "outreach_queue": "outreach_queue.md",
            "lead_summary": "lead_summary.json",
            "lead_ledger": "lead_ledger.json",
            "proposal_catalog": "proposal_catalog.md",
            "offer_snapshot": "offer_snapshot.json",
            "outreach_sequences": "outreach_sequences.json",
            "outbound_campaign_brief": "outbound_campaign_brief.md",
            "outbound_campaign_pack": "outbound_campaign_pack.json",
            "reply_triage_playbook": "reply_triage_playbook.md",
            "reply_triage_pack": "reply_triage_pack.json",
            "conversion_push_pack": "conversion_push_pack.json",
            "outreach_execution_log": "outreach_execution_log.json",
            "sales_execution_dashboard": "sales_execution_dashboard.json",
            "sales_execution_brief": "sales_execution_brief.md",
            "wechat_batch_messages": "wechat_batch_messages.md",
            "email_batch_messages": "email_batch_messages.md",
            "platform_dm_batch_messages": "platform_dm_batch_messages.md",
            "customer_success_dashboard": "customer_success_dashboard.json",
            "customer_success_brief": "customer_success_brief.md",
            "expansion_pipeline": "expansion_pipeline.json",
            "referral_pipeline_brief": "referral_pipeline_brief.md",
        }
        result: dict[str, str] = {}
        for key, filename in mapping.items():
            path = lead_root / filename
            result[key] = self._account_output_artifact_url(f"automation/lead_ops/{filename}") if path.exists() else ""
        return result

    def load_lead_config(self) -> dict[str, Any]:
        config_path = self.settings.project_root / "config" / "company_leads.yaml"
        payload = self._load_yaml(config_path)
        if not isinstance(payload, dict):
            return {"sources": [], "statuses": [], "intents": []}
        return {
            "sources": [item for item in payload.get("sources", []) if isinstance(item, dict)],
            "statuses": [str(item).strip() for item in payload.get("statuses", []) if str(item).strip()],
            "intents": [str(item).strip() for item in payload.get("intents", []) if str(item).strip()],
        }

    def load_offer_choices(self) -> list[dict[str, str]]:
        config_path = self.settings.project_root / "config" / "company_revenue.yaml"
        payload = self._load_yaml(config_path)
        if not isinstance(payload, dict):
            return []
        result: list[dict[str, str]] = []
        for item in payload.get("core_offers", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            key = str(item.get("key", "")).strip() or self._slugify_offer_name(name)
            price = str(item.get("price", "")).strip()
            result.append(
                {
                    "key": key,
                    "name": name,
                    "price": price,
                    "label": f"{name} ({price})" if price else name,
                }
            )
        return result

    def load_revenue_engine_assets(self) -> dict[str, str]:
        root = self.automation_dir / "revenue_engine"
        mapping = {
            "customer_targets": "customer_targets.json",
            "sponsor_targets": "sponsor_targets.json",
            "revenue_signal_report": "revenue_signal_report.json",
            "opportunity_sync_report": "opportunity_sync_report.json",
            "opportunity_queue": "opportunity_queue.json",
            "opportunity_queue_markdown": "opportunity_queue.md",
            "outreach_playbook": "outreach_playbook.md",
            "deer_flow_task_pack": "deer_flow_task_pack.yaml",
            "newsletter_offer": "newsletter_offer.md",
            "weekly_pipeline": "weekly_pipeline.md",
            "prompt_optimization_pack": "prompt_optimization_pack.json",
            "cloudflow_blueprint": "cloudflow_blueprint.yaml",
            "letter_campaign": "letter_campaign.md",
            "agennts_dispatch_queue": "agennts_dispatch_queue.json",
            "founder_profit_brief": "founder_profit_brief.md",
            "profit_action_pack": "profit_action_pack.json",
            "profit_loop_report": "profit_loop_report.json",
            "sponsor_outreach_log": "sponsor_outreach_log.json",
            "sponsor_pipeline": "sponsor_pipeline.json",
            "profit_action_execution_report": "profit_action_execution_report.json",
        }
        result: dict[str, str] = {}
        for key, filename in mapping.items():
            path = root / filename
            result[key] = self._account_output_artifact_url(f"automation/revenue_engine/{filename}") if path.exists() else ""
        return result

    def load_opportunity_sync_report(self) -> dict[str, Any]:
        payload = self._load_json(self.automation_dir / "revenue_engine" / "opportunity_sync_report.json")
        return payload if isinstance(payload, dict) else {}

    def _build_today_stats(self) -> dict[str, Any]:
        """Build today's monitoring stats: collected, drafted, published, intercepted."""
        today_prefix = datetime.now().strftime("%Y%m%d")
        collected = 0
        drafted = 0
        published = 0
        intercepted = 0

        if self.runs_dir.exists():
            for run_dir in self.runs_dir.iterdir():
                if not run_dir.is_dir() or not run_dir.name.startswith(today_prefix):
                    continue
                collected += 1

                # Check if content was generated (article.md exists)
                article_path = run_dir / "article.md"
                if article_path.exists():
                    drafted += 1

                # Check AI risk score from content_score.json
                score_payload = self._load_json(run_dir / "content_score.json")
                if isinstance(score_payload, dict):
                    scores = score_payload.get("scores", {}) if isinstance(score_payload.get("scores"), dict) else {}
                    ai_risk = int(scores.get("ai_generated_risk", 0) or 0)
                    if ai_risk >= 70:
                        intercepted += 1

                # Check review_status for interception
                review_payload = self._load_json(run_dir / "review_status.json")
                if isinstance(review_payload, dict):
                    recommended = str(review_payload.get("recommended_action", "")).strip().lower()
                    if recommended in {"reject", "blocked", "auto_rejected"}:
                        if intercepted == 0 or (isinstance(score_payload, dict) and
                            int((score_payload.get("scores", {}) if isinstance(score_payload.get("scores"), dict) else {}).get("ai_generated_risk", 0) or 0) < 70):
                            intercepted += 1

        # Count published from publish history
        history_payload = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        if isinstance(history_payload, list):
            for item in history_payload:
                if not isinstance(item, dict):
                    continue
                published_at = str(item.get("published_at", "")).strip()
                if published_at.startswith(today_prefix) or published_at.startswith(datetime.now().strftime("%Y-%m-%d")):
                    status = str(item.get("status", "")).strip().lower()
                    if status in {"published", "submitted", "submitted_for_review", "success", "draft_created"}:
                        published += 1

        return {
            "today_collected": collected,
            "today_drafted": drafted,
            "today_published": published,
            "today_intercepted": intercepted,
        }

    def _build_cost_summary(self) -> dict[str, Any]:
        today_prefix = datetime.now().strftime("%Y%m%d")
        total_cost = 0.0
        run_count = 0

        if self.runs_dir.exists():
            for run_dir in self.runs_dir.iterdir():
                if not run_dir.is_dir() or not run_dir.name.startswith(today_prefix):
                    continue
                workflow_payload = self._load_json(run_dir / "workflow_result.json")
                if not isinstance(workflow_payload, dict):
                    continue
                total_cost += float(workflow_payload.get("estimated_cost", 0.0) or 0.0)
                run_count += 1

        average_cost = total_cost / run_count if run_count else 0.0
        return {
            "today_total_cost": round(total_cost, 4),
            "average_cost_per_article": round(average_cost, 4),
            "run_count": run_count,
        }

    def _build_pipeline_timeline(
        self,
        *,
        scheduled_status: dict[str, Any],
        workflow_report: dict[str, Any],
        notifications: list[dict[str, Any]],
        latest_publish: PublishStatusSummary,
        latest_wechat: WechatPublishStatusSummary,
    ) -> list[dict[str, str]]:
        stages: list[dict[str, str]] = []
        final_attempt = workflow_report.get("final_attempt", {}) if isinstance(workflow_report, dict) else {}
        workflow_status = str((workflow_report or {}).get("status", "")).strip().lower()
        platform_results = final_attempt.get("platform_results", {}) if isinstance(final_attempt, dict) else {}
        topic_title = str(final_attempt.get("topic_title", "")).strip()
        run_dir = str(final_attempt.get("run_dir", "")).strip()

        image_fallback = any(
            str(item.get("type", "")).strip().lower() == "image_generation_fallback"
            or str(item.get("title", "")).strip().lower() == "image2 fallback triggered"
            for item in notifications
            if isinstance(item, dict)
        )
        schedule_message = str(
            scheduled_status.get("overall_message") or scheduled_status.get("message") or ""
        ).strip()
        platform_errors = self._collect_workflow_error_text(workflow_report)
        publish_states = [
            str(item.get("status", "")).strip().lower()
            for item in platform_results.values()
            if isinstance(item, dict) and str(item.get("status", "")).strip()
        ]

        precheck_tone = "success"
        precheck_status = "success"
        precheck_message = "等待调度器启动"
        if "blocked" in str(scheduled_status.get("overall_status", "")).lower() or "preflight" in schedule_message.lower():
            precheck_tone = "error"
            precheck_status = "blocked"
            precheck_message = schedule_message or "调度已就绪"
        elif str(scheduled_status.get("overall_status", "")).lower() == "aborted":
            precheck_tone = "warn"
            precheck_status = "aborted"
            precheck_message = schedule_message or "调度异常，请检查配置"
        stages.append(self._timeline_node("preflight", "预检", precheck_status, precheck_tone, precheck_message))

        if topic_title:
            topic_message = f"已选题：{topic_title}"
            topic_tone = "success"
            topic_status = "success"
        elif workflow_status == "no_topics":
            topic_message = "热点池暂无可用选题"
            topic_tone = "warn"
            topic_status = "no_topics"
        else:
            topic_message = "选题阶段未执行"
            topic_tone = "neutral"
            topic_status = "idle"
        stages.append(self._timeline_node("topics", "选题", topic_status, topic_tone, topic_message))

        if run_dir:
            generation_message = f"内容已生成：{Path(run_dir).name}"
            generation_tone = "success"
            generation_status = "success"
        elif workflow_status in {"failed", "generation_failed"}:
            generation_message = "内容生成失败"
            generation_tone = "error"
            generation_status = "failed"
        else:
            generation_message = "内容生成跳过"
            generation_tone = "neutral"
            generation_status = "idle"
        stages.append(self._timeline_node("generation", "生成", generation_status, generation_tone, generation_message))

        image_message = "配图已就绪"
        image_tone = "success"
        image_status = "success"
        if image_fallback:
            image_message = "配图生成失败，已使用占位图/跳过"
            image_tone = "warn"
            image_status = "fallback"
        elif not run_dir:
            image_message = "配图生成跳过"
            image_tone = "neutral"
            image_status = "idle"
        stages.append(self._timeline_node("images", "配图", image_status, image_tone, image_message))

        publish_tone = "neutral"
        publish_status = "idle"
        publish_message = "等待发布"
        if publish_states:
            if any(state in {"failed", "error", "blocked"} for state in publish_states) and any(
                state in {"submitted", "submitted_for_review", "published", "success", "draft_created"}
                for state in publish_states
            ):
                publish_tone = "warn"
                publish_status = "partial_success"
                publish_message = "已提交发布，请到平台确认"
            elif any(state in {"failed", "error", "blocked"} for state in publish_states):
                publish_tone = "error"
                publish_status = "failed"
                publish_message = platform_errors[0] if platform_errors else "发布失败"
            else:
                publish_tone = "success"
                publish_status = "success"
                publish_message = "发布步骤未执行"
        stages.append(self._timeline_node("publish", "发布/提交", publish_status, publish_tone, publish_message))

        archive_tone = "neutral"
        archive_status = "idle"
        archive_message = "归档已完成"
        overall_status = str(scheduled_status.get("overall_status") or scheduled_status.get("status") or "").strip().lower()
        if overall_status in {"success", "completed"}:
            archive_tone = "success"
            archive_status = overall_status
            archive_message = "归档写入失败"
        elif overall_status in {"partial_success", "review_required"}:
            archive_tone = "warn"
            archive_status = overall_status
            archive_message = schedule_message or "调度器异常，归档未完成"
        elif overall_status in {"failed", "blocked", "aborted"}:
            archive_tone = "error" if overall_status != "aborted" else "warn"
            archive_status = overall_status
            archive_message = schedule_message or "调度器异常，归档超时"
        elif latest_publish.has_result or latest_wechat.has_result:
            archive_tone = "success"
            archive_status = "success"
            archive_message = "归档步骤未执行"
        stages.append(self._timeline_node("archive", "归档", archive_status, archive_tone, archive_message))
        return stages

    def _timeline_node(self, key: str, label: str, status: str, tone: str, message: str) -> dict[str, str]:
        return {
            "key": key,
            "label": label,
            "status": status,
            "tone": tone,
            "message": message,
        }

    def _build_actionable_alerts(
        self,
        *,
        platform_health: dict[str, Any],
        workflow_report: dict[str, Any],
        notifications: list[dict[str, Any]],
        scheduled_status: dict[str, Any],
    ) -> list[dict[str, str]]:
        alerts: list[dict[str, str]] = []
        platforms = platform_health.get("platforms", {}) if isinstance(platform_health, dict) else {}
        all_messages: list[str] = []
        for item in notifications:
            if isinstance(item, dict):
                all_messages.append(str(item.get("message", "")))
                data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
                error = str(data.get("error", "")).strip()
                if error:
                    all_messages.append(error)
                workflow_errors = data.get("workflow_errors", []) if isinstance(data.get("workflow_errors"), list) else []
                all_messages.extend(str(error_text) for error_text in workflow_errors)
        all_messages.extend(self._collect_workflow_error_text(workflow_report))
        for platform_name in ("toutiao", "wechat"):
            payload = platforms.get(platform_name, {}) if isinstance(platforms, dict) else {}
            if isinstance(payload, dict):
                all_messages.extend(str(item) for item in payload.get("blocking_flags", []) if str(item).strip())
                all_messages.extend(str(item) for item in payload.get("warnings", []) if str(item).strip())

        invalid_ip = self._extract_invalid_ip("\n".join(all_messages))
        if invalid_ip:
            alerts.append(
                {
                    "tone": "error",
                    "title": "检测到 API 白名单外 IP",
                    "message": f"当前 IP 为 {invalid_ip}，请将其加入白名单后重试",
                    "action_kind": "copy_ip",
                    "action_label": "复制 IP",
                    "action_value": invalid_ip,
                    "secondary_label": "打开后台",
                    "secondary_value": "https://mp.weixin.qq.com/",
                }
            )

        login_message = self._detect_login_issue(platforms, workflow_report)
        if login_message:
            alerts.append(
                {
                    "tone": "warn",
                    "title": f"{login_message['label']}平台登录已过期",
                    "message": login_message["message"],
                    "action_kind": "init_login",
                    "action_label": "重新登录",
                    "action_value": self.account_key,
                    "secondary_label": "切换账号",
                    "secondary_value": self.account_key,
                }
            )

        fallback_item = next(
            (
                item
                for item in notifications
                if isinstance(item, dict) and str(item.get("type", "")).strip().lower() == "image_generation_fallback"
            ),
            None,
        )
        if isinstance(fallback_item, dict):
            normalized = self._normalize_notification_message(fallback_item)
            alerts.append(
                {
                    "tone": "info",
                    "title": "图片生成回退为占位图",
                    "message": normalized,
                    "action_kind": "none",
                    "action_label": "",
                    "action_value": "",
                    "secondary_label": "",
                    "secondary_value": "",
                }
            )

        scheduled_overall = str(scheduled_status.get("overall_status") or scheduled_status.get("status") or "").strip().lower()
        scheduled_message = str(scheduled_status.get("overall_message") or scheduled_status.get("message") or "").strip()
        if scheduled_overall in {"aborted", "failed", "blocked"} and scheduled_message:
            alerts.append(
                {
                    "tone": "warn" if scheduled_overall == "aborted" else "error",
                    "title": "定时调度执行异常",
                    "message": scheduled_message,
                    "action_kind": "refresh_health",
                    "action_label": "刷新状态",
                    "action_value": self.account_key,
                    "secondary_label": "查看日志",
                    "secondary_value": "open_log_modal",
                }
            )

        if not alerts:
            alerts.append(
                {
                    "tone": "success",
                    "title": "系统运行正常",
                    "message": "所有检测项均通过，无需操作",
                    "action_kind": "none",
                    "action_label": "",
                    "action_value": "",
                    "secondary_label": "",
                    "secondary_value": "",
                }
            )
        return alerts

    def _build_log_console_summary(self, scheduled_logs: list[LogSummary]) -> dict[str, Any]:
        latest = scheduled_logs[0] if scheduled_logs else None
        return {
            "has_logs": bool(scheduled_logs),
            "latest_name": latest.name if latest else "",
            "latest_modified_at": latest.modified_at if latest else "",
            "recent_count": len(scheduled_logs),
            "entries": [
                {
                    "name": item.name,
                    "modified_at": item.modified_at,
                    "size_kb": item.size_kb,
                    "url": item.url,
                }
                for item in scheduled_logs
            ],
        }

    def _collect_workflow_error_text(self, workflow_report: dict[str, Any]) -> list[str]:
        if not isinstance(workflow_report, dict):
            return []
        final_attempt = workflow_report.get("final_attempt", {}) if isinstance(workflow_report.get("final_attempt"), dict) else {}
        platform_errors = final_attempt.get("platform_errors", []) if isinstance(final_attempt.get("platform_errors"), list) else []
        return [str(item).strip() for item in platform_errors if str(item).strip()]

    def _extract_invalid_ip(self, raw_text: str) -> str:
        if not raw_text:
            return ""
        match = re.search(r"invalid ip\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})", raw_text, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"IP[^0-9]*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", raw_text, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _detect_login_issue(self, platforms: dict[str, Any], workflow_report: dict[str, Any]) -> dict[str, str] | None:
        checks = [
            ("toutiao", "头条"),
            ("wechat", "公众号"),
        ]
        workflow_errors = "\n".join(self._collect_workflow_error_text(workflow_report)).lower()
        for key, label in checks:
            payload = platforms.get(key, {}) if isinstance(platforms, dict) else {}
            if not isinstance(payload, dict):
                continue
            messages: list[str] = []
            messages.extend(str(item) for item in payload.get("blocking_flags", []) if str(item).strip())
            messages.extend(str(item) for item in payload.get("warnings", []) if str(item).strip())
            diagnostics = payload.get("diagnostics", {}) if isinstance(payload.get("diagnostics"), dict) else {}
            blocked_reason = str(diagnostics.get("blocked_reason", "")).strip().upper()
            combined = "\n".join(messages).lower()
            if blocked_reason == "BLOCKED_BY_IP":
                continue
            workflow_platform_hit = key in workflow_errors or label.lower() in workflow_errors
            if any(token in combined for token in ["login", "session", "登录", "expired", "draft box"]) or (
                workflow_platform_hit
                and any(token in workflow_errors for token in ["login", "session", "draft box", "creator login is required"])
            ):
                detail = messages[0] if messages else "登录状态已失效，请重新登录后再试"
                return {"platform": key, "label": label, "message": detail}
        return None

    def _normalize_dashboard_scheduled_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or not payload:
            return payload
        normalized = dict(payload)
        status_value = str(normalized.get("status", "")).strip().lower()
        workflow_status = str(normalized.get("workflow_status", "")).strip().lower()
        publish_status = str(normalized.get("publish_status", "")).strip()
        workflow_errors = normalized.get("workflow_errors", [])
        first_error = ""
        if isinstance(workflow_errors, list) and workflow_errors:
            first_error = str(workflow_errors[0]).strip().lower()
        if (
            status_value == "failed"
            and workflow_status in {"platform_publish_failed", "platform_partial_success"}
            and publish_status
        ):
            normalized["status"] = publish_status
            if "wechat publish failed" in first_error or "cover image not found" in first_error:
                normalized["message"] = "头条已正常提交，公众号发布失败：缺少封面图或公众号素材图。"
            else:
                normalized["message"] = "头条已正常提交，但至少有一个其他平台发布失败。"
        return normalized

    def _build_schedule_runtime_summary(self, scheduled_status: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(scheduled_status, dict) or not scheduled_status:
            return {
                "state": "unknown",
                "label": "暂无记录",
                "tone": "neutral",
                "is_running": False,
                "updated_at": "",
                "message": "还没有检测到定时执行记录。",
                "log_file": "",
                "log_name": "",
                "log_url": "",
                "attempt_label": "",
                "platform_cards": [],
            }

        status_value = str(scheduled_status.get("overall_status") or scheduled_status.get("status") or "").strip().lower()
        updated_at = str(scheduled_status.get("updated_at") or scheduled_status.get("last_run_time") or "").strip()
        message = str(scheduled_status.get("overall_message") or scheduled_status.get("message") or "").strip()
        log_file = str(scheduled_status.get("log_file", "")).strip()
        log_url = self.resolve_log_url(log_file) if log_file else ""
        attempt = int(scheduled_status.get("attempt", 0) or 0)
        max_attempts = int(scheduled_status.get("max_attempts", 0) or 0)
        attempt_label = f"第 {attempt}/{max_attempts} 次尝试" if attempt > 0 and max_attempts > 0 else ""

        mapping = {
            "running": ("执行中", "pending"),
            "blocked": ("已阻塞", "error"),
            "failed": ("执行失败", "error"),
            "partial_success": ("部分成功", "pending"),
            "success": ("执行成功", "approved"),
            "completed": ("执行完成", "approved"),
            "review_required": ("待人工审核", "pending"),
            "idle": ("空闲", "neutral"),
            "aborted": ("异常中止", "error"),
        }
        label, tone = mapping.get(status_value, ("未知", "neutral"))
        if not message:
            if status_value == "running":
                message = "定时流程仍在运行中。"
            elif status_value == "blocked":
                message = "定时流程被预检或登录态问题阻塞。"
            elif status_value in {"success", "completed"}:
                message = "最近一次定时流程已完成。"
            elif status_value == "partial_success":
                message = "最近一次定时流程部分成功，至少有一个平台失败。"
            elif status_value == "failed":
                message = "最近一次定时流程失败，请查看日志。"
            elif status_value == "aborted":
                message = "上一次定时流程异常中止，系统已自动回收运行状态。"
            else:
                message = "暂时没有可用的运行状态。"

        platform_cards: list[dict[str, str]] = []
        platforms = scheduled_status.get("platforms", {}) if isinstance(scheduled_status.get("platforms"), dict) else {}
        for key, title in (
            ("toutiao", "今日头条"),
            ("weitoutiao", "微头条"),
            ("wechat", "公众号"),
        ):
            item = platforms.get(key)
            if not isinstance(item, dict):
                continue
            item_status = str(item.get("status", "idle")).strip().lower() or "idle"
            item_meta = self._describe_status(item_status)
            platform_cards.append(
                {
                    "key": key,
                    "label": title,
                    "status": item_status,
                    "status_label": item_meta["label"],
                    "tone": item_meta["tone"],
                    "message": str(item.get("message", "")).strip() or "暂无平台执行说明。",
                    "updated_at": str(item.get("last_run", "")).strip(),
                }
            )

        return {
            "state": status_value or "unknown",
            "label": label,
            "tone": tone,
            "is_running": status_value == "running",
            "updated_at": updated_at,
            "message": message,
            "log_file": log_file,
            "log_name": Path(log_file).name if log_file else "",
            "log_url": log_url,
            "attempt_label": attempt_label,
            "platform_cards": platform_cards,
        }

    def build_account_snapshot(
        self,
        *,
        task_catalog: list[ScheduledTaskSummary] | None = None,
        is_current: bool = False,
    ) -> dict[str, Any]:
        run_count, latest_run = self.load_runs_overview()
        scheduled_status = self.load_scheduled_status()
        workflow_report = self.load_latest_workflow_report()
        latest_publish = self.load_latest_publish_status()
        latest_wechat = self.load_latest_wechat_status()
        publish_preflight = self.load_publish_preflight()
        wechat_preflight = self.load_wechat_publish_preflight()
        benchmark_merge = self.load_benchmark_topic_merge_summary()
        notifications = self.list_notifications(limit=3)
        latest_log = next(iter(self.list_scheduled_logs(limit=1)), None)

        toutiao_tasks = self._filter_task_catalog(task_catalog, platform="toutiao")
        wechat_tasks = self._filter_task_catalog(task_catalog, platform="wechat")

        workflow_attempt = workflow_report.get("final_attempt", {}) if isinstance(workflow_report, dict) else {}
        workflow_scores = workflow_attempt.get("score_snapshot", {}) if isinstance(workflow_attempt, dict) else {}
        if not workflow_scores and latest_run is not None:
            workflow_scores = {
                "overall_score": latest_run.overall_score,
                "validation_score": latest_run.validation_score,
                "publish_readiness_score": latest_run.publish_readiness_score,
                "ai_generated_risk": latest_run.ai_risk,
            }

        toutiao_platform = self._build_toutiao_platform_snapshot(
            latest_run=latest_run,
            scheduled_status=scheduled_status,
            latest_publish=latest_publish,
            publish_preflight=publish_preflight,
            latest_log=latest_log,
            tasks=toutiao_tasks,
            workflow_scores=workflow_scores if isinstance(workflow_scores, dict) else {},
            benchmark_merge=benchmark_merge,
        )
        wechat_platform = self._build_wechat_platform_snapshot(
            latest_wechat=latest_wechat,
            wechat_preflight=wechat_preflight,
            latest_log=latest_log,
            tasks=wechat_tasks,
            benchmark_merge=benchmark_merge,
        )

        attention_items = self._build_account_attention_items(
            latest_run=latest_run,
            notifications=notifications,
            toutiao_platform=toutiao_platform,
            wechat_platform=wechat_platform,
        )
        overall_meta = self._derive_overall_meta(toutiao_platform, wechat_platform, attention_items)

        return {
            "key": self.settings.account_key,
            "slug": self.settings.account_slug,
            "label": self.settings.account_label,
            "brand_name": self.settings.brand_name,
            "focus": self.settings.industry_focus,
            "audience": self.settings.audience,
            "is_current": is_current,
            "search_text": " ".join(
                part
                for part in (
                    self.settings.account_key,
                    self.settings.account_label,
                    self.settings.brand_name,
                    self.settings.industry_focus,
                    self.settings.audience,
                )
                if part
            ).lower(),
            "run_count": run_count,
            "toutiao_publish_count": self.count_publish_history(),
            "wechat_publish_count": self.count_wechat_history(),
            "latest_run_name": latest_run.name if latest_run is not None else "",
            "latest_topic_title": latest_run.selected_topic_title if latest_run is not None else "",
            "latest_review_status": latest_run.review_status if latest_run is not None else "",
            "latest_notification": notifications[0] if notifications else {},
            "has_pending_review": bool(latest_run and latest_run.review_status == "pending"),
            "overall_label": overall_meta["label"],
            "overall_tone": overall_meta["tone"],
            "attention_items": attention_items,
            "quick_links": {
                "runs": f"/runs?account={self.account_key}",
                "published": f"/published?account={self.account_key}",
                "topics": f"/topics?account={self.account_key}",
                "latest_run": f"/runs/{latest_run.name}?account={self.account_key}" if latest_run is not None else "",
            },
            "toutiao": toutiao_platform,
            "wechat": wechat_platform,
            "platform_health": self.load_platform_health(),
            "health_panel": self.build_health_panel(self.load_platform_health()),
            "topic_pool": self.load_topic_pool_summary(),
            "content_guard": self.load_content_guard_summary(),
        }

    def load_run_detail(self, run_name: str) -> dict[str, Any]:
        run_dir = (self.runs_dir / run_name).resolve()
        if not run_dir.exists() or run_dir.parent != self.runs_dir.resolve():
            raise FileNotFoundError(f"未找到批次：{run_name}")

        review_status = self._load_json(run_dir / "review_status.json") or {}
        selected_topic_payload = self._load_json(run_dir / "selected_topic.json") or {}
        publish_meta = self._load_json(run_dir / self.settings.review.publish_dirname / "publish_meta.json") or {}
        content_score = self._load_json(run_dir / "content_score.json") or {}
        workflow_result = self._load_json(run_dir / "workflow_result.json") or {}
        publish_result = self._load_json(run_dir / self.settings.review.publish_dirname / "publish_result.json") or {}
        publish_status = self._build_publish_status_summary(run_dir.name, publish_result, publish_meta)
        titles_payload = self._parse_titles_file(run_dir / "titles.txt")
        article_body = self._read_text(run_dir / "article.md")
        editorial_packet = self._read_text(run_dir / "editorial_packet.md")
        review_packet = self._read_text(run_dir / "review_packet.md")
        publish_package = self._read_text(run_dir / self.settings.review.publish_dirname / "toutiao_publish_package.md")
        selected_topic = selected_topic_payload.get("selected_topic", {})
        preflight_summary = content_score.get("preflight_summary", {}) if isinstance(content_score, dict) else {}

        artifacts = {
            "review_packet": self._artifact_url(run_name, "review_packet.md"),
            "review_status": self._artifact_url(run_name, "review_status.json"),
            "run_manifest": self._artifact_url(run_name, "run_manifest.json"),
            "article": self._artifact_url(run_name, "article.md"),
            "titles": self._artifact_url(run_name, "titles.txt"),
            "content_score": self._artifact_url(run_name, "content_score.json"),
            "workflow_result": self._artifact_url(run_name, "workflow_result.json"),
        }
        if publish_meta:
            artifacts["publish_package"] = self._artifact_url(
                run_name, f"{self.settings.review.publish_dirname}/toutiao_publish_package.md"
            )
        debug_bundle = self._build_debug_bundle_view(publish_result, run_name)
        if publish_result:
            artifacts["publish_result"] = self._artifact_url(
                run_name, f"{self.settings.review.publish_dirname}/publish_result.json"
            )
        if debug_bundle.get("screenshot_url"):
            artifacts["debug_screenshot"] = str(debug_bundle["screenshot_url"])
        if debug_bundle.get("dom_url"):
            artifacts["debug_dom"] = str(debug_bundle["dom_url"])

        return {
            "run": self._build_run_summary(run_dir),
            "review_status": review_status,
            "selected_topic": selected_topic,
            "publish_meta": publish_meta,
            "publish_result": publish_result,
            "publish_status": publish_status,
            "debug_bundle": debug_bundle,
            "content_score": content_score,
            "workflow_result": workflow_result,
            "preflight_summary": preflight_summary,
            "titles": titles_payload,
            "article_body": article_body,
            "editorial_packet": editorial_packet,
            "review_packet": review_packet,
            "publish_package": publish_package,
            "artifacts": artifacts,
        }

    def list_scheduled_tasks(self) -> list[ScheduledTaskSummary]:
        return self._filter_task_catalog(None, platform="toutiao")

    def list_wechat_scheduled_tasks(self) -> list[ScheduledTaskSummary]:
        return self._filter_task_catalog(None, platform="wechat")

    def list_scheduled_logs(self, limit: int = 6) -> list[LogSummary]:
        if not self.scheduled_logs_dir.exists():
            return []
        files = [item for item in self.scheduled_logs_dir.iterdir() if item.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return [
            LogSummary(
                name=path.name,
                modified_at=self._format_timestamp(path.stat().st_mtime),
                size_kb=round(path.stat().st_size / 1024, 1),
                url=self._account_log_artifact_url(f"scheduled/{path.name}"),
            )
            for path in files[:limit]
        ]

    def list_notifications(self, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._load_json(self.automation_dir / "notifications.json")
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []
        return [item for item in payload[:limit] if isinstance(item, dict)]

    def list_publish_history(self, limit: int = 8) -> list[dict[str, Any]]:
        payload = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        if not isinstance(payload, list):
            return []
        result: list[dict[str, Any]] = []
        for item in islice(reversed(payload), limit):
            if not isinstance(item, dict):
                continue
            run_name = Path(str(item.get("run_dir", ""))).name if item.get("run_dir") else ""
            result.append(
                {
                    "published_at": str(item.get("published_at", "")),
                    "title": str(item.get("title", "")),
                    "topic_title": str(item.get("topic_title", "")),
                    "status": str(item.get("status", "")),
                    "external_url": str(item.get("external_url", "")),
                    "run_name": run_name,
                }
            )
        return result

    def list_published_articles(self, limit: int = 10) -> list[dict[str, Any]]:
        payload = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        if not isinstance(payload, list):
            return []

        result: list[dict[str, Any]] = []
        for item in islice(reversed(payload), limit):
            if not isinstance(item, dict):
                continue
            run_dir_value = str(item.get("run_dir", "")).strip()
            run_name = Path(run_dir_value).name if run_dir_value else ""
            if not run_name:
                continue

            run_dir = (self.runs_dir / run_name).resolve()
            if not run_dir.exists() or run_dir.parent != self.runs_dir.resolve():
                continue

            publish_dir = run_dir / self.publish_dirname
            title_path = publish_dir / "title.txt"
            article_path = publish_dir / "article.md"
            publish_result_path = publish_dir / "publish_result.json"

            title = self._read_text(title_path).strip() or str(item.get("title", "")).strip()
            article_body = self._read_text(article_path).strip() or self._read_text(run_dir / "article.md").strip()
            article_paragraphs = self._split_article_paragraphs(article_body)

            publish_result = self._load_json(publish_result_path) or {}
            backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}
            notes = item.get("notes", []) if isinstance(item.get("notes"), list) else []

            # Read AI risk score and quality scores from content_score.json
            score_payload = self._load_json(run_dir / "content_score.json") or {}
            scores = score_payload.get("scores", {}) if isinstance(score_payload.get("scores"), dict) else {}
            overall_score = int(scores.get("overall_score", 0) or 0)
            ai_risk_score = int(scores.get("ai_generated_risk", 0) or 0)
            validation_score = int(scores.get("validation_score", 0) or 0)
            publish_readiness_score = int(scores.get("publish_readiness_score", 0) or 0)

            # Build per-platform publish status
            platform_statuses = self._collect_run_retry_state(run_dir)[1]

            result.append(
                {
                    "title": title,
                    "published_at": str(item.get("published_at", "")),
                    "status": str(item.get("status", "")),
                    "topic_title": str(item.get("topic_title", "")),
                    "topic_url": str(item.get("topic_url", "")),
                    "run_name": run_name,
                    "external_url": str(item.get("external_url", "")),
                    "pgc_id": str(backend.get("pgc_id", "")),
                    "message": str(backend.get("message", publish_result.get("message", "")))
                    if isinstance(publish_result, dict)
                    else "",
                    "article_body": article_body,
                    "article_paragraphs": article_paragraphs,
                    "excerpt": "\n\n".join(article_paragraphs[:3]),
                    "notes": [str(note) for note in notes if str(note).strip()],
                    "overall_score": overall_score,
                    "ai_risk_score": ai_risk_score,
                    "validation_score": validation_score,
                    "publish_readiness_score": publish_readiness_score,
                    "platform_statuses": platform_statuses,
                    "artifacts": {
                        "title": self._artifact_url(run_name, f"{self.publish_dirname}/title.txt"),
                        "article": self._artifact_url(run_name, f"{self.publish_dirname}/article.md"),
                        "publish_result": self._artifact_url(run_name, f"{self.publish_dirname}/publish_result.json"),
                        "run_detail": f"/runs/{run_name}?account={self.account_key}",
                        "compare": f"/runs/{run_name}/compare?account={self.account_key}",
                    },
                }
            )
        return result

    def load_latest_publish_status(self) -> PublishStatusSummary:
        history = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        if not isinstance(history, list) or not history:
            return PublishStatusSummary(
                status="not_published",
                submitted_at="",
                title="",
                run_name="",
                topic_title="",
                pgc_id="",
                message="",
                external_url="",
                screenshot_url="",
                dom_url="",
                debug_dir="",
                has_result=False,
            )

        latest = history[-1] if isinstance(history[-1], dict) else {}
        run_name = Path(str(latest.get("run_dir", ""))).name if latest.get("run_dir") else ""
        publish_result = {}
        if run_name:
            publish_result = (
                self._load_json(self.runs_dir / run_name / self.settings.review.publish_dirname / "publish_result.json")
                or {}
            )
        return self._build_publish_status_summary(run_name, publish_result, latest)

    def load_latest_wechat_status(self) -> WechatPublishStatusSummary:
        history = self._load_json(self.output_dir / self.settings.wechat.history_filename)
        if not isinstance(history, list) or not history:
            snapshot = self._load_json(self.automation_dir / "last_wechat_publish_result.json")
            if not isinstance(snapshot, dict):
                return WechatPublishStatusSummary(
                    status="not_published",
                    submitted_at="",
                    title="",
                    run_name="",
                    draft_media_id="",
                    submit_status="",
                    external_url="",
                    inline_image_count=0,
                    layout_backend="",
                    submit_requested=False,
                    has_result=False,
                )
            return self._build_wechat_status_summary(snapshot, snapshot)

        latest = history[-1] if isinstance(history[-1], dict) else {}
        run_name = Path(str(latest.get("run_dir", ""))).name if latest.get("run_dir") else ""
        publish_result = {}
        if run_name:
            publish_result = (
                self._load_json(self.runs_dir / run_name / self.settings.review.publish_dirname / "wechat" / "wechat_publish_result.json")
                or {}
            )
        return self._build_wechat_status_summary(publish_result, latest)

    def load_scheduled_status(self) -> dict[str, Any]:
        path = self.automation_dir / "scheduled_status.json"
        payload = recover_stale_running_status(path)
        return payload if isinstance(payload, dict) else {}

    def load_latest_workflow_report(self) -> dict[str, Any]:
        payload = self._load_json(self.automation_dir / "last_daily_workflow_report.json")
        return payload if isinstance(payload, dict) else {}

    def load_benchmark_topic_merge_summary(self) -> dict[str, Any]:
        path = self.automation_dir / "benchmark_topic_merge.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {}

        benchmark_accounts = payload.get("benchmark_accounts", [])
        if not isinstance(benchmark_accounts, list):
            benchmark_accounts = []
        top_titles = payload.get("top_titles", [])
        if not isinstance(top_titles, list):
            top_titles = []

        return {
            "generated_at": str(payload.get("generated_at", "")).strip(),
            "account_key": str(payload.get("account_key", "")).strip(),
            "account_label": str(payload.get("account_label", "")).strip(),
            "platform": str(payload.get("platform", "")).strip().lower(),
            "track": str(payload.get("track", "")).strip(),
            "base_topic_count": int(payload.get("base_topic_count", 0) or 0),
            "benchmark_topic_count": int(payload.get("benchmark_topic_count", 0) or 0),
            "merged_topic_count": int(payload.get("merged_topic_count", 0) or 0),
            "benchmark_source_count": int(payload.get("benchmark_source_count", 0) or 0),
            "benchmark_accounts": [item for item in benchmark_accounts if isinstance(item, dict)],
            "top_titles": [item for item in top_titles if isinstance(item, dict)],
            "artifact_url": self._account_output_artifact_url("automation/benchmark_topic_merge.json"),
        }

    def load_platform_health(self) -> dict[str, Any]:
        path = self.automation_dir / "platform_health.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {
                "generated_at": "",
                "status": "unknown",
                "status_label": "未知",
                "status_tone": "neutral",
                "blocking_count": 0,
                "platforms": {},
            }

        platforms = payload.get("platforms", {}) if isinstance(payload.get("platforms"), dict) else {}
        normalized_platforms: dict[str, Any] = {}
        blocking_count = 0
        worst_tone = "success"
        for name, item in platforms.items():
            if not isinstance(item, dict):
                continue
            status_value = str(item.get("status", "unknown")).strip().lower() or "unknown"
            meta = self._describe_status(status_value)
            blocking_flags = item.get("blocking_flags", []) if isinstance(item.get("blocking_flags"), list) else []
            warnings = item.get("warnings", []) if isinstance(item.get("warnings"), list) else []
            blocking_count += len(blocking_flags)
            if meta["tone"] == "error":
                worst_tone = "error"
            elif meta["tone"] == "warn" and worst_tone != "error":
                worst_tone = "warn"
            normalized_platforms[str(name)] = {
                **item,
                "status_label": meta["label"],
                "status_tone": meta["tone"],
                "blocking_flags": blocking_flags,
                "warnings": warnings,
            }

        if worst_tone == "error":
            status_value = "blocked"
        elif worst_tone == "warn":
            status_value = "pending"
        elif normalized_platforms:
            status_value = "ready"
        else:
            status_value = "unknown"
        status_meta = self._describe_status(status_value)
        return {
            "generated_at": str(payload.get("generated_at", "")).strip(),
            "status": status_value,
            "status_label": status_meta["label"],
            "status_tone": status_meta["tone"],
            "blocking_count": blocking_count,
            "platforms": normalized_platforms,
        }

    def load_topic_pool_summary(self) -> dict[str, Any]:
        path = self.automation_dir / "topic_pool" / "topic_pool_summary.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {
                "status": "unknown",
                "status_label": "未知",
                "status_tone": "neutral",
                "total_rows": 0,
                "pending_count": 0,
                "selected_count": 0,
                "discarded_count": 0,
            }

        pending_count = int(payload.get("pending_count", 0) or 0)
        total_rows = int(payload.get("total_rows", 0) or 0)
        selected_count = int(payload.get("selected_count", 0) or 0)
        discarded_count = int(payload.get("discarded_count", 0) or 0)
        if pending_count > 0:
            status_value = "ready"
        elif total_rows > 0:
            status_value = "no_topics"
        else:
            status_value = "unknown"
        status_meta = self._describe_status(status_value)
        return {
            "status": status_value,
            "status_label": status_meta["label"],
            "status_tone": status_meta["tone"],
            "total_rows": total_rows,
            "pending_count": pending_count,
            "selected_count": selected_count,
            "discarded_count": discarded_count,
        }

    def load_content_guard_summary(self) -> dict[str, Any]:
        path = self.automation_dir / "content_guard_report.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {
                "status": "unknown",
                "status_label": "未知",
                "status_tone": "neutral",
                "similarity": 0.0,
                "threshold": 0.0,
                "matched_title": "",
            }

        blocked = bool(payload.get("blocked", False))
        similarity = float(payload.get("max_similarity", 0.0) or 0.0)
        threshold = float(payload.get("threshold", 0.0) or 0.0)
        if blocked:
            status_value = "blocked"
        elif similarity > 0:
            status_value = "pending"
        else:
            status_value = "ready"
        status_meta = self._describe_status(status_value)
        return {
            "status": status_value,
            "status_label": status_meta["label"],
            "status_tone": status_meta["tone"],
            "similarity": similarity,
            "threshold": threshold,
            "matched_title": str(payload.get("matched_title", "")).strip(),
        }

    def load_performance_feedback(self) -> dict[str, Any]:
        performance_dir = self.output_dir / "growth_ops" / "performance"
        report_path = performance_dir / "performance_report.json"
        ledger_path = performance_dir / "performance_ledger.json"

        report_payload = self._load_json(report_path)
        ledger_payload = self._load_json(ledger_path)
        report = report_payload if isinstance(report_payload, dict) else {}
        summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
        ledger_rows = ledger_payload if isinstance(ledger_payload, list) else []
        valid_rows = [item for item in ledger_rows if isinstance(item, dict)]
        valid_rows.sort(
            key=lambda item: str(item.get("published_at", "")).strip() or str(item.get("updated_at", "")).strip(),
            reverse=True,
        )

        published_count = int(summary.get("published_count", 0) or 0)
        if published_count <= 0:
            published_count = len(valid_rows)

        with_metrics_count = int(summary.get("with_metrics_count", 0) or 0)
        if with_metrics_count <= 0:
            with_metrics_count = sum(
                1
                for item in valid_rows
                if isinstance(item.get("metrics_latest"), dict) and bool(item.get("metrics_latest"))
            )

        computed_analysis_eligible_count = sum(
            1
            for item in valid_rows
            if isinstance(item.get("metrics_latest"), dict)
            and bool(item.get("metrics_latest"))
            and not self._performance_row_is_pending_t1(item)
            and bool(
                item.get("metrics_latest", {}).get(
                    "analysis_eligible",
                    str(item.get("metrics_latest", {}).get("availability_status", "available")).strip()
                    == "available",
                )
            )
        )
        analysis_eligible_count = (
            computed_analysis_eligible_count if valid_rows else int(summary.get("analysis_eligible_count", 0) or 0)
        )
        computed_pending_t1_count = sum(1 for item in valid_rows if self._performance_row_is_pending_t1(item))
        pending_t1_count = (
            computed_pending_t1_count if valid_rows else int(summary.get("pending_t1_count", 0) or 0)
        )

        collection_dates = [
            str(item.get("metrics_latest", {}).get("collection_date", "")).strip()
            for item in valid_rows
            if isinstance(item.get("metrics_latest"), dict)
        ]
        collection_date = str(summary.get("collection_date", "")).strip() or max(collection_dates, default="")
        latest_earnings_dates = [
            str(
                item.get("metrics_latest", {}).get("earnings_date")
                or item.get("metrics_latest", {}).get("data_date", "")
            ).strip()
            for item in valid_rows
            if str(item.get("metrics_latest", {}).get("collection_date", "")).strip() == collection_date
            and str(
                item.get("metrics_latest", {}).get("earnings_date")
                or item.get("metrics_latest", {}).get("data_date", "")
            ).strip()
        ]
        earnings_date = str(summary.get("earnings_date", "")).strip() or max(latest_earnings_dates, default="")
        known_platform_revenue = 0.0
        platform_revenue_known_count = 0
        for item in valid_rows:
            metrics = item.get("metrics_latest", {}) if isinstance(item.get("metrics_latest"), dict) else {}
            if collection_date and str(metrics.get("collection_date", "")).strip() != collection_date:
                continue
            if earnings_date and str(metrics.get("earnings_date") or metrics.get("data_date", "")).strip() != earnings_date:
                continue
            if infer_availability_status(item) != "available":
                continue
            platform_revenue = metrics.get("platform_revenue")
            platform_status = str(metrics.get("platform_revenue_status", "")).strip()
            if platform_status == "available" and platform_revenue is not None:
                try:
                    known_platform_revenue += float(platform_revenue or 0.0)
                    platform_revenue_known_count += 1
                except (TypeError, ValueError):
                    continue
        if not valid_rows:
            try:
                known_platform_revenue = float(summary.get("known_platform_revenue", 0.0) or 0.0)
            except (TypeError, ValueError):
                known_platform_revenue = 0.0
            platform_revenue_known_count = int(summary.get("platform_revenue_known_count", 0) or 0)

        averages = summary.get("averages", {}) if isinstance(summary.get("averages"), dict) else {}
        top_by_reads = summary.get("top_by_reads", []) if isinstance(summary.get("top_by_reads"), list) else []
        top_articles = [item for item in top_by_reads if isinstance(item, dict)][:5]

        latest_records: list[dict[str, Any]] = []
        for item in valid_rows[:5]:
            metrics = item.get("metrics_latest", {}) if isinstance(item.get("metrics_latest"), dict) else {}
            tags = item.get("experiment_tags", {}) if isinstance(item.get("experiment_tags"), dict) else {}
            inferred_availability = infer_availability_status(item)
            raw_availability = str(metrics.get("availability_status", "")).strip()
            availability_status = (
                inferred_availability
                if inferred_availability in {"pending_t1", "not_available"}
                else raw_availability or inferred_availability
            )
            raw_revenue_status = str(metrics.get("revenue_status", "unknown")).strip() or "unknown"
            raw_platform_status = str(metrics.get("platform_revenue_status", "unknown")).strip() or "unknown"
            revenue_status = "pending_t1" if inferred_availability == "pending_t1" else raw_revenue_status
            platform_revenue_status = (
                "pending_t1" if inferred_availability == "pending_t1" else raw_platform_status
            )
            publish_status = str(item.get("publish_status", "")).strip() or "unknown"
            publish_meta = self._describe_status(publish_status)
            run_name = Path(str(item.get("run_dir", "")).strip()).name
            latest_records.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "platform": str(item.get("platform", "")).strip() or "toutiao",
                    "published_at": str(item.get("published_at", "")).strip(),
                    "published_date": str(metrics.get("published_date", "")).strip(),
                    "collection_date": str(metrics.get("collection_date", "")).strip(),
                    "earnings_date": str(metrics.get("earnings_date", "") or metrics.get("data_date", "")).strip(),
                    "availability_status": availability_status,
                    "revenue_status": revenue_status,
                    "platform_revenue_status": platform_revenue_status,
                    "platform_revenue": None if inferred_availability != "available" else metrics.get("platform_revenue"),
                    "platform_revenue_known": (
                        inferred_availability == "available"
                        and
                        platform_revenue_status == "available"
                        and metrics.get("platform_revenue") is not None
                    ),
                    "publish_status": publish_status,
                    "publish_status_label": publish_meta["label"],
                    "publish_status_tone": publish_meta["tone"],
                    "run_name": run_name,
                    "has_metrics": bool(metrics),
                    "read_count": int(metrics.get("read_count", 0) or 0),
                    "impression_count": int(metrics.get("impression_count", 0) or 0),
                    "read_rate": float(metrics.get("read_rate", 0) or 0),
                    "interaction_rate": float(metrics.get("interaction_rate", 0) or 0),
                    "recommendation_ratio": float(metrics.get("recommendation_ratio", 0) or 0),
                    "title_style": str(tags.get("title_style", "")).strip(),
                    "content_angle": str(tags.get("content_angle", "")).strip(),
                    "topic_bucket": str(tags.get("topic_bucket", "")).strip(),
                    "test_focus": str(tags.get("test_focus", "")).strip(),
                    "run_detail_url": f"/runs/{run_name}?account={self.account_key}" if run_name else "",
                }
            )

        normalized_averages = {
            "read_rate": float(averages.get("read_rate", 0) or 0),
            "interaction_rate": float(averages.get("interaction_rate", 0) or 0),
            "recommendation_ratio": float(averages.get("recommendation_ratio", 0) or 0),
        }

        generated_at = str(report.get("generated_at", "")).strip()
        if not generated_at and valid_rows:
            generated_at = str(valid_rows[0].get("updated_at", "")).strip()

        if published_count <= 0:
            status_value = "idle"
            summary_message = "还没有沉淀到可分析的发布数据。"
        elif analysis_eligible_count > 0:
            status_value = "ready"
            summary_message = (
                f"已收集到 {analysis_eligible_count} 条可分析样本，收益归属日：{earnings_date or '昨日'}。"
            )
        else:
            status_value = "pending"
            summary_message = f"当前没有可分析样本，收益归属日：{earnings_date or '昨日'}。"
        if pending_t1_count:
            summary_message += f" 今日文章 T+1 待结算（{pending_t1_count} 条），不按 0 收益处理。"
        status_meta = self._describe_status(status_value)

        recommendations = self._build_performance_recommendations(
            published_count=published_count,
            with_metrics_count=analysis_eligible_count,
            averages=normalized_averages,
            latest_records=latest_records,
            top_articles=top_articles,
        )

        return {
            "available": bool(report or valid_rows),
            "generated_at": generated_at,
            "status": status_value,
            "status_label": status_meta["label"],
            "status_tone": status_meta["tone"],
            "summary_message": summary_message,
            "published_count": published_count,
            "with_metrics_count": with_metrics_count,
            "analysis_eligible_count": analysis_eligible_count,
            "pending_t1_count": pending_t1_count,
            "collection_date": collection_date,
            "earnings_date": earnings_date,
            "known_platform_revenue": round(known_platform_revenue, 4),
            "platform_revenue_known_count": platform_revenue_known_count,
            "averages": normalized_averages,
            "top_articles": top_articles,
            "latest_records": latest_records,
            "recommendations": recommendations,
            "artifacts": {
                "report_json": self._account_output_artifact_url("growth_ops/performance/performance_report.json")
                if report_path.exists()
                else "",
                "ledger_json": self._account_output_artifact_url("growth_ops/performance/performance_ledger.json")
                if ledger_path.exists()
                else "",
                "report_md": self._account_output_artifact_url("growth_ops/performance/performance_report.md")
                if (performance_dir / "performance_report.md").exists()
                else "",
                "ledger_csv": self._account_output_artifact_url("growth_ops/performance/performance_ledger.csv")
                if (performance_dir / "performance_ledger.csv").exists()
                else "",
            },
        }

    def _performance_row_is_pending_t1(self, item: dict[str, Any]) -> bool:
        return infer_availability_status(item) == "pending_t1"

    def _build_performance_recommendations(
        self,
        *,
        published_count: int,
        with_metrics_count: int,
        averages: dict[str, float],
        latest_records: list[dict[str, Any]],
        top_articles: list[dict[str, Any]],
    ) -> list[str]:
        recommendations: list[str] = []
        if published_count <= 0:
            return ["先稳定发满 3 到 5 篇内容，再根据数据判断标题和选题方向。"]

        if with_metrics_count <= 0:
            recommendations.append("优先补齐曝光、阅读和互动数据，避免在没有反馈的情况下盲目调 prompt。")
        else:
            if averages.get("read_rate", 0.0) < 5.0:
                recommendations.append("平均阅读率低于 5%，优先优化标题和封面承诺感。")
            if averages.get("interaction_rate", 0.0) < 3.0:
                recommendations.append("平均互动率低于 3%，需要增强正文观点密度和结尾互动设计。")
            if averages.get("recommendation_ratio", 0.0) > 0:
                recommendations.append(
                    f"当前推荐流量占比约 {round(float(averages.get('recommendation_ratio', 0.0)), 2)}%，可以继续测试相近题材的标题打法。"
                )

        style_counts: dict[str, int] = {}
        bucket_counts: dict[str, int] = {}
        for item in latest_records:
            style = str(item.get("title_style", "")).strip()
            bucket = str(item.get("topic_bucket", "")).strip()
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if bucket:
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
            if str(item.get("publish_status", "")).strip() == "draft_saved":
                recommendations.append("近期存在仅保存草稿的样本，复盘时要单独区分草稿测试和真实发布效果。")
                break

        if style_counts:
            best_style = max(style_counts.items(), key=lambda pair: pair[1])[0]
            recommendations.append(f"最近产出最多的标题风格是 {best_style}，建议继续做 A/B 测试而不是长期只用一种写法。")
        if bucket_counts:
            best_bucket = max(bucket_counts.items(), key=lambda pair: pair[1])[0]
            recommendations.append(f"当前内容集中在 {best_bucket} 题材，后续可再补 3 到 5 篇相邻题材样本做对比。")

        if top_articles:
            top_item = top_articles[0]
            top_style = str(top_item.get("title_style", "")).strip()
            top_title = str(top_item.get("title", "")).strip()
            if top_style:
                recommendations.append(f"目前表现最好的标题风格是 {top_style}，可以拆解《{top_title}》的结构继续复用。")
            elif top_title:
                recommendations.append(f"优先复盘《{top_title}》为什么跑出来，再决定下一轮选题与标题策略。")

        deduped: list[str] = []
        seen: set[str] = set()
        for item in recommendations:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped[:5]

    def build_health_panel(self, platform_health: dict[str, Any]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        platforms = platform_health.get("platforms", {}) if isinstance(platform_health, dict) else {}
        for key, label in (("toutiao", "今日头条"), ("wechat", "公众号")):
            payload = platforms.get(key)
            if not isinstance(payload, dict):
                continue
            blocking_flags = payload.get("blocking_flags", []) if isinstance(payload.get("blocking_flags"), list) else []
            warnings = payload.get("warnings", []) if isinstance(payload.get("warnings"), list) else []
            message = ""
            if blocking_flags:
                message = str(blocking_flags[0])
            elif warnings:
                message = str(warnings[0])
            else:
                message = "状态正常"
            diagnostics = payload.get("diagnostics", {}) if isinstance(payload.get("diagnostics"), dict) else {}
            blocked_reason = str(diagnostics.get("blocked_reason", "")).strip().upper()
            needs_login = (
                bool(payload.get("enabled"))
                and str(payload.get("status_tone", "")) == "error"
                and blocked_reason != "BLOCKED_BY_IP"
            )
            items.append(
                {
                    "key": key,
                    "label": label,
                    "status_label": str(payload.get("status_label", "未知")),
                    "status_tone": str(payload.get("status_tone", "neutral")),
                    "needs_login": needs_login,
                    "message": message,
                    "blocked_reason": blocked_reason,
                    "config_url": "https://mp.weixin.qq.com/",
                    "login_command": (
                        f"powershell -ExecutionPolicy Bypass -File .\\scripts\\init_publish_login.ps1 -Account {self.settings.account_key}"
                        if needs_login and key in {"toutiao", "wechat"}
                        else ""
                    ),
                }
            )
        return {
            "updated_at": str(platform_health.get("generated_at", "")).strip() if isinstance(platform_health, dict) else "",
            "items": items,
        }

    def load_wechat_publish_preflight(self) -> dict[str, Any]:
        path = self.automation_dir / "wechat_publish_preflight.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {"updated_at": "", "state": "", "current_url": ""}
        return {
            "updated_at": self._format_timestamp(path.stat().st_mtime) if path.exists() else "",
            "state": str(payload.get("state", "")),
            "current_url": str(payload.get("current_url", "")),
            "profile_dir": str(payload.get("profile_dir", "")),
        }

    def list_scheduled_windows(self) -> list[ScheduledWindowSummary]:
        notifications = self._load_json(self.automation_dir / "notifications.json")
        items = notifications if isinstance(notifications, list) else []
        slot_map: dict[str, ScheduledWindowSummary] = {}
        latest_logs_by_slot = self._latest_scheduled_logs_by_slot()

        for item in items:
            if not isinstance(item, dict):
                continue
            created_at = str(item.get("created_at", ""))
            if not created_at:
                continue
            slot = self._resolve_schedule_slot(created_at)
            if not slot:
                continue
            data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
            log_url = ""
            log_file = str(data.get("log_file", ""))
            if log_file:
                log_name = Path(log_file).name
                log_url = self._account_log_artifact_url(f"scheduled/{log_name}")
            elif slot in latest_logs_by_slot:
                log_url = latest_logs_by_slot[slot]
            slot_map.setdefault(
                slot,
                ScheduledWindowSummary(
                    slot_label=slot,
                    expected_time=self._slot_to_expected_time(slot),
                    status=str(item.get("level", "info")),
                    updated_at=created_at,
                    message=str(item.get("message", "")),
                    run_name=str(data.get("run_name", "")),
                    publish_status=str(data.get("publish_status", "")),
                    publish_pgc_id=str(data.get("publish_pgc_id", "")),
                    log_url=log_url,
                ),
            )

        result = []
        for slot in ("morning", "noon", "evening", "night"):
            if slot in slot_map:
                result.append(slot_map[slot])
            else:
                result.append(
                    ScheduledWindowSummary(
                        slot_label=slot,
                        expected_time=self._slot_to_expected_time(slot),
                        status="unknown",
                        updated_at="",
                        message="当前还没有这个时段的执行记录。",
                        run_name="",
                        publish_status="",
                        publish_pgc_id="",
                        log_url=latest_logs_by_slot.get(slot, ""),
                    )
                )
        return result

    def _build_run_summary(self, path: Path) -> RunSummary:
        review_status = self._load_json(path / "review_status.json") or {}
        publish_meta = self._load_json(path / self.settings.review.publish_dirname / "publish_meta.json") or {}
        selected_topic_payload = self._load_json(path / "selected_topic.json") or {}
        score_payload = self._load_json(path / "content_score.json") or {}
        score_snapshot = review_status.get("score_snapshot") or score_payload.get("scores") or {}
        created_at = (
            review_status.get("updated_at")
            or review_status.get("created_at")
            or (self._load_json(path / "run_manifest.json") or {}).get("archived_at")
            or ""
        )
        selected_topic = selected_topic_payload.get("selected_topic", {})
        publish_title = str(publish_meta.get("selected_title", ""))
        retry_platforms, latest_platform_status = self._collect_run_retry_state(path)
        return RunSummary(
            name=path.name,
            created_at=str(created_at),
            selected_topic_title=str(
                selected_topic.get("title")
                or review_status.get("selected_topic_title")
                or publish_meta.get("selected_topic", {}).get("title", "")
            ),
            review_status=str(review_status.get("status", "pending")),
            recommended_action=str(review_status.get("recommended_action", "manual_review")),
            overall_score=int(score_snapshot.get("overall_score", 0)),
            ai_risk=int(score_snapshot.get("ai_generated_risk", 0)),
            validation_score=int(score_snapshot.get("validation_score", 0)),
            publish_readiness_score=int(score_snapshot.get("publish_readiness_score", 0)),
            has_publish=(path / self.settings.review.publish_dirname).exists(),
            publish_title=publish_title,
            can_retry=bool(retry_platforms),
            retry_platforms=retry_platforms,
            latest_platform_status=latest_platform_status,
            path=path,
        )

    def _collect_run_retry_state(self, run_dir: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
        publish_root = run_dir / self.settings.review.publish_dirname
        specs = [
            ("toutiao", "头条", publish_root / "publish_result.json", publish_root.exists()),
            ("wechat", "公众号", publish_root / "wechat" / "wechat_publish_result.json", (publish_root / "wechat").exists()),
        ]
        retry_platforms: list[dict[str, str]] = []
        latest_platform_status: dict[str, dict[str, str]] = {}
        success_states = {"published", "submitted", "submitted_for_review", "success", "approved", "draft_created"}

        for platform, label, result_path, has_assets in specs:
            payload = self._load_json(result_path) if result_path.exists() else {}
            payload = payload if isinstance(payload, dict) else {}
            status = str(payload.get("status", "")).strip().lower()
            message = str(payload.get("message") or payload.get("error") or payload.get("submit_status") or "").strip()
            if not status and has_assets:
                status = "pending"
                message = "发布结果文件为空"
            elif not status:
                status = "idle"
                message = "发布结果异常"
            meta = self._describe_status(status)
            latest_platform_status[platform] = {
                "platform": platform,
                "label": label,
                "status": status,
                "status_label": meta["label"],
                "tone": meta["tone"],
                "message": message or meta["label"],
            }
            if has_assets and status not in success_states:
                retry_platforms.append(
                    {
                        "platform": platform,
                        "label": label,
                        "status": status,
                        "status_label": meta["label"],
                        "tone": meta["tone"],
                        "message": message or meta["label"],
                    }
                )
        return retry_platforms, latest_platform_status

    def _build_publish_status_summary(
        self,
        run_name: str,
        publish_result: dict[str, Any],
        fallback_payload: dict[str, Any],
    ) -> PublishStatusSummary:
        backend = publish_result.get("backend_response_summary", {}) if isinstance(publish_result, dict) else {}
        record = publish_result.get("record", {}) if isinstance(publish_result, dict) else {}
        source = publish_result if isinstance(publish_result, dict) and publish_result else fallback_payload
        debug_bundle = self._build_debug_bundle_view(publish_result if isinstance(publish_result, dict) else {}, run_name)
        return PublishStatusSummary(
            status=str(source.get("status", fallback_payload.get("status", "not_published"))),
            submitted_at=str(source.get("published_at", fallback_payload.get("published_at", ""))),
            title=str(record.get("title", fallback_payload.get("title", ""))),
            run_name=run_name,
            topic_title=str(record.get("topic_title", fallback_payload.get("topic_title", ""))),
            pgc_id=str(backend.get("pgc_id", "")),
            message=str(backend.get("message", publish_result.get("message", ""))),
            external_url=str(source.get("external_url", fallback_payload.get("external_url", ""))),
            screenshot_url=str(debug_bundle.get("screenshot_url", "")),
            dom_url=str(debug_bundle.get("dom_url", "")),
            debug_dir=str(debug_bundle.get("debug_dir", "")),
            has_result=bool(publish_result or fallback_payload),
        )

    def _build_wechat_status_summary(
        self,
        publish_result: dict[str, Any],
        fallback_payload: dict[str, Any],
    ) -> WechatPublishStatusSummary:
        source = publish_result if isinstance(publish_result, dict) and publish_result else fallback_payload
        run_name = Path(str(source.get("run_dir", fallback_payload.get("run_dir", "")))).name
        inline_images = source.get("inline_image_urls", [])
        status = str(source.get("status", "")).strip()
        draft_media_id = str(source.get("draft_media_id", fallback_payload.get("draft_media_id", ""))).strip()
        submit_status = str(source.get("submit_status", fallback_payload.get("submit_status", ""))).strip()
        external_url = str(source.get("external_url", fallback_payload.get("external_url", ""))).strip()
        if not status:
            if submit_status or external_url:
                status = "submitted"
            elif draft_media_id:
                status = "draft_created"
            else:
                status = "not_published"
        return WechatPublishStatusSummary(
            status=status,
            submitted_at=str(source.get("published_at", fallback_payload.get("published_at", ""))),
            title=str(source.get("title", fallback_payload.get("title", ""))),
            run_name=run_name,
            draft_media_id=draft_media_id,
            submit_status=submit_status,
            external_url=external_url,
            inline_image_count=len(inline_images) if isinstance(inline_images, list) else 0,
            layout_backend=str(source.get("layout_backend", fallback_payload.get("layout_backend", ""))),
            submit_requested=bool(source.get("submit_requested", fallback_payload.get("submit_requested", False))),
            has_result=bool(publish_result or fallback_payload),
        )

    def load_publish_preflight(self) -> dict[str, Any]:
        path = self.automation_dir / "publish_preflight.json"
        payload = self._load_json(path)
        if not isinstance(payload, dict):
            return {
                "updated_at": "",
                "blocking_flags": [],
                "warnings": [],
                "diagnostics": {},
            }
        return {
            "updated_at": self._format_timestamp(path.stat().st_mtime) if path.exists() else "",
            "current_url": str(payload.get("current_url", "")),
            "blocking_flags": list(payload.get("blocking_flags", []))
            if isinstance(payload.get("blocking_flags"), list)
            else [],
            "warnings": list(payload.get("warnings", [])) if isinstance(payload.get("warnings"), list) else [],
            "diagnostics": payload.get("diagnostics", {}) if isinstance(payload.get("diagnostics"), dict) else {},
        }

    def _load_json(self, path: Path) -> Any | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _parse_titles_file(self, path: Path) -> dict[str, list[str]]:
        text = self._read_text(path)
        if not text:
            return {"all_titles": [], "recommended_titles": []}
        recommended_marker = "推荐标题："
        if recommended_marker in text:
            all_part, recommended_part = text.split(recommended_marker, 1)
        else:
            all_part, recommended_part = text, ""
        all_part = all_part.replace("全部标题：", "", 1).strip()
        return {
            "all_titles": self._parse_numbered_lines(all_part),
            "recommended_titles": self._parse_numbered_lines(recommended_part),
        }

    def _parse_numbered_lines(self, text: str) -> list[str]:
        result: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\d+\.\s*(.+)$", line.strip())
            if match:
                result.append(match.group(1).strip())
        return result

    def _format_timestamp(self, raw_timestamp: float) -> str:
        return datetime.fromtimestamp(raw_timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def _artifact_url(self, run_name: str, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/runs/{run_name}/{relative_path}"

    def _build_debug_bundle_view(self, publish_result: dict[str, Any], run_name: str) -> dict[str, str]:
        if not isinstance(publish_result, dict) or not publish_result:
            return {}

        screenshot_path = str(publish_result.get("screenshot_path", "") or "").strip()
        dom_path = str(publish_result.get("dom_path", "") or "").strip()
        debug_dir = str(publish_result.get("debug_bundle_dir", "") or "").strip()
        artifacts = publish_result.get("debug_artifacts", {}) if isinstance(publish_result.get("debug_artifacts"), dict) else {}

        if not screenshot_path:
            screenshot_path = str(artifacts.get("screenshot", "") or "").strip()
        if not dom_path:
            dom_path = str(artifacts.get("page_html", "") or "").strip()

        return {
            "debug_dir": debug_dir,
            "screenshot_url": self._absolute_path_to_output_artifact_url(screenshot_path),
            "dom_url": self._absolute_path_to_output_artifact_url(dom_path),
        }

    def _absolute_path_to_output_artifact_url(self, absolute_path: str) -> str:
        raw = str(absolute_path or "").strip()
        if not raw:
            return ""
        candidate = Path(raw)
        try:
            resolved = candidate.resolve(strict=False)
            output_root = self.output_dir.resolve()
        except Exception:
            return ""
        if resolved == output_root or output_root not in resolved.parents:
            return ""
        relative = resolved.relative_to(output_root).as_posix()
        return self._account_output_artifact_url(relative)

    def _account_output_artifact_url(self, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/output/{relative_path}"

    def _account_log_artifact_url(self, relative_path: str) -> str:
        return f"/artifacts/{self.account_key}/logs/{relative_path}"

    def count_runs(self) -> int:
        count, _ = self.load_runs_overview()
        return count

    def load_runs_overview(self) -> tuple[int, RunSummary | None]:
        if not self.runs_dir.exists():
            return 0, None
        candidates = [item for item in self.runs_dir.iterdir() if item.is_dir()]
        count = len(candidates)
        latest_path = max(candidates, key=self._run_sort_key, default=None)
        return count, self._build_run_summary(latest_path) if latest_path is not None else None

    def _run_sort_key(self, path: Path) -> tuple[float, float, str]:
        scheduled_at = self._run_name_timestamp(path.name)
        modified_at = path.stat().st_mtime
        return (scheduled_at or modified_at, modified_at, path.name)

    def _run_name_timestamp(self, run_name: str) -> float:
        prefix = str(run_name).split("_", 1)[0].strip()
        try:
            return datetime.strptime(prefix, "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            return 0.0

    def count_publish_history(self) -> int:
        payload = self._load_json(self.output_dir / self.settings.publishing.history_filename)
        return len(payload) if isinstance(payload, list) else 0

    def count_wechat_history(self) -> int:
        payload = self._load_json(self.output_dir / self.settings.wechat.history_filename)
        return len(payload) if isinstance(payload, list) else 0

    @classmethod
    def load_scheduled_task_catalog(cls) -> list[ScheduledTaskSummary]:
        now = time.time()
        if cls._TASK_CATALOG_CACHE and (now - cls._TASK_CATALOG_CACHE_AT) < cls._TASK_CATALOG_TTL_SECONDS:
            return list(cls._TASK_CATALOG_CACHE)
        filters = " -or ".join(
            f"$_.TaskName -like '{prefix}-*'" for prefix in cls.SCHEDULED_TASK_PREFIXES.values()
        )
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{ {filters} }}; "
                "if (-not $tasks) { '[]'; exit 0 }; "
                "$tasks | ForEach-Object { "
                "$info = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
                "[pscustomobject]@{"
                "task_name = $_.TaskName; "
                "state = $_.State.ToString(); "
                "last_run_time = if ($info.LastRunTime -and $info.LastRunTime -gt [datetime]::MinValue) { $info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }; "
                "next_run_time = if ($info.NextRunTime -and $info.NextRunTime -gt [datetime]::MinValue) { $info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }; "
                "last_task_result = [int64]$info.LastTaskResult"
                "} "
                "} | ConvertTo-Json -Depth 4"
            ),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                timeout=20,
            )
        except Exception:
            return []
        if result.returncode != 0 or not result.stdout.strip():
            return []
        try:
            payload = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return []
        items = payload if isinstance(payload, list) else [payload]
        result_items: list[ScheduledTaskSummary] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            task_name = str(item.get("task_name", ""))
            platform, account_slug, slot_index = cls._parse_task_name(task_name)
            try:
                last_task_result = int(item.get("last_task_result", 0))
            except (TypeError, ValueError):
                last_task_result = 0
            result_items.append(
                ScheduledTaskSummary(
                    task_name=task_name,
                    state=str(item.get("state", "")),
                    last_run_time=str(item.get("last_run_time", "")),
                    next_run_time=str(item.get("next_run_time", "")),
                    last_task_result=last_task_result,
                    platform=platform,
                    account_slug=account_slug,
                    slot_index=slot_index,
                )
            )
        cls._TASK_CATALOG_CACHE = list(result_items)
        cls._TASK_CATALOG_CACHE_AT = now
        return result_items

    @classmethod
    def _parse_task_name(cls, task_name: str) -> tuple[str, str, int]:
        for platform, prefix in cls.SCHEDULED_TASK_PREFIXES.items():
            marker = f"{prefix}-"
            if not task_name.startswith(marker):
                continue
            remainder = task_name[len(marker) :]
            match = re.match(r"^(?P<account>.+)-(?P<slot>\d+)$", remainder)
            if not match:
                return platform, remainder, 0
            return platform, match.group("account"), int(match.group("slot"))
        return "", "", 0

    def _filter_task_catalog(
        self,
        task_catalog: list[ScheduledTaskSummary] | None,
        *,
        platform: str,
    ) -> list[ScheduledTaskSummary]:
        items = task_catalog if task_catalog is not None else self.load_scheduled_task_catalog()
        filtered = [
            item
            for item in items
            if item.platform == platform and item.account_slug == self.settings.account_slug
        ]
        filtered.sort(key=lambda item: (item.slot_index or 999, item.task_name))
        return filtered

    def _summarize_task_group(self, tasks: list[ScheduledTaskSummary]) -> dict[str, Any]:
        if not tasks:
            return {
                "configured": False,
                "ready": False,
                "label": "未配置",
                "tone": "neutral",
                "state": "",
                "task_count": 0,
                "next_run_time": "",
                "last_run_time": "",
                "last_task_result": 0,
            }

        states = sorted({task.state for task in tasks if task.state})
        next_run_time = min((task.next_run_time for task in tasks if task.next_run_time), default="")
        last_run_time = max((task.last_run_time for task in tasks if task.last_run_time), default="")
        ready_states = {"Ready", "Queued", "Running"}
        has_state_issue = any(state not in ready_states for state in states)
        has_result_issue = any(
            task.last_task_result not in self.BENIGN_TASK_RESULTS for task in tasks
        )
        tone = "warn" if has_state_issue or has_result_issue else "success"
        label = "定时正常" if tone == "success" else "调度待关注"
        last_task_result = 0
        for task in tasks:
            if task.last_task_result not in self.BENIGN_TASK_RESULTS:
                last_task_result = task.last_task_result
                break
            if last_task_result == 0 and task.last_task_result != 0:
                last_task_result = task.last_task_result
        return {
            "configured": True,
            "ready": tone == "success",
            "label": label,
            "tone": tone,
            "state": " / ".join(states),
            "task_count": len(tasks),
            "next_run_time": next_run_time,
            "last_run_time": last_run_time,
            "last_task_result": last_task_result,
        }

    def _build_toutiao_platform_snapshot(
        self,
        *,
        latest_run: RunSummary | None,
        scheduled_status: dict[str, Any],
        latest_publish: PublishStatusSummary,
        publish_preflight: dict[str, Any],
        latest_log: LogSummary | None,
        tasks: list[ScheduledTaskSummary],
        workflow_scores: dict[str, Any],
        benchmark_merge: dict[str, Any],
    ) -> dict[str, Any]:
        schedule_meta = self._summarize_task_group(tasks)
        scheduled_status_value = str(scheduled_status.get("overall_status") or scheduled_status.get("status") or "").strip()
        scheduled_workflow_status = str(scheduled_status.get("workflow_status", "")).strip().lower()
        scheduled_publish_status = str(scheduled_status.get("publish_status", "")).strip()
        blocking_flags = publish_preflight.get("blocking_flags", []) if isinstance(publish_preflight, dict) else []
        warnings = publish_preflight.get("warnings", []) if isinstance(publish_preflight, dict) else []
        scheduled_updated_at = str(scheduled_status.get("updated_at", "")).strip()
        preflight_updated_at = str(self.load_platform_health().get("generated_at", "")).strip()
        scheduled_status_is_stale = self._is_stale_blocked_status(
            scheduled_status_value=scheduled_status_value,
            scheduled_updated_at=scheduled_updated_at,
            preflight_updated_at=preflight_updated_at,
            has_preflight_blockers=bool(blocking_flags),
        )
        if (
            scheduled_status_value.lower() == "failed"
            and scheduled_workflow_status in {"platform_publish_failed", "platform_partial_success"}
            and scheduled_publish_status
        ):
            status_value = scheduled_publish_status
        elif scheduled_status_is_stale:
            status_value = (
                str(latest_publish.status).strip()
                or (latest_run.review_status if latest_run is not None else "")
                or "ready"
            )
        else:
            status_value = (
                scheduled_status_value
                or str(latest_publish.status).strip()
                or (latest_run.review_status if latest_run is not None else "")
                or "idle"
            )
        status_meta = self._describe_status(status_value)
        creator_name = (
            ((publish_preflight.get("diagnostics", {}) or {}).get("summary", {}) or {}).get("creator_name", "")
            if isinstance(publish_preflight, dict)
            else ""
        )
        preflight_state = "blocked" if blocking_flags else ("ready" if publish_preflight.get("current_url") else "unknown")
        preflight_meta = self._describe_status(preflight_state)
        latest_title = (
            latest_publish.title
            or str(scheduled_status.get("selected_title", "")).strip()
            or (latest_run.publish_title if latest_run is not None else "")
            or (latest_run.selected_topic_title if latest_run is not None else "")
        )
        latest_run_name = (
            str(scheduled_status.get("run_name", "")).strip()
            or latest_publish.run_name
            or (latest_run.name if latest_run is not None else "")
        )
        latest_log_url = self.resolve_log_url(str(scheduled_status.get("log_file", ""))) or (latest_log.url if latest_log else "")
        latest_log_name = Path(str(scheduled_status.get("log_file", ""))).name if scheduled_status.get("log_file") else (latest_log.name if latest_log else "")
        benchmark_context = self._build_platform_benchmark_context(benchmark_merge, platform="toutiao")
        return {
            "key": "toutiao",
            "label": "今日头条",
            "enabled": True,
            "schedule": schedule_meta,
            "latest_status": status_value,
            "latest_status_label": status_meta["label"],
            "latest_status_tone": status_meta["tone"],
            "latest_title": latest_title,
            "latest_run_name": latest_run_name,
            "latest_updated_at": scheduled_updated_at
            or latest_publish.submitted_at
            or (latest_run.created_at if latest_run is not None else ""),
            "review_status": str(scheduled_status.get("review_status", "")).strip()
            or (latest_run.review_status if latest_run is not None else ""),
            "auto_publish_enabled": bool(scheduled_status.get("publish_enabled", False))
            or bool(scheduled_status.get("auto_publish", False)),
            "latest_detail_url": f"/runs/{latest_run_name}?account={self.account_key}" if latest_run_name else "",
            "history_url": f"/published?account={self.account_key}",
            "external_url": latest_publish.external_url or str(scheduled_status.get("publish_url", "")).strip(),
            "publish_message": latest_publish.message or str(scheduled_status.get("publish_message", "")).strip(),
            "publish_pgc_id": latest_publish.pgc_id or str(scheduled_status.get("publish_pgc_id", "")).strip(),
            "preflight_label": preflight_meta["label"],
            "preflight_tone": preflight_meta["tone"],
            "preflight_message": str(blocking_flags[0]) if blocking_flags else (creator_name or (warnings[0] if warnings else "未刷新头条发布预检")),
            "preflight_warning_count": len(warnings),
            "latest_log_url": latest_log_url,
            "latest_log_name": latest_log_name,
            "benchmark_context": benchmark_context,
            "score_snapshot": {
                "overall_score": int(workflow_scores.get("overall_score", 0) or 0),
                "validation_score": int(workflow_scores.get("validation_score", 0) or 0),
                "publish_readiness_score": int(workflow_scores.get("publish_readiness_score", 0) or 0),
                "ai_generated_risk": int(workflow_scores.get("ai_generated_risk", 0) or 0),
            },
        }

    def _is_stale_blocked_status(
        self,
        *,
        scheduled_status_value: str,
        scheduled_updated_at: str,
        preflight_updated_at: str,
        has_preflight_blockers: bool,
    ) -> bool:
        normalized = scheduled_status_value.strip().lower()
        if normalized not in {"blocked", "failed"}:
            return False
        if has_preflight_blockers:
            return False
        scheduled_ts = self._safe_iso_timestamp(scheduled_updated_at)
        preflight_ts = self._safe_iso_timestamp(preflight_updated_at)
        if scheduled_ts <= 0 or preflight_ts <= 0:
            return False
        return preflight_ts >= scheduled_ts

    def _safe_iso_timestamp(self, value: str) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return 0.0

    def _build_wechat_platform_snapshot(
        self,
        *,
        latest_wechat: WechatPublishStatusSummary,
        wechat_preflight: dict[str, Any],
        latest_log: LogSummary | None,
        tasks: list[ScheduledTaskSummary],
        benchmark_merge: dict[str, Any],
    ) -> dict[str, Any]:
        schedule_meta = self._summarize_task_group(tasks)
        health_payload = self.load_platform_health().get("platforms", {}).get("wechat", {})
        diagnostics = health_payload.get("diagnostics", {}) if isinstance(health_payload, dict) and isinstance(health_payload.get("diagnostics"), dict) else {}
        blocked_reason = str(diagnostics.get("blocked_reason", "")).strip().upper()
        invalid_ip = str(diagnostics.get("invalid_ip", "")).strip()

        status_value = "disabled" if not self.settings.wechat.enabled else (latest_wechat.status or "idle")
        if blocked_reason == "BLOCKED_BY_IP":
            status_value = "blocked_by_ip"
        status_meta = self._describe_status(status_value)

        preflight_state = "disabled"
        if self.settings.wechat.enabled:
            preflight_state = str(wechat_preflight.get("state", "")).strip()
            if not preflight_state:
                preflight_state = "not_initialized"
        if blocked_reason == "BLOCKED_BY_IP":
            preflight_state = "blocked_by_ip"
        preflight_meta = self._describe_status(preflight_state)

        latest_log_url = latest_log.url if latest_log is not None else ""
        latest_log_name = latest_log.name if latest_log is not None else ""
        benchmark_context = self._build_platform_benchmark_context(benchmark_merge, platform="wechat")
        preflight_message = (
            f"IP 白名单未放行，当前本机出口 IP: {invalid_ip or 'unknown'}。"
            if blocked_reason == "BLOCKED_BY_IP"
            else str(wechat_preflight.get("current_url", "")).strip()
            or ("公众号未启用" if not self.settings.wechat.enabled else "暂未初始化公众号浏览器登录态。")
        )
        return {
            "key": "wechat",
            "label": "公众号",
            "enabled": self.settings.wechat.enabled,
            "schedule": schedule_meta,
            "latest_status": status_value,
            "latest_status_label": status_meta["label"],
            "latest_status_tone": status_meta["tone"],
            "latest_title": latest_wechat.title,
            "latest_run_name": latest_wechat.run_name,
            "latest_updated_at": latest_wechat.submitted_at,
            "auto_publish_enabled": bool(self.settings.wechat.auto_publish or latest_wechat.submit_requested),
            "latest_detail_url": f"/runs/{latest_wechat.run_name}?account={self.account_key}" if latest_wechat.run_name else "",
            "history_url": "",
            "external_url": latest_wechat.external_url,
            "draft_media_id": latest_wechat.draft_media_id,
            "submit_status": latest_wechat.submit_status,
            "inline_image_count": latest_wechat.inline_image_count,
            "layout_backend": latest_wechat.layout_backend,
            "preflight_label": preflight_meta["label"],
            "preflight_tone": preflight_meta["tone"],
            "preflight_message": preflight_message,
            "latest_log_url": latest_log_url,
            "latest_log_name": latest_log_name,
            "benchmark_context": benchmark_context,
        }

    def _build_platform_benchmark_context(
        self,
        benchmark_merge: dict[str, Any],
        *,
        platform: str,
    ) -> dict[str, Any]:
        if not benchmark_merge or str(benchmark_merge.get("platform", "")).strip().lower() != platform:
            return {
                "available": False,
                "platform": platform,
                "track": "",
                "generated_at": "",
                "account_names": [],
                "headline_titles": [],
                "benchmark_topic_count": 0,
                "merged_topic_count": 0,
                "artifact_url": "",
            }

        account_names: list[str] = []
        for item in benchmark_merge.get("benchmark_accounts", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if name and name not in account_names:
                account_names.append(name)

        headline_titles: list[dict[str, str]] = []
        for item in benchmark_merge.get("top_titles", [])[:5]:
            if not isinstance(item, dict):
                continue
            headline_titles.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "source": str(item.get("source", "")).strip(),
                    "track": str(item.get("track", "")).strip(),
                }
            )

        return {
            "available": True,
            "platform": platform,
            "track": str(benchmark_merge.get("track", "")).strip(),
            "generated_at": str(benchmark_merge.get("generated_at", "")).strip(),
            "account_names": account_names[:6],
            "headline_titles": headline_titles,
            "benchmark_topic_count": int(benchmark_merge.get("benchmark_topic_count", 0) or 0),
            "merged_topic_count": int(benchmark_merge.get("merged_topic_count", 0) or 0),
            "artifact_url": str(benchmark_merge.get("artifact_url", "")).strip(),
        }

    def _build_account_attention_items(
        self,
        *,
        latest_run: RunSummary | None,
        notifications: list[dict[str, Any]],
        toutiao_platform: dict[str, Any],
        wechat_platform: dict[str, Any],
    ) -> list[str]:
        items: list[str] = []
        latest_status = str(toutiao_platform.get("latest_status", "")).strip().lower()
        latest_review_status = str(toutiao_platform.get("review_status", "")).strip().lower()
        latest_scheduled_run_name = str(toutiao_platform.get("latest_run_name", "")).strip()
        latest_scheduled_run_ts = self._run_name_timestamp(latest_scheduled_run_name)
        latest_run_ts = self._run_name_timestamp(latest_run.name) if latest_run is not None else 0.0

        if not toutiao_platform["schedule"]["configured"]:
            items.append("头条定时任务未配置。")
        elif not toutiao_platform["schedule"]["ready"]:
            items.append("头条定时任务状态需要检查。")

        if latest_status in {"review_required", "pending"} or latest_review_status == "pending" or (
            latest_run is not None
            and latest_run.review_status == "pending"
            and (
                not latest_scheduled_run_name
                or latest_run.name == latest_scheduled_run_name
                or (latest_run_ts and latest_scheduled_run_ts and latest_run_ts > latest_scheduled_run_ts)
            )
        ):
            items.append("最新头条稿件仍在待审核队列。")

        if toutiao_platform["preflight_tone"] == "error":
            items.append("头条发布预检存在阻塞项。")

        if wechat_platform["enabled"] and not wechat_platform["schedule"]["configured"]:
            items.append("公众号已启用，但没有检测到 07:00 定时任务。")

        for notification in notifications[:3]:
            if not isinstance(notification, dict):
                continue
            level = str(notification.get("level", "")).strip()
            message = self._normalize_notification_message(notification)
            if level in {"warn", "error"} and message:
                items.append(message)

        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped[:4]

    def _derive_overall_meta(
        self,
        toutiao_platform: dict[str, Any],
        wechat_platform: dict[str, Any],
        attention_items: list[str],
    ) -> dict[str, str]:
        latest_status = str(toutiao_platform.get("latest_status", "")).strip().lower()
        toutiao_ok = all(
            str(value) != "error"
            for value in (
                toutiao_platform.get("latest_status_tone", ""),
                toutiao_platform.get("preflight_tone", ""),
                toutiao_platform.get("schedule", {}).get("tone", ""),
            )
        )
        wechat_enabled = bool(wechat_platform.get("enabled"))
        wechat_bad = any(
            str(value) == "error"
            for value in (
                wechat_platform.get("latest_status_tone", ""),
                wechat_platform.get("preflight_tone", ""),
                wechat_platform.get("schedule", {}).get("tone", ""),
            )
        )
        tones = {
            str(toutiao_platform.get("latest_status_tone", "")),
            str(toutiao_platform.get("preflight_tone", "")),
            str(toutiao_platform.get("schedule", {}).get("tone", "")),
            str(wechat_platform.get("latest_status_tone", "")),
            str(wechat_platform.get("preflight_tone", "")),
            str(wechat_platform.get("schedule", {}).get("tone", "")),
        }
        if toutiao_ok and wechat_enabled and wechat_bad:
            return {"label": "头条正常，公众号异常", "tone": "warn"}
        if "error" in tones:
            return {"label": "存在阻塞", "tone": "error"}
        if latest_status == "no_topics" and (not attention_items or all(self._is_soft_attention_item(item) for item in attention_items)):
            return {"label": "候选不足", "tone": "neutral"}
        if attention_items or "warn" in tones:
            return {"label": "需要关注", "tone": "warn"}
        if toutiao_platform.get("schedule", {}).get("configured"):
            return {"label": "运行正常", "tone": "success"}
        return {"label": "等待配置", "tone": "neutral"}

    def _normalize_notification_message(self, latest_notification: dict[str, Any]) -> str:
        raw_message = str(latest_notification.get("message", "")).strip()
        notification_type = str(latest_notification.get("type", "")).strip().lower()
        title = str(latest_notification.get("title", "")).strip().lower()
        payload = latest_notification.get("data", {}) if isinstance(latest_notification, dict) else {}
        status = str(payload.get("status", "")).strip().lower() if isinstance(payload, dict) else ""
        error_text = str(payload.get("error", "")).strip().lower() if isinstance(payload, dict) else ""
        workflow_errors = payload.get("workflow_errors", []) if isinstance(payload, dict) else []
        workflow_error_text = " ".join(str(item).strip().lower() for item in workflow_errors) if isinstance(workflow_errors, list) else ""
        raw_lower = raw_message.lower()
        if notification_type == "image_generation_fallback" or title == "image2 fallback triggered":
            requested_backend = str(payload.get("requested_backend", "")).strip() if isinstance(payload, dict) else ""
            affected_modes = payload.get("affected_modes", []) if isinstance(payload, dict) else []
            mode_text = ""
            if isinstance(affected_modes, list):
                normalized_modes = [str(item).strip() for item in affected_modes if str(item).strip()]
                if normalized_modes:
                    mode_text = f"（影响环节：{', '.join(normalized_modes)}）"
            backend_label = requested_backend or "image2"
            return f"{backend_label} 出图失败，已自动降级为本地 renderer{mode_text}。请检查图片接口、中转站配置或额度。"

        if (
            status == "no_topics"
            or "no eligible topics were available after collection" in raw_lower
            or "no unpublished topics are available for today" in error_text
        ):
            return "本轮已完成热点抓取，但当前赛道没有新的合格选题，系统已自动跳过发布。"
        if raw_message == "Scheduled pipeline succeeded, but this run still requires manual review.":
            return "最新生成稿件仍需人工审核后才能发布。"
        if raw_message == "Scheduled pipeline finished generation, but at least one platform publish step failed.":
            if "wechat publish failed" in error_text or "cover image not found" in error_text:
                return "头条已正常提交，公众号发布失败：缺少封面图或公众号素材图。"
            return "本轮内容已生成，但至少有一个平台发布失败，需要检查日志。"
        if raw_message.startswith("Scheduled pipeline failed after ") and (
            "platform_publish_failed" == status
            or "wechat publish failed" in error_text
            or "cover image not found" in error_text
            or "wechat publish failed" in workflow_error_text
            or "cover image not found" in workflow_error_text
        ):
            if (
                "wechat publish failed" in error_text
                or "cover image not found" in error_text
                or "wechat publish failed" in workflow_error_text
                or "cover image not found" in workflow_error_text
            ):
                return "头条已正常提交，公众号发布失败：缺少封面图或公众号素材图。"
            return "本轮内容已生成，但至少有一个平台发布失败。"
        return raw_message

    def _is_soft_attention_item(self, item: str) -> bool:
        normalized = str(item).strip()
        soft_items = {
            "本轮已完成热点抓取，但当前赛道没有新的合格选题，系统已自动跳过发布。",
        }
        return normalized in soft_items

    def resolve_log_url(self, log_file: str) -> str:
        if not log_file:
            return ""
        candidate = Path(log_file)
        if not candidate.is_absolute():
            candidate = (self.logs_dir / log_file).resolve()
        else:
            candidate = candidate.resolve()
        root = self.logs_dir.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return ""
        if not candidate.exists():
            return ""
        return self._account_log_artifact_url(relative.as_posix())

    def _describe_status(self, status: str) -> dict[str, str]:
        normalized = (status or "").strip().lower()
        success_states = {
            "success": "运行正常",
            "published": "已发布",
            "submitted": "已提交",
            "submitted_for_review": "已提交审核",
            "draft_created": "已进草稿箱",
            "approved": "已通过审核",
            "ready": "登录态正常",
        }
        warn_states = {
            "review_required": "待人工审核",
            "pending": "待处理",
            "manual_review": "待人工审核",
            "manual_intervention_required": "需人工干预",
            "running": "执行中",
            "queued": "排队中",
            "partial_success": "部分成功",
        }
        error_states = {
            "failed": "执行失败",
            "error": "执行失败",
            "blocked": "存在阻塞",
            "blocked_by_ip": "IP 白名单阻塞",
            "missing": "缺少结果",
            "aborted": "异常中止",
            "rejected": "审核驳回（需人工干预）",
        }
        neutral_states = {
            "unknown": "未知",
            "idle": "暂无结果",
            "disabled": "未启用",
            "not_published": "暂无记录",
            "not_initialized": "未初始化登录态",
            "no_topics": "暂无合格选题",
        }
        if normalized in success_states:
            return {"label": success_states[normalized], "tone": "success"}
        if normalized in warn_states:
            return {"label": warn_states[normalized], "tone": "warn"}
        if normalized in error_states:
            return {"label": error_states[normalized], "tone": "error"}
        if normalized in neutral_states:
            return {"label": neutral_states[normalized], "tone": "neutral"}
        return {"label": status or "未知", "tone": "neutral"}

    def _resolve_schedule_slot(self, created_at: str) -> str:
        try:
            hour = datetime.fromisoformat(created_at).hour
        except ValueError:
            return ""
        if 7 <= hour < 11:
            return "morning"
        if 11 <= hour < 15:
            return "noon"
        if 17 <= hour < 20:
            return "evening"
        if 21 <= hour < 23:
            return "night"
        return ""

    def _slot_to_expected_time(self, slot: str) -> str:
        mapping = {
            "morning": "08:00",
            "noon": "12:00",
            "evening": "18:30",
            "night": "22:00",
        }
        return mapping.get(slot, "--:--")

    def _latest_scheduled_logs_by_slot(self) -> dict[str, str]:
        if not self.scheduled_logs_dir.exists():
            return {}
        files = [item for item in self.scheduled_logs_dir.iterdir() if item.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        result: dict[str, str] = {}
        for path in files:
            slot = self._resolve_schedule_slot(self._format_timestamp(path.stat().st_mtime).replace(" ", "T"))
            if slot and slot not in result:
                result[slot] = self._account_log_artifact_url(f"scheduled/{path.name}")
        return result

    def _split_article_paragraphs(self, article_body: str) -> list[str]:
        if not article_body:
            return []
        normalized = article_body.replace("\r\n", "\n").replace("\r", "\n")
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n+", normalized) if chunk.strip()]
        paragraphs: list[str] = []
        for chunk in chunks:
            if chunk == "---":
                continue
            paragraphs.append(chunk)
        return paragraphs

    def _load_yaml(self, path: Path) -> dict[str, Any] | list[Any] | None:
        if not path.exists():
            return None
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if isinstance(payload, (dict, list)):
            return payload
        return None

    def _output_url_from_absolute_path(self, raw_path: str) -> str:
        text = str(raw_path or "").strip()
        if not text:
            return ""
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = (self.output_dir / text).resolve()
        else:
            candidate = candidate.resolve()
        try:
            relative = candidate.relative_to(self.output_dir.resolve())
        except ValueError:
            return ""
        if not candidate.exists():
            return ""
        return self._account_output_artifact_url(relative.as_posix())

    def _slugify_offer_name(self, offer_name: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(offer_name))
        return cleaned.strip("_").lower() or "offer"
