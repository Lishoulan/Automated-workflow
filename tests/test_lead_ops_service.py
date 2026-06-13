from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from toutiao_content_factory.services.lead_ops_service import LeadOpsService


def _build_settings(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    output_dir = tmp_path / "output"
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        project_root=project_root,
        output_dir=output_dir,
        account_key="default",
        brand_name="测试品牌",
    )


def _write_revenue_config(settings: SimpleNamespace) -> None:
    revenue_config = settings.project_root / "config" / "company_revenue.yaml"
    revenue_config.write_text(
        (
            'core_offers:\n'
            '  - key: "content_diagnosis"\n'
            '    name: "内容代运营诊断"\n'
            '    price: "999-2999元 / 次"\n'
            '    target_customer: "增长停滞账号"\n'
            '    delivery: "诊断报告"\n'
            '  - key: "growth_advisory"\n'
            '    name: "顾问式增长陪跑"\n'
            '    price: "5000-20000元 / 月"\n'
            '    target_customer: "希望拿到线索的团队"\n'
            '    delivery: "周会 + SOP"\n'
            'proposal_templates:\n'
            '  - offer_key: "content_diagnosis"\n'
            '    offer_name: "内容代运营诊断"\n'
            '    scope:\n'
            '      - "诊断当前渠道和转化链路"\n'
            '    delivery_time: "2-3个工作日"\n'
            '  - offer_key: "growth_advisory"\n'
            '    offer_name: "顾问式增长陪跑"\n'
            '    scope:\n'
            '      - "每周复盘与策略会"\n'
            '    delivery_time: "按月交付"\n'
        ),
        encoding="utf-8",
    )


def test_record_lead_and_export_assets(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="张三",
        contact="wechat:zhangsan",
        intent="咨询",
        source="direct_message",
        notes="对顾问式陪跑感兴趣",
    )
    exported = service.ensure_assets()

    assert entry["name"] == "张三"
    assert Path(exported["lead_ledger"]).exists()
    assert Path(exported["lead_reply_manual"]).exists()
    assert Path(exported["lead_followup_queue"]).exists()
    assert Path(exported["lead_summary"]).exists()

    summary = service.load_summary()
    assert summary["total_leads"] == 1
    assert summary["recent_leads"][0]["intent"] == "咨询"


def test_update_lead_tracks_revenue_and_stage(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="李四",
        contact="wechat:lisi",
        intent="诊断",
        source="direct_message",
        estimated_value=2999,
    )
    updated = service.update_lead(
        lead_id=entry["id"],
        status="won",
        actual_value=2999,
        next_follow_up_at="2026-06-09 10:00",
    )

    assert updated["status"] == "won"
    assert float(updated["actual_value"]) == 2999
    assert updated["won_at"]
    summary = service.load_summary()
    assert summary["won_leads"] == 1
    assert float(summary["actual_revenue"]) == 2999


def test_generate_lead_proposal_uses_offer_key_and_updates_status(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="王五",
        contact="wechat:wangwu",
        intent="诊断",
        source="direct_message",
    )
    result = service.generate_lead_proposal(
        lead_id=entry["id"],
        offer_key="content_diagnosis",
        next_follow_up_at="2026-06-10 09:00",
    )

    assert Path(result["proposal_path"]).exists()
    ledger = service.list_leads()
    assert ledger[0]["status"] == "proposal_sent"
    assert ledger[0]["proposal_offer_key"] == "content_diagnosis"


def test_generate_lead_proposal_does_not_downgrade_won_lead(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="示例客户",
        contact="wechat:demo",
        intent="咨询",
        source="direct_message",
        estimated_value=6800,
    )
    service.update_lead(
        lead_id=entry["id"],
        status="won",
        actual_value=6800,
    )
    service.generate_lead_proposal(
        lead_id=entry["id"],
        offer_key="growth_advisory",
    )

    latest = service.list_leads()[0]
    assert latest["status"] == "won"
    assert float(latest["actual_value"]) == 6800
    assert latest["proposal_offer_key"] == "growth_advisory"


