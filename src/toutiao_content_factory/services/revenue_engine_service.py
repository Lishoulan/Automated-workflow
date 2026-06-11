from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.lead_ops_service import LeadOpsService


class RevenueEngineService:
    """Build proactive prospecting, opportunity scanning, and outreach assets."""

    DEFAULT_CONFIG = {
        "version": 1,
        "updated_at": "2026-06-08",
        "target_customer_segments": [],
        "sponsor_archetypes": [],
        "outreach_sequences": {},
        "newsletter": {},
        "deer_flow_playbooks": [],
        "agent_roles": {},
    }
    CLOSED_LEAD_STATUSES = {"won", "lost"}
    ACTIVE_MEMBER_STATUSES = {
        "new_subscriber",
        "free_sample_sent",
        "upgrade_prompt_sent",
        "member_interested",
        "renewal_due",
    }

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.lead_ops_service = LeadOpsService(settings, logger)
        self.config_path = settings.project_root / "config" / "company_growth_engine.yaml"
        self.revenue_config_path = settings.project_root / "config" / "company_revenue.yaml"
        self.output_dir = settings.output_dir / "automation" / "revenue_engine"
        self.customer_targets_path = self.output_dir / "customer_targets.json"
        self.sponsor_targets_path = self.output_dir / "sponsor_targets.json"
        self.outreach_playbook_path = self.output_dir / "outreach_playbook.md"
        self.deer_flow_task_pack_path = self.output_dir / "deer_flow_task_pack.yaml"
        self.newsletter_offer_path = self.output_dir / "newsletter_offer.md"
        self.weekly_pipeline_path = self.output_dir / "weekly_pipeline.md"
        self.commercial_catalog_path = self.output_dir / "commercial_catalog.json"
        self.closing_playbook_path = self.output_dir / "closing_playbook.md"
        self.closing_message_pack_path = self.output_dir / "closing_message_pack.json"
        self.closing_followup_queue_path = self.output_dir / "closing_followup_queue.json"
        self.closing_action_log_path = self.output_dir / "closing_action_log.json"
        self.onboarding_queue_path = self.output_dir / "onboarding_queue.json"
        self.service_kickoff_pack_path = self.output_dir / "service_kickoff_pack.json"
        self.sponsor_onboarding_pack_path = self.output_dir / "sponsor_onboarding_pack.json"
        self.letter_welcome_pack_path = self.output_dir / "letter_welcome_pack.json"
        self.service_kickoff_brief_path = self.output_dir / "service_kickoff_brief.md"
        self.sponsor_onboarding_guide_path = self.output_dir / "sponsor_onboarding_guide.md"
        self.letter_member_welcome_path = self.output_dir / "letter_member_welcome.md"
        self.prompt_optimization_pack_path = self.output_dir / "prompt_optimization_pack.json"
        self.cloudflow_blueprint_path = self.output_dir / "cloudflow_blueprint.yaml"
        self.letter_campaign_path = self.output_dir / "letter_campaign.md"
        self.agennts_dispatch_path = self.output_dir / "agennts_dispatch_queue.json"
        self.closing_dispatch_path = self.output_dir / "closing_dispatch_queue.json"
        self.founder_profit_brief_path = self.output_dir / "founder_profit_brief.md"
        self.profit_loop_report_path = self.output_dir / "profit_loop_report.json"
        self.profit_action_pack_path = self.output_dir / "profit_action_pack.json"
        self.sponsor_outreach_log_path = self.output_dir / "sponsor_outreach_log.json"
        self.sponsor_pipeline_path = self.output_dir / "sponsor_pipeline.json"
        self.sponsor_revenue_report_path = self.output_dir / "sponsor_revenue_report.json"
        self.newsletter_subscriber_ledger_path = self.output_dir / "newsletter_subscriber_ledger.json"
        self.membership_pipeline_path = self.output_dir / "membership_pipeline.json"
        self.letter_revenue_report_path = self.output_dir / "letter_revenue_report.json"
        self.profit_action_execution_report_path = self.output_dir / "profit_action_execution_report.json"
        self.opportunity_queue_path = self.output_dir / "opportunity_queue.json"
        self.opportunity_queue_md_path = self.output_dir / "opportunity_queue.md"
        self.revenue_signal_report_path = self.output_dir / "revenue_signal_report.json"
        self.opportunity_sync_report_path = self.output_dir / "opportunity_sync_report.json"
        self.auto_draft_report_path = self.output_dir / "auto_draft_proposal_report.json"

    def build_assets(
        self,
        *,
        integrations: list[dict[str, Any]] | None = None,
        benchmark_report: dict[str, Any] | None = None,
        self_benchmark_report: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = self._load_growth_payload()
        revenue_payload = self._load_revenue_payload()
        offers = self._build_offer_index(revenue_payload)
        daily_topics = self._load_json_if_exists(self.settings.output_dir / "daily_topics.json") or {}
        lead_ledger = self._load_json_if_exists(self.settings.output_dir / "automation" / "lead_ops" / "lead_ledger.json")
        leads = lead_ledger if isinstance(lead_ledger, list) else []

        customer_targets = self._build_customer_targets(payload, offers, benchmark_report, self_benchmark_report)
        sponsor_targets = self._build_sponsor_targets(payload, benchmark_report, self_benchmark_report)
        signal_report = self._build_revenue_signal_report(daily_topics, benchmark_report, self_benchmark_report)
        customer_sync_candidates = self._build_customer_pitch_candidates(daily_topics, customer_targets)
        opportunity_queue = self._build_opportunity_queue(
            daily_topics=daily_topics,
            leads=leads,
            customer_targets=customer_targets,
            sponsor_targets=sponsor_targets,
            signal_report=signal_report,
        )
        sync_candidates = customer_sync_candidates
        sync_result = self.lead_ops_service.sync_generated_opportunities(sync_candidates)
        opportunity_sync_report = self._build_opportunity_sync_report(
            opportunity_queue=opportunity_queue,
            sync_candidates=sync_candidates,
            sync_result=sync_result,
        )
        outreach_playbook = self._build_outreach_playbook(payload, customer_targets, sponsor_targets, offers)
        commercial_catalog = self._build_commercial_catalog(
            revenue_payload=revenue_payload,
            customer_targets=customer_targets,
            sponsor_targets=sponsor_targets,
        )
        closing_playbook = self._build_closing_playbook(
            payload=payload,
            customer_targets=customer_targets,
            sponsor_targets=sponsor_targets,
            commercial_catalog=commercial_catalog,
        )
        deer_flow_task_pack = self._build_deer_flow_task_pack(
            payload,
            customer_targets,
            sponsor_targets,
            opportunity_queue,
            integrations or [],
        )
        newsletter_offer = self._build_newsletter_offer(
            payload,
            customer_targets,
            sponsor_targets,
            signal_report,
            commercial_catalog,
        )
        weekly_pipeline = self._build_weekly_pipeline(payload, integrations or [], opportunity_queue)
        prompt_optimization_pack = self._build_prompt_optimization_pack(
            payload,
            signal_report,
            customer_targets,
            sponsor_targets,
        )
        cloudflow_blueprint = self._build_cloudflow_blueprint(
            payload,
            integrations or [],
            opportunity_queue,
        )
        letter_campaign = self._build_letter_campaign(
            payload,
            signal_report,
            customer_targets,
            sponsor_targets,
        )
        opportunity_queue_md = self._build_opportunity_queue_markdown(opportunity_queue)

        self.customer_targets_path.write_text(
            json.dumps(customer_targets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.sponsor_targets_path.write_text(
            json.dumps(sponsor_targets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.revenue_signal_report_path.write_text(
            json.dumps(signal_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.opportunity_sync_report_path.write_text(
            json.dumps(opportunity_sync_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.opportunity_queue_path.write_text(
            json.dumps(opportunity_queue, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.opportunity_queue_md_path.write_text(opportunity_queue_md, encoding="utf-8")
        self.outreach_playbook_path.write_text(outreach_playbook, encoding="utf-8")
        self.commercial_catalog_path.write_text(
            json.dumps(commercial_catalog, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.closing_playbook_path.write_text(closing_playbook, encoding="utf-8")
        self.deer_flow_task_pack_path.write_text(
            yaml.safe_dump(deer_flow_task_pack, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.newsletter_offer_path.write_text(newsletter_offer, encoding="utf-8")
        self.weekly_pipeline_path.write_text(weekly_pipeline, encoding="utf-8")
        self.prompt_optimization_pack_path.write_text(
            json.dumps(prompt_optimization_pack, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.cloudflow_blueprint_path.write_text(
            yaml.safe_dump(cloudflow_blueprint, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.letter_campaign_path.write_text(letter_campaign, encoding="utf-8")

        return {
            "customer_targets": self.customer_targets_path,
            "sponsor_targets": self.sponsor_targets_path,
            "revenue_signal_report": self.revenue_signal_report_path,
            "opportunity_sync_report": self.opportunity_sync_report_path,
            "opportunity_queue": self.opportunity_queue_path,
            "opportunity_queue_markdown": self.opportunity_queue_md_path,
            "outreach_playbook": self.outreach_playbook_path,
            "commercial_catalog": self.commercial_catalog_path,
            "closing_playbook": self.closing_playbook_path,
            "deer_flow_task_pack": self.deer_flow_task_pack_path,
            "newsletter_offer": self.newsletter_offer_path,
            "weekly_pipeline": self.weekly_pipeline_path,
            "prompt_optimization_pack": self.prompt_optimization_pack_path,
            "cloudflow_blueprint": self.cloudflow_blueprint_path,
            "letter_campaign": self.letter_campaign_path,
            "growth_engine_config": self.config_path,
        }

    def run_profit_loop(
        self,
        *,
        integrations: list[dict[str, Any]] | None = None,
        benchmark_report: dict[str, Any] | None = None,
        self_benchmark_report: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        assets = self.build_assets(
            integrations=integrations,
            benchmark_report=benchmark_report,
            self_benchmark_report=self_benchmark_report,
        )
        growth_payload = self._load_growth_payload()
        lead_summary = self._load_json_if_exists(self.settings.output_dir / "automation" / "lead_ops" / "lead_summary.json")
        opportunity_queue = self._load_json_if_exists(self.opportunity_queue_path) or {}
        sales_dashboard = self._load_json_if_exists(self.settings.output_dir / "automation" / "lead_ops" / "sales_execution_dashboard.json") or {}
        revenue_signal_report = self._load_json_if_exists(self.revenue_signal_report_path) or {}
        subscriber_ledger = self._ensure_newsletter_subscriber_ledger(
            leads=self.lead_ops_service.list_leads(limit=500),
            signal_report=revenue_signal_report if isinstance(revenue_signal_report, dict) else {},
        )
        commercial_catalog = self._load_json_if_exists(self.commercial_catalog_path) or {}
        closing_playbook = self.closing_playbook_path.read_text(encoding="utf-8") if self.closing_playbook_path.exists() else ""
        sponsor_pipeline = self._build_sponsor_pipeline()
        sponsor_log = self._load_sponsor_outreach_log()
        closing_dispatch = self._build_closing_dispatch_queue(
            opportunity_queue if isinstance(opportunity_queue, dict) else {},
            sales_dashboard if isinstance(sales_dashboard, dict) else {},
            subscriber_ledger,
            sponsor_pipeline if isinstance(sponsor_pipeline, dict) else {},
            sponsor_log,
            commercial_catalog if isinstance(commercial_catalog, dict) else {},
            closing_playbook,
        )
        closing_message_pack = self._build_closing_message_pack(closing_dispatch)
        closing_action_log = self._load_closing_action_log()
        closing_followup_queue = self._build_closing_followup_queue(closing_dispatch, closing_action_log)
        onboarding_queue = self._build_onboarding_queue(subscriber_ledger, sponsor_log)
        service_kickoff_pack = self._build_service_kickoff_pack()
        sponsor_onboarding_pack = self._build_sponsor_onboarding_pack(sponsor_log)
        letter_welcome_pack = self._build_letter_welcome_pack(subscriber_ledger)
        service_kickoff_brief = self._build_service_kickoff_brief(service_kickoff_pack)
        sponsor_onboarding_guide = self._build_sponsor_onboarding_guide(sponsor_onboarding_pack)
        letter_member_welcome = self._build_letter_member_welcome(letter_welcome_pack)

        agent_dispatch = self._build_agennts_dispatch_queue(
            growth_payload,
            opportunity_queue,
            sales_dashboard,
            subscriber_ledger,
            closing_dispatch,
        )
        founder_profit_brief = self._build_founder_profit_brief(
            lead_summary if isinstance(lead_summary, dict) else {},
            opportunity_queue if isinstance(opportunity_queue, dict) else {},
            sales_dashboard if isinstance(sales_dashboard, dict) else {},
            revenue_signal_report if isinstance(revenue_signal_report, dict) else {},
            subscriber_ledger,
        )
        profit_action_pack = self._build_profit_action_pack(
            opportunity_queue if isinstance(opportunity_queue, dict) else {},
            sales_dashboard if isinstance(sales_dashboard, dict) else {},
            subscriber_ledger,
        )
        sponsor_revenue_report = self._build_sponsor_revenue_report()
        membership_pipeline = self._build_membership_pipeline(subscriber_ledger)
        letter_revenue_report = self._build_letter_revenue_report(subscriber_ledger)
        profit_loop_report = self._build_profit_loop_report(
            agent_dispatch,
            lead_summary if isinstance(lead_summary, dict) else {},
            opportunity_queue if isinstance(opportunity_queue, dict) else {},
            sales_dashboard if isinstance(sales_dashboard, dict) else {},
            closing_dispatch,
            sponsor_revenue_report,
            letter_revenue_report,
        )

        self.agennts_dispatch_path.write_text(
            json.dumps(agent_dispatch, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.closing_dispatch_path.write_text(
            json.dumps(closing_dispatch, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.closing_message_pack_path.write_text(
            json.dumps(closing_message_pack, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.closing_followup_queue_path.write_text(
            json.dumps(closing_followup_queue, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.closing_action_log_path.write_text(
            json.dumps(closing_action_log[:300], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.onboarding_queue_path.write_text(
            json.dumps(onboarding_queue, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.service_kickoff_pack_path.write_text(
            json.dumps(service_kickoff_pack, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.sponsor_onboarding_pack_path.write_text(
            json.dumps(sponsor_onboarding_pack, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.letter_welcome_pack_path.write_text(
            json.dumps(letter_welcome_pack, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.service_kickoff_brief_path.write_text(service_kickoff_brief, encoding="utf-8")
        self.sponsor_onboarding_guide_path.write_text(sponsor_onboarding_guide, encoding="utf-8")
        self.letter_member_welcome_path.write_text(letter_member_welcome, encoding="utf-8")
        self.founder_profit_brief_path.write_text(founder_profit_brief, encoding="utf-8")
        self.profit_action_pack_path.write_text(
            json.dumps(profit_action_pack, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.sponsor_pipeline_path.write_text(
            json.dumps(sponsor_pipeline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.sponsor_revenue_report_path.write_text(
            json.dumps(sponsor_revenue_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.membership_pipeline_path.write_text(
            json.dumps(membership_pipeline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.letter_revenue_report_path.write_text(
            json.dumps(letter_revenue_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.profit_loop_report_path.write_text(
            json.dumps(profit_loop_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        assets.update(
            {
                "agennts_dispatch_queue": self.agennts_dispatch_path,
                "closing_dispatch_queue": self.closing_dispatch_path,
                "closing_message_pack": self.closing_message_pack_path,
                "closing_followup_queue": self.closing_followup_queue_path,
                "closing_action_log": self.closing_action_log_path,
                "onboarding_queue": self.onboarding_queue_path,
                "service_kickoff_pack": self.service_kickoff_pack_path,
                "sponsor_onboarding_pack": self.sponsor_onboarding_pack_path,
                "letter_welcome_pack": self.letter_welcome_pack_path,
                "service_kickoff_brief": self.service_kickoff_brief_path,
                "sponsor_onboarding_guide": self.sponsor_onboarding_guide_path,
                "letter_member_welcome": self.letter_member_welcome_path,
                "founder_profit_brief": self.founder_profit_brief_path,
                "profit_action_pack": self.profit_action_pack_path,
                "sponsor_pipeline": self.sponsor_pipeline_path,
                "sponsor_revenue_report": self.sponsor_revenue_report_path,
                "newsletter_subscriber_ledger": self.newsletter_subscriber_ledger_path,
                "membership_pipeline": self.membership_pipeline_path,
                "letter_revenue_report": self.letter_revenue_report_path,
                "profit_loop_report": self.profit_loop_report_path,
            }
        )
        return assets

    def execute_profit_actions(
        self,
        *,
        action_types: tuple[str, ...] = (),
        limit: int = 0,
        integrations: list[dict[str, Any]] | None = None,
        benchmark_report: dict[str, Any] | None = None,
        self_benchmark_report: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        assets = self.run_profit_loop(
            integrations=integrations,
            benchmark_report=benchmark_report,
            self_benchmark_report=self_benchmark_report,
        )
        action_pack = self._load_json_if_exists(self.profit_action_pack_path)
        items = action_pack.get("items", []) if isinstance(action_pack, dict) else []
        selected_types = {str(item).strip() for item in action_types if str(item).strip()}
        sponsor_log = self._load_sponsor_outreach_log()
        subscriber_ledger = self._load_newsletter_subscriber_ledger()
        closing_action_log = self._load_closing_action_log()

        processed = 0
        executed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        prepared: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("action_type", "")).strip()
            if selected_types and action_type not in selected_types:
                continue
            if limit > 0 and processed >= limit:
                break
            processed += 1
            try:
                result = self._execute_profit_action(item, sponsor_log, subscriber_ledger, closing_action_log)
            except Exception as exc:
                failed.append(
                    {
                        "action_type": action_type,
                        "target_name": str(item.get("target_name", "")).strip(),
                        "error": str(exc),
                    }
                )
                continue

            status = str(result.get("status", "")).strip()
            if status == "executed":
                executed.append(result)
            elif status == "prepared":
                prepared.append(result)
            else:
                skipped.append(result)

        self.sponsor_outreach_log_path.write_text(
            json.dumps(sponsor_log[:200], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.newsletter_subscriber_ledger_path.write_text(
            json.dumps(subscriber_ledger[:500], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.closing_action_log_path.write_text(
            json.dumps(closing_action_log[:300], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        execution_report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "selected_action_types": sorted(selected_types),
            "processed": processed,
            "executed_count": len(executed),
            "prepared_count": len(prepared),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "executed": executed,
            "prepared": prepared,
            "skipped": skipped,
            "failed": failed,
        }
        self.profit_action_execution_report_path.write_text(
            json.dumps(execution_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        refreshed_assets = self.run_profit_loop(
            integrations=integrations,
            benchmark_report=benchmark_report,
            self_benchmark_report=self_benchmark_report,
        )
        refreshed_assets.update(
            {
                "sponsor_outreach_log": self.sponsor_outreach_log_path,
                "sponsor_pipeline": self.sponsor_pipeline_path,
                "newsletter_subscriber_ledger": self.newsletter_subscriber_ledger_path,
                "membership_pipeline": self.membership_pipeline_path,
                "letter_revenue_report": self.letter_revenue_report_path,
                "closing_message_pack": self.closing_message_pack_path,
                "closing_followup_queue": self.closing_followup_queue_path,
                "closing_action_log": self.closing_action_log_path,
                "onboarding_queue": self.onboarding_queue_path,
                "profit_action_execution_report": self.profit_action_execution_report_path,
            }
        )
        return refreshed_assets

    def update_sponsor_outreach_status(
        self,
        *,
        action_key: str,
        status: str,
        notes: str = "",
        next_follow_up_at: str = "",
        deal_value: float | None = None,
    ) -> dict[str, Path]:
        sponsor_log = self._load_sponsor_outreach_log()
        normalized_key = str(action_key).strip()
        normalized_status = str(status).strip() or "sent"
        now = datetime.now().isoformat(timespec="seconds")
        target: dict[str, Any] | None = None
        for item in sponsor_log:
            if str(item.get("action_key", "")).strip() != normalized_key:
                continue
            target = item
            break
        if target is None:
            raise FileNotFoundError(f"Sponsor outreach action not found: {normalized_key}")

        target["status"] = normalized_status
        target["updated_at"] = now
        if notes.strip():
            target["notes"] = notes.strip()
        if next_follow_up_at.strip():
            target["next_follow_up_at"] = next_follow_up_at.strip()
        elif normalized_status in {"sent", "interested"} and not str(target.get("next_follow_up_at", "")).strip():
            target["next_follow_up_at"] = self._default_sponsor_follow_up_slot()
        if normalized_status == "sent" and not str(target.get("sent_at", "")).strip():
            target["sent_at"] = now
        if normalized_status == "interested" and not str(target.get("interested_at", "")).strip():
            target["interested_at"] = now
        if normalized_status == "call_booked" and not str(target.get("call_booked_at", "")).strip():
            target["call_booked_at"] = now
        if normalized_status == "won" and not str(target.get("won_at", "")).strip():
            target["won_at"] = now
        if normalized_status == "won":
            if deal_value is not None:
                target["deal_value"] = float(deal_value)
            elif float(target.get("deal_value", 0.0) or 0.0) <= 0.0:
                target["deal_value"] = self._infer_sponsor_deal_value(target)
        if normalized_status == "lost" and not str(target.get("lost_at", "")).strip():
            target["lost_at"] = now

        self.sponsor_outreach_log_path.write_text(
            json.dumps(sponsor_log[:200], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        refreshed_assets = self.run_profit_loop()
        refreshed_assets.update(
            {
                "sponsor_outreach_log": self.sponsor_outreach_log_path,
                "sponsor_pipeline": self.sponsor_pipeline_path,
            }
        )
        return refreshed_assets

    def update_newsletter_subscriber_status(
        self,
        *,
        subscriber_key: str,
        status: str,
        notes: str = "",
        next_follow_up_at: str = "",
        member_revenue: float | None = None,
    ) -> dict[str, Path]:
        ledger = self._load_newsletter_subscriber_ledger()
        normalized_key = str(subscriber_key).strip()
        normalized_status = str(status).strip() or "new_subscriber"
        now = datetime.now().isoformat(timespec="seconds")
        target: dict[str, Any] | None = None
        for item in ledger:
            if str(item.get("subscriber_key", "")).strip() != normalized_key:
                continue
            target = item
            break
        if target is None:
            raise FileNotFoundError(f"Newsletter subscriber not found: {normalized_key}")

        target["status"] = normalized_status
        target["updated_at"] = now
        if notes.strip():
            target["notes"] = notes.strip()
        if next_follow_up_at.strip():
            target["next_follow_up_at"] = next_follow_up_at.strip()
        elif normalized_status in self.ACTIVE_MEMBER_STATUSES and not str(target.get("next_follow_up_at", "")).strip():
            target["next_follow_up_at"] = self._default_letter_follow_up_slot(days=3)

        if normalized_status == "free_sample_sent" and not str(target.get("free_sample_sent_at", "")).strip():
            target["free_sample_sent_at"] = now
        if normalized_status == "upgrade_prompt_sent" and not str(target.get("upgrade_prompt_sent_at", "")).strip():
            target["upgrade_prompt_sent_at"] = now
        if normalized_status == "member_interested" and not str(target.get("member_interested_at", "")).strip():
            target["member_interested_at"] = now
        if normalized_status == "member_won":
            if not str(target.get("member_won_at", "")).strip():
                target["member_won_at"] = now
            if member_revenue is not None:
                target["member_revenue"] = float(member_revenue)
            elif float(target.get("member_revenue", 0.0) or 0.0) <= 0.0:
                target["member_revenue"] = self._default_member_revenue(target)
            target["renewal_due_at"] = self._default_letter_follow_up_slot(days=30)
        if normalized_status == "renewal_due" and not str(target.get("renewal_due_at", "")).strip():
            target["renewal_due_at"] = self._default_letter_follow_up_slot(days=30)
        if normalized_status == "renewed":
            if not str(target.get("renewed_at", "")).strip():
                target["renewed_at"] = now
            if member_revenue is not None:
                target["renewal_revenue"] = float(member_revenue)
            elif float(target.get("renewal_revenue", 0.0) or 0.0) <= 0.0:
                target["renewal_revenue"] = self._default_member_revenue(target)
            target["next_follow_up_at"] = self._default_letter_follow_up_slot(days=30)

        self.newsletter_subscriber_ledger_path.write_text(
            json.dumps(ledger[:500], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        refreshed_assets = self.run_profit_loop()
        refreshed_assets.update(
            {
                "newsletter_subscriber_ledger": self.newsletter_subscriber_ledger_path,
                "membership_pipeline": self.membership_pipeline_path,
                "letter_revenue_report": self.letter_revenue_report_path,
            }
        )
        return refreshed_assets

    def _build_customer_targets(
        self,
        payload: dict[str, Any],
        offers: dict[str, dict[str, str]],
        benchmark_report: dict[str, Any] | None,
        self_benchmark_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        top_track = self._pick_top_track(benchmark_report, self_benchmark_report)
        top_patterns = self._pick_top_patterns(self_benchmark_report)
        segments = payload.get("target_customer_segments", []) if isinstance(payload, dict) else []
        result_segments: list[dict[str, Any]] = []
        for item in segments:
            if not isinstance(item, dict):
                continue
            offer_key = str(item.get("recommended_offer_key", "")).strip()
            offer = offers.get(offer_key, {})
            result_segments.append(
                {
                    "segment_key": str(item.get("key", "")).strip(),
                    "segment_name": str(item.get("name", "")).strip(),
                    "recommended_offer_key": offer_key,
                    "recommended_offer_name": offer.get("name", offer_key),
                    "recommended_offer_price": offer.get("price", ""),
                    "recommended_offer_delivery": offer.get("delivery", ""),
                    "outreach_channel": str(item.get("outreach_channel", "")).strip(),
                    "why_now": [
                        f"当前账号最强赛道是「{top_track}」，适合把内容势能转为咨询。",
                        f"近期有效内容模式包括：{', '.join(top_patterns) if top_patterns else '热点解读、数据锚点、观点判断'}。",
                    ],
                    "pains": [str(line).strip() for line in item.get("pains", []) if str(line).strip()],
                    "qualifying_signal": [
                        str(line).strip() for line in item.get("qualifying_signal", []) if str(line).strip()
                    ],
                    "ideal_opening": self._build_customer_opening(
                        segment_name=str(item.get("name", "")).strip(),
                        offer_name=offer.get("name", offer_key),
                        top_track=top_track,
                    ),
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "brand_name": self.settings.brand_name,
            "best_current_track": top_track,
            "segments": result_segments,
        }

    def _build_sponsor_targets(
        self,
        payload: dict[str, Any],
        benchmark_report: dict[str, Any] | None,
        self_benchmark_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        top_track = self._pick_top_track(benchmark_report, self_benchmark_report)
        sponsor_archetypes = payload.get("sponsor_archetypes", []) if isinstance(payload, dict) else []
        top_keywords = self._pick_top_keywords(benchmark_report)
        result_archetypes: list[dict[str, Any]] = []
        for item in sponsor_archetypes:
            if not isinstance(item, dict):
                continue
            result_archetypes.append(
                {
                    "archetype_key": str(item.get("key", "")).strip(),
                    "archetype_name": str(item.get("name", "")).strip(),
                    "package_name": str(item.get("package_name", "")).strip(),
                    "package_price": str(item.get("package_price", "")).strip(),
                    "fit_reasons": [str(line).strip() for line in item.get("sponsor_fit", []) if str(line).strip()],
                    "content_angle": [
                        f"围绕「{top_track}」做深度内容共创。",
                        f"结合高频关键词 {', '.join(top_keywords[:4]) if top_keywords else 'AI / 增长 / 行业信号'} 做联合投放。",
                    ],
                    "ideal_opening": self._build_sponsor_opening(
                        archetype_name=str(item.get("name", "")).strip(),
                        package_name=str(item.get("package_name", "")).strip(),
                        top_track=top_track,
                    ),
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "brand_name": self.settings.brand_name,
            "best_current_track": top_track,
            "archetypes": result_archetypes,
        }

    def _build_revenue_signal_report(
        self,
        daily_topics: dict[str, Any],
        benchmark_report: dict[str, Any] | None,
        self_benchmark_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        topics = daily_topics.get("topics", []) if isinstance(daily_topics, dict) else []
        top_topics: list[dict[str, Any]] = []
        for item in topics[:5]:
            if not isinstance(item, dict):
                continue
            top_topics.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "track": str(item.get("title_playbook_track", "")).strip(),
                    "score": int(item.get("total_score", 0) or 0),
                    "matched_keywords": [str(keyword).strip() for keyword in item.get("matched_keywords", []) if str(keyword).strip()],
                    "recommended_format": str(item.get("recommended_format_label", "")).strip(),
                    "commercial_angle": self._infer_commercial_angle(item),
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "best_current_track": self._pick_top_track(benchmark_report, self_benchmark_report),
            "best_patterns": self._pick_top_patterns(self_benchmark_report),
            "top_keywords": self._pick_top_keywords(benchmark_report),
            "top_topics": top_topics,
            "open_lead_count": self._count_open_leads(),
        }

    def _build_opportunity_queue(
        self,
        *,
        daily_topics: dict[str, Any],
        leads: list[dict[str, Any]],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
        signal_report: dict[str, Any],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        open_leads = [item for item in leads if str(item.get("status", "")).strip() not in self.CLOSED_LEAD_STATUSES]
        known_opportunity_ids = {
            str(item.get("opportunity_id", "")).strip()
            for item in leads
            if isinstance(item, dict) and str(item.get("opportunity_id", "")).strip()
        }
        for lead in open_leads[:10]:
            status = str(lead.get("status", "")).strip() or "new"
            items.append(
                {
                    "id": f"lead_followup::{lead.get('id', '')}",
                    "type": "lead_followup",
                    "priority": self._lead_priority(status),
                    "headline": f"跟进现有线索：{lead.get('name', '未命名线索')}",
                    "why_now": f"当前状态为 {status}，避免已有咨询沉没。",
                    "recommended_action": self._lead_next_action(lead),
                    "asset_hint": str(lead.get("proposal_path", "")).strip() or "lead_ledger.json",
                }
            )

        topics = daily_topics.get("topics", []) if isinstance(daily_topics, dict) else []
        segments = customer_targets.get("segments", []) if isinstance(customer_targets, dict) else []
        archetypes = sponsor_targets.get("archetypes", []) if isinstance(sponsor_targets, dict) else []

        for index, topic in enumerate(topics[:4], start=1):
            if not isinstance(topic, dict):
                continue
            customer_segment = segments[(index - 1) % len(segments)] if segments else {}
            if customer_segment:
                customer_opportunity_id = self._build_opportunity_id(
                    "customer_pitch",
                    str(topic.get("title", "")).strip(),
                    str(customer_segment.get("segment_key", "")).strip() or str(customer_segment.get("segment_name", "")).strip(),
                )
                if customer_opportunity_id not in known_opportunity_ids:
                    items.append(
                        {
                            "id": customer_opportunity_id,
                            "type": "customer_pitch",
                            "priority": "high" if index <= 2 else "medium",
                            "headline": f"把热点文章变成客户开发入口：{str(topic.get('title', '')).strip()}",
                            "why_now": self._infer_commercial_angle(topic),
                            "recommended_action": (
                                f"围绕该话题去联系「{customer_segment.get('segment_name', '')}」，"
                                f"主推 {customer_segment.get('recommended_offer_name', '')}。"
                            ),
                            "asset_hint": "customer_targets.json / outreach_playbook.md",
                            "segment_key": str(customer_segment.get("segment_key", "")).strip(),
                            "segment_name": str(customer_segment.get("segment_name", "")).strip(),
                            "recommended_offer_key": str(customer_segment.get("recommended_offer_key", "")).strip(),
                            "recommended_offer_name": str(customer_segment.get("recommended_offer_name", "")).strip(),
                            "recommended_offer_price": str(customer_segment.get("recommended_offer_price", "")).strip(),
                            "recommended_offer_delivery": str(customer_segment.get("recommended_offer_delivery", "")).strip(),
                        }
                    )
            sponsor_archetype = archetypes[(index - 1) % len(archetypes)] if archetypes else {}
            if sponsor_archetype:
                sponsor_opportunity_id = self._build_opportunity_id(
                    "sponsor_pitch",
                    str(topic.get("title", "")).strip(),
                    str(sponsor_archetype.get("archetype_key", "")).strip()
                    or str(sponsor_archetype.get("archetype_name", "")).strip(),
                )
                if sponsor_opportunity_id not in known_opportunity_ids:
                    items.append(
                        {
                            "id": sponsor_opportunity_id,
                            "type": "sponsor_pitch",
                            "priority": "medium" if index <= 2 else "low",
                            "headline": f"把热点文章包装成赞助合作角度：{str(topic.get('title', '')).strip()}",
                            "why_now": f"该选题适合承接 {sponsor_archetype.get('archetype_name', '')} 的品牌合作。",
                            "recommended_action": (
                                f"按 {sponsor_archetype.get('package_name', '')} 打包，"
                                f"向相关品牌发一轮合作试探。"
                            ),
                            "asset_hint": "sponsor_targets.json / outreach_playbook.md",
                        }
                    )

        items.sort(key=self._opportunity_sort_key)
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "open_lead_items": len([item for item in items if item.get("type") == "lead_followup"]),
                "customer_pitch_items": len([item for item in items if item.get("type") == "customer_pitch"]),
                "sponsor_pitch_items": len([item for item in items if item.get("type") == "sponsor_pitch"]),
                "top_signal_topic": (signal_report.get("top_topics") or [{}])[0].get("title", "") if signal_report.get("top_topics") else "",
            },
            "items": items[:12],
        }

    def _build_customer_pitch_candidates(
        self,
        daily_topics: dict[str, Any],
        customer_targets: dict[str, Any],
    ) -> list[dict[str, Any]]:
        topics = daily_topics.get("topics", []) if isinstance(daily_topics, dict) else []
        segments = customer_targets.get("segments", []) if isinstance(customer_targets, dict) else []
        items: list[dict[str, Any]] = []
        for index, topic in enumerate(topics[:4], start=1):
            if not isinstance(topic, dict):
                continue
            customer_segment = segments[(index - 1) % len(segments)] if segments else {}
            if not customer_segment:
                continue
            items.append(
                {
                    "id": self._build_opportunity_id(
                        "customer_pitch",
                        str(topic.get("title", "")).strip(),
                        str(customer_segment.get("segment_key", "")).strip() or str(customer_segment.get("segment_name", "")).strip(),
                    ),
                    "type": "customer_pitch",
                    "priority": "high" if index <= 2 else "medium",
                    "headline": f"把热点文章变成客户开发入口：{str(topic.get('title', '')).strip()}",
                    "why_now": self._infer_commercial_angle(topic),
                    "recommended_action": (
                        f"围绕该话题去联系「{customer_segment.get('segment_name', '')}」，"
                        f"主推 {customer_segment.get('recommended_offer_name', '')}。"
                    ),
                    "asset_hint": "customer_targets.json / outreach_playbook.md",
                    "segment_key": str(customer_segment.get("segment_key", "")).strip(),
                    "segment_name": str(customer_segment.get("segment_name", "")).strip(),
                    "recommended_offer_key": str(customer_segment.get("recommended_offer_key", "")).strip(),
                    "recommended_offer_name": str(customer_segment.get("recommended_offer_name", "")).strip(),
                    "recommended_offer_price": str(customer_segment.get("recommended_offer_price", "")).strip(),
                    "recommended_offer_delivery": str(customer_segment.get("recommended_offer_delivery", "")).strip(),
                }
            )
        return items

    def _build_outreach_playbook(
        self,
        payload: dict[str, Any],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
        offers: dict[str, dict[str, str]],
    ) -> str:
        sequences = payload.get("outreach_sequences", {}) if isinstance(payload, dict) else {}
        lines = [
            f"# {self.settings.brand_name} 主动获客与赞助外联手册",
            "",
            "## 客户开发策略",
            "",
        ]
        for item in customer_targets.get("segments", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"### {item.get('segment_name', '未命名客户群')}")
            lines.append("")
            lines.append(f"- 推荐服务：{item.get('recommended_offer_name', '')} {item.get('recommended_offer_price', '')}".strip())
            lines.append(f"- 首选渠道：{item.get('outreach_channel', '')}")
            lines.append("- 进入条件：")
            for signal in item.get("qualifying_signal", []):
                lines.append(f"  - {signal}")
            lines.append(f"- 开场话术：{item.get('ideal_opening', '')}")
            lines.append("")

        lines.extend(["## 赞助开发策略", ""])
        for item in sponsor_targets.get("archetypes", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"### {item.get('archetype_name', '未命名赞助商类型')}")
            lines.append("")
            lines.append(f"- 合作包：{item.get('package_name', '')} / {item.get('package_price', '')}")
            lines.append("- 适配理由：")
            for reason in item.get("fit_reasons", []):
                lines.append(f"  - {reason}")
            lines.append(f"- 开场话术：{item.get('ideal_opening', '')}")
            lines.append("")

        lines.extend(["## 外联序列", ""])
        for key, steps in sequences.items():
            lines.append(f"### {key}")
            lines.append("")
            for step in steps:
                lines.append(f"- {str(step).strip()}")
            lines.append("")

        lines.extend(["## 可售服务索引", ""])
        for offer_key, offer in offers.items():
            lines.append(f"- {offer.get('name', offer_key)} | {offer.get('price', '')} | {offer.get('delivery', '')}")
        lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_commercial_catalog(
        self,
        *,
        revenue_payload: dict[str, Any],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
    ) -> dict[str, Any]:
        core_offers = revenue_payload.get("core_offers", []) if isinstance(revenue_payload, dict) else []
        service_packages: list[dict[str, Any]] = []
        for item in core_offers:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            service_packages.append(
                {
                    "package_key": key,
                    "package_type": "service",
                    "name": str(item.get("name", "")).strip(),
                    "price": str(item.get("price", "")).strip(),
                    "delivery": str(item.get("delivery", "")).strip(),
                    "target_customer": str(item.get("target_customer", "")).strip(),
                    "recommended_for_segments": [
                        str(segment.get("segment_name", "")).strip()
                        for segment in customer_targets.get("segments", [])
                        if isinstance(segment, dict) and str(segment.get("recommended_offer_key", "")).strip() == key
                    ],
                }
            )

        sponsor_packages = [
            {
                "package_key": str(item.get("archetype_key", "")).strip() or self._slugify(str(item.get("archetype_name", "")).strip()),
                "package_type": "sponsor",
                "name": str(item.get("package_name", "")).strip(),
                "price": str(item.get("package_price", "")).strip(),
                "target_customer": str(item.get("archetype_name", "")).strip(),
                "delivery": "Brand integration, sponsor slot, and co-created content package.",
            }
            for item in sponsor_targets.get("archetypes", [])
            if isinstance(item, dict)
        ]

        letter_packages = [
            {
                "package_key": "letter_free",
                "package_type": "membership",
                "name": f"{self._newsletter_product_name()} Free",
                "price": "0",
                "delivery": "Weekly sample issue and light CTA follow-up.",
                "target_customer": "Audience members evaluating your industry intelligence.",
            },
            {
                "package_key": "letter_member",
                "package_type": "membership",
                "name": f"{self._newsletter_product_name()} Member",
                "price": "199-299 / cycle",
                "delivery": "Full issue, customer list, sponsor monitoring, next-step action list.",
                "target_customer": "Operators, founders, and researchers who need executable weekly intelligence.",
            },
            {
                "package_key": "letter_operator",
                "package_type": "membership",
                "name": f"{self._newsletter_product_name()} Operator Pro",
                "price": "599-999 / cycle",
                "delivery": "Member package plus direct founder Q&A and custom monitoring requests.",
                "target_customer": "Teams using the newsletter as a weekly commercial operating layer.",
            },
        ]

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "brand_name": self.settings.brand_name,
            "service_packages": service_packages,
            "sponsor_packages": sponsor_packages,
            "letter_packages": letter_packages,
        }

    def _build_closing_playbook(
        self,
        *,
        payload: dict[str, Any],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
        commercial_catalog: dict[str, Any],
    ) -> str:
        lines = [
            f"# {self.settings.brand_name} Closing Playbook",
            "",
            "## Service Ladder",
            "",
            "1. Sell the smallest credible next step first.",
            "2. Move diagnosis or research buyers into advisory once trust is earned.",
            "3. Use proposal + clear timeline + specific outcome as the closing wedge.",
            "",
            "## Service Packages",
            "",
        ]
        for item in commercial_catalog.get("service_packages", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"### {item.get('name', '')}")
            lines.append("")
            lines.append(f"- Price: {item.get('price', '')}")
            lines.append(f"- Delivery: {item.get('delivery', '')}")
            lines.append(f"- Best for: {item.get('target_customer', '')}")
            lines.append("")

        lines.extend(["## Sponsor Packages", ""])
        for item in commercial_catalog.get("sponsor_packages", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"### {item.get('name', '')}")
            lines.append("")
            lines.append(f"- Price: {item.get('price', '')}")
            lines.append(f"- Best for: {item.get('target_customer', '')}")
            lines.append("- Closing move: start with a small package, then upsell if audience fit is proven.")
            lines.append("")

        lines.extend(["## Letter Packages", ""])
        for item in commercial_catalog.get("letter_packages", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"### {item.get('name', '')}")
            lines.append("")
            lines.append(f"- Price: {item.get('price', '')}")
            lines.append(f"- Delivery: {item.get('delivery', '')}")
            lines.append("")

        lines.extend(["## Segment-to-Offer Match", ""])
        for item in customer_targets.get("segments", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('segment_name', '')}: lead with {item.get('recommended_offer_name', '')}")

        lines.extend(["", "## Sponsor-to-Package Match", ""])
        for item in sponsor_targets.get("archetypes", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('archetype_name', '')}: lead with {item.get('package_name', '')}")

        sequences = payload.get("outreach_sequences", {}) if isinstance(payload, dict) else {}
        if sequences:
            lines.extend(["", "## Follow-up Sequences", ""])
            for key, steps in sequences.items():
                lines.append(f"### {key}")
                lines.append("")
                for step in steps:
                    lines.append(f"- {str(step).strip()}")
                lines.append("")

        return "\n".join(lines).strip() + "\n"

    def _build_deer_flow_task_pack(
        self,
        payload: dict[str, Any],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
        opportunity_queue: dict[str, Any],
        integrations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        project_map = {str(item.get("key", "")).strip(): item for item in integrations if isinstance(item, dict)}
        deer_flow_status = str(project_map.get("deer_flow", {}).get("integration_status", "")).strip() or "planned"
        tasks = []
        for item in payload.get("deer_flow_playbooks", []):
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            if key == "customer_prospecting":
                output_assets = [self.customer_targets_path.name, self.opportunity_queue_path.name]
            elif key == "sponsor_prospecting":
                output_assets = [self.sponsor_targets_path.name, self.opportunity_queue_path.name]
            else:
                output_assets = [self.newsletter_offer_path.name, self.weekly_pipeline_path.name]
            tasks.append(
                {
                    "key": key,
                    "objective": str(item.get("objective", "")).strip(),
                    "status": "ready" if deer_flow_status == "confirmed" else "adapter_ready",
                    "expected_outputs": output_assets,
                }
            )
        return {
            "version": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "integrations": {
                "deer_flow_status": deer_flow_status,
                "agent_roles": payload.get("agent_roles", {}),
            },
            "inputs": {
                "customer_segments": len(customer_targets.get("segments", [])),
                "sponsor_archetypes": len(sponsor_targets.get("archetypes", [])),
                "queue_items": len(opportunity_queue.get("items", [])),
                "brand_name": self.settings.brand_name,
            },
            "tasks": tasks,
        }

    def _build_newsletter_offer(
        self,
        payload: dict[str, Any],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
        signal_report: dict[str, Any],
        commercial_catalog: dict[str, Any],
    ) -> str:
        newsletter = payload.get("newsletter", {}) if isinstance(payload, dict) else {}
        lines = [
            f"# {newsletter.get('product_name', '行业情报周报')}",
            "",
            f"- 节奏：{newsletter.get('cadence', 'weekly')}",
            f"- 免费入口：{newsletter.get('free_call_to_action', '')}",
            f"- 付费入口：{newsletter.get('paid_call_to_action', '')}",
            "",
            "## 为什么值得做",
            "",
            "- 把平台流量沉淀为可反复触达的订阅资产。",
            "- 承接研究、诊断、顾问服务和赞助合作。",
            "- 作为 letter 适配层的核心变现产品。",
            "",
            "## 本周最该写进 newsletter 的主题",
            "",
        ]
        for item in signal_report.get("top_topics", [])[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('title', '')} -> {item.get('commercial_angle', '')}")
        lines.extend(["", "## 本周可承接客户", ""])
        for item in customer_targets.get("segments", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('segment_name', '')} -> {item.get('recommended_offer_name', '')}")
        lines.extend(["", "## 可售赞助位", ""])
        for item in sponsor_targets.get("archetypes", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('package_name', '')} / {item.get('package_price', '')}")
        letter_packages = commercial_catalog.get("letter_packages", []) if isinstance(commercial_catalog, dict) else []
        if letter_packages:
            lines.extend(["", "## Membership Ladder", ""])
            for item in letter_packages:
                if not isinstance(item, dict):
                    continue
                lines.append(f"- {item.get('name', '')}: {item.get('price', '')}")
        lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_weekly_pipeline(
        self,
        payload: dict[str, Any],
        integrations: list[dict[str, Any]],
        opportunity_queue: dict[str, Any],
    ) -> str:
        project_map = {str(item.get("key", "")).strip(): item for item in integrations if isinstance(item, dict)}
        lines = [
            f"# {self.settings.brand_name} 每周赚钱执行管线",
            "",
            "## 周一到周五",
            "",
            "- 采集热点并发布内容，持续扩大赛道影响力。",
            "- 从评论、私信、转介绍中记录线索并推进状态。",
            "- 每天刷新商机扫描器，优先处理高优先级机会。",
            "",
            "## 周度商业动作",
            "",
            f"- deer-flow：{project_map.get('deer_flow', {}).get('role', '用于复杂研究与 prospecting。')}",
            f"- agennts：{', '.join(payload.get('agent_roles', {}).get('agennts', [])) or '用于售前分诊与客户跟进。'}",
            f"- cloudflow：{', '.join(payload.get('agent_roles', {}).get('cloudflow', [])) or '用于编排日报、周报、商机扫描。'}",
            f"- letter：{', '.join(payload.get('agent_roles', {}).get('letter', [])) or '用于 newsletter 订阅与赞助转化。'}",
            "",
            "## 本周优先处理",
            "",
        ]
        for item in opportunity_queue.get("items", [])[:5]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- [{item.get('priority', '').upper()}] {item.get('headline', '')}")
        lines.extend(
            [
                "",
                "## 每周交付物",
                "",
                f"- {self.customer_targets_path.name}",
                f"- {self.sponsor_targets_path.name}",
                f"- {self.revenue_signal_report_path.name}",
                f"- {self.opportunity_queue_path.name}",
                f"- {self.outreach_playbook_path.name}",
                f"- {self.commercial_catalog_path.name}",
                f"- {self.closing_playbook_path.name}",
                f"- {self.newsletter_offer_path.name}",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def _build_prompt_optimization_pack(
        self,
        payload: dict[str, Any],
        signal_report: dict[str, Any],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
    ) -> dict[str, Any]:
        top_topic = ""
        top_topics = signal_report.get("top_topics", []) if isinstance(signal_report, dict) else []
        if top_topics and isinstance(top_topics[0], dict):
            top_topic = str(top_topics[0].get("title", "")).strip()
        best_track = str(signal_report.get("best_current_track", "")).strip() if isinstance(signal_report, dict) else ""
        patterns = signal_report.get("best_patterns", []) if isinstance(signal_report, dict) else []
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "adapter": {
                "project_key": "prompt_optimized",
                "assumed_repo": "linshenkx/prompt-optimizer",
                "status": "adapter_ready",
            },
            "prompt_targets": [
                {
                    "name": "topic_selection",
                    "objective": "Increase topic click-through and buyer relevance.",
                    "source_prompt_files": [
                        "prompts/title_prompt_v2.md",
                        "prompts/article_prompt_v2.md",
                    ],
                    "optimization_brief": {
                        "top_track": best_track,
                        "top_topic": top_topic,
                        "customer_segment_count": len(customer_targets.get("segments", [])),
                    },
                },
                {
                    "name": "sales_outreach",
                    "objective": "Increase reply rate and proposal acceptance.",
                    "source_prompt_files": [
                        "output/automation/revenue_engine/outreach_playbook.md",
                        "output/automation/revenue_engine/closing_playbook.md",
                        "output/automation/lead_ops/wechat_batch_messages.md",
                    ],
                    "optimization_brief": {
                        "customer_targets": [
                            str(item.get("segment_name", "")).strip()
                            for item in customer_targets.get("segments", [])[:3]
                            if isinstance(item, dict)
                        ],
                        "sponsor_targets": [
                            str(item.get("archetype_name", "")).strip()
                            for item in sponsor_targets.get("archetypes", [])[:3]
                            if isinstance(item, dict)
                        ],
                        "commercial_assets": [
                            self.commercial_catalog_path.name,
                            self.closing_playbook_path.name,
                        ],
                    },
                },
            ],
            "training_examples": [
                {
                    "input_signal": top_topic or "AI industry trend",
                    "desired_output_trait": "Specific, commercially relevant, founder-facing angle",
                },
                {
                    "input_signal": ", ".join(str(item) for item in patterns[:3]) or "trend analysis",
                    "desired_output_trait": "Higher conversion language with stronger urgency and clearer CTA",
                },
            ],
        }

    def _build_cloudflow_blueprint(
        self,
        payload: dict[str, Any],
        integrations: list[dict[str, Any]],
        opportunity_queue: dict[str, Any],
    ) -> dict[str, Any]:
        project_map = {str(item.get("key", "")).strip(): item for item in integrations if isinstance(item, dict)}
        cloudflow_status = str(project_map.get("cloudflow", {}).get("integration_status", "")).strip() or "adapter_ready"
        queue_items = opportunity_queue.get("items", []) if isinstance(opportunity_queue, dict) else []
        return {
            "version": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "adapter": {
                "project_key": "cloudflow",
                "status": cloudflow_status,
                "role": project_map.get("cloudflow", {}).get("role", "Coordinate recurring revenue operations."),
            },
            "flows": [
                {
                    "name": "daily_revenue_scan",
                    "trigger": "0 9 * * *",
                    "steps": [
                        "refresh_revenue_signal_report",
                        "refresh_opportunity_queue",
                        "sync_customer_pitch_leads",
                        "rebuild_sales_execution_dashboard",
                    ],
                    "artifacts": [
                        self.revenue_signal_report_path.name,
                        self.opportunity_queue_path.name,
                        "sales_execution_dashboard.json",
                    ],
                },
                {
                    "name": "weekly_letter_and_sponsor_push",
                    "trigger": "0 10 * * 1",
                    "steps": [
                        "refresh_commercial_catalog",
                        "refresh_closing_playbook",
                        "refresh_closing_dispatch_queue",
                        "refresh_closing_followup_queue",
                        "refresh_onboarding_queue",
                        "refresh_service_kickoff_pack",
                        "refresh_sponsor_onboarding_pack",
                        "refresh_letter_welcome_pack",
                        "refresh_service_kickoff_brief",
                        "refresh_sponsor_onboarding_guide",
                        "refresh_letter_member_welcome",
                        "build_newsletter_offer",
                        "build_letter_campaign",
                        "refresh_sponsor_targets",
                        "emit_founder_summary",
                    ],
                    "artifacts": [
                        self.commercial_catalog_path.name,
                        self.closing_playbook_path.name,
                        self.closing_dispatch_path.name,
                        self.closing_message_pack_path.name,
                        self.closing_followup_queue_path.name,
                        self.onboarding_queue_path.name,
                        self.service_kickoff_pack_path.name,
                        self.sponsor_onboarding_pack_path.name,
                        self.letter_welcome_pack_path.name,
                        self.service_kickoff_brief_path.name,
                        self.sponsor_onboarding_guide_path.name,
                        self.letter_member_welcome_path.name,
                        self.newsletter_offer_path.name,
                        self.letter_campaign_path.name,
                        self.sponsor_targets_path.name,
                    ],
                },
            ],
            "live_queue_snapshot": [
                {
                    "headline": str(item.get("headline", "")).strip(),
                    "priority": str(item.get("priority", "")).strip(),
                    "recommended_action": str(item.get("recommended_action", "")).strip(),
                }
                for item in queue_items[:5]
                if isinstance(item, dict)
            ],
            "agent_roles": payload.get("agent_roles", {}) if isinstance(payload, dict) else {},
        }

    def _build_letter_campaign(
        self,
        payload: dict[str, Any],
        signal_report: dict[str, Any],
        customer_targets: dict[str, Any],
        sponsor_targets: dict[str, Any],
    ) -> str:
        newsletter = payload.get("newsletter", {}) if isinstance(payload, dict) else {}
        top_topics = signal_report.get("top_topics", []) if isinstance(signal_report, dict) else []
        customer_segments = customer_targets.get("segments", []) if isinstance(customer_targets, dict) else []
        sponsor_archetypes = sponsor_targets.get("archetypes", []) if isinstance(sponsor_targets, dict) else []
        lines = [
            "# Letter Campaign Pack",
            "",
            f"- Product: {newsletter.get('product_name', 'Industry Intelligence Weekly')}",
            f"- Free CTA: {newsletter.get('free_call_to_action', '')}",
            f"- Paid CTA: {newsletter.get('paid_call_to_action', '')}",
            f"- Offer Stack Asset: {self.commercial_catalog_path.name}",
            "",
            "## Welcome Email",
            "",
            "Subject: 欢迎加入本周行业情报内参",
            "",
            "你会先收到一版免费样刊，我们会持续给你最值得交易、研究或跟进的行业信号。",
            "",
            "## Upgrade Email",
            "",
            "Subject: 升级会员，获取完整客户名单与赞助监测",
            "",
            "如果你希望把内容影响力直接转成客户、赞助和合作机会，完整版会提供可执行名单与下周动作。",
            "",
            "## This Week's Topics",
            "",
        ]
        for item in top_topics[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('title', '')}: {item.get('commercial_angle', '')}")
        lines.extend(["", "## Best Customer Angles", ""])
        for item in customer_segments[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('segment_name', '')}: {item.get('recommended_offer_name', '')}")
        lines.extend(["", "## Best Sponsor Angles", ""])
        for item in sponsor_archetypes[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('archetype_name', '')}: {item.get('package_name', '')}")
        lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_agennts_dispatch_queue(
        self,
        payload: dict[str, Any],
        opportunity_queue: dict[str, Any],
        sales_dashboard: dict[str, Any],
        subscriber_ledger: list[dict[str, Any]],
        closing_dispatch: dict[str, Any],
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        agent_roles = payload.get("agent_roles", {}) if isinstance(payload, dict) else {}
        queue_items = opportunity_queue.get("items", []) if isinstance(opportunity_queue, dict) else []
        sales_items = sales_dashboard.get("items", []) if isinstance(sales_dashboard, dict) else []
        closing_items = closing_dispatch.get("items", []) if isinstance(closing_dispatch, dict) else []

        for item in queue_items[:6]:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).strip()
            role = "售前分诊代理"
            if item_type == "sponsor_pitch":
                role = "赞助合作代理"
            elif item_type == "lead_followup":
                role = "客户成功 / 跟进代理"
            items.append(
                {
                    "agent_family": "agennts",
                    "agent_role": role,
                    "source": "opportunity_queue",
                    "priority": str(item.get("priority", "")).strip(),
                    "headline": str(item.get("headline", "")).strip(),
                    "next_action": str(item.get("recommended_action", "")).strip(),
                    "asset_hint": str(item.get("asset_hint", "")).strip(),
                }
            )

        for item in sales_items[:6]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "agent_family": "agennts",
                    "agent_role": "售前分诊代理",
                    "source": "sales_execution_dashboard",
                    "priority": str(item.get("priority", "")).strip(),
                    "headline": str(item.get("lead_name", "")).strip(),
                    "next_action": str(item.get("recommended_next_action", "")).strip(),
                    "asset_hint": str(item.get("proposal_path", "")).strip() or str(item.get("action_type", "")).strip(),
                }
            )

        for item in subscriber_ledger[:4]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            if status not in {"new_subscriber", "member_interested", "renewal_due"}:
                continue
            items.append(
                {
                    "agent_family": "agennts",
                    "agent_role": "letter 会员转化代理",
                    "source": "newsletter_subscriber_ledger",
                    "priority": "high" if status in {"member_interested", "renewal_due"} else "medium",
                    "headline": str(item.get("subscriber_name", "")).strip() or str(item.get("contact", "")).strip(),
                    "next_action": self._letter_next_action(item),
                    "asset_hint": self.newsletter_subscriber_ledger_path.name,
                }
            )

        for item in closing_items[:6]:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "agent_family": "agennts",
                    "agent_role": str(item.get("agent_role", "")).strip() or "成交推进代理",
                    "source": "closing_dispatch_queue",
                    "priority": str(item.get("priority", "")).strip(),
                    "headline": str(item.get("headline", "")).strip(),
                    "next_action": str(item.get("closing_wedge", "")).strip(),
                    "asset_hint": self.closing_dispatch_path.name,
                    "recommended_package": str(item.get("recommended_package_name", "")).strip(),
                    "target_value": float(item.get("target_value", 0.0) or 0.0),
                }
            )

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "roles_configured": agent_roles.get("agennts", []) if isinstance(agent_roles, dict) else [],
            "items": items[:12],
        }

    def _build_closing_dispatch_queue(
        self,
        opportunity_queue: dict[str, Any],
        sales_dashboard: dict[str, Any],
        subscriber_ledger: list[dict[str, Any]],
        sponsor_pipeline: dict[str, Any],
        sponsor_log: list[dict[str, Any]],
        commercial_catalog: dict[str, Any],
        closing_playbook: str,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        queue_items = opportunity_queue.get("items", []) if isinstance(opportunity_queue, dict) else []
        sales_items = sales_dashboard.get("items", []) if isinstance(sales_dashboard, dict) else []
        sponsor_items = sponsor_pipeline.get("items", []) if isinstance(sponsor_pipeline, dict) else []

        for item in sales_items[:6]:
            if not isinstance(item, dict):
                continue
            package = self._match_service_package(
                commercial_catalog,
                lead_name=str(item.get("lead_name", "")).strip(),
                proposal_path=str(item.get("proposal_path", "")).strip(),
            )
            items.append(
                {
                    "queue_key": f"closing::lead::{str(item.get('lead_id', '')).strip()}",
                    "pipeline_type": "service",
                    "agent_role": "售前成交推进代理",
                    "priority": str(item.get("priority", "")).strip(),
                    "headline": str(item.get("lead_name", "")).strip(),
                    "recommended_package_key": str(package.get("package_key", "")).strip(),
                    "recommended_package_name": str(package.get("name", "")).strip(),
                    "target_value": self._price_floor(str(package.get("price", "")).strip()),
                    "closing_wedge": self._service_closing_wedge(item, package),
                    "asset_hint": str(item.get("proposal_path", "")).strip() or self.closing_playbook_path.name,
                }
            )

        sponsor_index = {
            str(item.get("action_key", "")).strip(): item
            for item in sponsor_log
            if isinstance(item, dict)
        }
        for item in sponsor_items[:4]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            if status not in {"prepared", "interested", "call_booked"}:
                continue
            sponsor_key = str(item.get("action_key", "")).strip()
            sponsor_detail = sponsor_index.get(sponsor_key, {})
            package = self._match_sponsor_package(
                commercial_catalog,
                target_name=str(item.get("target_name", "")).strip(),
                message_subject=str(item.get("message_subject", "")).strip(),
            )
            items.append(
                {
                    "queue_key": f"closing::sponsor::{sponsor_key}",
                    "pipeline_type": "sponsor",
                    "agent_role": "赞助成交推进代理",
                    "priority": "high" if status in {"interested", "call_booked"} else "medium",
                    "headline": str(item.get("target_name", "")).strip(),
                    "recommended_package_key": str(package.get("package_key", "")).strip(),
                    "recommended_package_name": str(package.get("name", "")).strip(),
                    "target_value": self._price_floor(str(package.get("price", "")).strip()),
                    "closing_wedge": self._sponsor_closing_wedge(item, sponsor_detail, package),
                    "asset_hint": self.sponsor_pipeline_path.name,
                }
            )

        for item in subscriber_ledger[:8]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            if status not in {"new_subscriber", "upgrade_prompt_sent", "member_interested", "renewal_due"}:
                continue
            package = self._match_letter_package(commercial_catalog, status)
            items.append(
                {
                    "queue_key": f"closing::letter::{str(item.get('subscriber_key', '')).strip()}",
                    "pipeline_type": "letter",
                    "agent_role": "letter 会员成交推进代理",
                    "priority": "high" if status in {"member_interested", "renewal_due"} else "medium",
                    "headline": str(item.get("subscriber_name", "")).strip() or str(item.get("contact", "")).strip(),
                    "recommended_package_key": str(package.get("package_key", "")).strip(),
                    "recommended_package_name": str(package.get("name", "")).strip(),
                    "target_value": self._price_floor(str(package.get("price", "")).strip()),
                    "closing_wedge": self._letter_closing_wedge(item, package),
                    "asset_hint": self.newsletter_subscriber_ledger_path.name,
                }
            )

        items.sort(key=self._closing_dispatch_sort_key)
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "closing_playbook_asset": self.closing_playbook_path.name,
            "summary": {
                "total_items": len(items),
                "service_items": len([item for item in items if item.get("pipeline_type") == "service"]),
                "sponsor_items": len([item for item in items if item.get("pipeline_type") == "sponsor"]),
                "letter_items": len([item for item in items if item.get("pipeline_type") == "letter"]),
                "target_pipeline_value": round(sum(float(item.get("target_value", 0.0) or 0.0) for item in items), 2),
            },
            "items": items[:16],
            "playbook_excerpt": closing_playbook.splitlines()[:12] if closing_playbook else [],
        }

    def _build_closing_message_pack(self, closing_dispatch: dict[str, Any]) -> dict[str, Any]:
        items = closing_dispatch.get("items", []) if isinstance(closing_dispatch, dict) else []
        messages: list[dict[str, Any]] = []
        for item in items[:16]:
            if not isinstance(item, dict):
                continue
            pipeline_type = str(item.get("pipeline_type", "")).strip()
            recommended_package = str(item.get("recommended_package_name", "")).strip()
            target_value = float(item.get("target_value", 0.0) or 0.0)
            closing_wedge = str(item.get("closing_wedge", "")).strip()
            headline = str(item.get("headline", "")).strip()
            queue_key = str(item.get("queue_key", "")).strip()
            if pipeline_type == "service":
                channel = "wechat"
                message = f"围绕 {headline} 这个机会，我建议直接推进 {recommended_package}，预算锚点可先参考 {target_value:.0f}。{closing_wedge}"
            elif pipeline_type == "sponsor":
                channel = "email"
                message = f"如果你们愿意先从小单验证，我建议先上 {recommended_package}。预算锚点从 {target_value:.0f} 起。{closing_wedge}"
            else:
                channel = "wechat"
                message = f"如果你希望下周开始拿到完整版本，我建议直接升级到 {recommended_package}。{closing_wedge}"
            messages.append(
                {
                    "queue_key": queue_key,
                    "pipeline_type": pipeline_type,
                    "channel": channel,
                    "headline": headline,
                    "recommended_package_name": recommended_package,
                    "target_value": target_value,
                    "message": message,
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_messages": len(messages),
                "service_messages": len([item for item in messages if item.get("pipeline_type") == "service"]),
                "sponsor_messages": len([item for item in messages if item.get("pipeline_type") == "sponsor"]),
                "letter_messages": len([item for item in messages if item.get("pipeline_type") == "letter"]),
            },
            "items": messages,
        }

    def _build_closing_followup_queue(
        self,
        closing_dispatch: dict[str, Any],
        closing_action_log: list[dict[str, Any]],
    ) -> dict[str, Any]:
        dispatch_items = closing_dispatch.get("items", []) if isinstance(closing_dispatch, dict) else []
        logged_keys = {
            str(item.get("queue_key", "")).strip()
            for item in closing_action_log
            if isinstance(item, dict) and str(item.get("queue_key", "")).strip()
        }
        items: list[dict[str, Any]] = []
        for item in dispatch_items[:20]:
            if not isinstance(item, dict):
                continue
            queue_key = str(item.get("queue_key", "")).strip()
            if queue_key not in logged_keys:
                continue
            items.append(
                {
                    "queue_key": queue_key,
                    "pipeline_type": str(item.get("pipeline_type", "")).strip(),
                    "headline": str(item.get("headline", "")).strip(),
                    "priority": str(item.get("priority", "")).strip(),
                    "recommended_package_name": str(item.get("recommended_package_name", "")).strip(),
                    "target_value": float(item.get("target_value", 0.0) or 0.0),
                    "follow_up_action": self._closing_follow_up_text(item),
                    "next_follow_up_at": self._default_letter_follow_up_slot(days=2),
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "high_priority_items": len([item for item in items if str(item.get("priority", "")).strip() == "high"]),
            },
            "items": items[:20],
        }

    def _build_onboarding_queue(
        self,
        subscriber_ledger: list[dict[str, Any]],
        sponsor_log: list[dict[str, Any]],
    ) -> dict[str, Any]:
        lead_ledger = self.lead_ops_service.list_leads(limit=500)
        items: list[dict[str, Any]] = []

        for lead in lead_ledger:
            if not isinstance(lead, dict):
                continue
            if str(lead.get("status", "")).strip() != "won":
                continue
            if str(lead.get("delivery_started_at", "")).strip():
                continue
            items.append(
                {
                    "queue_key": f"onboarding::lead::{str(lead.get('id', '')).strip()}",
                    "pipeline_type": "service",
                    "headline": str(lead.get("name", "")).strip(),
                    "recommended_next_action": "Start delivery kickoff, align milestones, and confirm the first deliverable.",
                    "asset_hint": "customer_success_dashboard.json",
                }
            )

        for item in subscriber_ledger:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).strip() not in {"member_won", "renewed"}:
                continue
            if str(item.get("onboarding_started_at", "")).strip():
                continue
            items.append(
                {
                    "queue_key": f"onboarding::letter::{str(item.get('subscriber_key', '')).strip()}",
                    "pipeline_type": "letter",
                    "headline": str(item.get("subscriber_name", "")).strip(),
                    "recommended_next_action": "Send member welcome pack, confirm delivery cadence, and offer the first full issue.",
                    "asset_hint": self.newsletter_subscriber_ledger_path.name,
                }
            )

        for item in sponsor_log:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).strip() != "won":
                continue
            if str(item.get("onboarding_started_at", "")).strip():
                continue
            items.append(
                {
                    "queue_key": f"onboarding::sponsor::{str(item.get('action_key', '')).strip()}",
                    "pipeline_type": "sponsor",
                    "headline": str(item.get("target_name", "")).strip(),
                    "recommended_next_action": "Confirm sponsor assets, delivery schedule, and success metrics before launch.",
                    "asset_hint": self.sponsor_pipeline_path.name,
                }
            )

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "service_items": len([item for item in items if item.get("pipeline_type") == "service"]),
                "sponsor_items": len([item for item in items if item.get("pipeline_type") == "sponsor"]),
                "letter_items": len([item for item in items if item.get("pipeline_type") == "letter"]),
            },
            "items": items[:20],
        }

    def _build_service_kickoff_pack(self) -> dict[str, Any]:
        lead_ledger = self.lead_ops_service.list_leads(limit=500)
        items: list[dict[str, Any]] = []
        for lead in lead_ledger:
            if not isinstance(lead, dict):
                continue
            if str(lead.get("status", "")).strip() != "won":
                continue
            if str(lead.get("delivery_started_at", "")).strip():
                continue
            items.append(
                {
                    "lead_id": str(lead.get("id", "")).strip(),
                    "lead_name": str(lead.get("name", "")).strip(),
                    "proposal_path": str(lead.get("proposal_path", "")).strip(),
                    "kickoff_steps": [
                        "Confirm the primary business goal for the first delivery cycle.",
                        "Lock the first deliverable, owner, and due date.",
                        "Confirm communication rhythm and reporting cadence.",
                    ],
                    "first_deliverable_hint": str(lead.get("suggested_offer_name", "")).strip() or "First service deliverable",
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {"total_items": len(items)},
            "items": items[:20],
        }

    def _build_sponsor_onboarding_pack(self, sponsor_log: list[dict[str, Any]]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for item in sponsor_log:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).strip() != "won":
                continue
            items.append(
                {
                    "action_key": str(item.get("action_key", "")).strip(),
                    "target_name": str(item.get("target_name", "")).strip(),
                    "deal_value": float(item.get("deal_value", 0.0) or 0.0),
                    "onboarding_steps": [
                        "Collect logo, copy, landing page, and tracking links.",
                        "Confirm publishing slot, format, and audience promise.",
                        "Align the success metric for the first sponsor cycle.",
                    ],
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {"total_items": len(items)},
            "items": items[:20],
        }

    def _build_letter_welcome_pack(self, subscriber_ledger: list[dict[str, Any]]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for item in subscriber_ledger:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).strip() not in {"member_won", "renewed"}:
                continue
            items.append(
                {
                    "subscriber_key": str(item.get("subscriber_key", "")).strip(),
                    "subscriber_name": str(item.get("subscriber_name", "")).strip(),
                    "member_revenue": float(item.get("member_revenue", 0.0) or 0.0),
                    "welcome_steps": [
                        "Send the member welcome note and explain cadence.",
                        "Share the first full issue and how to request deeper monitoring.",
                        "Set the next renewal reminder and value check-in date.",
                    ],
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {"total_items": len(items)},
            "items": items[:20],
        }

    def _build_service_kickoff_brief(self, payload: dict[str, Any]) -> str:
        lines = [
            "# Service Kickoff Brief",
            "",
            f"- Generated at: {payload.get('generated_at', '')}",
            f"- Total items: {payload.get('summary', {}).get('total_items', 0)}",
            "",
        ]
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"## {item.get('lead_name', '')}")
            lines.append("")
            lines.append(f"- Lead ID: {item.get('lead_id', '')}")
            lines.append(f"- Proposal: {item.get('proposal_path', '')}")
            lines.append(f"- First deliverable hint: {item.get('first_deliverable_hint', '')}")
            lines.append("- Kickoff steps:")
            for step in item.get("kickoff_steps", []):
                lines.append(f"  - {step}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_sponsor_onboarding_guide(self, payload: dict[str, Any]) -> str:
        lines = [
            "# Sponsor Onboarding Guide",
            "",
            f"- Generated at: {payload.get('generated_at', '')}",
            f"- Total items: {payload.get('summary', {}).get('total_items', 0)}",
            "",
        ]
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"## {item.get('target_name', '')}")
            lines.append("")
            lines.append(f"- Action Key: {item.get('action_key', '')}")
            lines.append(f"- Deal value: {float(item.get('deal_value', 0.0) or 0.0):.2f}")
            lines.append("- Onboarding steps:")
            for step in item.get("onboarding_steps", []):
                lines.append(f"  - {step}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_letter_member_welcome(self, payload: dict[str, Any]) -> str:
        lines = [
            "# Letter Member Welcome Pack",
            "",
            f"- Generated at: {payload.get('generated_at', '')}",
            f"- Total items: {payload.get('summary', {}).get('total_items', 0)}",
            "",
        ]
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"## {item.get('subscriber_name', '')}")
            lines.append("")
            lines.append(f"- Subscriber Key: {item.get('subscriber_key', '')}")
            lines.append(f"- Member revenue: {float(item.get('member_revenue', 0.0) or 0.0):.2f}")
            lines.append("- Welcome steps:")
            for step in item.get("welcome_steps", []):
                lines.append(f"  - {step}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_founder_profit_brief(
        self,
        lead_summary: dict[str, Any],
        opportunity_queue: dict[str, Any],
        sales_dashboard: dict[str, Any],
        signal_report: dict[str, Any],
        subscriber_ledger: list[dict[str, Any]],
    ) -> str:
        queue_summary = opportunity_queue.get("summary", {}) if isinstance(opportunity_queue, dict) else {}
        sales_summary = sales_dashboard.get("summary", {}) if isinstance(sales_dashboard, dict) else {}
        top_topics = signal_report.get("top_topics", []) if isinstance(signal_report, dict) else []
        sponsor_revenue_report = self._build_sponsor_revenue_report()
        lines = [
            "# Founder Profit Brief",
            "",
            f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
            f"- Actual revenue: {float(lead_summary.get('actual_revenue', 0.0) or 0.0):.2f}",
            f"- Estimated pipeline revenue: {float(lead_summary.get('estimated_revenue', 0.0) or 0.0):.2f}",
            f"- Sponsor revenue: {float(sponsor_revenue_report.get('revenue', {}).get('won_sponsor_revenue', 0.0) or 0.0):.2f}",
            f"- Paying members: {len([item for item in subscriber_ledger if str(item.get('status', '')).strip() in {'member_won', 'renewed'}])}",
            f"- Open opportunities this week: {int(queue_summary.get('total_items', 0) or 0)}",
            f"- Immediate sales actions: {int(sales_summary.get('total_execution_items', 0) or 0)}",
            "",
            "## Top Priorities",
            "",
        ]
        if int(sales_summary.get("proposal_send_due_leads", 0) or 0) > 0:
            lines.append("- Send ready proposals first. This is the fastest path to cash collection.")
        if int(sales_summary.get("unsent_outreach_leads", 0) or 0) > 0:
            lines.append("- Clear first-touch outreach backlog to keep the pipeline growing.")
        if int(queue_summary.get("sponsor_pitch_items", 0) or 0) > 0:
            lines.append("- Push sponsor outreach this week to diversify revenue beyond services.")
        if any(str(item.get("status", "")).strip() == "member_interested" for item in subscriber_ledger):
            lines.append("- Close interested newsletter members before they cool off.")
        if any(str(item.get("status", "")).strip() == "renewal_due" for item in subscriber_ledger):
            lines.append("- Send renewal prompts to active members with revenue history.")
        if top_topics and isinstance(top_topics[0], dict):
            lines.append(f"- Anchor this week's newsletter and outbound around: {str(top_topics[0].get('title', '')).strip()}")
        lines.extend(["", "## Execution Stack", ""])
        lines.append("- deer-flow: research and prospecting task pack")
        lines.append("- prompt-optimized: improve topic, outreach, and conversion prompts")
        lines.append("- agennts: dispatch follow-up, sponsor, and sales actions")
        lines.append("- cloudflow: schedule recurring revenue scans and weekly pushes")
        lines.append("- letter: convert audience into newsletter subscribers and paying members")
        lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_profit_loop_report(
        self,
        agent_dispatch: dict[str, Any],
        lead_summary: dict[str, Any],
        opportunity_queue: dict[str, Any],
        sales_dashboard: dict[str, Any],
        closing_dispatch: dict[str, Any],
        sponsor_revenue_report: dict[str, Any],
        letter_revenue_report: dict[str, Any],
    ) -> dict[str, Any]:
        queue_summary = opportunity_queue.get("summary", {}) if isinstance(opportunity_queue, dict) else {}
        sales_summary = sales_dashboard.get("summary", {}) if isinstance(sales_dashboard, dict) else {}
        dispatch_items = agent_dispatch.get("items", []) if isinstance(agent_dispatch, dict) else []
        closing_summary = closing_dispatch.get("summary", {}) if isinstance(closing_dispatch, dict) else {}
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "revenue_snapshot": {
                "actual_revenue": float(lead_summary.get("actual_revenue", 0.0) or 0.0),
                "estimated_revenue": float(lead_summary.get("estimated_revenue", 0.0) or 0.0),
                "sponsor_revenue": float(sponsor_revenue_report.get("revenue", {}).get("won_sponsor_revenue", 0.0) or 0.0),
                "letter_member_revenue": float(letter_revenue_report.get("revenue", {}).get("member_revenue", 0.0) or 0.0),
                "letter_renewal_revenue": float(letter_revenue_report.get("revenue", {}).get("renewal_revenue", 0.0) or 0.0),
                "won_leads": int(lead_summary.get("won_leads", 0) or 0),
            },
            "execution_snapshot": {
                "opportunity_items": int(queue_summary.get("total_items", 0) or 0),
                "sales_actions": int(sales_summary.get("total_execution_items", 0) or 0),
                "dispatch_items": len(dispatch_items),
                "closing_items": int(closing_summary.get("total_items", 0) or 0),
                "closing_target_pipeline_value": float(closing_summary.get("target_pipeline_value", 0.0) or 0.0),
                "active_sponsor_conversations": int(sponsor_revenue_report.get("summary", {}).get("active_sponsor_conversations", 0) or 0),
                "active_letter_subscribers": int(letter_revenue_report.get("summary", {}).get("active_subscribers", 0) or 0),
            },
            "next_money_moves": [
                str(item.get("headline", "")).strip()
                for item in dispatch_items[:5]
                if isinstance(item, dict) and str(item.get("headline", "")).strip()
            ],
        }

    def _build_sponsor_pipeline(self) -> dict[str, Any]:
        sponsor_log = self._load_sponsor_outreach_log()
        items = [item for item in sponsor_log if isinstance(item, dict)]
        active_statuses = {"prepared", "sent", "interested", "call_booked"}
        follow_up_due = [
            item
            for item in items
            if str(item.get("status", "")).strip() in active_statuses
            and str(item.get("next_follow_up_at", "")).strip()
        ]
        won_revenue = sum(float(item.get("deal_value", 0.0) or 0.0) for item in items if str(item.get("status", "")).strip() == "won")
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "prepared_items": len([item for item in items if str(item.get("status", "")).strip() == "prepared"]),
                "sent_items": len([item for item in items if str(item.get("status", "")).strip() == "sent"]),
                "interested_items": len([item for item in items if str(item.get("status", "")).strip() == "interested"]),
                "call_booked_items": len([item for item in items if str(item.get("status", "")).strip() == "call_booked"]),
                "won_items": len([item for item in items if str(item.get("status", "")).strip() == "won"]),
                "lost_items": len([item for item in items if str(item.get("status", "")).strip() == "lost"]),
                "follow_up_due_items": len(follow_up_due),
                "won_revenue": round(won_revenue, 2),
            },
            "items": [
                {
                    "action_key": str(item.get("action_key", "")).strip(),
                    "target_name": str(item.get("target_name", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "channel": str(item.get("channel", "")).strip(),
                    "message_subject": str(item.get("message_subject", "")).strip(),
                    "next_follow_up_at": str(item.get("next_follow_up_at", "")).strip(),
                    "deal_value": float(item.get("deal_value", 0.0) or 0.0),
                    "notes": str(item.get("notes", "")).strip(),
                }
                for item in items[:50]
            ],
        }

    def _build_sponsor_revenue_report(self) -> dict[str, Any]:
        sponsor_log = self._load_sponsor_outreach_log()
        items = [item for item in sponsor_log if isinstance(item, dict)]
        won_items = [item for item in items if str(item.get("status", "")).strip() == "won"]
        active_items = [
            item for item in items if str(item.get("status", "")).strip() in {"prepared", "sent", "interested", "call_booked"}
        ]
        won_revenue = sum(float(item.get("deal_value", 0.0) or 0.0) for item in won_items)
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "active_sponsor_conversations": len(active_items),
                "won_sponsors": len(won_items),
                "average_sponsor_deal_value": round((won_revenue / len(won_items)), 2) if won_items else 0.0,
            },
            "revenue": {
                "won_sponsor_revenue": round(won_revenue, 2),
            },
            "won_sponsor_items": [
                {
                    "action_key": str(item.get("action_key", "")).strip(),
                    "target_name": str(item.get("target_name", "")).strip(),
                    "deal_value": float(item.get("deal_value", 0.0) or 0.0),
                    "won_at": str(item.get("won_at", "")).strip(),
                }
                for item in won_items[:20]
            ],
        }

    def _build_membership_pipeline(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        items = [item for item in ledger if isinstance(item, dict)]
        follow_up_due = [
            item
            for item in items
            if str(item.get("status", "")).strip() in self.ACTIVE_MEMBER_STATUSES
            and str(item.get("next_follow_up_at", "")).strip()
        ]
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "new_subscribers": len([item for item in items if str(item.get("status", "")).strip() == "new_subscriber"]),
                "free_sample_sent": len([item for item in items if str(item.get("status", "")).strip() == "free_sample_sent"]),
                "upgrade_prompt_sent": len([item for item in items if str(item.get("status", "")).strip() == "upgrade_prompt_sent"]),
                "member_interested": len([item for item in items if str(item.get("status", "")).strip() == "member_interested"]),
                "member_won": len([item for item in items if str(item.get("status", "")).strip() == "member_won"]),
                "renewal_due": len([item for item in items if str(item.get("status", "")).strip() == "renewal_due"]),
                "renewed": len([item for item in items if str(item.get("status", "")).strip() == "renewed"]),
                "follow_up_due_items": len(follow_up_due),
            },
            "items": [
                {
                    "subscriber_key": str(item.get("subscriber_key", "")).strip(),
                    "subscriber_name": str(item.get("subscriber_name", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "channel": str(item.get("channel", "")).strip(),
                    "contact": str(item.get("contact", "")).strip(),
                    "next_follow_up_at": str(item.get("next_follow_up_at", "")).strip(),
                    "member_revenue": float(item.get("member_revenue", 0.0) or 0.0),
                    "renewal_revenue": float(item.get("renewal_revenue", 0.0) or 0.0),
                    "notes": str(item.get("notes", "")).strip(),
                }
                for item in items[:80]
            ],
        }

    def _build_letter_revenue_report(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        items = [item for item in ledger if isinstance(item, dict)]
        member_revenue = sum(float(item.get("member_revenue", 0.0) or 0.0) for item in items)
        renewal_revenue = sum(float(item.get("renewal_revenue", 0.0) or 0.0) for item in items)
        active_subscribers = len(
            [
                item
                for item in items
                if str(item.get("status", "")).strip()
                in {"new_subscriber", "free_sample_sent", "upgrade_prompt_sent", "member_interested", "member_won", "renewal_due", "renewed"}
            ]
        )
        paying_members = len(
            [item for item in items if str(item.get("status", "")).strip() in {"member_won", "renewed"}]
        )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "active_subscribers": active_subscribers,
                "paying_members": paying_members,
                "free_to_paid_conversion_rate": round((paying_members / active_subscribers), 4) if active_subscribers else 0.0,
                "renewal_due_members": len([item for item in items if str(item.get("status", "")).strip() == "renewal_due"]),
            },
            "revenue": {
                "member_revenue": round(member_revenue, 2),
                "renewal_revenue": round(renewal_revenue, 2),
                "total_letter_revenue": round(member_revenue + renewal_revenue, 2),
            },
            "next_actions": [
                "Send free samples to new subscribers.",
                "Prompt engaged subscribers to upgrade into paying members.",
                "Renew paying members before the current cycle expires.",
            ],
        }

    def _ensure_newsletter_subscriber_ledger(
        self,
        *,
        leads: list[dict[str, Any]],
        signal_report: dict[str, Any],
    ) -> list[dict[str, Any]]:
        current = self._load_newsletter_subscriber_ledger()
        index = {
            str(item.get("source_lead_id", "")).strip(): item
            for item in current
            if isinstance(item, dict) and str(item.get("source_lead_id", "")).strip()
        }
        sample_topic = ""
        top_topics = signal_report.get("top_topics", []) if isinstance(signal_report, dict) else []
        if top_topics and isinstance(top_topics[0], dict):
            sample_topic = str(top_topics[0].get("title", "")).strip()

        for lead in leads:
            if not isinstance(lead, dict):
                continue
            lead_id = str(lead.get("id", "")).strip()
            if not lead_id:
                continue
            subscriber = index.get(lead_id)
            if subscriber is None:
                subscriber = self._build_subscriber_from_lead(lead, sample_topic=sample_topic)
                current.insert(0, subscriber)
                index[lead_id] = subscriber
            else:
                subscriber["subscriber_name"] = str(lead.get("name", "")).strip() or str(subscriber.get("subscriber_name", "")).strip()
                subscriber["contact"] = str(lead.get("contact", "")).strip() or str(subscriber.get("contact", "")).strip()
                subscriber["updated_at"] = datetime.now().isoformat(timespec="seconds")
                if sample_topic and not str(subscriber.get("sample_topic", "")).strip():
                    subscriber["sample_topic"] = sample_topic

        self.newsletter_subscriber_ledger_path.write_text(
            json.dumps(current[:500], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return current[:500]

    def _build_subscriber_from_lead(self, lead: dict[str, Any], *, sample_topic: str) -> dict[str, Any]:
        lead_status = str(lead.get("status", "")).strip().lower()
        subscriber_status = "new_subscriber"
        if lead_status == "won":
            subscriber_status = "renewal_due"
        elif lead_status == "contacted":
            subscriber_status = "free_sample_sent"
        elif lead_status == "qualified":
            subscriber_status = "upgrade_prompt_sent"
        created_at = datetime.now().isoformat(timespec="seconds")
        payload = {
            "subscriber_key": f"subscriber::{str(lead.get('id', '')).strip()}",
            "source_lead_id": str(lead.get("id", "")).strip(),
            "subscriber_name": str(lead.get("name", "")).strip(),
            "contact": str(lead.get("contact", "")).strip(),
            "channel": self._infer_contact_channel(str(lead.get("contact", "")).strip()),
            "status": subscriber_status,
            "origin": str(lead.get("source", "")).strip() or "lead_ops",
            "sample_topic": sample_topic,
            "newsletter_product": self._newsletter_product_name(),
            "free_offer": self._newsletter_free_cta(),
            "paid_offer": self._newsletter_paid_cta(),
            "created_at": created_at,
            "updated_at": created_at,
            "free_sample_sent_at": created_at if subscriber_status in {"free_sample_sent", "upgrade_prompt_sent", "renewal_due"} else "",
            "upgrade_prompt_sent_at": created_at if subscriber_status in {"upgrade_prompt_sent", "renewal_due"} else "",
            "member_interested_at": "",
            "member_won_at": "",
            "renewal_due_at": self._default_letter_follow_up_slot(days=30) if subscriber_status == "renewal_due" else "",
            "renewed_at": "",
            "member_revenue": self._default_member_revenue(lead) if subscriber_status == "renewal_due" else 0.0,
            "renewal_revenue": 0.0,
            "next_follow_up_at": self._default_letter_follow_up_slot(days=3),
            "notes": f"Auto-synced from lead status={lead_status or 'new'}",
        }
        return payload

    def _load_newsletter_subscriber_ledger(self) -> list[dict[str, Any]]:
        payload = self._load_json_if_exists(self.newsletter_subscriber_ledger_path)
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _build_profit_action_pack(
        self,
        opportunity_queue: dict[str, Any],
        sales_dashboard: dict[str, Any],
        subscriber_ledger: list[dict[str, Any]],
    ) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        sales_items = sales_dashboard.get("items", []) if isinstance(sales_dashboard, dict) else []
        queue_items = opportunity_queue.get("items", []) if isinstance(opportunity_queue, dict) else []
        closing_dispatch_payload = self._load_json_if_exists(self.closing_dispatch_path) or {}
        closing_items = closing_dispatch_payload.get("items", []) if isinstance(closing_dispatch_payload, dict) else []

        sponsor_targets_payload = self._load_json_if_exists(self.sponsor_targets_path) or {}
        sponsor_archetypes = (
            sponsor_targets_payload.get("archetypes", [])
            if isinstance(sponsor_targets_payload, dict)
            else []
        )

        for item in sales_items:
            if not isinstance(item, dict):
                continue
            action_type = str(item.get("action_type", "")).strip()
            if action_type == "send_referral_intro":
                lead_name = str(item.get("lead_name", "")).strip()
                actions.append(
                    {
                        "action_key": f"send_referral_intro::{str(item.get('lead_id', '')).strip()}",
                        "action_type": action_type,
                        "priority": str(item.get("priority", "")).strip(),
                        "lead_id": str(item.get("lead_id", "")).strip(),
                        "target_name": lead_name,
                        "channel": str(item.get("preferred_channel", "")).strip() or "wechat",
                        "message": (
                            f"你好，我这边是做产业内容增长和研究服务的。"
                            f"这次是通过老客户介绍认识你，想先和你交流一下 {lead_name} 相关的内容增长或研究需求。"
                            "如果你方便，我可以先发你一个非常轻量的思路框架。"
                        ),
                        "follow_up": "如果 24 小时未回复，补发一句“这边先给你一个轻量框架，要不要我直接发你看？”",
                    }
                )
            elif action_type == "send_expansion_proposal":
                proposal_path = str(item.get("proposal_path", "")).strip()
                actions.append(
                    {
                        "action_key": f"send_expansion_proposal::{str(item.get('lead_id', '')).strip()}",
                        "action_type": action_type,
                        "priority": str(item.get("priority", "")).strip(),
                        "lead_id": str(item.get("lead_id", "")).strip(),
                        "target_name": str(item.get("lead_name", "")).strip(),
                        "channel": str(item.get("preferred_channel", "")).strip() or "email",
                        "message": (
                            "这边我已经把下一阶段的续约 / 升级方案整理好了，核心是把当前成果继续放大，"
                            "并把更稳定的客户线索和行业影响力沉淀下来。"
                            "如果你方便，我现在把 proposal 发你，你看完我们再约 15 分钟快速确认。"
                        ),
                        "proposal_path": proposal_path,
                        "follow_up": "发送后 24 小时跟进一句：先看你是否方便，本周我可以按你的反馈再调一次范围和节奏。",
                    }
                )

        for index, item in enumerate(queue_items):
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip() != "sponsor_pitch":
                continue
            archetype = sponsor_archetypes[index % len(sponsor_archetypes)] if sponsor_archetypes else {}
            package_name = str(archetype.get("package_name", "")).strip() or "周报赞助 + 深度共创"
            opening = str(archetype.get("ideal_opening", "")).strip() or str(item.get("recommended_action", "")).strip()
            actions.append(
                {
                    "action_key": f"send_sponsor_outreach::{str(archetype.get('archetype_key', '')).strip() or index}",
                    "action_type": "send_sponsor_outreach",
                    "priority": str(item.get("priority", "")).strip(),
                    "sponsor_archetype_key": str(archetype.get("archetype_key", "")).strip(),
                    "target_name": str(archetype.get("archetype_name", "")).strip() or str(item.get("headline", "")).strip(),
                    "channel": "email",
                    "message_subject": f"一个适合 {package_name} 的合作切入口",
                    "message": (
                        f"{opening}\n\n"
                        f"我们建议先从「{package_name}」这种轻量合作切入，"
                        "先验证线索质量和读者匹配度，再决定是否放大合作。"
                    ),
                    "follow_up": "48 小时未回复，补发一句“如果这类合作方向对你们今年有效，我可以再补一个更具体的执行样例。”",
                }
            )
            if len([entry for entry in actions if entry.get("action_type") == "send_sponsor_outreach"]) >= 3:
                break

        for item in subscriber_ledger[:12]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            subscriber_key = str(item.get("subscriber_key", "")).strip()
            target_name = str(item.get("subscriber_name", "")).strip() or str(item.get("contact", "")).strip()
            if status == "new_subscriber":
                actions.append(
                    {
                        "action_key": f"send_free_sample::{subscriber_key}",
                        "action_type": "send_free_sample",
                        "priority": "medium",
                        "subscriber_key": subscriber_key,
                        "target_name": target_name,
                        "channel": str(item.get("channel", "")).strip() or "wechat",
                        "message_subject": f"{self._newsletter_product_name()} sample issue",
                        "message": (
                            f"先把这期 {self._newsletter_product_name()} 的样刊发你。"
                            "你可以直接看我会怎么把热点、客户名单和合作信号整理成可执行情报。"
                        ),
                        "follow_up": "24 小时后跟进对方最感兴趣的主题，并顺势引导升级到完整会员版。",
                    }
                )
            elif status in {"free_sample_sent", "upgrade_prompt_sent", "member_interested"}:
                actions.append(
                    {
                        "action_key": f"send_upgrade_prompt::{subscriber_key}",
                        "action_type": "send_upgrade_prompt",
                        "priority": "high" if status == "member_interested" else "medium",
                        "subscriber_key": subscriber_key,
                        "target_name": target_name,
                        "channel": str(item.get("channel", "")).strip() or "wechat",
                        "message_subject": f"Upgrade to paid {self._newsletter_product_name()} membership",
                        "message": (
                            "如果你希望不只是看热点，而是直接拿到完整客户名单、赞助监测和下周动作清单，"
                            "我建议升级到会员版。这样每周都能直接拿到可执行的商业化情报。"
                        ),
                        "follow_up": "如果对方回复感兴趣，立刻确认预算和周期，推进 member_won。",
                    }
                )
            elif status == "renewal_due":
                actions.append(
                    {
                        "action_key": f"send_membership_renewal::{subscriber_key}",
                        "action_type": "send_membership_renewal",
                        "priority": "high",
                        "subscriber_key": subscriber_key,
                        "target_name": target_name,
                        "channel": str(item.get("channel", "")).strip() or "wechat",
                        "message_subject": f"Renew your {self._newsletter_product_name()} membership",
                        "message": (
                            "你当前这轮会员周期快到了。"
                            "如果你想继续稳定拿到完整情报、客户名单和赞助合作监测，这周可以直接续费延续。"
                        ),
                        "follow_up": "48 小时内未回复则补发一条：是否需要我先把下一期重点样例发你确认？",
                    }
                )

        for item in closing_items[:10]:
            if not isinstance(item, dict):
                continue
            pipeline_type = str(item.get("pipeline_type", "")).strip()
            queue_key = str(item.get("queue_key", "")).strip()
            target_name = str(item.get("headline", "")).strip()
            recommended_package = str(item.get("recommended_package_name", "")).strip()
            closing_wedge = str(item.get("closing_wedge", "")).strip()
            target_value = float(item.get("target_value", 0.0) or 0.0)
            if pipeline_type == "service" and target_value >= 1999.0:
                lead_id = queue_key.split("closing::lead::", 1)[-1]
                actions.append(
                    {
                        "action_key": f"send_service_closing::{lead_id}",
                        "action_type": "send_service_closing",
                        "priority": str(item.get("priority", "")).strip(),
                        "lead_id": lead_id,
                        "target_name": target_name,
                        "channel": "wechat",
                        "message_subject": f"{recommended_package} closing note",
                        "message": (
                            f"我建议我们直接按 {recommended_package} 这个最合适的方案推进。"
                            f"预算锚点可以先参考 {target_value:.0f} 起。{closing_wedge}"
                        ),
                        "follow_up": "24 小时未回复则补一句：如果你愿意，我今天就把最终版本方案发你确认。",
                    }
                )
            elif pipeline_type == "sponsor" and target_value >= 3000.0:
                actions.append(
                    {
                        "action_key": f"send_sponsor_closing::{queue_key}",
                        "action_type": "send_sponsor_closing",
                        "priority": str(item.get("priority", "")).strip(),
                        "closing_queue_key": queue_key,
                        "target_name": target_name,
                        "channel": "email",
                        "message_subject": f"{recommended_package} sponsor close",
                        "message": (
                            f"如果你们希望先小步验证合作效果，我建议就从 {recommended_package} 开始。"
                            f"这样既能控制试错成本，也能最快验证 audience fit 和线索质量。{closing_wedge}"
                        ),
                        "follow_up": "48 小时未回复则补发执行样例和最小合作包。",
                    }
                )
            elif pipeline_type == "letter" and target_value >= 199.0:
                subscriber_key = queue_key.split("closing::letter::", 1)[-1]
                actions.append(
                    {
                        "action_key": f"send_letter_closing::{subscriber_key}",
                        "action_type": "send_letter_closing",
                        "priority": str(item.get("priority", "")).strip(),
                        "subscriber_key": subscriber_key,
                        "target_name": target_name,
                        "channel": "wechat",
                        "message_subject": f"{recommended_package} membership close",
                        "message": (
                            f"如果你希望下周开始直接拿到完整情报和动作清单，我建议就直接上 {recommended_package}。"
                            f"{closing_wedge}"
                        ),
                        "follow_up": "24 小时后补发一条：要不要我先把下一期完整版本的目录发你看一下？",
                    }
                )

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_actions": len(actions),
                "referral_intro_actions": len([item for item in actions if item.get("action_type") == "send_referral_intro"]),
                "expansion_proposal_actions": len([item for item in actions if item.get("action_type") == "send_expansion_proposal"]),
                "sponsor_outreach_actions": len([item for item in actions if item.get("action_type") == "send_sponsor_outreach"]),
                "free_sample_actions": len([item for item in actions if item.get("action_type") == "send_free_sample"]),
                "upgrade_prompt_actions": len([item for item in actions if item.get("action_type") == "send_upgrade_prompt"]),
                "membership_renewal_actions": len([item for item in actions if item.get("action_type") == "send_membership_renewal"]),
                "service_closing_actions": len([item for item in actions if item.get("action_type") == "send_service_closing"]),
                "sponsor_closing_actions": len([item for item in actions if item.get("action_type") == "send_sponsor_closing"]),
                "letter_closing_actions": len([item for item in actions if item.get("action_type") == "send_letter_closing"]),
            },
            "items": actions,
        }

    def _execute_profit_action(
        self,
        action: dict[str, Any],
        sponsor_log: list[dict[str, Any]],
        subscriber_ledger: list[dict[str, Any]],
        closing_action_log: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action_type = str(action.get("action_type", "")).strip()
        if action_type == "send_referral_intro":
            lead_id = str(action.get("lead_id", "")).strip()
            lead = self._find_lead_by_id(lead_id)
            if lead is None:
                return {"status": "skipped", "reason": "lead_not_found", "action_type": action_type, "lead_id": lead_id}
            if str(lead.get("outreach_sent_at", "")).strip():
                return {"status": "skipped", "reason": "already_contacted", "action_type": action_type, "lead_id": lead_id}
            updated = self.lead_ops_service.mark_outreach_sent(
                lead_id=lead_id,
                channel=str(action.get("channel", "")).strip(),
                message=str(action.get("message", "")).strip(),
            )
            return {
                "status": "executed",
                "action_type": action_type,
                "lead_id": lead_id,
                "target_name": str(updated.get("name", "")).strip(),
                "outreach_sent_at": str(updated.get("outreach_sent_at", "")).strip(),
            }

        if action_type == "send_expansion_proposal":
            lead_id = str(action.get("lead_id", "")).strip()
            lead = self._find_lead_by_id(lead_id)
            if lead is None:
                return {"status": "skipped", "reason": "lead_not_found", "action_type": action_type, "lead_id": lead_id}
            if str(lead.get("expansion_proposal_sent_at", "")).strip():
                return {"status": "skipped", "reason": "already_sent", "action_type": action_type, "lead_id": lead_id}
            updated = self.lead_ops_service.mark_proposal_sent(
                lead_id=lead_id,
                channel=str(action.get("channel", "")).strip(),
                message=str(action.get("message", "")).strip(),
            )
            return {
                "status": "executed",
                "action_type": action_type,
                "lead_id": lead_id,
                "target_name": str(updated.get("name", "")).strip(),
                "expansion_proposal_sent_at": str(updated.get("expansion_proposal_sent_at", "")).strip(),
            }

        if action_type == "send_sponsor_outreach":
            action_key = str(action.get("action_key", "")).strip()
            if any(str(item.get("action_key", "")).strip() == action_key for item in sponsor_log):
                return {"status": "skipped", "reason": "already_logged", "action_type": action_type, "action_key": action_key}
            sponsor_log.insert(
                0,
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "account_key": self.settings.account_key,
                    "action_key": action_key,
                    "action_type": action_type,
                    "target_name": str(action.get("target_name", "")).strip(),
                    "channel": str(action.get("channel", "")).strip(),
                    "message_subject": str(action.get("message_subject", "")).strip(),
                    "message": str(action.get("message", "")).strip(),
                    "follow_up": str(action.get("follow_up", "")).strip(),
                    "status": "prepared",
                    "next_follow_up_at": "",
                },
            )
            return {
                "status": "prepared",
                "action_type": action_type,
                "action_key": action_key,
                "target_name": str(action.get("target_name", "")).strip(),
            }

        if action_type in {"send_free_sample", "send_upgrade_prompt", "send_membership_renewal"}:
            subscriber_key = str(action.get("subscriber_key", "")).strip()
            subscriber = self._find_subscriber_by_key(subscriber_ledger, subscriber_key)
            if subscriber is None:
                return {"status": "skipped", "reason": "subscriber_not_found", "action_type": action_type, "subscriber_key": subscriber_key}
            now = datetime.now().isoformat(timespec="seconds")
            if action_type == "send_free_sample":
                if str(subscriber.get("free_sample_sent_at", "")).strip():
                    return {"status": "skipped", "reason": "already_sent", "action_type": action_type, "subscriber_key": subscriber_key}
                subscriber["status"] = "free_sample_sent"
                subscriber["free_sample_sent_at"] = now
                subscriber["next_follow_up_at"] = self._default_letter_follow_up_slot(days=2)
            elif action_type == "send_upgrade_prompt":
                subscriber["status"] = "upgrade_prompt_sent"
                subscriber["upgrade_prompt_sent_at"] = now
                subscriber["next_follow_up_at"] = self._default_letter_follow_up_slot(days=3)
            else:
                subscriber["status"] = "renewal_due"
                subscriber["next_follow_up_at"] = self._default_letter_follow_up_slot(days=2)
                subscriber["renewal_due_at"] = subscriber.get("renewal_due_at") or self._default_letter_follow_up_slot(days=30)
            subscriber["updated_at"] = now
            subscriber["last_message_subject"] = str(action.get("message_subject", "")).strip()
            subscriber["last_message"] = str(action.get("message", "")).strip()
            return {
                "status": "executed",
                "action_type": action_type,
                "subscriber_key": subscriber_key,
                "target_name": str(subscriber.get("subscriber_name", "")).strip(),
                "subscriber_status": str(subscriber.get("status", "")).strip(),
            }

        if action_type == "send_service_closing":
            lead_id = str(action.get("lead_id", "")).strip()
            lead = self._find_lead_by_id(lead_id)
            if lead is None:
                return {"status": "skipped", "reason": "lead_not_found", "action_type": action_type, "lead_id": lead_id}
            lead["recommended_next_action"] = str(action.get("follow_up", "")).strip() or str(action.get("message", "")).strip()
            closing_action_log.insert(
                0,
                {
                    "logged_at": datetime.now().isoformat(timespec="seconds"),
                    "queue_key": f"closing::lead::{lead_id}",
                    "action_type": action_type,
                    "target_name": str(lead.get("name", "")).strip(),
                    "status": "prepared",
                },
            )
            return {
                "status": "prepared",
                "action_type": action_type,
                "lead_id": lead_id,
                "target_name": str(lead.get("name", "")).strip(),
                "message_subject": str(action.get("message_subject", "")).strip(),
            }

        if action_type == "send_sponsor_closing":
            action_key = str(action.get("action_key", "")).strip()
            closing_queue_key = str(action.get("closing_queue_key", "")).strip()
            if any(str(item.get("action_key", "")).strip() == action_key for item in sponsor_log):
                return {"status": "skipped", "reason": "already_logged", "action_type": action_type, "action_key": action_key}
            sponsor_log.insert(
                0,
                {
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "account_key": self.settings.account_key,
                    "action_key": action_key,
                    "action_type": action_type,
                    "target_name": str(action.get("target_name", "")).strip(),
                    "channel": str(action.get("channel", "")).strip(),
                    "message_subject": str(action.get("message_subject", "")).strip(),
                    "message": str(action.get("message", "")).strip(),
                    "follow_up": str(action.get("follow_up", "")).strip(),
                    "status": "prepared",
                    "closing_queue_key": closing_queue_key,
                    "next_follow_up_at": self._default_sponsor_follow_up_slot(),
                },
            )
            closing_action_log.insert(
                0,
                {
                    "logged_at": datetime.now().isoformat(timespec="seconds"),
                    "queue_key": closing_queue_key,
                    "action_type": action_type,
                    "target_name": str(action.get("target_name", "")).strip(),
                    "status": "prepared",
                },
            )
            return {
                "status": "prepared",
                "action_type": action_type,
                "action_key": action_key,
                "target_name": str(action.get("target_name", "")).strip(),
            }

        if action_type == "send_letter_closing":
            subscriber_key = str(action.get("subscriber_key", "")).strip()
            subscriber = self._find_subscriber_by_key(subscriber_ledger, subscriber_key)
            if subscriber is None:
                return {"status": "skipped", "reason": "subscriber_not_found", "action_type": action_type, "subscriber_key": subscriber_key}
            now = datetime.now().isoformat(timespec="seconds")
            subscriber["status"] = "member_interested" if str(subscriber.get("status", "")).strip() != "renewal_due" else "renewal_due"
            subscriber["updated_at"] = now
            subscriber["last_message_subject"] = str(action.get("message_subject", "")).strip()
            subscriber["last_message"] = str(action.get("message", "")).strip()
            subscriber["next_follow_up_at"] = self._default_letter_follow_up_slot(days=2)
            closing_action_log.insert(
                0,
                {
                    "logged_at": now,
                    "queue_key": f"closing::letter::{subscriber_key}",
                    "action_type": action_type,
                    "target_name": str(subscriber.get("subscriber_name", "")).strip(),
                    "status": "prepared",
                },
            )
            return {
                "status": "prepared",
                "action_type": action_type,
                "subscriber_key": subscriber_key,
                "target_name": str(subscriber.get("subscriber_name", "")).strip(),
                "subscriber_status": str(subscriber.get("status", "")).strip(),
            }

        return {"status": "skipped", "reason": "unsupported_action_type", "action_type": action_type}

    def _find_lead_by_id(self, lead_id: str) -> dict[str, Any] | None:
        leads = self.lead_ops_service.list_leads(limit=500)
        normalized = str(lead_id).strip()
        for item in leads:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).strip() == normalized:
                return item
        return None

    def _find_subscriber_by_key(
        self,
        ledger: list[dict[str, Any]],
        subscriber_key: str,
    ) -> dict[str, Any] | None:
        normalized = str(subscriber_key).strip()
        for item in ledger:
            if not isinstance(item, dict):
                continue
            if str(item.get("subscriber_key", "")).strip() == normalized:
                return item
        return None

    def _load_closing_action_log(self) -> list[dict[str, Any]]:
        payload = self._load_json_if_exists(self.closing_action_log_path)
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _load_sponsor_outreach_log(self) -> list[dict[str, Any]]:
        payload = self._load_json_if_exists(self.sponsor_outreach_log_path)
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _default_sponsor_follow_up_slot(self) -> str:
        follow_up = datetime.now().replace(second=0, microsecond=0)
        follow_up = follow_up.replace(hour=11, minute=0) + timedelta(days=2)
        return follow_up.isoformat(sep=" ", timespec="minutes")

    def _default_letter_follow_up_slot(self, *, days: int) -> str:
        follow_up = datetime.now().replace(second=0, microsecond=0)
        follow_up = follow_up.replace(hour=10, minute=30) + timedelta(days=max(1, days))
        return follow_up.isoformat(sep=" ", timespec="minutes")

    def _newsletter_product_name(self) -> str:
        payload = self._load_growth_payload()
        newsletter = payload.get("newsletter", {}) if isinstance(payload, dict) else {}
        return str(newsletter.get("product_name", "")).strip() or "Industry Intelligence Weekly"

    def _newsletter_free_cta(self) -> str:
        payload = self._load_growth_payload()
        newsletter = payload.get("newsletter", {}) if isinstance(payload, dict) else {}
        return str(newsletter.get("free_call_to_action", "")).strip()

    def _newsletter_paid_cta(self) -> str:
        payload = self._load_growth_payload()
        newsletter = payload.get("newsletter", {}) if isinstance(payload, dict) else {}
        return str(newsletter.get("paid_call_to_action", "")).strip()

    def _infer_contact_channel(self, contact: str) -> str:
        normalized = str(contact).strip().lower()
        if normalized.startswith("wechat:"):
            return "wechat"
        if normalized.startswith("email:") or "@" in normalized:
            return "email"
        return "direct"

    def _default_member_revenue(self, payload: dict[str, Any]) -> float:
        estimated = float(payload.get("actual_value", 0.0) or 0.0)
        if estimated > 0.0:
            return estimated
        return 199.0

    def _infer_sponsor_deal_value(self, sponsor_item: dict[str, Any]) -> float:
        explicit = float(sponsor_item.get("deal_value", 0.0) or 0.0)
        if explicit > 0.0:
            return explicit
        subject = str(sponsor_item.get("message_subject", "")).strip()
        digits = "".join(ch if ch.isdigit() else " " for ch in subject).split()
        if digits:
            try:
                return float(digits[0])
            except ValueError:
                pass
        return 5000.0

    def _price_floor(self, price_text: str) -> float:
        digits = "".join(ch if ch.isdigit() else " " for ch in str(price_text)).split()
        if not digits:
            return 0.0
        try:
            return float(digits[0])
        except ValueError:
            return 0.0

    def _match_service_package(
        self,
        commercial_catalog: dict[str, Any],
        *,
        lead_name: str,
        proposal_path: str,
    ) -> dict[str, Any]:
        service_packages = commercial_catalog.get("service_packages", []) if isinstance(commercial_catalog, dict) else []
        proposal_lower = proposal_path.lower()
        lead_lower = lead_name.lower()
        for item in service_packages:
            if not isinstance(item, dict):
                continue
            key = str(item.get("package_key", "")).strip().lower()
            name = str(item.get("name", "")).strip().lower()
            if key and key in proposal_lower:
                return item
            if name and name in lead_lower:
                return item
        return service_packages[0] if service_packages else {}

    def _match_sponsor_package(
        self,
        commercial_catalog: dict[str, Any],
        *,
        target_name: str,
        message_subject: str,
    ) -> dict[str, Any]:
        sponsor_packages = commercial_catalog.get("sponsor_packages", []) if isinstance(commercial_catalog, dict) else []
        target_lower = target_name.lower()
        subject_lower = message_subject.lower()
        for item in sponsor_packages:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip().lower()
            customer = str(item.get("target_customer", "")).strip().lower()
            if customer and customer in target_lower:
                return item
            if name and name in subject_lower:
                return item
        return sponsor_packages[0] if sponsor_packages else {}

    def _match_letter_package(self, commercial_catalog: dict[str, Any], status: str) -> dict[str, Any]:
        letter_packages = commercial_catalog.get("letter_packages", []) if isinstance(commercial_catalog, dict) else []
        wanted_key = "letter_member"
        if status == "renewal_due":
            wanted_key = "letter_operator"
        for item in letter_packages:
            if not isinstance(item, dict):
                continue
            if str(item.get("package_key", "")).strip() == wanted_key:
                return item
        return letter_packages[0] if letter_packages else {}

    def _service_closing_wedge(self, sales_item: dict[str, Any], package: dict[str, Any]) -> str:
        package_name = str(package.get("name", "")).strip() or "this package"
        price = str(package.get("price", "")).strip()
        proposal_path = str(sales_item.get("proposal_path", "")).strip()
        if proposal_path:
            return f"Send the ready proposal for {package_name}, anchor the budget at {price}, and lock a 15-minute confirmation call."
        return f"Position {package_name} as the fastest low-risk next step, anchor the budget at {price}, and ask for a decision window this week."

    def _sponsor_closing_wedge(
        self,
        sponsor_item: dict[str, Any],
        sponsor_detail: dict[str, Any],
        package: dict[str, Any],
    ) -> str:
        package_name = str(package.get("name", "")).strip() or "this sponsor package"
        price = str(package.get("price", "")).strip()
        status = str(sponsor_item.get("status", "")).strip()
        if status == "call_booked":
            return f"Use the booked call to close {package_name}, confirm deliverables, and ask for a yes/no on the {price} package."
        if status == "interested":
            return f"Send the sponsor deck and sample assets, then ask whether they want to start with the {price} version of {package_name}."
        return f"Lead with the smallest version of {package_name}, tie it to audience fit, and anchor the package around {price}."

    def _letter_closing_wedge(self, subscriber: dict[str, Any], package: dict[str, Any]) -> str:
        package_name = str(package.get("name", "")).strip() or "member package"
        price = str(package.get("price", "")).strip()
        status = str(subscriber.get("status", "")).strip()
        if status == "renewal_due":
            return f"Remind them of last cycle value and ask for a renewal decision on {package_name} at {price}."
        if status == "member_interested":
            return f"Convert interest into payment by clarifying what {package_name} includes and anchoring the cycle at {price}."
        return f"Use the free sample to show tangible value, then invite them to upgrade into {package_name} at {price}."

    def _closing_dispatch_sort_key(self, item: dict[str, Any]) -> tuple[int, float, str]:
        priority_order = {"high": 0, "medium": 1, "low": 2}
        return (
            priority_order.get(str(item.get("priority", "")).strip().lower(), 3),
            -float(item.get("target_value", 0.0) or 0.0),
            str(item.get("headline", "")).strip(),
        )

    def _closing_follow_up_text(self, item: dict[str, Any]) -> str:
        pipeline_type = str(item.get("pipeline_type", "")).strip()
        package_name = str(item.get("recommended_package_name", "")).strip()
        if pipeline_type == "service":
            return f"Follow up on {package_name}, ask for a yes/no decision, and offer one last scope clarification."
        if pipeline_type == "sponsor":
            return f"Follow up on {package_name}, resend sample assets, and ask whether they want to start with the smallest package."
        return f"Follow up on {package_name}, remind them what they unlock next cycle, and ask if they want the full version."

    def _letter_next_action(self, subscriber: dict[str, Any]) -> str:
        status = str(subscriber.get("status", "")).strip()
        if status == "new_subscriber":
            return "Send the free sample and ask which topic they want expanded."
        if status == "member_interested":
            return "Close the paid membership with a clear cycle, price, and deliverables."
        if status == "renewal_due":
            return "Push renewal before the current cycle expires and remind them of prior value."
        return "Move this subscriber from free readership into a paid member conversation."

    def _build_opportunity_queue_markdown(self, payload: dict[str, Any]) -> str:
        summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
        lines = [
            f"# {self.settings.brand_name} 本周商机队列",
            "",
            f"- 生成时间：{payload.get('generated_at', '')}",
            f"- 总机会数：{summary.get('total_items', 0)}",
            f"- 现有线索跟进：{summary.get('open_lead_items', 0)}",
            f"- 客户开发机会：{summary.get('customer_pitch_items', 0)}",
            f"- 赞助开发机会：{summary.get('sponsor_pitch_items', 0)}",
            "",
        ]
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            lines.append(f"## [{str(item.get('priority', '')).upper()}] {item.get('headline', '')}")
            lines.append("")
            lines.append(f"- 类型：{item.get('type', '')}")
            lines.append(f"- 原因：{item.get('why_now', '')}")
            lines.append(f"- 建议动作：{item.get('recommended_action', '')}")
            lines.append(f"- 关联资产：{item.get('asset_hint', '')}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_opportunity_sync_report(
        self,
        *,
        opportunity_queue: dict[str, Any],
        sync_candidates: list[dict[str, Any]],
        sync_result: dict[str, Any],
    ) -> dict[str, Any]:
        leads = self.lead_ops_service.list_leads(limit=500)
        auto_generated_leads = [
            item
            for item in leads
            if isinstance(item, dict) and bool(item.get("auto_generated")) and str(item.get("source", "")).strip() == "revenue_engine"
        ]
        open_auto_generated_leads = [
            item
            for item in auto_generated_leads
            if str(item.get("status", "")).strip() not in self.CLOSED_LEAD_STATUSES
        ]
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "queue_summary": opportunity_queue.get("summary", {}) if isinstance(opportunity_queue, dict) else {},
            "sync_policy": {
                "included_types": ["customer_pitch"],
                "excluded_types": ["lead_followup", "sponsor_pitch"],
            },
            "candidate_count": len(sync_candidates),
            "sync_result": sync_result,
            "auto_generated_leads": {
                "total": len(auto_generated_leads),
                "open": len(open_auto_generated_leads),
                "won": len([item for item in auto_generated_leads if str(item.get("status", "")).strip() == "won"]),
                "lost": len([item for item in auto_generated_leads if str(item.get("status", "")).strip() == "lost"]),
            },
            "top_candidates": [
                {
                    "id": str(item.get("id", "")).strip(),
                    "headline": str(item.get("headline", "")).strip(),
                    "priority": str(item.get("priority", "")).strip(),
                    "asset_hint": str(item.get("asset_hint", "")).strip(),
                }
                for item in sync_candidates[:5]
            ],
        }

    def _build_offer_index(self, payload: dict[str, Any]) -> dict[str, dict[str, str]]:
        offers = payload.get("core_offers", []) if isinstance(payload, dict) else []
        result: dict[str, dict[str, str]] = {}
        for item in offers:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            name = str(item.get("name", "")).strip()
            if not key and not name:
                continue
            resolved_key = key or self._slugify(name)
            result[resolved_key] = {
                "name": name,
                "price": str(item.get("price", "")).strip(),
                "delivery": str(item.get("delivery", "")).strip(),
            }
        return result

    def _pick_top_track(
        self,
        benchmark_report: dict[str, Any] | None,
        self_benchmark_report: dict[str, Any] | None,
    ) -> str:
        for payload in (self_benchmark_report, benchmark_report):
            if not payload:
                continue
            summary = payload.get("summary", {})
            if isinstance(summary, dict):
                distribution = summary.get("track_distribution", {})
                if isinstance(distribution, dict) and distribution:
                    return str(next(iter(distribution.keys()))).strip()
        return "行业热点解读"

    def _pick_top_patterns(self, self_benchmark_report: dict[str, Any] | None) -> list[str]:
        if not self_benchmark_report:
            return []
        summary = self_benchmark_report.get("summary", {})
        if not isinstance(summary, dict):
            return []
        patterns = summary.get("top_patterns", {})
        if not isinstance(patterns, dict):
            return []
        return [str(key).strip() for key in patterns.keys() if str(key).strip()][:5]

    def _pick_top_keywords(self, benchmark_report: dict[str, Any] | None) -> list[str]:
        if not benchmark_report:
            return []
        summary = benchmark_report.get("summary", {})
        if not isinstance(summary, dict):
            return []
        keywords = summary.get("top_keywords", {})
        if not isinstance(keywords, dict):
            return []
        return [str(key).strip() for key in keywords.keys() if str(key).strip()][:6]

    def _build_customer_opening(self, *, segment_name: str, offer_name: str, top_track: str) -> str:
        return (
            f"最近我在连续跟踪「{top_track}」这条线，看到很多 {segment_name} 都有内容有观点，"
            f"但还缺把流量稳定转成线索的动作。如果你愿意，我可以先用一版「{offer_name}」帮你把卡点梳理出来。"
        )

    def _build_sponsor_opening(self, *, archetype_name: str, package_name: str, top_track: str) -> str:
        return (
            f"我们最近在「{top_track}」赛道做连续内容更新，读者更偏从业者和决策者。"
            f"如果你们属于 {archetype_name}，比较适合先从「{package_name}」这种小合作包开始验证线索质量。"
        )

    def _infer_commercial_angle(self, topic: dict[str, Any]) -> str:
        matched_keywords = [str(item).strip() for item in topic.get("matched_keywords", []) if str(item).strip()]
        joined_keywords = " / ".join(matched_keywords[:3]) if matched_keywords else "行业热点"
        return f"可把「{joined_keywords}」包装成研究、诊断或赞助合作切入口。"

    def _lead_priority(self, status: str) -> str:
        normalized = str(status).strip().lower()
        if normalized == "proposal_sent":
            return "high"
        if normalized == "qualified":
            return "high"
        if normalized == "contacted":
            return "medium"
        return "medium"

    def _lead_next_action(self, lead: dict[str, Any]) -> str:
        status = str(lead.get("status", "")).strip().lower()
        if status == "proposal_sent":
            return "催确认 proposal，并明确预算、起始时间和交付范围。"
        if status == "qualified":
            return "尽快生成 proposal 或安排一次 15 分钟需求确认。"
        if status == "contacted":
            return "继续分诊，确认对方最想解决的是流量、线索还是成交。"
        return "补充背景信息，推进到 contacted / qualified。"

    def _opportunity_sort_key(self, item: dict[str, Any]) -> tuple[int, str]:
        priority_order = {"high": 0, "medium": 1, "low": 2}
        return (priority_order.get(str(item.get("priority", "")).strip().lower(), 3), str(item.get("headline", "")))

    def _build_opportunity_id(self, opportunity_type: str, primary_text: str, secondary_text: str = "") -> str:
        parts = [self._slugify(primary_text)]
        secondary_slug = self._slugify(secondary_text)
        if secondary_slug:
            parts.append(secondary_slug)
        return f"{opportunity_type}::{'::'.join(parts)}"

    def _count_open_leads(self) -> int:
        payload = self._load_json_if_exists(self.settings.output_dir / "automation" / "lead_ops" / "lead_ledger.json")
        if not isinstance(payload, list):
            return 0
        return sum(
            1
            for item in payload
            if isinstance(item, dict) and str(item.get("status", "")).strip() not in self.CLOSED_LEAD_STATUSES
        )

    def _load_growth_payload(self) -> dict[str, Any]:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(
                yaml.safe_dump(self.DEFAULT_CONFIG, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        try:
            payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.logger.warning("Failed to load growth engine config, using defaults: %s", exc)
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _load_revenue_payload(self) -> dict[str, Any]:
        if not self.revenue_config_path.exists():
            return {}
        try:
            payload = yaml.safe_load(self.revenue_config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.logger.warning("Failed to load revenue config for growth engine: %s", exc)
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _load_json_if_exists(self, path: Path) -> dict[str, Any] | list[Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(payload, (dict, list)):
            return payload
        return None

    def _slugify(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value))
        return cleaned.strip("_").lower() or "offer"
