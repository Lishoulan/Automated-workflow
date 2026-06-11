from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.lead_ops_service import LeadOpsService
from toutiao_content_factory.services.monetization_service import MonetizationService
from toutiao_content_factory.services.revenue_engine_service import RevenueEngineService


class CompanyOSService:
    """Builds a one-person-company operating system from the current repo context."""

    DEFAULT_INTEGRATION_TEMPLATE = {
        "version": 1,
        "updated_at": "2026-06-08",
        "projects": [],
    }

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.root_dir = settings.output_dir / "automation" / "company_os"
        self.integration_config_path = settings.project_root / "config" / "company_os_integrations.yaml"
        self.blueprint_json_path = self.root_dir / "company_blueprint.json"
        self.blueprint_md_path = self.root_dir / "company_blueprint.md"
        self.backlog_yaml_path = self.root_dir / "execution_backlog.yaml"
        self.scorecard_json_path = self.root_dir / "weekly_scorecard.json"
        self.monetization_service = MonetizationService(settings, logger)
        self.lead_ops_service = LeadOpsService(settings, logger)
        self.revenue_engine_service = RevenueEngineService(settings, logger)

    def build_company_os(self) -> dict[str, Path]:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_integration_catalog()

        integrations = self._load_integrations()
        benchmark_report = self._load_json_if_exists(self.settings.output_dir / "growth_ops" / "benchmark_report.json")
        performance_report = self._load_json_if_exists(
            self.settings.output_dir / "growth_ops" / "performance" / "performance_report.json"
        )
        self_benchmark_report = self._load_json_if_exists(
            self.settings.output_dir / "growth_ops" / "self_benchmark_report.json"
        )

        payload = self._build_blueprint_payload(
            integrations=integrations,
            benchmark_report=benchmark_report,
            performance_report=performance_report,
            self_benchmark_report=self_benchmark_report,
        )

        self.blueprint_json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.blueprint_md_path.write_text(
            self._render_markdown(payload),
            encoding="utf-8",
        )

        backlog = self._build_execution_backlog(payload)
        self.backlog_yaml_path.write_text(
            yaml.safe_dump(backlog, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        scorecard = self._build_scorecard(payload)
        self.scorecard_json_path.write_text(
            json.dumps(scorecard, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        monetization_assets = self.monetization_service.export_assets()
        lead_assets = self.lead_ops_service.ensure_assets()
        revenue_engine_assets = self.revenue_engine_service.build_assets(
            integrations=integrations,
            benchmark_report=benchmark_report,
            self_benchmark_report=self_benchmark_report,
        )

        result = {
            "company_os_blueprint": self.blueprint_md_path,
            "company_os_blueprint_json": self.blueprint_json_path,
            "company_os_backlog": self.backlog_yaml_path,
            "company_os_scorecard": self.scorecard_json_path,
            "company_os_integrations": self.integration_config_path,
        }
        result.update(
            {
                "lead_magnet": monetization_assets["lead_magnet"],
                "service_menu": monetization_assets["service_menu"],
                "sales_playbook": monetization_assets["sales_playbook"],
                "revenue_config": monetization_assets["revenue_config"],
                "lead_reply_manual": lead_assets["lead_reply_manual"],
                "lead_followup_queue": lead_assets["lead_followup_queue"],
                "lead_summary": lead_assets["lead_summary"],
                "lead_ledger": lead_assets["lead_ledger"],
                "lead_config": lead_assets["lead_config"],
                "proposal_catalog": lead_assets["proposal_catalog"],
                "offer_snapshot": lead_assets["offer_snapshot"],
            }
        )
        result.update(revenue_engine_assets)
        return result

    def _ensure_integration_catalog(self) -> None:
        self.integration_config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.integration_config_path.exists():
            self.integration_config_path.write_text(
                yaml.safe_dump(self.DEFAULT_INTEGRATION_TEMPLATE, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

    def _load_integrations(self) -> list[dict[str, Any]]:
        try:
            payload = yaml.safe_load(self.integration_config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.logger.warning("Failed to load company OS integrations: %s", exc)
            payload = {}
        projects = payload.get("projects", [])
        if not isinstance(projects, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in projects:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "key": str(item.get("key", "")).strip(),
                    "name": str(item.get("name", "")).strip(),
                    "category": str(item.get("category", "")).strip(),
                    "repo_url": str(item.get("repo_url", "")).strip(),
                    "confidence": str(item.get("confidence", "low")).strip() or "low",
                    "integration_status": str(item.get("integration_status", "planned")).strip() or "planned",
                    "role": str(item.get("role", "")).strip(),
                    "integration_mode": str(item.get("integration_mode", "")).strip(),
                    "revenue_contribution": [
                        str(line).strip()
                        for line in item.get("revenue_contribution", [])
                        if str(line).strip()
                    ],
                    "notes": [str(line).strip() for line in item.get("notes", []) if str(line).strip()],
                }
            )
        return normalized

    def _build_blueprint_payload(
        self,
        *,
        integrations: list[dict[str, Any]],
        benchmark_report: dict[str, Any] | None,
        performance_report: dict[str, Any] | None,
        self_benchmark_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        focus = self.settings.industry_focus.strip() or "产业内容"
        audience = self.settings.audience.strip() or "行业读者"
        brand_name = self.settings.brand_name.strip() or self.settings.account_label
        platform = self.settings.platform.strip() or "多平台"
        top_track = self._pick_top_track(benchmark_report, self_benchmark_report)

        assumptions = self._build_assumptions(integrations)
        offers = self._build_offer_ladder(focus=focus, top_track=top_track)
        channels = self._build_distribution_channels(platform=platform)
        profit_loops = self._build_profit_loops()
        kpis = self._build_kpis()
        cadence = self._build_operating_cadence()
        moat = self._build_moat()
        risks = self._build_risks()
        next_actions = self._build_next_actions(integrations=integrations)

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "business": {
                "name": brand_name,
                "account_key": self.settings.account_key,
                "account_label": self.settings.account_label,
                "focus": focus,
                "audience": audience,
                "primary_platform": platform,
                "best_current_track": top_track,
                "thesis": (
                    f"把 {focus} 领域的热点追踪、观点生产、分发、线索沉淀与付费转化自动化，"
                    "把流量资产逐步变成咨询、赞助、会员和产品化服务收入。"
                ),
            },
            "assumptions": assumptions,
            "integration_map": integrations,
            "offer_ladder": offers,
            "distribution_channels": channels,
            "profit_loops": profit_loops,
            "kpis": kpis,
            "operating_cadence": cadence,
            "competitive_moat": moat,
            "risks": risks,
            "next_30_day_actions": next_actions,
            "evidence": {
                "benchmark_report_available": bool(benchmark_report),
                "performance_report_available": bool(performance_report),
                "self_benchmark_report_available": bool(self_benchmark_report),
                "existing_capabilities": [
                    "热点采集",
                    "文章生成",
                    "标题优化",
                    "发布审核",
                    "头条自动发布",
                    "公众号草稿/发布",
                    "抖音素材准备",
                    "增长复盘",
                ],
                "recommended_commands": [
                    "python .\\main.py --build-company-os",
                    "python .\\main.py --build-benchmark-report",
                    "python .\\main.py --daily-run --refresh-cache",
                    "python .\\main.py --build-performance-report",
                ],
            },
        }

    def _build_assumptions(self, integrations: list[dict[str, Any]]) -> list[str]:
        assumptions = [
            "当前仓库已具备内容生产与多平台分发能力，适合作为一人公司的执行引擎。",
            "盈利不会来自单纯发文本身，而是来自发文带来的线索、信任、私域和产品化服务。",
            "至少有 3 个项目名称存在歧义，因此本轮先做成可替换适配位，避免架构被错误仓库绑定。",
        ]
        ambiguous = [item["name"] for item in integrations if item.get("integration_status") == "ambiguous_name"]
        if ambiguous:
            assumptions.append(f"以下项目目前按适配位处理，待你提供准确 repo 后可无缝替换：{', '.join(ambiguous)}。")
        return assumptions

    def _build_offer_ladder(self, *, focus: str, top_track: str) -> list[dict[str, Any]]:
        return [
            {
                "stage": "引流产品",
                "offer": f"{focus} 热点解读内容矩阵",
                "price": "免费",
                "goal": "获取平台流量、建立专业认知、筛出高意向读者",
            },
            {
                "stage": "轻付费",
                "offer": f"{top_track} 周报 / 行业情报简报 / 选题包",
                "price": "99-299 元/月",
                "goal": "验证用户愿意为高密度信息付费",
            },
            {
                "stage": "中客单",
                "offer": "企业定制研究、内容代运营、选题咨询",
                "price": "1999-9999 元/单",
                "goal": "把内容能力转成服务收入",
            },
            {
                "stage": "高毛利",
                "offer": "顾问式增长方案、行业赞助、培训工作坊",
                "price": "1-5 万元/项目",
                "goal": "用自动化交付提升单人产能上限",
            },
        ]

    def _build_distribution_channels(self, *, platform: str) -> list[dict[str, Any]]:
        return [
            {
                "channel": "今日头条",
                "role": "公域获客与热点验证主阵地",
                "automation": "已内置自动选题、生成、审核与发布能力",
            },
            {
                "channel": "微信公众号",
                "role": "私域沉淀、深度观点、商务承接",
                "automation": "已支持草稿/发布链路，适合挂产品、咨询和群入口",
            },
            {
                "channel": "抖音",
                "role": "短内容放大、切片分发、获客补量",
                "automation": "已具备素材准备与发布扩展位，可做图文切片和观点摘要",
            },
            {
                "channel": "Newsletter / 邮件简报",
                "role": "平台外用户资产与付费转化通道",
                "automation": "当前作为 letter 适配位设计，先把流程和文案资产跑通",
            },
            {
                "channel": "销售私信 / 商务触达",
                "role": "将高意向读者转化为咨询、赞助、共创客户",
                "automation": "建议由 deer-flow + agennts 适配位承接名单筛选与话术执行",
            },
        ]

    def _build_profit_loops(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "内容 -> 留资 -> 咨询",
                "flow": [
                    "热点内容获取精准流量",
                    "文章结尾引导加微信/订阅简报",
                    "自动分诊高意向线索",
                    "售卖研究、代运营、咨询服务",
                ],
            },
            {
                "name": "内容 -> 周报 -> 会员",
                "flow": [
                    "高频热点解读沉淀成周报",
                    "用 letter 适配位承接订阅",
                    "把免费读者转成付费情报会员",
                ],
            },
            {
                "name": "内容 -> 数据资产 -> 产品化",
                "flow": [
                    "积累 benchmark、performance、prompt 迭代数据",
                    "抽象成标题库、行业信号库、选题雷达",
                    "打包成模板包、仪表盘或 SaaS 服务",
                ],
            },
            {
                "name": "内容 -> 影响力 -> 赞助",
                "flow": [
                    "在垂直行业形成稳定更新和专业认知",
                    "主动筛选相关品牌与工具方",
                    "出售赞助位、白皮书共创、直播连麦与培训机会",
                ],
            },
        ]

    def _build_kpis(self) -> list[dict[str, Any]]:
        return [
            {"metric": "每周发布量", "target": "7-14 篇", "why": "维持算法活跃与样本量"},
            {"metric": "线索捕获量", "target": "每周 20-50 个", "why": "把流量转成私域资产"},
            {"metric": "咨询成交数", "target": "每月 2-5 单", "why": "优先验证现金流"},
            {"metric": "会员/周报订阅数", "target": "每月净增 50+", "why": "建立可复利订阅收入"},
            {"metric": "单篇内容转化率", "target": "持续环比提升", "why": "验证 prompt 和分发优化是否有效"},
            {"metric": "自动化覆盖率", "target": "80%+", "why": "确保一人公司能规模化运行"},
        ]

    def _build_operating_cadence(self) -> dict[str, list[str]]:
        return {
            "daily": [
                "早上刷新热点池并筛出最高商业价值选题",
                "自动生成 1-2 篇内容并分发到头条 / 公众号 / 抖音素材池",
                "把每篇内容挂上 CTA：订阅周报、领取行业情报、咨询合作",
                "汇总当天新增线索、评论高频问题和潜在赞助品牌",
            ],
            "weekly": [
                "运行 benchmark 与 performance 报告，复盘标题、角度和转化表现",
                "用 prompt 优化器迭代 prompts/ 下的关键提示词",
                "由 deer-flow 发起一轮长周期研究任务：热点跟踪、客户名单、赞助筛选",
                "输出 1 份周报 / 简报，测试会员或私域订阅转化",
            ],
            "monthly": [
                "选择表现最好的垂直主题，推出付费研究或咨询产品",
                "梳理 SOP，把高频人工动作下沉给 agennts / cloudflow / letter 适配位",
                "评估哪个渠道最赚钱，收缩低回报动作，集中火力放大高利润链路",
            ],
        }

    def _build_moat(self) -> list[str]:
        return [
            "不是单篇文章，而是持续积累的行业选题库、标题库、提示词库和表现数据库。",
            "多平台内容分发能力叠加私域沉淀，使流量不只依赖单个平台规则。",
            "自动化流程跑顺以后，可把个人经验封装成产品、服务或对外代运营能力。",
        ]

    def _build_risks(self) -> list[dict[str, str]]:
        return [
            {"risk": "项目名称歧义", "mitigation": "用配置化适配层承接，避免错误深绑定。"},
            {"risk": "平台限流或登录失效", "mitigation": "保留草稿模式、健康检查和多平台分发。"},
            {"risk": "有流量无转化", "mitigation": "每篇内容必须带 CTA，并建立线索台账与销售脚本。"},
            {"risk": "内容同质化", "mitigation": "使用 benchmark、performance、prompt 优化与 insight 流程持续迭代。"},
        ]

    def _build_next_actions(self, *, integrations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ambiguous_count = sum(1 for item in integrations if item.get("integration_status") == "ambiguous_name")
        return [
            {
                "priority": "P0",
                "action": "先运行 `--build-company-os`、`--build-benchmark-report`、`--daily-run`，建立第一版自动化经营节奏。",
            },
            {
                "priority": "P0",
                "action": "在文章末尾统一加入订阅、咨询、社群或情报包 CTA，把流量引向可成交场景。",
            },
            {
                "priority": "P1",
                "action": "每周至少发布 1 份可收集联系方式的行业周报样品，先验证订阅转化。",
            },
            {
                "priority": "P1",
                "action": "挑 1 个最容易成交的服务产品先卖出去，例如企业定制研究或内容代运营诊断。",
            },
            {
                "priority": "P2",
                "action": f"补全 {ambiguous_count} 个歧义项目的准确 repo 地址，再把适配位替换成真实集成。",
            },
        ]

    def _build_execution_backlog(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "generated_at": payload["generated_at"],
            "objective": "用当前仓库搭建可盈利的一人公司自动化操作系统",
            "phases": [
                {
                    "name": "Phase 1 - 流量与数据底座",
                    "tasks": [
                        "每天稳定运行热点采集、生成、审核、发布链路",
                        "完成 benchmark 与 performance 报告初始化",
                        "统一 CTA 和线索收集入口",
                    ],
                },
                {
                    "name": "Phase 2 - 私域与转化",
                    "tasks": [
                        "推出免费周报 / 行业简报作为 lead magnet",
                        "建立咨询、赞助、代运营三个成交入口",
                        "记录每个入口的线索与成交情况",
                    ],
                },
                {
                    "name": "Phase 3 - 产品化",
                    "tasks": [
                        "把高转化选题、标题和交付方式沉淀成模板包",
                        "推出轻付费会员或情报订阅",
                        "把交付流程自动化到 80% 以上",
                    ],
                },
            ],
        }

    def _build_scorecard(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "generated_at": payload["generated_at"],
            "business_name": payload["business"]["name"],
            "weekly_targets": [
                {"metric": "publish_count", "target": "7-14"},
                {"metric": "lead_count", "target": "20-50"},
                {"metric": "consult_calls", "target": "3-10"},
                {"metric": "paid_conversions", "target": "1+"},
            ],
            "review_questions": [
                "哪类选题最容易带来评论、私信和订阅？",
                "哪一个渠道贡献了最多高意向线索？",
                "本周最有机会产品化的内容资产是什么？",
                "下周要砍掉哪些低回报动作？",
            ],
        }

    def _pick_top_track(
        self,
        benchmark_report: dict[str, Any] | None,
        self_benchmark_report: dict[str, Any] | None,
    ) -> str:
        for payload in (self_benchmark_report, benchmark_report):
            if not payload:
                continue
            tracks = payload.get("top_tracks") or payload.get("track_summary") or []
            if isinstance(tracks, list) and tracks:
                first = tracks[0]
                if isinstance(first, dict):
                    for key in ("track", "name", "label"):
                        value = str(first.get(key, "")).strip()
                        if value:
                            return value
                value = str(first).strip()
                if value:
                    return value
        return "行业热点解读"

    def _load_json_if_exists(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _render_markdown(self, payload: dict[str, Any]) -> str:
        business = payload["business"]
        lines: list[str] = []
        lines.append(f"# {business['name']} 自动化一人公司蓝图")
        lines.append("")
        lines.append(f"- 生成时间：{payload['generated_at']}")
        lines.append(f"- 主账号：{business['account_label']} ({business['account_key']})")
        lines.append(f"- 赛道：{business['focus']}")
        lines.append(f"- 受众：{business['audience']}")
        lines.append(f"- 主阵地：{business['primary_platform']}")
        lines.append("")
        lines.append("## 商业定位")
        lines.append("")
        lines.append(business["thesis"])
        lines.append("")
        lines.append("## 五个项目如何分工")
        lines.append("")
        for item in payload["integration_map"]:
            lines.append(f"### {item['name']}")
            lines.append("")
            lines.append(f"- 角色：{item['role']}")
            lines.append(f"- 集成方式：{item['integration_mode']}")
            lines.append(f"- 确认度：{item['confidence']} / {item['integration_status']}")
            if item["repo_url"]:
                lines.append(f"- 仓库：{item['repo_url']}")
            for contribution in item["revenue_contribution"]:
                lines.append(f"- 赚钱作用：{contribution}")
            for note in item["notes"]:
                lines.append(f"- 备注：{note}")
            lines.append("")
        lines.append("## 收入梯度")
        lines.append("")
        for offer in payload["offer_ladder"]:
            lines.append(f"- {offer['stage']}：{offer['offer']} | 定价：{offer['price']} | 目标：{offer['goal']}")
        lines.append("")
        lines.append("## 经营闭环")
        lines.append("")
        for loop in payload["profit_loops"]:
            lines.append(f"### {loop['name']}")
            lines.append("")
            for step in loop["flow"]:
                lines.append(f"- {step}")
            lines.append("")
        lines.append("## 运营节奏")
        lines.append("")
        for cadence_name, tasks in payload["operating_cadence"].items():
            lines.append(f"### {cadence_name}")
            lines.append("")
            for task in tasks:
                lines.append(f"- {task}")
            lines.append("")
        lines.append("## 30 天优先动作")
        lines.append("")
        for action in payload["next_30_day_actions"]:
            lines.append(f"- {action['priority']}：{action['action']}")
        lines.append("")
        lines.append("## 关键提醒")
        lines.append("")
        for item in payload["assumptions"]:
            lines.append(f"- {item}")
        lines.append("")
        return "\n".join(lines).strip() + "\n"
