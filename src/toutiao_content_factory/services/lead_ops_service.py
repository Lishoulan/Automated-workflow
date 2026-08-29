from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from toutiao_content_factory.config import AppSettings


class LeadOpsService:
    """Lightweight lead capture and tracking for the one-person-company workflow."""

    DEFAULT_CONFIG = {
        "version": 1,
        "updated_at": "2026-06-08",
        "sources": [],
        "statuses": ["new", "contacted", "qualified", "proposal_sent", "won", "lost"],
        "default_replies": {},
    }
    TERMINAL_STATUSES = {"won", "lost"}
    DEFAULT_PRICE = "待沟通"
    DEFAULT_DELIVERY_TIME = "待沟通"

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.config_path = settings.project_root / "config" / "company_leads.yaml"
        self.output_dir = settings.output_dir / "automation" / "lead_ops"
        self.ledger_path = self.output_dir / "lead_ledger.json"
        self.reply_manual_path = self.output_dir / "keyword_reply_manual.md"
        self.followup_queue_path = self.output_dir / "followup_queue.md"
        self.summary_path = self.output_dir / "lead_summary.json"
        self.proposal_catalog_path = self.output_dir / "proposal_catalog.md"
        self.offer_snapshot_path = self.output_dir / "offer_snapshot.json"
        self.outreach_queue_path = self.output_dir / "outreach_queue.md"
        self.outreach_sequences_path = self.output_dir / "outreach_sequences.json"
        self.outbound_campaign_brief_path = self.output_dir / "outbound_campaign_brief.md"
        self.outbound_campaign_pack_path = self.output_dir / "outbound_campaign_pack.json"
        self.reply_triage_playbook_path = self.output_dir / "reply_triage_playbook.md"
        self.reply_triage_pack_path = self.output_dir / "reply_triage_pack.json"
        self.conversion_push_pack_path = self.output_dir / "conversion_push_pack.json"
        self.outreach_execution_log_path = self.output_dir / "outreach_execution_log.json"
        self.sales_execution_dashboard_path = self.output_dir / "sales_execution_dashboard.json"
        self.sales_execution_brief_path = self.output_dir / "sales_execution_brief.md"
        self.wechat_batch_messages_path = self.output_dir / "wechat_batch_messages.md"
        self.email_batch_messages_path = self.output_dir / "email_batch_messages.md"
        self.platform_dm_batch_messages_path = self.output_dir / "platform_dm_batch_messages.md"
        self.customer_success_dashboard_path = self.output_dir / "customer_success_dashboard.json"
        self.customer_success_brief_path = self.output_dir / "customer_success_brief.md"
        self.expansion_pipeline_path = self.output_dir / "expansion_pipeline.json"
        self.referral_pipeline_brief_path = self.output_dir / "referral_pipeline_brief.md"
        self.proposals_dir = self.output_dir / "proposals"

    def ensure_assets(self) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = self._load_config()
        ledger = self._load_ledger()

        self.reply_manual_path.write_text(self._build_reply_manual(payload), encoding="utf-8")
        self.followup_queue_path.write_text(self._build_followup_queue(ledger), encoding="utf-8")
        self.outreach_queue_path.write_text(self._build_outreach_queue(ledger), encoding="utf-8")
        self.proposal_catalog_path.write_text(self._build_proposal_catalog(), encoding="utf-8")
        self.offer_snapshot_path.write_text(
            json.dumps(self._build_offer_snapshot(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.outreach_sequences_path.write_text(
            json.dumps(self._build_outreach_sequences(ledger), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.outbound_campaign_brief_path.write_text(
            self._build_outbound_campaign_brief(ledger),
            encoding="utf-8",
        )
        self.outbound_campaign_pack_path.write_text(
            json.dumps(self._build_outbound_campaign_pack(ledger), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.reply_triage_playbook_path.write_text(
            self._build_reply_triage_playbook(ledger),
            encoding="utf-8",
        )
        self.reply_triage_pack_path.write_text(
            json.dumps(self._build_reply_triage_pack(ledger), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.conversion_push_pack_path.write_text(
            json.dumps(self._build_conversion_push_pack(ledger), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.outreach_execution_log_path.write_text(
            json.dumps(self._load_outreach_execution_events(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        sales_execution_dashboard = self._build_sales_execution_dashboard(ledger)
        self.sales_execution_dashboard_path.write_text(
            json.dumps(sales_execution_dashboard, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.sales_execution_brief_path.write_text(
            self._build_sales_execution_brief(sales_execution_dashboard),
            encoding="utf-8",
        )
        self.wechat_batch_messages_path.write_text(
            self._build_channel_batch_markdown(ledger, channel_key="wechat"),
            encoding="utf-8",
        )
        self.email_batch_messages_path.write_text(
            self._build_channel_batch_markdown(ledger, channel_key="email"),
            encoding="utf-8",
        )
        self.platform_dm_batch_messages_path.write_text(
            self._build_channel_batch_markdown(ledger, channel_key="platform_dm"),
            encoding="utf-8",
        )
        customer_success_dashboard = self._build_customer_success_dashboard(ledger)
        self.customer_success_dashboard_path.write_text(
            json.dumps(customer_success_dashboard, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.customer_success_brief_path.write_text(
            self._build_customer_success_brief(customer_success_dashboard),
            encoding="utf-8",
        )
        expansion_pipeline = self._build_expansion_pipeline(ledger)
        self.expansion_pipeline_path.write_text(
            json.dumps(expansion_pipeline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.referral_pipeline_brief_path.write_text(
            self._build_expansion_pipeline_brief(expansion_pipeline),
            encoding="utf-8",
        )
        self.summary_path.write_text(
            json.dumps(self._build_summary(ledger), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "lead_reply_manual": self.reply_manual_path,
            "lead_followup_queue": self.followup_queue_path,
            "lead_outreach_queue": self.outreach_queue_path,
            "lead_summary": self.summary_path,
            "lead_ledger": self.ledger_path,
            "lead_config": self.config_path,
            "proposal_catalog": self.proposal_catalog_path,
            "offer_snapshot": self.offer_snapshot_path,
            "outreach_sequences": self.outreach_sequences_path,
            "outbound_campaign_brief": self.outbound_campaign_brief_path,
            "outbound_campaign_pack": self.outbound_campaign_pack_path,
            "reply_triage_playbook": self.reply_triage_playbook_path,
            "reply_triage_pack": self.reply_triage_pack_path,
            "conversion_push_pack": self.conversion_push_pack_path,
            "outreach_execution_log": self.outreach_execution_log_path,
            "sales_execution_dashboard": self.sales_execution_dashboard_path,
            "sales_execution_brief": self.sales_execution_brief_path,
            "wechat_batch_messages": self.wechat_batch_messages_path,
            "email_batch_messages": self.email_batch_messages_path,
            "platform_dm_batch_messages": self.platform_dm_batch_messages_path,
            "customer_success_dashboard": self.customer_success_dashboard_path,
            "customer_success_brief": self.customer_success_brief_path,
            "expansion_pipeline": self.expansion_pipeline_path,
            "referral_pipeline_brief": self.referral_pipeline_brief_path,
        }

    def generate_lead_proposal(
        self,
        *,
        lead_id: str,
        offer_name: str = "",
        offer_key: str = "",
        custom_price: str = "",
        custom_scope: str = "",
        next_follow_up_at: str = "",
        mark_as_sent: bool = True,
        auto_generated: bool = False,
    ) -> dict[str, Path | str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        ledger = self._load_ledger()
        lead = self._find_lead(ledger, lead_id)
        if lead is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        result = self._create_proposal_for_lead(
            ledger=ledger,
            lead=lead,
            offer_name=offer_name,
            offer_key=offer_key,
            custom_price=custom_price,
            custom_scope=custom_scope,
            next_follow_up_at=next_follow_up_at,
            mark_as_sent=mark_as_sent,
            auto_generated=auto_generated,
        )
        self._save_ledger(ledger)
        self.ensure_assets()

        return result

    def record_lead(
        self,
        *,
        name: str,
        contact: str,
        intent: str,
        source: str,
        notes: str = "",
        status: str = "new",
        estimated_value: float = 0.0,
        next_follow_up_at: str = "",
        source_content_id: str = "",
        cta_source_code: str = "",
        platform: str = "",
    ) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = self._load_config()
        valid_statuses = {str(item).strip() for item in payload.get("statuses", []) if str(item).strip()}
        final_status = status if status in valid_statuses else "new"

        ledger = self._load_ledger()
        now = datetime.now().isoformat(timespec="seconds")
        lead_id = self._generate_lead_id()
        attribution = self._resolve_lead_attribution(
            source_content_id=source_content_id,
            cta_source_code=cta_source_code,
            source=source,
            platform=platform,
        )
        contact_value = str(contact).strip()
        entry = {
            "id": lead_id,
            "created_at": now,
            "account_key": self.settings.account_key,
            "name": str(name).strip(),
            "contact": contact_value,
            "contact_verified": not contact_value.lower().startswith("auto://"),
            "lead_kind": "real_lead" if not contact_value.lower().startswith("auto://") else "auto_opportunity",
            "opportunity_kind": "real_lead" if not contact_value.lower().startswith("auto://") else "auto_opportunity",
            "intent": str(intent).strip(),
            "source": str(source).strip(),
            "source_content_id": attribution["content_id"],
            "cta_source_code": attribution["cta_source_code"],
            "attribution_status": attribution["status"],
            "attribution_platform": str(platform).strip().lower(),
            "status": final_status,
            "notes": str(notes).strip(),
            "estimated_value": float(estimated_value or 0.0),
            "actual_value": 0.0,
            "proposal_sent_at": "",
            "proposal_offer_key": "",
            "proposal_offer_name": "",
            "proposal_path": "",
            "won_at": "",
            "lost_at": "",
            "next_follow_up_at": str(next_follow_up_at).strip(),
            "last_action_at": now,
        }
        self._apply_status_side_effects(entry, final_status)
        ledger.insert(0, entry)
        self._save_ledger(ledger)
        self._sync_lead_revenue(entry)
        self.ensure_assets()
        return entry

    def update_lead(
        self,
        *,
        lead_id: str,
        status: str | None = None,
        notes: str | None = None,
        estimated_value: float | None = None,
        actual_value: float | None = None,
        next_follow_up_at: str | None = None,
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        payload = self._load_config()
        valid_statuses = {str(item).strip() for item in payload.get("statuses", []) if str(item).strip()}
        if status is not None and status in valid_statuses:
            target["status"] = status
            self._apply_status_side_effects(target, status)
        if notes is not None:
            target["notes"] = str(notes).strip()
        if estimated_value is not None:
            target["estimated_value"] = float(estimated_value or 0.0)
        if actual_value is not None:
            target["actual_value"] = float(actual_value or 0.0)
            if float(actual_value or 0.0) > 0 and str(target.get("status", "")).strip() != "won":
                target["status"] = "won"
            if float(actual_value or 0.0) > 0:
                self._apply_status_side_effects(target, "won")
        if next_follow_up_at is not None:
            target["next_follow_up_at"] = str(next_follow_up_at).strip()
        target["last_action_at"] = datetime.now().isoformat(timespec="seconds")

        self._save_ledger(ledger)
        self._sync_lead_revenue(target)
        self.ensure_assets()
        return target

    def mark_outreach_sent(
        self,
        *,
        lead_id: str,
        channel: str = "",
        message: str = "",
        next_follow_up_at: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        now = datetime.now().isoformat(timespec="seconds")
        normalized_channel = str(channel).strip()
        normalized_message = str(message).strip()
        follow_up_value = str(next_follow_up_at).strip()

        if normalized_channel:
            target["last_outreach_channel"] = normalized_channel
            if not str(target.get("preferred_outreach_channel", "")).strip():
                target["preferred_outreach_channel"] = normalized_channel
        if normalized_message:
            target["last_outreach_message"] = normalized_message

        target["outreach_sent_at"] = now
        target["last_contacted_at"] = now
        target["last_action_at"] = now
        target["recommended_next_action"] = "等待客户回复；如 24-48 小时未回，发送一次简短跟进。"

        current_status = str(target.get("status", "")).strip() or "new"
        if current_status == "new":
            target["status"] = "contacted"

        if follow_up_value:
            target["next_follow_up_at"] = follow_up_value
        elif not str(target.get("next_follow_up_at", "")).strip():
            target["next_follow_up_at"] = self._build_follow_up_slot(days=2, hour=10)

        note_bits = ["[OUTREACH]"]
        if normalized_channel:
            note_bits.append(f"channel={normalized_channel}")
        if normalized_message:
            note_bits.append(f"message={normalized_message}")
        target["notes"] = self._append_note_line(target.get("notes", ""), " | ".join(note_bits))

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "outreach_sent",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "channel": normalized_channel,
                "message": normalized_message,
                "status_after": str(target.get("status", "")).strip(),
                "next_follow_up_at": str(target.get("next_follow_up_at", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def triage_reply(
        self,
        *,
        lead_id: str,
        reply_text: str,
        next_follow_up_at: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        normalized_reply = str(reply_text).strip()
        if not normalized_reply:
            raise ValueError("Reply text is required for triage.")

        now = datetime.now().isoformat(timespec="seconds")
        reply_temperature = self._classify_reply_temperature(normalized_reply)
        suggested_reply = self._build_triage_reply_script(target, reply_temperature)
        recommended_next_action = self._build_triage_next_action(target, reply_temperature)

        target["last_reply_at"] = now
        target["last_reply_text"] = normalized_reply
        target["reply_temperature"] = reply_temperature
        target["reply_triaged_at"] = now
        target["next_reply_script"] = suggested_reply
        target["recommended_next_action"] = recommended_next_action
        target["last_action_at"] = now

        current_status = str(target.get("status", "")).strip() or "new"
        if current_status not in self.TERMINAL_STATUSES:
            if reply_temperature == "hot":
                if current_status in {"new", "contacted"}:
                    target["status"] = "qualified"
            elif reply_temperature == "warm":
                if current_status == "new":
                    target["status"] = "qualified"
                elif current_status == "contacted":
                    target["status"] = "qualified"
            elif reply_temperature == "cold" and current_status == "new":
                target["status"] = "contacted"

        follow_up_value = str(next_follow_up_at).strip()
        if follow_up_value:
            target["next_follow_up_at"] = follow_up_value
        else:
            target["next_follow_up_at"] = self._default_follow_up_for_reply_temperature(reply_temperature)

        target["notes"] = self._append_note_line(
            target.get("notes", ""),
            f"[REPLY:{reply_temperature.upper()}] {normalized_reply}",
        )

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "reply_triaged",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "reply_temperature": reply_temperature,
                "reply_text": normalized_reply,
                "status_after": str(target.get("status", "")).strip(),
                "recommended_next_action": recommended_next_action,
                "next_follow_up_at": str(target.get("next_follow_up_at", "")).strip(),
            }
        )
        self.ensure_assets()
        return {
            "lead_id": str(target.get("id", "")).strip(),
            "reply_temperature": reply_temperature,
            "recommended_status": str(target.get("status", "")).strip(),
            "suggested_reply": suggested_reply,
            "recommended_next_action": recommended_next_action,
            "proposal_path": str(target.get("proposal_path", "")).strip(),
            "next_follow_up_at": str(target.get("next_follow_up_at", "")).strip(),
        }

    def mark_proposal_sent(
        self,
        *,
        lead_id: str,
        channel: str = "",
        message: str = "",
        next_follow_up_at: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")
        if not str(target.get("proposal_path", "")).strip():
            raise ValueError("Proposal is not ready for this lead.")

        now = datetime.now().isoformat(timespec="seconds")
        normalized_channel = str(channel).strip()
        normalized_message = str(message).strip()
        follow_up_value = str(next_follow_up_at).strip()
        current_status = str(target.get("status", "")).strip() or "new"
        is_expansion_motion = bool(target.get("proposal_auto_generated")) and bool(
            str(target.get("expansion_opportunity_type", "")).strip()
        )

        if current_status not in self.TERMINAL_STATUSES:
            target["status"] = "proposal_sent"
            target["proposal_sent_at"] = now
        elif is_expansion_motion:
            # An expansion proposal is a new commercial touch even when the
            # original service lead is already won.
            target["proposal_sent_at"] = now
        if current_status == "won" and is_expansion_motion:
            target["expansion_proposal_sent_at"] = now
        target["last_action_at"] = now
        target["last_contacted_at"] = now
        target["recommended_next_action"] = "等待对方确认 proposal；优先推进 15 分钟通话或预算确认。"
        if normalized_channel:
            target["last_outreach_channel"] = normalized_channel
        if normalized_message:
            target["last_outreach_message"] = normalized_message
        if follow_up_value:
            target["next_follow_up_at"] = follow_up_value
        elif not str(target.get("next_follow_up_at", "")).strip():
            target["next_follow_up_at"] = self._build_follow_up_slot(days=1, hour=11)
        if current_status not in self.TERMINAL_STATUSES:
            self._apply_status_side_effects(target, "proposal_sent")
        target["notes"] = self._append_note_line(
            target.get("notes", ""),
            "[PROPOSAL_SENT]" + (f" channel={normalized_channel}" if normalized_channel else ""),
        )

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "proposal_sent",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "channel": normalized_channel,
                "message": normalized_message,
                "status_after": str(target.get("status", "")).strip(),
                "next_follow_up_at": str(target.get("next_follow_up_at", "")).strip(),
                "proposal_path": str(target.get("proposal_path", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def mark_call_booked(
        self,
        *,
        lead_id: str,
        call_scheduled_for: str,
        notes: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        now = datetime.now().isoformat(timespec="seconds")
        scheduled_for = str(call_scheduled_for).strip()
        if not scheduled_for:
            raise ValueError("call_scheduled_for is required.")

        target["call_booked_at"] = now
        target["call_scheduled_for"] = scheduled_for
        target["last_action_at"] = now
        target["next_follow_up_at"] = scheduled_for
        target["recommended_next_action"] = "准备通话议程，通话后尽快推进到付款确认。"
        if str(target.get("status", "")).strip() in {"new", "contacted"}:
            target["status"] = "qualified"
        if notes.strip():
            target["notes"] = self._append_note_line(target.get("notes", ""), f"[CALL_BOOKED] {notes.strip()}")
        else:
            target["notes"] = self._append_note_line(target.get("notes", ""), f"[CALL_BOOKED] {scheduled_for}")

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "call_booked",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "scheduled_for": scheduled_for,
                "status_after": str(target.get("status", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def mark_payment_received(
        self,
        *,
        lead_id: str,
        amount: float,
        received_at: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        amount_value = float(amount or 0.0)
        if amount_value <= 0:
            raise ValueError("Payment amount must be greater than zero.")

        now = datetime.now().isoformat(timespec="seconds")
        effective_received_at = str(received_at).strip() or now
        target["actual_value"] = amount_value
        target["payment_received_at"] = effective_received_at
        target["status"] = "won"
        target["won_at"] = target.get("won_at") or effective_received_at
        target["last_action_at"] = now
        target["recommended_next_action"] = "已收款；推进交付、复购和转介绍。"
        target["notes"] = self._append_note_line(
            target.get("notes", ""),
            f"[PAYMENT_RECEIVED] amount={amount_value:.2f}" + (f" {notes.strip()}" if notes.strip() else ""),
        )
        self._apply_status_side_effects(target, "won")

        self._save_ledger(ledger)
        self._sync_lead_revenue(target)
        self._append_outreach_execution_event(
            {
                "event_type": "payment_received",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "amount": amount_value,
                "received_at": effective_received_at,
                "status_after": str(target.get("status", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def mark_delivery_started(
        self,
        *,
        lead_id: str,
        delivery_notes: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")
        if str(target.get("status", "")).strip() != "won":
            raise ValueError("Delivery can only start for a won lead.")

        now = datetime.now().isoformat(timespec="seconds")
        target["delivery_started_at"] = now
        target["last_action_at"] = now
        target["recommended_next_action"] = "推进交付并沉淀中期成果，准备在交付尾声索取评价。"
        target["notes"] = self._append_note_line(
            target.get("notes", ""),
            "[DELIVERY_STARTED]" + (f" {delivery_notes.strip()}" if delivery_notes.strip() else ""),
        )

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "delivery_started",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "notes": delivery_notes.strip(),
                "status_after": str(target.get("status", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def mark_delivery_completed(
        self,
        *,
        lead_id: str,
        delivery_notes: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        now = datetime.now().isoformat(timespec="seconds")
        target["delivery_completed_at"] = now
        target["last_action_at"] = now
        target["recommended_next_action"] = "优先索取评价，并识别复购或续费机会。"
        target["notes"] = self._append_note_line(
            target.get("notes", ""),
            "[DELIVERY_COMPLETED]" + (f" {delivery_notes.strip()}" if delivery_notes.strip() else ""),
        )

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "delivery_completed",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "notes": delivery_notes.strip(),
                "status_after": str(target.get("status", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def request_testimonial(
        self,
        *,
        lead_id: str,
        message: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        now = datetime.now().isoformat(timespec="seconds")
        target["testimonial_requested_at"] = now
        target["last_action_at"] = now
        target["recommended_next_action"] = "等待客户评价，同时顺势询问是否愿意做转介绍。"
        target["notes"] = self._append_note_line(
            target.get("notes", ""),
            "[TESTIMONIAL_REQUESTED]" + (f" {message.strip()}" if message.strip() else ""),
        )

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "testimonial_requested",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "message": message.strip(),
                "status_after": str(target.get("status", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def mark_expansion_opportunity(
        self,
        *,
        lead_id: str,
        expansion_type: str,
        notes: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        target = self._find_lead(ledger, lead_id)
        if target is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        normalized_type = str(expansion_type).strip() or "upsell"
        now = datetime.now().isoformat(timespec="seconds")
        target["expansion_opportunity_type"] = normalized_type
        target["expansion_identified_at"] = now
        target["last_action_at"] = now
        target["recommended_next_action"] = "围绕当前成果推进续费、升级服务或转介绍。"
        target["notes"] = self._append_note_line(
            target.get("notes", ""),
            f"[EXPANSION_OPPORTUNITY] type={normalized_type}" + (f" {notes.strip()}" if notes.strip() else ""),
        )

        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "expansion_opportunity",
                "lead_id": str(target.get("id", "")).strip(),
                "lead_name": str(target.get("name", "")).strip(),
                "expansion_type": normalized_type,
                "notes": notes.strip(),
                "status_after": str(target.get("status", "")).strip(),
            }
        )
        self.ensure_assets()
        return target

    def convert_expansion_to_lead(
        self,
        *,
        source_lead_id: str,
        referral_name: str,
        referral_contact: str,
        referral_intent: str = "咨询",
        notes: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        source_lead = self._find_lead(ledger, source_lead_id)
        if source_lead is None:
            raise FileNotFoundError(f"Lead not found: {source_lead_id}")

        now = datetime.now().isoformat(timespec="seconds")
        entry = {
            "id": self._generate_lead_id(),
            "created_at": now,
            "account_key": self.settings.account_key,
            "name": str(referral_name).strip(),
            "contact": str(referral_contact).strip(),
            "contact_verified": not str(referral_contact).strip().lower().startswith("auto://"),
            "lead_kind": "real_lead",
            "opportunity_kind": "real_lead",
            "intent": str(referral_intent).strip() or "咨询",
            "source": "customer_referral",
            "status": "new",
            "notes": self._append_note_line(
                "",
                f"[REFERRAL_FROM] {str(source_lead.get('name', '')).strip()} {notes.strip()}".strip(),
            ),
            "estimated_value": float(source_lead.get("estimated_value", 0.0) or 0.0),
            "actual_value": 0.0,
            "proposal_sent_at": "",
            "proposal_offer_key": "",
            "proposal_offer_name": "",
            "proposal_path": "",
            "won_at": "",
            "lost_at": "",
            "next_follow_up_at": self._build_follow_up_slot(days=1, hour=10),
            "last_action_at": now,
            "referral_source_lead_id": str(source_lead.get("id", "")).strip(),
            "referral_source_customer_name": str(source_lead.get("name", "")).strip(),
            "suggested_offer_key": str(source_lead.get("suggested_offer_key", "")).strip(),
            "suggested_offer_name": str(source_lead.get("suggested_offer_name", "")).strip(),
            "suggested_offer_price": str(source_lead.get("suggested_offer_price", "")).strip(),
            "suggested_offer_delivery": str(source_lead.get("suggested_offer_delivery", "")).strip(),
            "preferred_outreach_channel": str(source_lead.get("preferred_outreach_channel", "")).strip(),
            "recommended_next_action": "优先联系这条转介绍线索，借助老客户背书推进首次沟通。",
        }
        ledger.insert(0, entry)
        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "referral_converted_to_lead",
                "lead_id": str(entry.get("id", "")).strip(),
                "lead_name": str(entry.get("name", "")).strip(),
                "referral_source_lead_id": str(source_lead.get("id", "")).strip(),
                "referral_source_customer_name": str(source_lead.get("name", "")).strip(),
            }
        )
        self.ensure_assets()
        return entry

    def generate_expansion_proposal(
        self,
        *,
        lead_id: str,
        expansion_type: str,
        next_follow_up_at: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        lead = self._find_lead(ledger, lead_id)
        if lead is None:
            raise FileNotFoundError(f"Lead not found: {lead_id}")

        normalized_type = str(expansion_type).strip() or str(lead.get("expansion_opportunity_type", "")).strip() or "upsell"
        offer_key = self._recommend_expansion_offer_key(lead, normalized_type)
        if not offer_key:
            raise FileNotFoundError(f"No expansion offer found for type: {normalized_type}")

        result = self._create_proposal_for_lead(
            ledger=ledger,
            lead=lead,
            offer_key=offer_key,
            next_follow_up_at=next_follow_up_at or self._build_follow_up_slot(days=2, hour=11),
            mark_as_sent=False,
            auto_generated=True,
        )
        lead["expansion_opportunity_type"] = normalized_type
        lead["expansion_identified_at"] = lead.get("expansion_identified_at") or datetime.now().isoformat(timespec="seconds")
        lead["recommended_next_action"] = "发送续费/升级 proposal，并围绕当前成果推进下一阶段合作。"
        lead["notes"] = self._append_note_line(
            lead.get("notes", ""),
            f"[EXPANSION_PROPOSAL_READY] type={normalized_type}",
        )
        self._save_ledger(ledger)
        self._append_outreach_execution_event(
            {
                "event_type": "expansion_proposal_ready",
                "lead_id": str(lead.get("id", "")).strip(),
                "lead_name": str(lead.get("name", "")).strip(),
                "expansion_type": normalized_type,
                "proposal_path": str(result.get("proposal_path", "")),
            }
        )
        self.ensure_assets()
        return {
            "lead_id": str(lead.get("id", "")).strip(),
            "proposal_path": str(result.get("proposal_path", "")),
            "offer_key": str(result.get("offer_key", "")),
            "offer_name": str(result.get("offer_name", "")),
            "expansion_type": normalized_type,
        }

    def list_leads(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._load_ledger()[:limit]

    def list_offer_choices(self) -> list[dict[str, str]]:
        revenue_payload = self._load_revenue_config()
        offers = revenue_payload.get("core_offers", []) if isinstance(revenue_payload, dict) else []
        choices: list[dict[str, str]] = []
        for item in offers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            key = str(item.get("key", "")).strip() or self._slugify_offer_name(name)
            price = str(item.get("price", "")).strip()
            choices.append(
                {
                    "key": key,
                    "name": name,
                    "price": price,
                    "label": f"{name} ({price})" if price else name,
                }
            )
        return choices

    def sync_generated_opportunities(
        self,
        opportunities: list[dict[str, Any]],
        *,
        next_follow_up_at: str = "",
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        inserted = 0
        updated = 0
        skipped = 0
        now = datetime.now().isoformat(timespec="seconds")
        follow_up_value = str(next_follow_up_at).strip()

        for item in opportunities:
            if not isinstance(item, dict):
                skipped += 1
                continue
            opportunity_id = str(item.get("id", "")).strip()
            if not opportunity_id:
                skipped += 1
                continue
            existing = self._find_lead_by_opportunity_id(ledger, opportunity_id)
            title = str(item.get("headline", "")).strip() or "自动商机"
            opportunity_type = str(item.get("type", "")).strip() or "customer_pitch"
            priority = str(item.get("priority", "")).strip() or "medium"
            source = "revenue_engine"
            intent = "赞助" if opportunity_type == "sponsor_pitch" else "咨询"
            notes = self._build_generated_lead_notes(item)
            recommended_status = "qualified" if priority == "high" else "new"
            contact = f"auto://{opportunity_id}"

            if existing is not None:
                if str(existing.get("status", "")).strip() in self.TERMINAL_STATUSES:
                    skipped += 1
                    continue
                existing["name"] = title
                existing["contact"] = contact
                existing["contact_verified"] = False
                existing["lead_kind"] = "auto_opportunity"
                existing["opportunity_kind"] = "auto_opportunity"
                existing["intent"] = intent
                existing["source"] = source
                existing["notes"] = notes
                existing["opportunity_id"] = opportunity_id
                existing["opportunity_type"] = opportunity_type
                existing["auto_generated"] = True
                existing["asset_hint"] = str(item.get("asset_hint", "")).strip()
                existing["suggested_offer_key"] = str(item.get("recommended_offer_key", "")).strip()
                existing["suggested_offer_name"] = str(item.get("recommended_offer_name", "")).strip()
                existing["suggested_offer_price"] = self._normalize_offer_price_display(
                    str(item.get("recommended_offer_price", "")).strip()
                )
                existing["suggested_offer_delivery"] = str(item.get("recommended_offer_delivery", "")).strip()
                existing["customer_segment_key"] = str(item.get("segment_key", "")).strip()
                existing["customer_segment_name"] = str(item.get("segment_name", "")).strip()
                existing["preferred_outreach_channel"] = str(item.get("outreach_channel", "")).strip()
                existing["last_action_at"] = now
                if follow_up_value:
                    existing["next_follow_up_at"] = follow_up_value
                if str(existing.get("status", "")).strip() == "new" and recommended_status == "qualified":
                    existing["status"] = "qualified"
                if not float(existing.get("estimated_value", 0.0) or 0.0):
                    existing["estimated_value"] = self._parse_offer_price_floor(str(item.get("recommended_offer_price", "")).strip())
                updated += 1
                continue

            entry = {
                "id": self._generate_lead_id(),
                "created_at": now,
                "account_key": self.settings.account_key,
                "name": title,
                "contact": contact,
                "contact_verified": False,
                "lead_kind": "auto_opportunity",
                "opportunity_kind": "auto_opportunity",
                "intent": intent,
                "source": source,
                "status": recommended_status,
                "notes": notes,
                "estimated_value": 0.0,
                "actual_value": 0.0,
                "proposal_sent_at": "",
                "proposal_offer_key": "",
                "proposal_offer_name": "",
                "proposal_path": "",
                "won_at": "",
                "lost_at": "",
                "next_follow_up_at": follow_up_value,
                "last_action_at": now,
                "opportunity_id": opportunity_id,
                "opportunity_type": opportunity_type,
                "auto_generated": True,
                "asset_hint": str(item.get("asset_hint", "")).strip(),
                "suggested_offer_key": str(item.get("recommended_offer_key", "")).strip(),
                "suggested_offer_name": str(item.get("recommended_offer_name", "")).strip(),
                "suggested_offer_price": self._normalize_offer_price_display(
                    str(item.get("recommended_offer_price", "")).strip()
                ),
                "suggested_offer_delivery": str(item.get("recommended_offer_delivery", "")).strip(),
                "customer_segment_key": str(item.get("segment_key", "")).strip(),
                "customer_segment_name": str(item.get("segment_name", "")).strip(),
                "preferred_outreach_channel": str(item.get("outreach_channel", "")).strip(),
            }
            if not float(entry.get("estimated_value", 0.0) or 0.0):
                entry["estimated_value"] = self._parse_offer_price_floor(str(item.get("recommended_offer_price", "")).strip())
            self._apply_status_side_effects(entry, recommended_status)
            ledger.insert(0, entry)
            inserted += 1

        self._save_ledger(ledger)
        self.enrich_generated_leads_with_recommendations()
        return {
            "generated_at": now,
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "processed": inserted + updated + skipped,
        }

    def auto_draft_generated_proposals(
        self,
        *,
        target_statuses: tuple[str, ...] = ("qualified",),
        default_follow_up_at: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        ledger = self._load_ledger()
        drafted = 0
        skipped = 0
        drafted_leads: list[dict[str, str]] = []
        normalized_statuses = {str(item).strip() for item in target_statuses if str(item).strip()}
        follow_up_value = str(default_follow_up_at).strip()

        for lead in ledger:
            if drafted >= max(limit, 0):
                break
            if not isinstance(lead, dict):
                skipped += 1
                continue
            if not bool(lead.get("auto_generated")):
                skipped += 1
                continue
            if str(lead.get("source", "")).strip() != "revenue_engine":
                skipped += 1
                continue
            if str(lead.get("status", "")).strip() not in normalized_statuses:
                skipped += 1
                continue
            if str(lead.get("proposal_path", "")).strip():
                skipped += 1
                continue

            offer_key = self._recommend_offer_key_for_lead(lead)
            if not offer_key:
                skipped += 1
                continue

            result = self._create_proposal_for_lead(
                ledger=ledger,
                lead=lead,
                offer_key=offer_key,
                next_follow_up_at=follow_up_value or self._default_auto_follow_up_at(),
                mark_as_sent=False,
                auto_generated=True,
            )
            drafted += 1
            drafted_leads.append(
                {
                    "lead_id": str(result["lead_id"]),
                    "offer_key": str(result["offer_key"]),
                    "offer_name": str(result["offer_name"]),
                    "proposal_path": str(result["proposal_path"]),
                }
            )

        self._save_ledger(ledger)
        self.ensure_assets()
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "drafted": drafted,
            "skipped": skipped,
            "processed": drafted + skipped,
            "target_statuses": sorted(normalized_statuses),
            "drafted_leads": drafted_leads,
        }

    def load_summary(self) -> dict[str, Any]:
        if self.summary_path.exists():
            try:
                payload = json.loads(self.summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                return payload
        ledger = self._load_ledger()
        return self._build_summary(ledger)

    def _build_reply_manual(self, payload: dict[str, Any]) -> str:
        replies = payload.get("default_replies", {}) if isinstance(payload, dict) else {}
        lines = ["# 关键词回复手册", ""]
        for keyword, reply in replies.items():
            lines.append(f"## {keyword}")
            lines.append("")
            lines.append(str(reply).strip())
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_followup_queue(self, ledger: list[dict[str, Any]]) -> str:
        pending = [item for item in ledger if str(item.get("status", "")).strip() not in self.TERMINAL_STATUSES]
        lines = ["# 待跟进线索", ""]
        if not pending:
            lines.append("- 暂无线索，先用内容和 CTA 持续收集。")
            return "\n".join(lines).strip() + "\n"

        for item in pending[:20]:
            lines.append(f"## {str(item.get('name', '')).strip() or '未命名线索'}")
            lines.append("")
            lines.append(f"- 联系方式：{str(item.get('contact', '')).strip()}")
            lines.append(f"- 意向：{str(item.get('intent', '')).strip()}")
            lines.append(f"- 来源：{str(item.get('source', '')).strip()}")
            lines.append(f"- 状态：{str(item.get('status', '')).strip()}")
            lines.append(f"- 预计金额：{float(item.get('estimated_value', 0.0) or 0.0):.2f}")
            lines.append(f"- 下次跟进：{str(item.get('next_follow_up_at', '')).strip() or '未设置'}")
            lines.append(f"- 备注：{str(item.get('notes', '')).strip() or '无'}")
            proposal_path = str(item.get("proposal_path", "")).strip()
            if proposal_path:
                lines.append(f"- 方案文件：{proposal_path}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_outreach_queue(self, ledger: list[dict[str, Any]]) -> str:
        candidates = [
            item
            for item in ledger
            if isinstance(item, dict)
            and str(item.get("status", "")).strip() not in self.TERMINAL_STATUSES
            and bool(item.get("auto_generated"))
            and str(item.get("source", "")).strip() == "revenue_engine"
        ]
        lines = ["# 自动外联队列", ""]
        if not candidates:
            lines.append("- 暂无自动外联对象。")
            return "\n".join(lines).strip() + "\n"

        for item in candidates[:20]:
            opening = self._build_outreach_opening(item)
            next_step = self._build_outreach_next_step(item)
            lines.append(f"## {str(item.get('name', '')).strip() or '未命名线索'}")
            lines.append("")
            lines.append(f"- 推荐报价：{str(item.get('suggested_offer_name', '')).strip() or '待判断'}")
            lines.append(f"- 预计金额：{float(item.get('estimated_value', 0.0) or 0.0):.2f}")
            lines.append(f"- 当前阶段：{str(item.get('status', '')).strip() or 'new'}")
            if str(item.get("outreach_sent_at", "")).strip():
                lines.append(f"- 最近外联：{str(item.get('outreach_sent_at', '')).strip()}")
            if str(item.get("reply_temperature", "")).strip():
                lines.append(f"- 回复热度：{str(item.get('reply_temperature', '')).strip()}")
            lines.append(f"- 开场消息：{opening}")
            lines.append(f"- 下一步动作：{next_step}")
            proposal_path = str(item.get("proposal_path", "")).strip()
            if proposal_path:
                lines.append(f"- 方案草稿：{proposal_path}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_outreach_sequences(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = [
            item
            for item in ledger
            if isinstance(item, dict)
            and str(item.get("status", "")).strip() not in self.TERMINAL_STATUSES
            and bool(item.get("auto_generated"))
            and str(item.get("source", "")).strip() == "revenue_engine"
        ]
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "items": [
                {
                    "lead_id": str(item.get("id", "")).strip(),
                    "lead_name": str(item.get("name", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "suggested_offer_key": str(item.get("suggested_offer_key", "")).strip(),
                    "suggested_offer_name": str(item.get("suggested_offer_name", "")).strip(),
                    "suggested_offer_price": str(item.get("suggested_offer_price", "")).strip(),
                    "preferred_outreach_channel": str(item.get("preferred_outreach_channel", "")).strip(),
                    "outreach_opening": self._build_outreach_opening(item),
                    "follow_up_message": self._build_follow_up_message(item),
                    "next_step": self._build_outreach_next_step(item),
                    "outreach_sent_at": str(item.get("outreach_sent_at", "")).strip(),
                    "reply_temperature": str(item.get("reply_temperature", "")).strip(),
                    "proposal_path": str(item.get("proposal_path", "")).strip(),
                }
                for item in candidates[:20]
            ],
        }

    def _build_outbound_campaign_brief(self, ledger: list[dict[str, Any]]) -> str:
        pack = self._build_outbound_campaign_pack(ledger)
        items = pack.get("items", []) if isinstance(pack, dict) else []
        lines = ["# 批量外联发送包", ""]
        if not items:
            lines.append("- 暂无可发送的自动外联对象。")
            return "\n".join(lines).strip() + "\n"

        summary = pack.get("summary", {}) if isinstance(pack, dict) else {}
        lines.append(f"- 总对象数：{summary.get('total_items', 0)}")
        lines.append(f"- 已有方案支撑：{summary.get('proposal_backed_items', 0)}")
        lines.append(f"- 微信优先：{summary.get('wechat_priority_items', 0)}")
        lines.append(f"- 邮件优先：{summary.get('email_priority_items', 0)}")
        lines.append("")

        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            channels = ", ".join(item.get("channel_list", [])) if isinstance(item.get("channel_list"), list) else ""
            messages = item.get("messages", {}) if isinstance(item.get("messages"), dict) else {}
            lines.append(f"## {str(item.get('lead_name', '')).strip() or '未命名线索'}")
            lines.append("")
            lines.append(f"- 推荐渠道：{channels or str(item.get('preferred_channel', '')).strip() or '待判断'}")
            lines.append(f"- 推荐报价：{str(item.get('suggested_offer_name', '')).strip() or '待判断'}")
            lines.append(f"- 微信 / 私信开场：{str(messages.get('wechat', '')).strip() or str(messages.get('platform_dm', '')).strip()}")
            lines.append(f"- 跟进消息：{str(messages.get('follow_up', '')).strip()}")
            lines.append(f"- 下一步动作：{str(item.get('next_step', '')).strip()}")
            proposal_path = str(item.get("proposal_path", "")).strip()
            if proposal_path:
                lines.append(f"- Proposal：{proposal_path}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_outbound_campaign_pack(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = [
            item
            for item in ledger
            if isinstance(item, dict)
            and str(item.get("status", "")).strip() not in self.TERMINAL_STATUSES
            and bool(item.get("auto_generated"))
            and str(item.get("source", "")).strip() == "revenue_engine"
        ]
        items: list[dict[str, Any]] = []
        for item in candidates[:20]:
            channels = self._split_outreach_channels(str(item.get("preferred_outreach_channel", "")).strip())
            messages = self._build_channel_messages(item, channels)
            items.append(
                {
                    "lead_id": str(item.get("id", "")).strip(),
                    "lead_name": str(item.get("name", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "preferred_channel": str(item.get("preferred_outreach_channel", "")).strip(),
                    "channel_list": channels,
                    "suggested_offer_key": str(item.get("suggested_offer_key", "")).strip(),
                    "suggested_offer_name": str(item.get("suggested_offer_name", "")).strip(),
                    "suggested_offer_price": str(item.get("suggested_offer_price", "")).strip(),
                    "proposal_path": str(item.get("proposal_path", "")).strip(),
                    "messages": messages,
                    "next_step": self._build_outreach_next_step(item),
                    "outreach_sent_at": str(item.get("outreach_sent_at", "")).strip(),
                    "reply_temperature": str(item.get("reply_temperature", "")).strip(),
                }
            )

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "proposal_backed_items": len([item for item in items if str(item.get("proposal_path", "")).strip()]),
                "wechat_priority_items": len([item for item in items if any("微信" in channel for channel in item.get("channel_list", []))]),
                "email_priority_items": len([item for item in items if any("邮件" in channel for channel in item.get("channel_list", []))]),
            },
            "items": items,
        }

    def _build_summary(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        estimated_revenue = 0.0
        actual_revenue = 0.0
        estimated_pipeline_revenue = 0.0
        weighted_pipeline_revenue = 0.0
        verified_estimated_pipeline_revenue = 0.0
        verified_weighted_pipeline_revenue = 0.0
        auto_estimated_pipeline_revenue = 0.0
        auto_weighted_pipeline_revenue = 0.0
        verified_leads = 0
        verified_won_leads = 0
        verified_qualified_leads = 0
        verified_proposal_sent_leads = 0
        auto_generated_leads = 0
        auto_generated_qualified_leads = 0
        auto_proposal_ready_leads = 0
        outbound_ready_leads = 0
        conversion_ready_leads = 0
        outreach_sent_leads = 0
        hot_reply_leads = 0
        proposal_pending_response_leads = 0
        call_booked_leads = 0
        delivery_started_leads = 0
        delivery_completed_leads = 0
        testimonial_requested_leads = 0
        expansion_ready_leads = 0
        pipeline_weights = {
            "new": 0.10,
            "contacted": 0.20,
            "qualified": 0.35,
            "proposal_sent": 0.60,
        }
        for item in ledger:
            key = str(item.get("status", "new")).strip() or "new"
            status_counts[key] = status_counts.get(key, 0) + 1
            estimated_value = float(item.get("estimated_value", 0.0) or 0.0)
            estimated_revenue += estimated_value
            actual_revenue += float(item.get("actual_value", 0.0) or 0.0)
            is_auto_opportunity = bool(item.get("auto_generated")) or str(
                item.get("contact", "")
            ).strip().lower().startswith("auto://")
            is_verified = bool(item.get("contact_verified")) and not is_auto_opportunity
            if is_verified:
                verified_leads += 1
                if key == "won":
                    verified_won_leads += 1
                if key == "qualified":
                    verified_qualified_leads += 1
                if key == "proposal_sent":
                    verified_proposal_sent_leads += 1
            if key in pipeline_weights and float(item.get("actual_value", 0.0) or 0.0) <= 0:
                weighted_value = estimated_value * pipeline_weights[key]
                estimated_pipeline_revenue += estimated_value
                weighted_pipeline_revenue += weighted_value
                if is_auto_opportunity:
                    auto_estimated_pipeline_revenue += estimated_value
                    auto_weighted_pipeline_revenue += weighted_value
                elif is_verified:
                    verified_estimated_pipeline_revenue += estimated_value
                    verified_weighted_pipeline_revenue += weighted_value
            if bool(item.get("auto_generated")):
                auto_generated_leads += 1
                if key == "qualified":
                    auto_generated_qualified_leads += 1
                if str(item.get("preferred_outreach_channel", "")).strip():
                    outbound_ready_leads += 1
                if key in {"new", "qualified", "proposal_sent"}:
                    conversion_ready_leads += 1
            if str(item.get("outreach_sent_at", "")).strip():
                outreach_sent_leads += 1
            if str(item.get("reply_temperature", "")).strip() == "hot":
                hot_reply_leads += 1
            if key == "proposal_sent":
                proposal_pending_response_leads += 1
            if str(item.get("call_scheduled_for", "")).strip():
                call_booked_leads += 1
            if str(item.get("delivery_started_at", "")).strip():
                delivery_started_leads += 1
            if str(item.get("delivery_completed_at", "")).strip():
                delivery_completed_leads += 1
            if str(item.get("testimonial_requested_at", "")).strip():
                testimonial_requested_leads += 1
            if str(item.get("expansion_identified_at", "")).strip():
                expansion_ready_leads += 1
            if str(item.get("proposal_path", "")).strip() and bool(item.get("proposal_auto_generated")):
                auto_proposal_ready_leads += 1
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "total_leads": len(ledger),
            "real_leads": verified_leads,
            "won_leads": verified_won_leads,
            "qualified_leads": verified_qualified_leads,
            "proposal_sent_leads": verified_proposal_sent_leads,
            "estimated_revenue": round(estimated_revenue, 2),
            "actual_revenue": round(actual_revenue, 2),
            "estimated_pipeline_revenue": round(estimated_pipeline_revenue, 2),
            "weighted_pipeline_revenue": round(weighted_pipeline_revenue, 2),
            "verified_estimated_pipeline_revenue": round(verified_estimated_pipeline_revenue, 2),
            "verified_weighted_pipeline_revenue": round(verified_weighted_pipeline_revenue, 2),
            "auto_estimated_pipeline_revenue": round(auto_estimated_pipeline_revenue, 2),
            "auto_weighted_pipeline_revenue": round(auto_weighted_pipeline_revenue, 2),
            "verified_leads": verified_leads,
            "verified_won_leads": verified_won_leads,
            "verified_qualified_leads": verified_qualified_leads,
            "auto_generated_leads": auto_generated_leads,
            "auto_generated_qualified_leads": auto_generated_qualified_leads,
            "auto_proposal_ready_leads": auto_proposal_ready_leads,
            "outbound_ready_leads": outbound_ready_leads,
            "conversion_ready_leads": conversion_ready_leads,
            "outreach_sent_leads": outreach_sent_leads,
            "hot_reply_leads": hot_reply_leads,
            "proposal_pending_response_leads": proposal_pending_response_leads,
            "call_booked_leads": call_booked_leads,
            "delivery_started_leads": delivery_started_leads,
            "delivery_completed_leads": delivery_completed_leads,
            "testimonial_requested_leads": testimonial_requested_leads,
            "expansion_ready_leads": expansion_ready_leads,
            "status_counts": status_counts,
            "recent_leads": ledger[:10],
        }

    def _build_proposal_catalog(self) -> str:
        revenue_payload = self._load_revenue_config()
        templates = revenue_payload.get("proposal_templates", []) if isinstance(revenue_payload, dict) else []
        offers = revenue_payload.get("core_offers", []) if isinstance(revenue_payload, dict) else []
        offer_index: dict[str, dict[str, str]] = {}
        for item in offers:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            key = str(item.get("key", "")).strip() or self._slugify_offer_name(name)
            offer_index[key] = {"name": name, "price": str(item.get("price", "")).strip()}
            if name:
                offer_index[name] = {"name": name, "price": str(item.get("price", "")).strip()}

        lines = ["# 合作方案模板", ""]
        for item in templates:
            if not isinstance(item, dict):
                continue
            offer_key = str(item.get("offer_key", "")).strip()
            offer_name = str(item.get("offer_name", "")).strip()
            offer_meta = offer_index.get(offer_key) or offer_index.get(offer_name) or {"name": offer_name, "price": self.DEFAULT_PRICE}
            lines.append(f"## {offer_meta.get('name', offer_name) or '未命名服务'}")
            lines.append("")
            if offer_key:
                lines.append(f"- Offer Key：{offer_key}")
            lines.append(f"- 价格区间：{offer_meta.get('price', self.DEFAULT_PRICE) or self.DEFAULT_PRICE}")
            lines.append(f"- 交付周期：{str(item.get('delivery_time', '')).strip() or self.DEFAULT_DELIVERY_TIME}")
            lines.append("- 交付范围：")
            for scope_item in item.get("scope", []):
                if str(scope_item).strip():
                    lines.append(f"  - {str(scope_item).strip()}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_offer_snapshot(self) -> dict[str, Any]:
        revenue_payload = self._load_revenue_config()
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "brand_name": self.settings.brand_name,
            "offers": revenue_payload.get("core_offers", []) if isinstance(revenue_payload, dict) else [],
            "proposal_templates": revenue_payload.get("proposal_templates", []) if isinstance(revenue_payload, dict) else [],
        }

    def _build_lead_proposal_markdown(
        self,
        *,
        lead: dict[str, Any],
        offer: dict[str, Any],
        price: str,
        scope_items: list[str],
        delivery_time: str,
    ) -> str:
        lead_name = str(lead.get("name", "")).strip() or "合作方"
        intent = str(lead.get("intent", "")).strip() or "咨询"
        offer_name = str(offer.get("name", "")).strip() or "合作服务"
        target_customer = str(offer.get("target_customer", "")).strip()
        delivery = str(offer.get("delivery", "")).strip()
        lines = [
            f"# {lead_name} 合作提案",
            "",
            f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
            f"- 品牌：{self.settings.brand_name}",
            f"- 客户意向：{intent}",
            f"- 方案类型：{offer_name}",
            "",
            "## 你当前的需求理解",
            "",
            (
                f"根据当前记录，你是通过「{str(lead.get('source', '')).strip() or '未知来源'}」进入的，"
                f"当前主要意向是「{intent}」。"
            ),
            (
                f"我们建议先从「{offer_name}」开始，因为这更适合像"
                f"「{target_customer or '当前客户'}」这样的场景。"
            ),
            "",
            "## 服务范围",
            "",
        ]
        for item in scope_items:
            lines.append(f"- {item}")
        lines.extend(
            [
                "",
                "## 交付方式",
                "",
                f"- 标准交付：{delivery or self.DEFAULT_DELIVERY_TIME}",
                f"- 交付周期：{delivery_time or self.DEFAULT_DELIVERY_TIME}",
                f"- 报价建议：{price}",
                "",
                "## 下一步建议",
                "",
                "- 如果你认可方向，可以直接回复确认当前最优先目标。",
                "- 确认后我会按这个提案进入正式交付或启动会。",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def _load_config(self) -> dict[str, Any]:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(
                yaml.safe_dump(self.DEFAULT_CONFIG, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        try:
            payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.logger.warning("Failed to load lead config, using defaults: %s", exc)
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _load_revenue_config(self) -> dict[str, Any]:
        revenue_path = self.settings.project_root / "config" / "company_revenue.yaml"
        if not revenue_path.exists():
            return {}
        try:
            payload = yaml.safe_load(revenue_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.logger.warning("Failed to load revenue config for proposals: %s", exc)
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _load_ledger(self) -> list[dict[str, Any]]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.ledger_path.exists():
            self._save_ledger([])
            return []
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = []
        if not isinstance(payload, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            normalized_item = self._normalize_lead(item)
            self._hydrate_referral_lead_defaults(normalized_item)
            lead_id = str(normalized_item.get("id", "")).strip()
            if not lead_id or lead_id in seen_ids:
                normalized_item["id"] = self._generate_lead_id()
            seen_ids.add(str(normalized_item.get("id", "")).strip())
            normalized.append(normalized_item)
        if normalized != payload:
            self._save_ledger(normalized)
        return normalized

    def _save_ledger(self, ledger: list[dict[str, Any]]) -> None:
        self.ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")

    def _hydrate_referral_lead_defaults(self, lead: dict[str, Any]) -> None:
        if str(lead.get("source", "")).strip() != "customer_referral":
            return
        if not str(lead.get("preferred_outreach_channel", "")).strip():
            lead["preferred_outreach_channel"] = "wechat"
        if not str(lead.get("recommended_next_action", "")).strip():
            lead["recommended_next_action"] = "优先联系这条转介绍线索，借助老客户背书推进首次沟通。"

    def _find_lead(self, ledger: list[dict[str, Any]], lead_id: str) -> dict[str, Any] | None:
        for item in ledger:
            if str(item.get("id", "")).strip() == str(lead_id).strip():
                return item
        return None

    def _find_lead_by_opportunity_id(
        self,
        ledger: list[dict[str, Any]],
        opportunity_id: str,
    ) -> dict[str, Any] | None:
        normalized = str(opportunity_id).strip()
        for item in ledger:
            if str(item.get("opportunity_id", "")).strip() == normalized:
                return item
        return None

    def _find_offer(
        self,
        offers: list[Any],
        *,
        offer_key: str = "",
        offer_name: str = "",
    ) -> dict[str, Any] | None:
        normalized_key = self._normalize_lookup_key(offer_key)
        normalized_name = self._normalize_lookup_key(offer_name)
        for item in offers:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get("name", "")).strip()
            item_key = str(item.get("key", "")).strip() or self._slugify_offer_name(item_name)
            if normalized_key and self._normalize_lookup_key(item_key) == normalized_key:
                item["key"] = item_key
                return item
            if normalized_name and self._normalize_lookup_key(item_name) == normalized_name:
                item["key"] = item_key
                return item
            if normalized_name and self._normalize_lookup_key(item_key) == normalized_name:
                item["key"] = item_key
                return item
        return None

    def _find_template(
        self,
        templates: list[Any],
        *,
        offer_identifier: str,
        offer_name: str,
    ) -> dict[str, Any] | None:
        normalized_identifier = self._normalize_lookup_key(offer_identifier)
        normalized_name = self._normalize_lookup_key(offer_name)
        for item in templates:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("offer_key", "")).strip()
            item_name = str(item.get("offer_name", "")).strip()
            if item_key and self._normalize_lookup_key(item_key) == normalized_identifier:
                return item
            if item_name and self._normalize_lookup_key(item_name) == normalized_name:
                return item
        return None

    def _normalize_lookup_key(self, value: str) -> str:
        return str(value or "").strip().lower().replace(" ", "_")

    def _normalize_lead(self, lead: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(lead)
        normalized.setdefault("proposal_offer_key", "")
        normalized.setdefault("proposal_offer_name", "")
        normalized.setdefault("proposal_path", "")
        normalized.setdefault("won_at", "")
        normalized.setdefault("lost_at", "")
        normalized.setdefault("proposal_sent_at", "")
        normalized.setdefault("actual_value", 0.0)
        normalized.setdefault("estimated_value", 0.0)
        normalized.setdefault("source_content_id", "")
        normalized.setdefault("cta_source_code", "")
        normalized.setdefault("attribution_status", "unattributed")
        normalized.setdefault("attribution_platform", "")
        contact = str(normalized.get("contact", "")).strip()
        is_auto_opportunity = bool(normalized.get("auto_generated")) or contact.lower().startswith("auto://")
        normalized.setdefault("contact_verified", bool(contact) and not is_auto_opportunity)
        normalized.setdefault("lead_kind", "auto_opportunity" if is_auto_opportunity else "real_lead")
        normalized.setdefault("opportunity_kind", normalized.get("lead_kind", "real_lead"))
        if is_auto_opportunity:
            normalized["contact_verified"] = False
            normalized["lead_kind"] = "auto_opportunity"
            normalized["opportunity_kind"] = "auto_opportunity"
        normalized.setdefault("opportunity_id", "")
        normalized.setdefault("opportunity_type", "")
        normalized.setdefault("asset_hint", "")
        normalized.setdefault("auto_generated", False)
        normalized.setdefault("proposal_drafted_at", "")
        normalized.setdefault("proposal_auto_generated", False)
        normalized.setdefault("expansion_proposal_sent_at", "")
        normalized.setdefault("suggested_offer_key", "")
        normalized.setdefault("suggested_offer_name", "")
        normalized.setdefault("suggested_offer_price", "")
        normalized.setdefault("suggested_offer_delivery", "")
        normalized.setdefault("customer_segment_key", "")
        normalized.setdefault("customer_segment_name", "")
        normalized.setdefault("preferred_outreach_channel", "")
        normalized.setdefault("outreach_sent_at", "")
        normalized.setdefault("last_contacted_at", "")
        normalized.setdefault("last_outreach_channel", "")
        normalized.setdefault("last_outreach_message", "")
        normalized.setdefault("last_reply_at", "")
        normalized.setdefault("last_reply_text", "")
        normalized.setdefault("reply_temperature", "")
        normalized.setdefault("reply_triaged_at", "")
        normalized.setdefault("next_reply_script", "")
        normalized.setdefault("recommended_next_action", "")
        normalized.setdefault("call_booked_at", "")
        normalized.setdefault("call_scheduled_for", "")
        normalized.setdefault("payment_received_at", "")
        normalized.setdefault("delivery_started_at", "")
        normalized.setdefault("delivery_completed_at", "")
        normalized.setdefault("testimonial_requested_at", "")
        normalized.setdefault("expansion_identified_at", "")
        normalized.setdefault("expansion_opportunity_type", "")
        status = str(normalized.get("status", "")).strip() or "new"
        if status == "proposal_sent" and float(normalized.get("actual_value", 0.0) or 0.0) > 0:
            status = "won"
            normalized["status"] = status
        self._apply_status_side_effects(normalized, status)
        return normalized

    def _resolve_lead_attribution(
        self,
        *,
        source_content_id: str = "",
        cta_source_code: str = "",
        source: str = "",
        platform: str = "",
    ) -> dict[str, str]:
        """Resolve explicit attribution and report ambiguity instead of guessing."""
        from toutiao_content_factory.services.revenue_attribution_service import (
            RevenueAttributionService,
        )

        attribution = RevenueAttributionService(self.settings.output_dir, self.logger)
        explicit_id = str(source_content_id or "").strip()
        source_code = str(cta_source_code or "").strip()
        if not explicit_id and str(source or "").strip().startswith("ci_"):
            explicit_id = str(source).strip()
        if explicit_id:
            resolved = attribution.resolve_content_id(
                source_content_id=explicit_id,
                account_key=self.settings.account_key,
                platform=platform,
            )
            return {
                "content_id": resolved or "",
                "cta_source_code": source_code,
                "status": "explicit" if resolved else "unattributed",
            }
        if not source_code:
            return {"content_id": "", "cta_source_code": "", "status": "unattributed"}

        resolved = attribution.resolve_content_id(
            cta_source_code=source_code,
            account_key=self.settings.account_key,
            platform=platform,
        )
        index = attribution._load_json(attribution.content_index_path, default={})
        candidates = []
        if isinstance(index, dict):
            for content_id, record in index.items():
                if not isinstance(record, dict):
                    continue
                if str(record.get("cta_source_code", "")).strip().casefold() != source_code.casefold():
                    continue
                if str(record.get("account_key", "")).strip() != self.settings.account_key:
                    continue
                if platform and str(record.get("platform", "")).strip().lower() != platform.strip().lower():
                    continue
                candidates.append(str(content_id))
        status = "resolved_cta" if resolved else ("ambiguous" if len(candidates) > 1 else "unattributed")
        return {"content_id": resolved or "", "cta_source_code": source_code, "status": status}

    def _sync_lead_revenue(self, lead: dict[str, Any]) -> None:
        """Mirror verified lead/deal transitions into the content revenue ledger."""
        if not bool(lead.get("contact_verified")):
            return
        content_id = str(lead.get("source_content_id", "")).strip()
        lead_id = str(lead.get("id", "")).strip()
        if not content_id or not lead_id:
            return
        try:
            from toutiao_content_factory.services.revenue_attribution_service import (
                RevenueAttributionService,
                RevenueEvent,
            )

            attribution = RevenueAttributionService(self.settings.output_dir, self.logger)
            attribution.record_revenue_event(
                content_id,
                RevenueEvent(
                    event_type="lead",
                    amount=0.0,
                    source_article_id=content_id,
                    lead_id=lead_id,
                    channel=str(lead.get("source", "")).strip(),
                    event_id=f"lead:{lead_id}",
                ),
            )
            actual_value = float(lead.get("actual_value", 0.0) or 0.0)
            if str(lead.get("status", "")).strip() == "won" and actual_value > 0:
                attribution.record_revenue_event(
                    content_id,
                    RevenueEvent(
                        event_type="deal",
                        amount=actual_value,
                        source_article_id=content_id,
                        lead_id=lead_id,
                        channel=str(lead.get("source", "")).strip(),
                        occurred_at=str(lead.get("payment_received_at", "") or lead.get("won_at", "")),
                        event_id=f"deal:{lead_id}",
                    ),
                    replace_existing=True,
                )
        except Exception as exc:  # pragma: no cover - revenue side effect is best effort
            self.logger.warning("Lead revenue attribution failed (non-fatal): %s", exc)

    def _apply_status_side_effects(self, lead: dict[str, Any], status: str) -> None:
        normalized_status = str(status).strip()
        now = datetime.now().isoformat(timespec="seconds")
        if normalized_status == "proposal_sent" and not str(lead.get("proposal_sent_at", "")).strip():
            lead["proposal_sent_at"] = now
        if normalized_status == "won":
            if float(lead.get("actual_value", 0.0) or 0.0) <= 0:
                lead["actual_value"] = float(lead.get("estimated_value", 0.0) or 0.0)
            if not str(lead.get("won_at", "")).strip():
                lead["won_at"] = now
            lead["lost_at"] = ""
        elif normalized_status == "lost":
            if not str(lead.get("lost_at", "")).strip():
                lead["lost_at"] = now
        else:
            if normalized_status != "lost":
                lead["lost_at"] = ""

    def _slugify_offer_name(self, offer_name: str) -> str:
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(offer_name))
        return cleaned.strip("_").lower() or "offer"

    def _parse_offer_price_floor(self, price_text: str) -> float:
        digits = []
        current = []
        for ch in str(price_text):
            if ch.isdigit():
                current.append(ch)
            elif current:
                digits.append("".join(current))
                current = []
        if current:
            digits.append("".join(current))
        if not digits:
            return 0.0
        try:
            return float(digits[0])
        except ValueError:
            return 0.0

    def _normalize_offer_price_display(self, price_text: str) -> str:
        """Keep price ranges compact and consistent in generated sales assets."""
        return str(price_text or "").strip().replace(" /", "/")

    def _generate_lead_id(self) -> str:
        return f"lead_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"

    def _create_proposal_for_lead(
        self,
        *,
        ledger: list[dict[str, Any]],
        lead: dict[str, Any],
        offer_name: str = "",
        offer_key: str = "",
        custom_price: str = "",
        custom_scope: str = "",
        next_follow_up_at: str = "",
        mark_as_sent: bool = True,
        auto_generated: bool = False,
    ) -> dict[str, Path | str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        revenue_payload = self._load_revenue_config()
        offers = revenue_payload.get("core_offers", []) if isinstance(revenue_payload, dict) else []
        templates = revenue_payload.get("proposal_templates", []) if isinstance(revenue_payload, dict) else []
        offer = self._find_offer(offers, offer_key=offer_key, offer_name=offer_name)
        if offer is None:
            missing = offer_key.strip() or offer_name.strip()
            raise FileNotFoundError(f"Offer not found: {missing}")
        offer_identifier = str(offer.get("key", "")).strip() or str(offer.get("name", "")).strip()
        template = self._find_template(templates, offer_identifier=offer_identifier, offer_name=str(offer.get("name", "")).strip())

        price = custom_price.strip() or str(offer.get("price", "")).strip() or self.DEFAULT_PRICE
        scope_items = [
            str(item).strip()
            for item in (template.get("scope", []) if isinstance(template, dict) else [])
            if str(item).strip()
        ]
        if custom_scope.strip():
            scope_items = [line.strip() for line in custom_scope.splitlines() if line.strip()]
        delivery_time = (
            str(template.get("delivery_time", "")).strip()
            if isinstance(template, dict)
            else self.DEFAULT_DELIVERY_TIME
        ) or self.DEFAULT_DELIVERY_TIME

        lead["suggested_offer_key"] = lead.get("suggested_offer_key") or str(offer.get("key", "")).strip()
        lead["suggested_offer_name"] = lead.get("suggested_offer_name") or str(offer.get("name", "")).strip()
        lead["suggested_offer_price"] = lead.get("suggested_offer_price") or price
        lead["suggested_offer_delivery"] = lead.get("suggested_offer_delivery") or delivery_time

        proposal_text = self._build_lead_proposal_markdown(
            lead=lead,
            offer=offer,
            price=price,
            scope_items=scope_items,
            delivery_time=delivery_time,
        )
        proposal_offer_slug = str(offer.get("key", "")).strip() or self._slugify_offer_name(str(offer.get("name", "")).strip())
        proposal_path = self.proposals_dir / f"{str(lead.get('id', '')).strip()}_{proposal_offer_slug}.md"
        proposal_path.write_text(proposal_text, encoding="utf-8")

        current_status = str(lead.get("status", "")).strip() or "new"
        if mark_as_sent and current_status not in self.TERMINAL_STATUSES:
            lead["status"] = "proposal_sent"
            lead["proposal_sent_at"] = lead.get("proposal_sent_at") or datetime.now().isoformat(timespec="seconds")
        lead["proposal_offer_key"] = str(offer.get("key", "")).strip()
        lead["proposal_offer_name"] = str(offer.get("name", "")).strip()
        lead["proposal_path"] = str(proposal_path)
        lead["proposal_drafted_at"] = datetime.now().isoformat(timespec="seconds")
        lead["proposal_auto_generated"] = bool(auto_generated)
        if not auto_generated:
            lead["expansion_proposal_sent_at"] = ""
        if next_follow_up_at.strip():
            lead["next_follow_up_at"] = next_follow_up_at.strip()
        if not float(lead.get("estimated_value", 0.0) or 0.0):
            lead["estimated_value"] = self._parse_offer_price_floor(price)
        lead["last_action_at"] = datetime.now().isoformat(timespec="seconds")
        return {
            "proposal_path": proposal_path,
            "lead_id": str(lead.get("id", "")).strip(),
            "offer_key": str(offer.get("key", "")).strip(),
            "offer_name": str(offer.get("name", "")).strip(),
        }

    def _recommend_offer_key_for_lead(self, lead: dict[str, Any]) -> str:
        explicit = str(lead.get("suggested_offer_key", "")).strip()
        if explicit:
            return explicit
        segment = self._find_segment_config_for_lead(lead)
        if segment:
            return str(segment.get("recommended_offer_key", "")).strip()
        # A low-friction diagnosis is the safest default when an automated
        # opportunity has no segment metadata yet.
        return "content_diagnosis"

    def _recommend_outreach_channel_for_lead(self, lead: dict[str, Any]) -> str:
        explicit = str(lead.get("preferred_outreach_channel", "")).strip()
        if explicit:
            return explicit
        segment = self._find_segment_config_for_lead(lead)
        if segment:
            return str(segment.get("outreach_channel", "")).strip()
        return ""

    def _find_segment_config_for_lead(self, lead: dict[str, Any]) -> dict[str, Any] | None:
        segment_key = str(lead.get("customer_segment_key", "")).strip()
        if not segment_key:
            opportunity_id = str(lead.get("opportunity_id", "")).strip()
            segment_key = opportunity_id.split("::")[-1].strip() if opportunity_id else ""
        if not segment_key:
            return None
        growth_payload = self._load_growth_payload()
        segments = growth_payload.get("target_customer_segments", []) if isinstance(growth_payload, dict) else []
        for item in segments:
            if not isinstance(item, dict):
                continue
            if str(item.get("key", "")).strip() != segment_key:
                continue
            return item
        return None

    def _load_growth_payload(self) -> dict[str, Any]:
        growth_path = self.settings.project_root / "config" / "company_growth_engine.yaml"
        if not growth_path.exists():
            return {}
        try:
            payload = yaml.safe_load(growth_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.logger.warning("Failed to load growth engine config for lead automation: %s", exc)
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _default_auto_follow_up_at(self) -> str:
        follow_up = datetime.now() + timedelta(days=2)
        follow_up = follow_up.replace(hour=10, minute=0, second=0, microsecond=0)
        return follow_up.isoformat(sep=" ", timespec="minutes")

    def _build_outreach_opening(self, lead: dict[str, Any]) -> str:
        topic_name = str(lead.get("name", "")).strip().replace("把热点文章变成客户开发入口：", "")
        segment_name = str(lead.get("customer_segment_name", "")).strip() or "你的团队"
        offer_name = str(lead.get("suggested_offer_name", "")).strip() or "这项服务"
        return (
            f"我最近在跟踪「{topic_name}」这类话题，看到 {segment_name} 很适合把这波关注度转成真实线索。"
            f"如果你愿意，我可以先用「{offer_name}」帮你快速梳理最值得优先解决的增长卡点。"
        )

    def _build_follow_up_message(self, lead: dict[str, Any]) -> str:
        offer_name = str(lead.get("suggested_offer_name", "")).strip() or "这项服务"
        offer_price = str(lead.get("suggested_offer_price", "")).strip() or self.DEFAULT_PRICE
        if str(lead.get("proposal_path", "")).strip():
            return f"我已经按你当前场景准备了一版 {offer_name} 草案，如果你方便，我可以直接发给你看一下。"
        return f"如果你想先小步验证，可以先从 {offer_name} 开始，通常预算区间是 {offer_price}。"

    def _build_outreach_next_step(self, lead: dict[str, Any]) -> str:
        explicit = str(lead.get("recommended_next_action", "")).strip()
        if explicit:
            return explicit
        reply_temperature = str(lead.get("reply_temperature", "")).strip()
        if reply_temperature == "hot":
            if str(lead.get("proposal_path", "")).strip():
                return "立即发送已准备好的 proposal，并锁定一个 15 分钟确认通话。"
            return "先预约一个 15 分钟沟通，确认目标、预算和交付边界。"
        if reply_temperature == "warm":
            return "围绕对方的兴趣点补一条更具体的执行建议，并推进到诊断或 proposal。"
        if reply_temperature == "cold":
            return "降低打扰频率，等待更合适窗口，再用轻量切入口重新触达。"
        if str(lead.get("outreach_sent_at", "")).strip():
            return "外联已发出，优先等待回复；如 24-48 小时未回，发送一次跟进。"
        if str(lead.get("proposal_path", "")).strip():
            return "发送已准备好的 proposal 草稿，并约一个 15 分钟需求确认。"
        return "先发开场消息确认兴趣，再根据回复推进到 proposal。"

    def _split_outreach_channels(self, raw_channel: str) -> list[str]:
        text = str(raw_channel or "").strip()
        if not text:
            return []
        normalized = text.replace("／", "/").replace("、", "/").replace("，", "/").replace(",", "/")
        parts = [item.strip() for item in normalized.split("/") if item.strip()]
        seen: set[str] = set()
        result: list[str] = []
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _build_channel_messages(self, lead: dict[str, Any], channels: list[str]) -> dict[str, str]:
        opening = self._build_outreach_opening(lead)
        follow_up = self._build_follow_up_message(lead)
        topic_name = str(lead.get("name", "")).strip().replace("把热点文章变成客户开发入口：", "")
        offer_name = str(lead.get("suggested_offer_name", "")).strip() or "合作方案"
        email_subject = f"{topic_name} 这类话题，或许适合先试试 {offer_name}"
        email_body = (
            f"{opening}\n\n"
            f"{follow_up}\n\n"
            "如果你方便，我可以把更具体的执行建议或方案草稿直接发你。"
        )
        platform_dm = f"{opening} {follow_up}"
        wechat = f"{opening} {follow_up}"
        return {
            "wechat": wechat if (not channels or any("微信" in item for item in channels)) else "",
            "platform_dm": platform_dm if (not channels or any(token in item for item in channels for token in ("私信", "头条", "公众号"))) else "",
            "email_subject": email_subject if (not channels or any("邮件" in item for item in channels)) else "",
            "email_body": email_body if (not channels or any("邮件" in item for item in channels)) else "",
            "follow_up": follow_up,
        }

    def _build_reply_triage_playbook(self, ledger: list[dict[str, Any]]) -> str:
        pack = self._build_reply_triage_pack(ledger)
        items = pack.get("items", []) if isinstance(pack, dict) else []
        lines = ["# 回复分诊与成交推进手册", ""]
        if not items:
            lines.append("- 暂无可分诊的自动线索。")
            return "\n".join(lines).strip() + "\n"

        summary = pack.get("summary", {}) if isinstance(pack, dict) else {}
        lines.append(f"- 可分诊线索：{summary.get('total_items', 0)}")
        lines.append(f"- 直接约沟通优先：{summary.get('call_ready_items', 0)}")
        lines.append(f"- 适合先发诊断/研究：{summary.get('light_offer_items', 0)}")
        lines.append("")

        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            reply_modes = item.get("reply_modes", {}) if isinstance(item.get("reply_modes"), dict) else {}
            lines.append(f"## {str(item.get('lead_name', '')).strip() or '未命名线索'}")
            lines.append("")
            lines.append(f"- 推荐报价：{str(item.get('suggested_offer_name', '')).strip()}")
            lines.append(f"- 回复冷淡时：{str(reply_modes.get('cold', '')).strip()}")
            lines.append(f"- 回复感兴趣时：{str(reply_modes.get('warm', '')).strip()}")
            lines.append(f"- 回复明确预算时：{str(reply_modes.get('hot', '')).strip()}")
            lines.append(f"- 推进动作：{str(item.get('conversion_step', '')).strip()}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_reply_triage_pack(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = self._list_conversion_candidates(ledger)
        items: list[dict[str, Any]] = []
        for item in candidates[:20]:
            items.append(
                {
                    "lead_id": str(item.get("id", "")).strip(),
                    "lead_name": str(item.get("name", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "suggested_offer_name": str(item.get("suggested_offer_name", "")).strip(),
                    "suggested_offer_price": str(item.get("suggested_offer_price", "")).strip(),
                    "reply_modes": {
                        "cold": self._build_cold_reply(item),
                        "warm": self._build_warm_reply(item),
                        "hot": self._build_hot_reply(item),
                    },
                    "reply_temperature": str(item.get("reply_temperature", "")).strip(),
                    "last_reply_text": str(item.get("last_reply_text", "")).strip(),
                    "next_reply_script": str(item.get("next_reply_script", "")).strip(),
                    "conversion_step": self._build_conversion_step(item),
                    "proposal_path": str(item.get("proposal_path", "")).strip(),
                }
            )

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "call_ready_items": len([item for item in items if "15 分钟" in str(item.get("conversion_step", ""))]),
                "light_offer_items": len([item for item in items if "诊断" in str(item.get("suggested_offer_name", "")) or "研究" in str(item.get("suggested_offer_name", ""))]),
            },
            "items": items,
        }

    def _build_conversion_push_pack(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = self._list_conversion_candidates(ledger)
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "items": [
                {
                    "lead_id": str(item.get("id", "")).strip(),
                    "lead_name": str(item.get("name", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "suggested_offer_name": str(item.get("suggested_offer_name", "")).strip(),
                    "budget_anchor": str(item.get("suggested_offer_price", "")).strip(),
                    "qualification_questions": self._build_qualification_questions(item),
                    "call_to_action": self._build_call_to_action(item),
                    "objection_handle": self._build_objection_handle(item),
                    "reply_temperature": str(item.get("reply_temperature", "")).strip(),
                    "recommended_next_action": self._build_outreach_next_step(item),
                    "proposal_path": str(item.get("proposal_path", "")).strip(),
                }
                for item in candidates[:20]
            ],
        }

    def _build_sales_execution_dashboard(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        now = datetime.now()
        execution_items: list[dict[str, Any]] = []
        unsent_outreach = 0
        follow_up_due = 0
        proposal_send_due = 0
        hot_reply_due = 0
        call_booked_due = 0

        for item in ledger:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            source = str(item.get("source", "")).strip()
            is_referral_lead = source == "customer_referral" and status not in self.TERMINAL_STATUSES
            is_generated_prospect = (
                status not in self.TERMINAL_STATUSES
                and bool(item.get("auto_generated"))
                and source == "revenue_engine"
            )
            is_expansion_revenue_motion = (
                source == "revenue_engine"
                and status == "won"
                and bool(item.get("proposal_auto_generated"))
                and str(item.get("expansion_opportunity_type", "")).strip() != ""
            )
            if not (is_generated_prospect or is_referral_lead or is_expansion_revenue_motion):
                continue

            next_follow_up_at = str(item.get("next_follow_up_at", "")).strip()
            reply_temperature = str(item.get("reply_temperature", "")).strip()
            proposal_path = str(item.get("proposal_path", "")).strip()
            outreach_sent_at = str(item.get("outreach_sent_at", "")).strip()
            call_scheduled_for = str(item.get("call_scheduled_for", "")).strip()
            channels = self._split_outreach_channels(str(item.get("preferred_outreach_channel", "")).strip())

            if is_expansion_revenue_motion:
                expansion_proposal_sent_at = str(item.get("expansion_proposal_sent_at", "")).strip()
                if proposal_path and not expansion_proposal_sent_at:
                    proposal_send_due += 1
                    execution_items.append(
                        self._build_execution_item(
                            item,
                            action_type="send_expansion_proposal",
                            priority="critical",
                            reason="Expansion proposal is ready for an existing customer and should be sent now.",
                            channels=channels,
                        )
                    )
                elif self._is_follow_up_due(next_follow_up_at, now):
                    follow_up_due += 1
                    execution_items.append(
                        self._build_execution_item(
                            item,
                            action_type="follow_up_expansion_proposal",
                            priority="high",
                            reason="Expansion follow-up is due for an existing customer.",
                            channels=channels,
                        )
                    )
                continue

            if not outreach_sent_at:
                unsent_outreach += 1
                execution_items.append(
                    self._build_execution_item(
                        item,
                        action_type="send_referral_intro" if is_referral_lead else "send_first_touch",
                        priority="high" if (status == "qualified" or is_referral_lead) else "medium",
                        reason=(
                            "Warm referral lead is ready for the first intro touch."
                            if is_referral_lead
                            else "This lead has not received the first outbound touch yet."
                        ),
                        channels=channels,
                    )
                )
                continue

            if reply_temperature == "hot":
                hot_reply_due += 1
                execution_items.append(
                    self._build_execution_item(
                        item,
                        action_type="close_hot_reply",
                        priority="critical",
                        reason="Hot reply detected. Push proposal or call booking immediately.",
                        channels=channels,
                    )
                )
                continue

            if proposal_path and not str(item.get("proposal_sent_at", "")).strip():
                proposal_send_due += 1
                execution_items.append(
                    self._build_execution_item(
                        item,
                        action_type="send_proposal",
                        priority="high",
                        reason="Proposal exists but has not been marked as sent.",
                        channels=channels,
                    )
                )

            if call_scheduled_for:
                call_booked_due += 1
                execution_items.append(
                    self._build_execution_item(
                        item,
                        action_type="prepare_booked_call",
                        priority="high",
                        reason="Call has been booked. Prepare agenda and close plan.",
                        channels=channels,
                    )
                )

            if self._is_follow_up_due(next_follow_up_at, now):
                follow_up_due += 1
                execution_items.append(
                    self._build_execution_item(
                        item,
                        action_type="send_follow_up",
                        priority="high" if status in {"qualified", "proposal_sent"} else "medium",
                        reason="Follow-up time has arrived or is overdue.",
                        channels=channels,
                    )
                )

        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        execution_items.sort(
            key=lambda item: (
                priority_order.get(str(item.get("priority", "")).strip(), 9),
                str(item.get("next_follow_up_at", "")).strip() or "9999-99-99 99:99",
                str(item.get("lead_name", "")).strip(),
            )
        )
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_execution_items": len(execution_items),
                "unsent_outreach_leads": unsent_outreach,
                "follow_up_due_leads": follow_up_due,
                "proposal_send_due_leads": proposal_send_due,
                "hot_reply_close_leads": hot_reply_due,
                "call_booked_due_leads": call_booked_due,
            },
            "items": execution_items[:30],
        }

    def _build_sales_execution_brief(self, dashboard: dict[str, Any]) -> str:
        summary = dashboard.get("summary", {}) if isinstance(dashboard, dict) else {}
        items = dashboard.get("items", []) if isinstance(dashboard, dict) else []
        lines = ["# Sales Execution Brief", ""]
        lines.append(f"- Generated at: {dashboard.get('generated_at', '')}")
        lines.append(f"- Total actions: {summary.get('total_execution_items', 0)}")
        lines.append(f"- First-touch sends pending: {summary.get('unsent_outreach_leads', 0)}")
        lines.append(f"- Follow-ups due: {summary.get('follow_up_due_leads', 0)}")
        lines.append(f"- Proposals ready to send: {summary.get('proposal_send_due_leads', 0)}")
        lines.append(f"- Hot replies to close: {summary.get('hot_reply_close_leads', 0)}")
        lines.append("")
        for item in items[:15]:
            if not isinstance(item, dict):
                continue
            lines.append(f"## {str(item.get('lead_name', '')).strip() or 'Unnamed lead'}")
            lines.append("")
            lines.append(f"- Action: {str(item.get('action_type', '')).strip()}")
            lines.append(f"- Priority: {str(item.get('priority', '')).strip()}")
            lines.append(f"- Recommended move: {str(item.get('recommended_next_action', '')).strip()}")
            lines.append(f"- Reason: {str(item.get('reason', '')).strip()}")
            lines.append(f"- Suggested channel: {str(item.get('preferred_channel', '')).strip() or 'TBD'}")
            if str(item.get("next_follow_up_at", "")).strip():
                lines.append(f"- Next follow-up: {str(item.get('next_follow_up_at', '')).strip()}")
            if str(item.get("proposal_path", "")).strip():
                lines.append(f"- Proposal: {str(item.get('proposal_path', '')).strip()}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_customer_success_dashboard(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        delivery_start_due = 0
        delivery_complete_due = 0
        testimonial_due = 0
        expansion_due = 0

        for item in ledger:
            if not isinstance(item, dict):
                continue
            if str(item.get("status", "")).strip() != "won":
                continue

            lead_name = str(item.get("name", "")).strip()
            recommended_action = str(item.get("recommended_next_action", "")).strip()

            if not str(item.get("delivery_started_at", "")).strip():
                delivery_start_due += 1
                items.append(
                    {
                        "lead_id": str(item.get("id", "")).strip(),
                        "lead_name": lead_name,
                        "action_type": "start_delivery",
                        "priority": "high",
                        "reason": "Payment has been received but delivery has not started.",
                        "recommended_next_action": recommended_action or "启动交付并对齐第一阶段交付物。",
                    }
                )
                continue

            if not str(item.get("delivery_completed_at", "")).strip():
                delivery_complete_due += 1
                items.append(
                    {
                        "lead_id": str(item.get("id", "")).strip(),
                        "lead_name": lead_name,
                        "action_type": "complete_delivery",
                        "priority": "high",
                        "reason": "Delivery is in progress; push to a visible outcome and close loop.",
                        "recommended_next_action": recommended_action or "推进交付完成并沉淀成果截图/案例。",
                    }
                )

            if str(item.get("delivery_completed_at", "")).strip() and not str(item.get("testimonial_requested_at", "")).strip():
                testimonial_due += 1
                items.append(
                    {
                        "lead_id": str(item.get("id", "")).strip(),
                        "lead_name": lead_name,
                        "action_type": "request_testimonial",
                        "priority": "medium",
                        "reason": "Delivery completed but no testimonial request recorded yet.",
                        "recommended_next_action": "索取一句评价或一段案例反馈，沉淀成社证。",
                    }
                )

            if str(item.get("testimonial_requested_at", "")).strip() and not str(item.get("expansion_identified_at", "")).strip():
                expansion_due += 1
                items.append(
                    {
                        "lead_id": str(item.get("id", "")).strip(),
                        "lead_name": lead_name,
                        "action_type": "identify_expansion",
                        "priority": "medium",
                        "reason": "Customer is warm; look for renewal, upsell, or referral motion.",
                        "recommended_next_action": "识别续费、升级服务或转介绍机会。",
                    }
                )

        priority_order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda item: (priority_order.get(str(item.get("priority", "")).strip(), 9), str(item.get("lead_name", "")).strip()))
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "delivery_start_due_leads": delivery_start_due,
                "delivery_complete_due_leads": delivery_complete_due,
                "testimonial_due_leads": testimonial_due,
                "expansion_due_leads": expansion_due,
            },
            "items": items[:30],
        }

    def _build_customer_success_brief(self, dashboard: dict[str, Any]) -> str:
        summary = dashboard.get("summary", {}) if isinstance(dashboard, dict) else {}
        items = dashboard.get("items", []) if isinstance(dashboard, dict) else []
        lines = ["# Customer Success Brief", ""]
        lines.append(f"- Generated at: {dashboard.get('generated_at', '')}")
        lines.append(f"- Total actions: {summary.get('total_items', 0)}")
        lines.append(f"- Delivery start due: {summary.get('delivery_start_due_leads', 0)}")
        lines.append(f"- Delivery complete due: {summary.get('delivery_complete_due_leads', 0)}")
        lines.append(f"- Testimonial due: {summary.get('testimonial_due_leads', 0)}")
        lines.append(f"- Expansion due: {summary.get('expansion_due_leads', 0)}")
        lines.append("")
        for item in items[:15]:
            if not isinstance(item, dict):
                continue
            lines.append(f"## {str(item.get('lead_name', '')).strip() or 'Unnamed customer'}")
            lines.append("")
            lines.append(f"- Action: {str(item.get('action_type', '')).strip()}")
            lines.append(f"- Priority: {str(item.get('priority', '')).strip()}")
            lines.append(f"- Reason: {str(item.get('reason', '')).strip()}")
            lines.append(f"- Recommended move: {str(item.get('recommended_next_action', '')).strip()}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_expansion_pipeline(self, ledger: list[dict[str, Any]]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for item in ledger:
            if not isinstance(item, dict):
                continue
            expansion_type = str(item.get("expansion_opportunity_type", "")).strip()
            referral_source = str(item.get("referral_source_lead_id", "")).strip()
            if not expansion_type and not referral_source:
                continue
            items.append(
                {
                    "lead_id": str(item.get("id", "")).strip(),
                    "lead_name": str(item.get("name", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "source": str(item.get("source", "")).strip(),
                    "expansion_type": expansion_type,
                    "expansion_identified_at": str(item.get("expansion_identified_at", "")).strip(),
                    "referral_source_lead_id": referral_source,
                    "referral_source_customer_name": str(item.get("referral_source_customer_name", "")).strip(),
                    "proposal_path": str(item.get("proposal_path", "")).strip(),
                    "recommended_next_action": str(item.get("recommended_next_action", "")).strip(),
                    "estimated_value": float(item.get("estimated_value", 0.0) or 0.0),
                }
            )
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "summary": {
                "total_items": len(items),
                "referral_leads": len([item for item in items if str(item.get("source", "")).strip() == "customer_referral"]),
                "expansion_candidates": len([item for item in items if str(item.get("expansion_type", "")).strip()]),
                "proposal_ready_items": len([item for item in items if str(item.get("proposal_path", "")).strip()]),
            },
            "items": items[:50],
        }

    def _build_expansion_pipeline_brief(self, pipeline: dict[str, Any]) -> str:
        summary = pipeline.get("summary", {}) if isinstance(pipeline, dict) else {}
        items = pipeline.get("items", []) if isinstance(pipeline, dict) else []
        lines = ["# Expansion Pipeline Brief", ""]
        lines.append(f"- Generated at: {pipeline.get('generated_at', '')}")
        lines.append(f"- Total items: {summary.get('total_items', 0)}")
        lines.append(f"- Referral leads: {summary.get('referral_leads', 0)}")
        lines.append(f"- Expansion candidates: {summary.get('expansion_candidates', 0)}")
        lines.append(f"- Proposal-ready items: {summary.get('proposal_ready_items', 0)}")
        lines.append("")
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            lines.append(f"## {str(item.get('lead_name', '')).strip() or 'Unnamed lead'}")
            lines.append("")
            lines.append(f"- Status: {str(item.get('status', '')).strip()}")
            if str(item.get("expansion_type", "")).strip():
                lines.append(f"- Expansion type: {str(item.get('expansion_type', '')).strip()}")
            if str(item.get("referral_source_customer_name", "")).strip():
                lines.append(f"- Referred by: {str(item.get('referral_source_customer_name', '')).strip()}")
            lines.append(f"- Estimated value: {float(item.get('estimated_value', 0.0) or 0.0):.2f}")
            lines.append(f"- Recommended move: {str(item.get('recommended_next_action', '')).strip()}")
            if str(item.get("proposal_path", "")).strip():
                lines.append(f"- Proposal: {str(item.get('proposal_path', '')).strip()}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _recommend_expansion_offer_key(self, lead: dict[str, Any], expansion_type: str) -> str:
        normalized_type = str(expansion_type).strip().lower()
        if normalized_type in {"renewal", "upsell"}:
            return "growth_advisory"
        if normalized_type == "referral":
            return str(lead.get("suggested_offer_key", "")).strip() or "research_sprint"
        return str(lead.get("suggested_offer_key", "")).strip() or "growth_advisory"

    def _build_channel_batch_markdown(self, ledger: list[dict[str, Any]], *, channel_key: str) -> str:
        pack = self._build_outbound_campaign_pack(ledger)
        items = pack.get("items", []) if isinstance(pack, dict) else []
        channel_labels = {
            "wechat": "WeChat",
            "email": "Email",
            "platform_dm": "Platform DM",
        }
        header = channel_labels.get(channel_key, channel_key)
        lines = [f"# {header} Batch Messages", ""]
        filtered_count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            messages = item.get("messages", {}) if isinstance(item.get("messages"), dict) else {}
            message_key = "wechat" if channel_key == "wechat" else ("platform_dm" if channel_key == "platform_dm" else "email_body")
            primary_message = str(messages.get(message_key, "")).strip()
            if not primary_message:
                continue
            filtered_count += 1
            lines.append(f"## {str(item.get('lead_name', '')).strip() or 'Unnamed lead'}")
            lines.append("")
            lines.append(f"- Lead ID: {str(item.get('lead_id', '')).strip()}")
            lines.append(f"- Status: {str(item.get('status', '')).strip()}")
            lines.append(f"- Suggested offer: {str(item.get('suggested_offer_name', '')).strip()}")
            if channel_key == "email":
                lines.append(f"- Subject: {str(messages.get('email_subject', '')).strip()}")
                lines.append("")
                lines.append(str(messages.get("email_body", "")).strip())
            else:
                lines.append(primary_message)
            if str(messages.get("follow_up", "")).strip():
                lines.append("")
                lines.append(f"Follow-up: {str(messages.get('follow_up', '')).strip()}")
            lines.append("")
        if filtered_count == 0:
            lines.append("- No batch-ready messages for this channel right now.")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_execution_item(
        self,
        lead: dict[str, Any],
        *,
        action_type: str,
        priority: str,
        reason: str,
        channels: list[str],
    ) -> dict[str, Any]:
        return {
            "lead_id": str(lead.get("id", "")).strip(),
            "lead_name": str(lead.get("name", "")).strip(),
            "status": str(lead.get("status", "")).strip(),
            "action_type": action_type,
            "priority": priority,
            "reason": reason,
            "preferred_channel": channels[0] if channels else str(lead.get("preferred_outreach_channel", "")).strip(),
            "channel_list": channels,
            "reply_temperature": str(lead.get("reply_temperature", "")).strip(),
            "next_follow_up_at": str(lead.get("next_follow_up_at", "")).strip(),
            "proposal_path": str(lead.get("proposal_path", "")).strip(),
            "recommended_next_action": self._build_outreach_next_step(lead),
        }

    def _append_outreach_execution_event(self, payload: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        events = self._load_outreach_execution_events()
        event = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
        }
        event.update({key: value for key, value in payload.items()})
        events.insert(0, event)
        self.outreach_execution_log_path.write_text(
            json.dumps(events[:200], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_outreach_execution_events(self) -> list[dict[str, Any]]:
        if not self.outreach_execution_log_path.exists():
            return []
        try:
            payload = json.loads(self.outreach_execution_log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _list_conversion_candidates(self, ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            item
            for item in ledger
            if isinstance(item, dict)
            and bool(item.get("auto_generated"))
            and str(item.get("source", "")).strip() == "revenue_engine"
            and str(item.get("status", "")).strip() in {"new", "qualified", "proposal_sent"}
        ]

    def _build_cold_reply(self, lead: dict[str, Any]) -> str:
        offer_name = str(lead.get("suggested_offer_name", "")).strip() or "这项服务"
        return f"没关系，我先不打扰。后面如果你想先小步验证，也可以先从 {offer_name} 这种轻量切入口开始。"

    def _build_warm_reply(self, lead: dict[str, Any]) -> str:
        offer_name = str(lead.get("suggested_offer_name", "")).strip() or "这项服务"
        return f"如果你感兴趣，我建议先用 {offer_name} 把最关键的卡点梳理清楚，我也可以按你的当前情况给一个更具体的执行建议。"

    def _build_hot_reply(self, lead: dict[str, Any]) -> str:
        if str(lead.get("proposal_path", "")).strip():
            return "太好了，我这边已经有一版草案，可以直接发你，并约个 15 分钟把目标、预算和交付边界确认掉。"
        return "太好了，那我建议直接约个 15 分钟，把目标、预算和交付边界确认一下，我再给你整理正式方案。"

    def _build_conversion_step(self, lead: dict[str, Any]) -> str:
        if str(lead.get("proposal_path", "")).strip():
            return "优先发 proposal，并推进 15 分钟确认通话。"
        return "先确认兴趣和预算，再推进到正式 proposal。"

    def _build_qualification_questions(self, lead: dict[str, Any]) -> list[str]:
        offer_name = str(lead.get("suggested_offer_name", "")).strip() or "这项服务"
        return [
            "你现在最想先解决的是流量、线索还是成交？",
            f"如果先从 {offer_name} 开始，你最希望 30 天内看到什么结果？",
            "当前这件事是谁来负责推进，是否已经有预算范围？",
        ]

    def _build_call_to_action(self, lead: dict[str, Any]) -> str:
        if str(lead.get("proposal_path", "")).strip():
            return "把草案发过去，并邀请对方约一个 15 分钟确认通话。"
        return "邀请对方约一个 15 分钟诊断 / 需求确认，再进入正式方案。"

    def _build_objection_handle(self, lead: dict[str, Any]) -> str:
        offer_name = str(lead.get("suggested_offer_name", "")).strip() or "这项服务"
        offer_price = str(lead.get("suggested_offer_price", "")).strip() or self.DEFAULT_PRICE
        return f"如果对方担心预算，可以强调先从 {offer_name} 的最小版本开始，通常可参考 {offer_price} 这个区间。"

    def _append_note_line(self, existing_notes: Any, line: str) -> str:
        prefix = str(existing_notes or "").strip()
        suffix = str(line or "").strip()
        if not suffix:
            return prefix
        if not prefix:
            return suffix
        return f"{prefix}\n{suffix}"

    def _build_follow_up_slot(self, *, days: int, hour: int) -> str:
        target = datetime.now() + timedelta(days=max(days, 0))
        target = target.replace(hour=hour, minute=0, second=0, microsecond=0)
        return target.isoformat(sep=" ", timespec="minutes")

    def _default_follow_up_for_reply_temperature(self, reply_temperature: str) -> str:
        normalized = str(reply_temperature).strip().lower()
        if normalized == "hot":
            return self._build_follow_up_slot(days=0, hour=18)
        if normalized == "warm":
            return self._build_follow_up_slot(days=1, hour=11)
        return self._build_follow_up_slot(days=5, hour=15)

    def _classify_reply_temperature(self, reply_text: str) -> str:
        normalized = str(reply_text or "").strip().lower()
        if not normalized:
            return "cold"

        hot_keywords = (
            "报价",
            "预算",
            "合作",
            "方案",
            "细聊",
            "电话",
            "通话",
            "会议",
            "约",
            "安排",
            "可以聊",
            "发我方案",
            "schedule",
            "call",
            "meeting",
            "proposal",
            "price",
            "budget",
            "send",
        )
        cold_keywords = (
            "不用",
            "不需要",
            "暂时不",
            "暂时没有",
            "以后再说",
            "先不了",
            "没预算",
            "算了",
            "pass",
            "not now",
            "no need",
            "later",
        )
        warm_keywords = (
            "了解",
            "看看",
            "有兴趣",
            "感兴趣",
            "介绍",
            "资料",
            "怎么做",
            "怎么合作",
            "发我",
            "more",
            "interested",
            "curious",
        )

        hot_score = sum(1 for token in hot_keywords if token in normalized)
        cold_score = sum(1 for token in cold_keywords if token in normalized)
        warm_score = sum(1 for token in warm_keywords if token in normalized)

        if cold_score > 0 and hot_score == 0:
            return "cold"
        if hot_score > 0:
            return "hot"
        if warm_score > 0:
            return "warm"
        return "warm"

    def _build_triage_reply_script(self, lead: dict[str, Any], reply_temperature: str) -> str:
        normalized = str(reply_temperature).strip().lower()
        if normalized == "hot":
            return self._build_hot_reply(lead)
        if normalized == "cold":
            return self._build_cold_reply(lead)
        return self._build_warm_reply(lead)

    def _build_triage_next_action(self, lead: dict[str, Any], reply_temperature: str) -> str:
        normalized = str(reply_temperature).strip().lower()
        if normalized == "hot":
            if str(lead.get("proposal_path", "")).strip():
                return "立刻发送 proposal，并推动 15 分钟确认通话。"
            return "优先约 15 分钟通话；若预算和目标明确，马上生成并发送 proposal。"
        if normalized == "cold":
            return "降低频率，先结束本轮推进，5 天后再用轻量切入口重启触达。"
        if str(lead.get("proposal_path", "")).strip():
            return "补充一条更具体的执行建议，并把现成 proposal 发过去。"
        return "补充一条针对性建议，确认兴趣与预算，再推进到 proposal。"

    def _build_generated_lead_notes(self, item: dict[str, Any]) -> str:
        lines = [
            "[AUTO] Revenue engine generated this lead.",
            f"Priority: {str(item.get('priority', '')).strip() or 'medium'}",
            f"Why now: {str(item.get('why_now', '')).strip()}",
            f"Recommended action: {str(item.get('recommended_action', '')).strip()}",
        ]
        asset_hint = str(item.get("asset_hint", "")).strip()
        if asset_hint:
            lines.append(f"Asset hint: {asset_hint}")
        offer_name = str(item.get("recommended_offer_name", "")).strip()
        offer_key = str(item.get("recommended_offer_key", "")).strip()
        offer_price = str(item.get("recommended_offer_price", "")).strip()
        if offer_name or offer_key:
            lines.append(f"Recommended offer: {offer_name or offer_key}")
        if offer_price:
            lines.append(f"Suggested price: {offer_price}")
        return "\n".join(line for line in lines if line.strip())

    def _is_follow_up_due(self, next_follow_up_at: str, now: datetime) -> bool:
        text = str(next_follow_up_at or "").strip()
        if not text:
            return False
        try:
            scheduled = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                scheduled = datetime.strptime(text, "%Y-%m-%d %H:%M")
            except ValueError:
                return False
        return scheduled <= now

    def enrich_generated_leads_with_recommendations(self) -> dict[str, Any]:
        ledger = self._load_ledger()
        updated = 0
        for lead in ledger:
            if not isinstance(lead, dict):
                continue
            if not bool(lead.get("auto_generated")) or str(lead.get("source", "")).strip() != "revenue_engine":
                continue
            offer_key = self._recommend_offer_key_for_lead(lead)
            if not offer_key:
                continue
            offer = self._find_offer(
                self._load_revenue_config().get("core_offers", []),
                offer_key=offer_key,
            )
            if offer is None:
                continue
            changed = False
            if not str(lead.get("suggested_offer_key", "")).strip():
                lead["suggested_offer_key"] = str(offer.get("key", "")).strip() or offer_key
                changed = True
            if not str(lead.get("suggested_offer_name", "")).strip():
                lead["suggested_offer_name"] = str(offer.get("name", "")).strip()
                changed = True
            if not str(lead.get("suggested_offer_price", "")).strip():
                lead["suggested_offer_price"] = self._normalize_offer_price_display(
                    str(offer.get("price", "")).strip()
                )
                changed = True
            if not str(lead.get("suggested_offer_delivery", "")).strip():
                lead["suggested_offer_delivery"] = str(offer.get("delivery", "")).strip()
                changed = True
            if not str(lead.get("preferred_outreach_channel", "")).strip():
                preferred_channel = self._recommend_outreach_channel_for_lead(lead)
                if preferred_channel:
                    lead["preferred_outreach_channel"] = preferred_channel
                    changed = True
            if not float(lead.get("estimated_value", 0.0) or 0.0):
                lead["estimated_value"] = self._parse_offer_price_floor(str(offer.get("price", "")).strip())
                changed = True
            if changed:
                lead["last_action_at"] = datetime.now().isoformat(timespec="seconds")
                updated += 1
        self._save_ledger(ledger)
        self.ensure_assets()
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "updated": updated,
            "processed": len(ledger),
        }
