from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import yaml

from toutiao_content_factory.services.company_os_service import CompanyOSService


def _build_settings(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    output_dir = tmp_path / "output"
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    integration_payload = {
        "version": 1,
        "updated_at": "2026-06-08",
        "projects": [
            {
                "key": "deer_flow",
                "name": "deer-flow",
                "category": "super_agent",
                "repo_url": "https://github.com/bytedance/deer-flow",
                "confidence": "high",
                "integration_status": "confirmed",
                "role": "研究与复杂任务编排",
                "integration_mode": "外部代理",
                "revenue_contribution": ["寻找高价值商机"],
                "notes": ["已确认项目"],
            },
            {
                "key": "letter",
                "name": "letter",
                "category": "newsletter",
                "repo_url": "",
                "confidence": "low",
                "integration_status": "ambiguous_name",
                "role": "订阅沉淀",
                "integration_mode": "适配层预留",
                "revenue_contribution": ["沉淀私域资产"],
                "notes": ["待确认准确 repo"],
            },
        ],
    }
    revenue_payload = {
        "core_offers": [
            {
                "key": "growth_advisory",
                "name": "顾问式增长陪跑",
                "price": "5000-20000元 / 月",
                "target_customer": "想把内容变成线索的团队",
                "delivery": "周会 + SOP",
            }
        ],
        "lead_magnet": {
            "name": "行业情报周报",
            "promise": "每周给出行业判断",
            "delivery": "私域 / 邮件",
            "signup_call_to_action": "回复周报领取样刊",
        },
        "sales_playbook": {
            "qualification_questions": ["你最想解决的是流量还是成交？"],
            "conversion_script": ["先卖诊断，再升级长期合作。"],
        },
    }
    growth_payload = {
        "target_customer_segments": [
            {
                "key": "ai_founder_team",
                "name": "AI 创始团队",
                "recommended_offer_key": "growth_advisory",
                "outreach_channel": "微信",
                "pains": ["缺稳定内容获客"],
                "qualifying_signal": ["持续发产业内容"],
            }
        ],
        "sponsor_archetypes": [
            {
                "key": "ai_tool_vendor",
                "name": "AI 工具厂商",
                "sponsor_fit": ["受众高度重合"],
                "package_name": "周报赞助",
                "package_price": "5000-20000元 / 期",
            }
        ],
        "outreach_sequences": {"consult": ["引用内容后再切入产品"]},
        "newsletter": {
            "product_name": "行业情报周报",
            "cadence": "weekly",
            "free_call_to_action": "回复周报领取样刊",
            "paid_call_to_action": "升级会员获取完整版",
        },
        "deer_flow_playbooks": [
            {"key": "customer_prospecting", "objective": "找客户"},
            {"key": "sponsor_prospecting", "objective": "找赞助"},
        ],
        "agent_roles": {
            "agennts": ["售前分诊代理"],
            "cloudflow": ["调度日报、周报和商机扫描"],
            "letter": ["承接订阅与赞助转化"],
        },
    }
    leads_payload = {
        "version": 1,
        "updated_at": "2026-06-08",
        "sources": [{"key": "direct_message", "label": "私域私信"}],
        "statuses": ["new", "proposal_sent", "won"],
        "intents": ["咨询"],
        "default_replies": {"咨询": "可以聊。"},
    }

    (project_root / "config" / "company_os_integrations.yaml").write_text(
        yaml.safe_dump(integration_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (project_root / "config" / "company_revenue.yaml").write_text(
        yaml.safe_dump(revenue_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (project_root / "config" / "company_growth_engine.yaml").write_text(
        yaml.safe_dump(growth_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (project_root / "config" / "company_leads.yaml").write_text(
        yaml.safe_dump(leads_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    return SimpleNamespace(
        project_root=project_root,
        output_dir=output_dir,
        brand_name="测试品牌",
        industry_focus="机器人与新能源",
        audience="产业读者",
        platform="今日头条",
        account_key="default",
        account_label="默认账号",
    )


def test_build_company_os_exports_blueprint_and_backlog(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    service = CompanyOSService(settings, logging.getLogger("test"))

    exported = service.build_company_os()

    required_keys = {
        "company_os_blueprint",
        "company_os_blueprint_json",
        "company_os_backlog",
        "company_os_scorecard",
        "company_os_integrations",
        "lead_magnet",
        "service_menu",
        "sales_playbook",
        "lead_reply_manual",
        "lead_followup_queue",
        "lead_summary",
        "lead_ledger",
        "lead_config",
        "proposal_catalog",
        "offer_snapshot",
        "customer_targets",
        "sponsor_targets",
        "outreach_playbook",
        "deer_flow_task_pack",
        "newsletter_offer",
        "weekly_pipeline",
        "growth_engine_config",
    }
    assert required_keys.issubset(set(exported))
    for path in exported.values():
        assert Path(path).exists()

    payload = json.loads(Path(exported["company_os_blueprint_json"]).read_text(encoding="utf-8"))
    assert payload["business"]["name"] == "测试品牌"
    assert payload["business"]["focus"] == "机器人与新能源"
    assert len(payload["integration_map"]) == 2
    assert any(item["integration_status"] == "ambiguous_name" for item in payload["integration_map"])

    markdown = Path(exported["company_os_blueprint"]).read_text(encoding="utf-8")
    assert "自动化一人公司蓝图" in markdown
    assert "deer-flow" in markdown
    assert "30 天优先动作" in markdown

    outreach_playbook = Path(exported["outreach_playbook"]).read_text(encoding="utf-8")
    assert "主动获客与赞助外联手册" in outreach_playbook
