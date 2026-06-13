from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import yaml

from toutiao_content_factory.services.revenue_engine_service import RevenueEngineService


def _build_settings(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    output_dir = tmp_path / "output"
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    revenue_payload = {
        "core_offers": [
            {
                "key": "research_sprint",
                "name": "行业研究定制",
                "price": "1999-4999元 / 份",
                "delivery": "研究报告 + 解读",
            },
            {
                "key": "content_diagnosis",
                "name": "内容代运营诊断",
                "price": "999-2999元 / 次",
                "delivery": "诊断报告",
            },
            {
                "key": "growth_advisory",
                "name": "顾问式增长陪跑",
                "price": "5000-20000元 / 月",
                "delivery": "周会 + SOP",
            },
        ]
    }
    growth_payload = {
        "target_customer_segments": [
            {
                "key": "ai_founder_team",
                "name": "AI 创始团队",
                "recommended_offer_key": "growth_advisory",
                "outreach_channel": "wechat",
                "pains": ["缺稳定内容获客系统"],
                "qualifying_signal": ["持续发布产业内容"],
            }
        ],
        "sponsor_archetypes": [
            {
                "key": "ai_tool_vendor",
                "name": "AI 工具厂商",
                "sponsor_fit": ["读者高度重合"],
                "package_name": "周报赞助",
                "package_price": "5000-20000元 / 月",
            }
        ],
        "outreach_sequences": {
            "consult": ["先引用对方内容", "再指出增长卡点"],
        },
        "newsletter": {
            "product_name": "行业情报周报",
            "cadence": "weekly",
            "free_call_to_action": "回复周报领取样刊",
            "paid_call_to_action": "升级会员获取完整版",
        },
        "deer_flow_playbooks": [
            {"key": "customer_prospecting", "objective": "寻找潜在客户"},
            {"key": "sponsor_prospecting", "objective": "寻找潜在赞助商"},
        ],
        "agent_roles": {
            "agennts": ["售前分诊代理"],
            "cloudflow": ["调度日报和商机扫描"],
            "letter": ["承接 newsletter 订阅"],
        },
    }
    (project_root / "config" / "company_revenue.yaml").write_text(
        yaml.safe_dump(revenue_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (project_root / "config" / "company_growth_engine.yaml").write_text(
        yaml.safe_dump(growth_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (output_dir / "daily_topics.json").write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "title": "AI 出海进入兑现期",
                        "matched_keywords": ["AI", "出海", "订单"],
                        "total_score": 92,
                        "title_playbook_track": "科技商业",
                        "recommended_format_label": "快讯点评",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lead_dir = output_dir / "automation" / "lead_ops"
    lead_dir.mkdir(parents=True, exist_ok=True)
    (lead_dir / "lead_ledger.json").write_text(
        json.dumps(
            [
                {
                    "id": "lead_1",
                    "name": "测试客户",
                    "status": "qualified",
                    "notes": "想了解诊断服务",
                    "proposal_path": "",
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(
        project_root=project_root,
        output_dir=output_dir,
        brand_name="测试品牌",
        account_key="default",
    )


def test_build_revenue_engine_assets(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = RevenueEngineService(settings, logging.getLogger("test"))

    benchmark_report = {
        "summary": {
            "track_distribution": {"科技商业": 3},
            "top_keywords": {"AI": 2, "利润": 1},
        }
    }
    self_benchmark_report = {
        "summary": {
            "track_distribution": {"科技商业": 1},
            "top_patterns": {"对比式开头": 1, "数据锚点": 1},
        }
    }
    exported = service.build_assets(
        integrations=[{"key": "deer_flow", "integration_status": "confirmed"}],
        benchmark_report=benchmark_report,
        self_benchmark_report=self_benchmark_report,
    )

    for path in exported.values():
        assert Path(path).exists()

    customer_targets = json.loads(Path(exported["customer_targets"]).read_text(encoding="utf-8"))
    sponsor_targets = json.loads(Path(exported["sponsor_targets"]).read_text(encoding="utf-8"))
    opportunity_queue = json.loads(Path(exported["opportunity_queue"]).read_text(encoding="utf-8"))
    signal_report = json.loads(Path(exported["revenue_signal_report"]).read_text(encoding="utf-8"))
    sync_report = json.loads(Path(exported["opportunity_sync_report"]).read_text(encoding="utf-8"))
    lead_ledger = json.loads((settings.output_dir / "automation" / "lead_ops" / "lead_ledger.json").read_text(encoding="utf-8"))

    assert customer_targets["segments"][0]["recommended_offer_key"] == "growth_advisory"
    assert sponsor_targets["archetypes"][0]["package_name"] == "周报赞助"
    assert signal_report["top_topics"][0]["title"] == "AI 出海进入兑现期"
    assert opportunity_queue["summary"]["total_items"] >= 2
    assert any(item["type"] == "lead_followup" for item in opportunity_queue["items"])
    assert sync_report["candidate_count"] >= 1
    assert sync_report["sync_policy"]["included_types"] == ["customer_pitch"]
    assert any(item.get("auto_generated") for item in lead_ledger)
    assert any(item.get("source") == "revenue_engine" for item in lead_ledger)
    assert any(item.get("suggested_offer_key") for item in lead_ledger if item.get("auto_generated"))
    assert any(item.get("proposal_auto_generated") for item in lead_ledger if item.get("status") == "qualified")

    deer_pack = Path(exported["deer_flow_task_pack"]).read_text(encoding="utf-8")
    assert "customer_prospecting" in deer_pack
    assert "queue_items" in deer_pack

    newsletter_offer = Path(exported["newsletter_offer"]).read_text(encoding="utf-8")
    assert "行业情报周报" in newsletter_offer
    assert "升级会员获取完整版" in newsletter_offer


def test_build_revenue_engine_adapter_assets(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = RevenueEngineService(settings, logging.getLogger("test"))

    exported = service.build_assets(
        integrations=[
            {"key": "deer_flow", "integration_status": "confirmed"},
            {"key": "cloudflow", "integration_status": "adapter_ready", "role": "Cloud orchestration layer"},
        ],
        benchmark_report={"summary": {"track_distribution": {"科技商业": 2}, "top_keywords": {"AI": 2}}},
        self_benchmark_report={"summary": {"track_distribution": {"科技商业": 1}, "top_patterns": {"数据锚点": 1}}},
    )

    prompt_pack = json.loads(Path(exported["prompt_optimization_pack"]).read_text(encoding="utf-8"))
    assert prompt_pack["adapter"]["project_key"] == "prompt_optimized"
    assert len(prompt_pack["prompt_targets"]) >= 2

    cloudflow_blueprint = Path(exported["cloudflow_blueprint"]).read_text(encoding="utf-8")
    assert "daily_revenue_scan" in cloudflow_blueprint
    assert "weekly_letter_and_sponsor_push" in cloudflow_blueprint

    letter_campaign = Path(exported["letter_campaign"]).read_text(encoding="utf-8")
    assert "Welcome Email" in letter_campaign
    assert "Upgrade Email" in letter_campaign


def test_run_profit_loop_exports_dispatch_and_founder_brief(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = RevenueEngineService(settings, logging.getLogger("test"))

    exported = service.run_profit_loop(
        integrations=[
            {"key": "deer_flow", "integration_status": "confirmed"},
            {"key": "cloudflow", "integration_status": "adapter_ready"},
        ],
        benchmark_report={"summary": {"track_distribution": {"科技商业": 2}, "top_keywords": {"AI": 2}}},
        self_benchmark_report={"summary": {"track_distribution": {"科技商业": 1}, "top_patterns": {"数据锚点": 1}}},
    )

    dispatch_payload = json.loads(Path(exported["agennts_dispatch_queue"]).read_text(encoding="utf-8"))
    assert dispatch_payload["items"]

    founder_brief = Path(exported["founder_profit_brief"]).read_text(encoding="utf-8")
    assert "Founder Profit Brief" in founder_brief
    assert "Execution Stack" in founder_brief

    action_pack = json.loads(Path(exported["profit_action_pack"]).read_text(encoding="utf-8"))
    action_types = {str(item.get("action_type", "")).strip() for item in action_pack.get("items", []) if isinstance(item, dict)}
    assert "send_sponsor_outreach" in action_types
    assert "send_free_sample" in action_types or "send_upgrade_prompt" in action_types

    profit_report = json.loads(Path(exported["profit_loop_report"]).read_text(encoding="utf-8"))
    assert "revenue_snapshot" in profit_report
    assert "execution_snapshot" in profit_report
    membership_pipeline = json.loads(Path(exported["membership_pipeline"]).read_text(encoding="utf-8"))
    assert "summary" in membership_pipeline
    letter_revenue_report = json.loads(Path(exported["letter_revenue_report"]).read_text(encoding="utf-8"))
    assert "revenue" in letter_revenue_report


def test_execute_profit_actions_writes_back_and_logs_sponsor_outreach(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = RevenueEngineService(settings, logging.getLogger("test"))

    source = service.lead_ops_service.record_lead(
        name="已成交客户",
        contact="wechat:customer-a",
        intent="咨询",
        source="revenue_engine",
        estimated_value=6800,
        status="won",
    )
    service.lead_ops_service.mark_expansion_opportunity(
        lead_id=source["id"],
        expansion_type="renewal",
        notes="Renewal ready",
    )
    service.lead_ops_service.generate_expansion_proposal(
        lead_id=source["id"],
        expansion_type="renewal",
    )
    service.lead_ops_service.convert_expansion_to_lead(
        source_lead_id=source["id"],
        referral_name="转介绍客户",
        referral_contact="wechat:referral-b",
        notes="Warm intro",
    )

    exported = service.execute_profit_actions(
        action_types=("send_referral_intro", "send_expansion_proposal", "send_sponsor_outreach"),
        integrations=[
            {"key": "deer_flow", "integration_status": "confirmed"},
            {"key": "cloudflow", "integration_status": "adapter_ready"},
        ],
        benchmark_report={"summary": {"track_distribution": {"科技商业": 2}, "top_keywords": {"AI": 2}}},
        self_benchmark_report={"summary": {"track_distribution": {"科技商业": 1}, "top_patterns": {"数据锚点": 1}}},
    )

    execution_report = json.loads(Path(exported["profit_action_execution_report"]).read_text(encoding="utf-8"))
    assert execution_report["executed_count"] >= 2
    assert execution_report["prepared_count"] >= 1

    lead_ledger = service.lead_ops_service.list_leads(limit=20)
    assert any(str(item.get("outreach_sent_at", "")).strip() for item in lead_ledger if str(item.get("source", "")).strip() == "customer_referral")
    assert any(str(item.get("expansion_proposal_sent_at", "")).strip() for item in lead_ledger if str(item.get("id", "")).strip() == source["id"])

    sponsor_log = json.loads(Path(exported["sponsor_outreach_log"]).read_text(encoding="utf-8"))
    assert sponsor_log


def test_update_sponsor_outreach_status_refreshes_pipeline(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = RevenueEngineService(settings, logging.getLogger("test"))

    exported = service.execute_profit_actions(
        action_types=("send_sponsor_outreach",),
        integrations=[
            {"key": "deer_flow", "integration_status": "confirmed"},
            {"key": "cloudflow", "integration_status": "adapter_ready"},
        ],
        benchmark_report={"summary": {"track_distribution": {"科技商业": 2}, "top_keywords": {"AI": 2}}},
        self_benchmark_report={"summary": {"track_distribution": {"科技商业": 1}, "top_patterns": {"数据锚点": 1}}},
    )
    sponsor_log = json.loads(Path(exported["sponsor_outreach_log"]).read_text(encoding="utf-8"))
    action_key = str(sponsor_log[0]["action_key"]).strip()

    updated = service.update_sponsor_outreach_status(
        action_key=action_key,
        status="interested",
        notes="Requested a sponsor package deck",
        next_follow_up_at="2026-06-10 11:00",
    )

    pipeline = json.loads(Path(updated["sponsor_pipeline"]).read_text(encoding="utf-8"))
    assert pipeline["summary"]["interested_items"] >= 1
    assert any(str(item.get("action_key", "")).strip() == action_key and str(item.get("status", "")).strip() == "interested" for item in pipeline["items"])


def test_update_sponsor_outreach_status_records_revenue(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = RevenueEngineService(settings, logging.getLogger("test"))

    exported = service.execute_profit_actions(
        action_types=("send_sponsor_outreach",),
        integrations=[
            {"key": "deer_flow", "integration_status": "confirmed"},
            {"key": "cloudflow", "integration_status": "adapter_ready"},
        ],
        benchmark_report={"summary": {"track_distribution": {"绉戞妧鍟嗕笟": 2}, "top_keywords": {"AI": 2}}},
        self_benchmark_report={"summary": {"track_distribution": {"绉戞妧鍟嗕笟": 1}, "top_patterns": {"鏁版嵁閿氱偣": 1}}},
    )
    sponsor_log = json.loads(Path(exported["sponsor_outreach_log"]).read_text(encoding="utf-8"))
    action_key = str(sponsor_log[0]["action_key"]).strip()

    updated = service.update_sponsor_outreach_status(
        action_key=action_key,
        status="won",
        notes="Signed first sponsor package",
        deal_value=8888.0,
    )

    revenue_report = json.loads(Path(updated["sponsor_revenue_report"]).read_text(encoding="utf-8"))
    assert float(revenue_report["revenue"]["won_sponsor_revenue"]) >= 8888.0
    pipeline = json.loads(Path(updated["sponsor_pipeline"]).read_text(encoding="utf-8"))
    assert pipeline["summary"]["won_items"] >= 1


def test_update_newsletter_subscriber_status_refreshes_membership_pipeline(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = RevenueEngineService(settings, logging.getLogger("test"))

    exported = service.run_profit_loop(
        integrations=[
            {"key": "deer_flow", "integration_status": "confirmed"},
            {"key": "cloudflow", "integration_status": "adapter_ready"},
        ],
        benchmark_report={"summary": {"track_distribution": {"绉戞妧鍟嗕笟": 2}, "top_keywords": {"AI": 2}}},
        self_benchmark_report={"summary": {"track_distribution": {"绉戞妧鍟嗕笟": 1}, "top_patterns": {"鏁版嵁閿氱偣": 1}}},
    )
    ledger = json.loads(Path(exported["newsletter_subscriber_ledger"]).read_text(encoding="utf-8"))
    subscriber_key = str(ledger[0]["subscriber_key"]).strip()

    updated = service.update_newsletter_subscriber_status(
        subscriber_key=subscriber_key,
        status="member_won",
        notes="Upgraded after sample issue",
        member_revenue=299.0,
    )

    pipeline = json.loads(Path(updated["membership_pipeline"]).read_text(encoding="utf-8"))
    assert pipeline["summary"]["member_won"] >= 1
    report = json.loads(Path(updated["letter_revenue_report"]).read_text(encoding="utf-8"))
    assert float(report["revenue"]["member_revenue"]) >= 299.0