def test_existing_dirty_won_state_is_normalized_on_load(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = LeadOpsService(settings, logging.getLogger("test"))
    service.output_dir.mkdir(parents=True, exist_ok=True)
    service.ledger_path.write_text(
        (
            '[\n'
            '  {\n'
            '    "id": "lead_1",\n'
            '    "name": "旧客户",\n'
            '    "contact": "wechat:old",\n'
            '    "intent": "咨询",\n'
            '    "source": "direct_message",\n'
            '    "status": "proposal_sent",\n'
            '    "estimated_value": 5000,\n'
            '    "actual_value": 6800,\n'
            '    "notes": "已成交",\n'
            '    "last_action_at": "2026-06-08T16:59:50"\n'
            '  }\n'
            ']'
        ),
        encoding="utf-8",
    )

    latest = service.list_leads()[0]
    assert latest["status"] == "won"
    assert latest["won_at"]


def test_sync_generated_opportunities_inserts_updates_and_skips_closed(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = LeadOpsService(settings, logging.getLogger("test"))
    _write_revenue_config(settings)

    open_lead = service.record_lead(
        name="Old auto lead",
        contact="auto://customer_pitch::old",
        intent="咨询",
        source="revenue_engine",
        status="new",
        notes="old note",
    )
    service.update_lead(
        lead_id=open_lead["id"],
        notes="still open",
    )
    ledger = service.list_leads()
    ledger[0]["opportunity_id"] = "customer_pitch::same"
    ledger[0]["auto_generated"] = True
    ledger[0]["opportunity_type"] = "customer_pitch"
    ledger[0]["asset_hint"] = "old"
    service._save_ledger(ledger)

    closed_lead = service.record_lead(
        name="Closed auto lead",
        contact="auto://customer_pitch::closed",
        intent="咨询",
        source="revenue_engine",
        status="won",
        estimated_value=1000,
    )
    ledger = service.list_leads()
    for item in ledger:
        if item["id"] == closed_lead["id"]:
            item["opportunity_id"] = "customer_pitch::closed"
            item["auto_generated"] = True
            item["opportunity_type"] = "customer_pitch"
    service._save_ledger(ledger)

    result = service.sync_generated_opportunities(
        [
            {
                "id": "customer_pitch::same",
                "type": "customer_pitch",
                "priority": "high",
                "headline": "Updated generated lead",
                "why_now": "Fresh signal",
                "recommended_action": "Follow up fast",
                "asset_hint": "customer_targets.json",
            },
            {
                "id": "customer_pitch::new_one",
                "type": "customer_pitch",
                "priority": "medium",
                "headline": "New generated lead",
                "why_now": "New signal",
                "recommended_action": "Open outreach",
                "asset_hint": "outreach_playbook.md",
            },
            {
                "id": "customer_pitch::closed",
                "type": "customer_pitch",
                "priority": "high",
                "headline": "Should stay closed",
                "why_now": "Existing won deal",
                "recommended_action": "Skip",
                "asset_hint": "ignore",
            },
        ],
        next_follow_up_at="2026-06-20 10:00",
    )

    assert result["inserted"] == 1
    assert result["updated"] == 1
    assert result["skipped"] == 1

    refreshed = service.list_leads(limit=10)
    inserted = next(item for item in refreshed if item.get("opportunity_id") == "customer_pitch::new_one")
    updated = next(item for item in refreshed if item.get("opportunity_id") == "customer_pitch::same")
    closed = next(item for item in refreshed if item.get("opportunity_id") == "customer_pitch::closed")

    assert inserted["source"] == "revenue_engine"
    assert inserted["auto_generated"] is True
    assert inserted["status"] == "new"
    assert inserted["suggested_offer_price"] == "999-2999元/ 次"
    assert updated["name"] == "Updated generated lead"
    assert updated["status"] == "qualified"
    assert updated["next_follow_up_at"] == "2026-06-20 10:00"
    assert closed["status"] == "won"


def test_auto_draft_generated_proposals_creates_drafts_for_qualified_leads(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    growth_config = settings.project_root / "config" / "company_growth_engine.yaml"
    growth_config.write_text(
        (
            'target_customer_segments:\n'
            '  - key: "ai_founder_team"\n'
            '    recommended_offer_key: "growth_advisory"\n'
        ),
        encoding="utf-8",
    )
    service = LeadOpsService(settings, logging.getLogger("test"))

    service.sync_generated_opportunities(
        [
            {
                "id": "customer_pitch::qualified_one",
                "type": "customer_pitch",
                "priority": "high",
                "headline": "Qualified generated lead",
                "why_now": "High urgency",
                "recommended_action": "Send a proposal",
                "asset_hint": "customer_targets.json",
                "segment_key": "ai_founder_team",
                "segment_name": "AI 创始团队",
                "recommended_offer_key": "growth_advisory",
                "recommended_offer_name": "顾问式增长陪跑",
                "recommended_offer_price": "5000-20000元/ 月",
                "recommended_offer_delivery": "周会 + SOP",
            }
        ]
    )

    result = service.auto_draft_generated_proposals()

    assert result["drafted"] == 1
    drafted_lead = service.list_leads()[0]
    assert drafted_lead["status"] == "qualified"
    assert drafted_lead["proposal_auto_generated"] is True
    assert drafted_lead["proposal_offer_key"] == "growth_advisory"
    assert drafted_lead["suggested_offer_key"] == "growth_advisory"
    assert Path(drafted_lead["proposal_path"]).exists()


def test_ensure_assets_exports_outreach_materials(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    growth_config = settings.project_root / "config" / "company_growth_engine.yaml"
    growth_config.write_text(
        (
            'target_customer_segments:\n'
            '  - key: "growth_stalled_brand"\n'
            '    recommended_offer_key: "content_diagnosis"\n'
        ),
        encoding="utf-8",
    )
    service = LeadOpsService(settings, logging.getLogger("test"))
    service.sync_generated_opportunities(
        [
            {
                "id": "customer_pitch::outreach_test",
                "type": "customer_pitch",
                "priority": "medium",
                "headline": "Outreach candidate",
                "why_now": "Need a first touch",
                "recommended_action": "Send opening message",
                "asset_hint": "customer_targets.json",
                "segment_key": "growth_stalled_brand",
                "segment_name": "增长停滞的企业账号",
                "recommended_offer_key": "content_diagnosis",
                "recommended_offer_name": "内容代运营诊断",
                "recommended_offer_price": "999-2999元/ 次",
                "recommended_offer_delivery": "诊断报告",
            }
        ]
    )
    service.enrich_generated_leads_with_recommendations()

    assets = service.ensure_assets()

    assert Path(assets["lead_outreach_queue"]).exists()
    assert Path(assets["outreach_sequences"]).exists()
    assert Path(assets["outbound_campaign_brief"]).exists()
    assert Path(assets["outbound_campaign_pack"]).exists()


def test_mark_outreach_sent_updates_status_and_follow_up(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="外联测试客户",
        contact="wechat:outreach-demo",
        intent="咨询",
        source="direct_message",
        status="new",
    )

    updated = service.mark_outreach_sent(
        lead_id=entry["id"],
        channel="wechat",
        message="你好，我这边整理了一份适合你团队的增长建议。",
        next_follow_up_at="2026-06-11 10:30",
    )

    assert updated["status"] == "contacted"
    assert updated["last_outreach_channel"] == "wechat"
    assert updated["last_outreach_message"]
    assert updated["outreach_sent_at"]
    assert updated["next_follow_up_at"] == "2026-06-11 10:30"

    summary = service.load_summary()
    assert summary["outreach_sent_leads"] == 1
    assert (service.output_dir / "outreach_execution_log.json").exists()


def test_triage_reply_classifies_hot_reply_and_promotes_lead(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="回复分诊客户",
        contact="wechat:reply-demo",
        intent="咨询",
        source="direct_message",
        status="contacted",
    )
    service.generate_lead_proposal(
        lead_id=entry["id"],
        offer_key="growth_advisory",
        mark_as_sent=False,
    )

    result = service.triage_reply(
        lead_id=entry["id"],
        reply_text="可以，把方案和报价发我，我们约个电话聊聊。",
    )

    assert result["reply_temperature"] == "hot"
    assert "proposal" in result["recommended_next_action"].lower() or "通话" in result["recommended_next_action"]

    latest = service.list_leads()[0]
    assert latest["status"] == "qualified"
    assert latest["reply_temperature"] == "hot"
    assert latest["next_reply_script"]
    assert latest["next_follow_up_at"]

    summary = service.load_summary()
    assert summary["hot_reply_leads"] == 1


def test_ensure_assets_exports_sales_execution_files(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    growth_config = settings.project_root / "config" / "company_growth_engine.yaml"
    growth_config.write_text(
        (
            'target_customer_segments:\n'
            '  - key: "industrial_research_team"\n'
            '    recommended_offer_key: "growth_advisory"\n'
            '    outreach_channel: "email / wechat"\n'
        ),
        encoding="utf-8",
    )
    service = LeadOpsService(settings, logging.getLogger("test"))
    service.sync_generated_opportunities(
        [
            {
                "id": "customer_pitch::sales_exec",
                "type": "customer_pitch",
                "priority": "high",
                "headline": "Sales execution candidate",
                "why_now": "Need immediate commercial follow-up",
                "recommended_action": "Send first touch",
                "asset_hint": "customer_targets.json",
                "segment_key": "industrial_research_team",
                "segment_name": "Industrial Research Team",
                "recommended_offer_key": "growth_advisory",
                "recommended_offer_name": "Growth Advisory",
                "recommended_offer_price": "5000-20000",
                "recommended_offer_delivery": "Weekly advisory",
                "outreach_channel": "email / wechat",
            }
        ]
    )

    assets = service.ensure_assets()

    assert Path(assets["sales_execution_dashboard"]).exists()
    assert Path(assets["sales_execution_brief"]).exists()
    assert Path(assets["wechat_batch_messages"]).exists()
    assert Path(assets["email_batch_messages"]).exists()
    assert Path(assets["platform_dm_batch_messages"]).exists()


def test_mark_proposal_sent_call_booked_and_payment_received(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="成交节点客户",
        contact="wechat:cashflow-demo",
        intent="咨询",
        source="direct_message",
        estimated_value=6800,
        status="qualified",
    )
    service.generate_lead_proposal(
        lead_id=entry["id"],
        offer_key="growth_advisory",
        mark_as_sent=False,
    )

    proposal_sent = service.mark_proposal_sent(
        lead_id=entry["id"],
        channel="wechat",
        next_follow_up_at="2026-06-10 11:00",
    )
    assert proposal_sent["status"] == "proposal_sent"
    assert proposal_sent["proposal_sent_at"]

    call_booked = service.mark_call_booked(
        lead_id=entry["id"],
        call_scheduled_for="2026-06-10 15:00",
        notes="Booked a 15-minute closing call",
    )
    assert call_booked["call_scheduled_for"] == "2026-06-10 15:00"
    assert call_booked["call_booked_at"]

    paid = service.mark_payment_received(
        lead_id=entry["id"],
        amount=6800,
        received_at="2026-06-10T16:30:00",
        notes="Paid in full",
    )
    assert paid["status"] == "won"
    assert float(paid["actual_value"]) == 6800
    assert paid["payment_received_at"] == "2026-06-10T16:30:00"

    summary = service.load_summary()
    assert summary["won_leads"] == 1


def test_post_sale_customer_success_flow_exports_assets(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    entry = service.record_lead(
        name="复购客户",
        contact="wechat:success-demo",
        intent="咨询",
        source="direct_message",
        estimated_value=8800,
        status="won",
    )
    service.mark_delivery_started(
        lead_id=entry["id"],
        delivery_notes="Kickoff complete",
    )
    service.mark_delivery_completed(
        lead_id=entry["id"],
        delivery_notes="Delivered final report",
    )
    service.request_testimonial(
        lead_id=entry["id"],
        message="Requested a short testimonial",
    )
    service.mark_expansion_opportunity(
        lead_id=entry["id"],
        expansion_type="referral",
        notes="Customer may refer two peers",
    )

    summary = service.load_summary()
    assert summary["delivery_started_leads"] == 1
    assert summary["delivery_completed_leads"] == 1
    assert summary["testimonial_requested_leads"] == 1
    assert summary["expansion_ready_leads"] == 1

    assets = service.ensure_assets()
    assert Path(assets["customer_success_dashboard"]).exists()
    assert Path(assets["customer_success_brief"]).exists()


def test_referral_conversion_and_expansion_proposal_flow(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    source = service.record_lead(
        name="老客户A",
        contact="wechat:customer-a",
        intent="咨询",
        source="direct_message",
        estimated_value=8800,
        status="won",
    )
    service.mark_expansion_opportunity(
        lead_id=source["id"],
        expansion_type="renewal",
        notes="Likely to renew next month",
    )
    expansion = service.generate_expansion_proposal(
        lead_id=source["id"],
        expansion_type="renewal",
    )
    referral = service.convert_expansion_to_lead(
        source_lead_id=source["id"],
        referral_name="转介绍客户B",
        referral_contact="wechat:referral-b",
        notes="Warm intro from existing customer",
    )

    assert Path(expansion["proposal_path"]).exists()
    assert referral["source"] == "customer_referral"
    assert referral["referral_source_lead_id"] == source["id"]

    assets = service.ensure_assets()
    assert Path(assets["expansion_pipeline"]).exists()
    assert Path(assets["referral_pipeline_brief"]).exists()
    assert Path(assets["reply_triage_playbook"]).exists()
    assert Path(assets["reply_triage_pack"]).exists()
    assert Path(assets["conversion_push_pack"]).exists()


def test_sales_execution_dashboard_includes_referral_and_expansion_revenue_actions(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    source = service.record_lead(
        name="老客户续费样本",
        contact="wechat:renewal-source",
        intent="咨询",
        source="revenue_engine",
        estimated_value=8800,
        status="won",
    )
    service.mark_expansion_opportunity(
        lead_id=source["id"],
        expansion_type="renewal",
        notes="Customer is open to renewal",
    )
    service.generate_expansion_proposal(
        lead_id=source["id"],
        expansion_type="renewal",
    )
    service.convert_expansion_to_lead(
        source_lead_id=source["id"],
        referral_name="转介绍客户样本",
        referral_contact="wechat:referral-sample",
        notes="Warm intro from paying customer",
    )

    assets = service.ensure_assets()
    dashboard_path = Path(assets["sales_execution_dashboard"])
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    items = dashboard.get("items", [])

    action_types = {str(item.get("action_type", "")).strip() for item in items if isinstance(item, dict)}
    assert "send_referral_intro" in action_types
    assert "send_expansion_proposal" in action_types

    referral_item = next(
        item for item in items if isinstance(item, dict) and str(item.get("action_type", "")).strip() == "send_referral_intro"
    )
    expansion_item = next(
        item for item in items if isinstance(item, dict) and str(item.get("action_type", "")).strip() == "send_expansion_proposal"
    )

    assert str(referral_item.get("preferred_channel", "")).strip()
    assert "customer" in str(expansion_item.get("reason", "")).lower()


def test_mark_proposal_sent_preserves_won_status_for_expansion_motion(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    _write_revenue_config(settings)
    service = LeadOpsService(settings, logging.getLogger("test"))

    customer = service.record_lead(
        name="续约客户",
        contact="wechat:renewal-customer",
        intent="咨询",
        source="revenue_engine",
        estimated_value=6800,
        status="won",
    )
    service.mark_expansion_opportunity(
        lead_id=customer["id"],
        expansion_type="renewal",
        notes="Renewal motion ready",
    )
    service.generate_expansion_proposal(
        lead_id=customer["id"],
        expansion_type="renewal",
    )

    updated = service.mark_proposal_sent(
        lead_id=customer["id"],
        channel="wechat",
        next_follow_up_at="2026-06-11 10:00",
    )

    assert updated["status"] == "won"
    assert updated["expansion_proposal_sent_at"]
    assert updated["proposal_sent_at"]
