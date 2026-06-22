from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from toutiao_content_factory.config import AppSettings


class MonetizationService:
    CTA_MARKER = "## 延伸服务"

    DEFAULT_CONFIG = {
        "version": 1,
        "updated_at": "2026-06-08",
        "lead_magnet": {},
        "core_offers": [],
        "cta_blocks": {},
        "sales_playbook": {},
    }

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.config_path = settings.project_root / "config" / "company_revenue.yaml"
        self.output_dir = settings.output_dir / "automation" / "monetization"

    def append_cta(self, article: str, *, platform_key: str = "default") -> str:
        clean_article = str(article or "").rstrip()
        if not clean_article:
            return ""
        if self.CTA_MARKER in clean_article:
            return clean_article + "\n"

        payload = self._load_payload()
        cta_blocks = payload.get("cta_blocks", {}) if isinstance(payload, dict) else {}
        block = cta_blocks.get(platform_key) or cta_blocks.get("default") or {}
        heading = str(block.get("heading", "如果你想进一步交流")).strip() or "如果你想进一步交流"
        bullets = [str(item).strip() for item in block.get("bullets", []) if str(item).strip()]
        closing = str(block.get("closing", "")).strip()

        cta_lines = ["", self.CTA_MARKER, "", heading]
        for item in bullets:
            cta_lines.append(f"- {item}")
        if closing:
            cta_lines.extend(["", closing])
        return clean_article + "\n" + "\n".join(cta_lines).rstrip() + "\n"

    def get_cta_payload(self, *, platform_key: str = "default") -> dict[str, Any]:
        payload = self._load_payload()
        cta_blocks = payload.get("cta_blocks", {}) if isinstance(payload, dict) else {}
        block = cta_blocks.get(platform_key) or cta_blocks.get("default") or {}
        heading = str(block.get("heading", "")).strip()
        bullets = [str(item).strip() for item in block.get("bullets", []) if str(item).strip()]
        closing = str(block.get("closing", "")).strip()
        return {
            "platform_key": platform_key,
            "marker": self.CTA_MARKER,
            "heading": heading,
            "bullets": bullets,
            "closing": closing,
        }

    def export_assets(self) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = self._load_payload()

        lead_magnet_path = self.output_dir / "lead_magnet.md"
        service_menu_path = self.output_dir / "service_menu.md"
        sales_playbook_path = self.output_dir / "sales_playbook.md"

        lead_magnet_path.write_text(self._build_lead_magnet_markdown(payload), encoding="utf-8")
        service_menu_path.write_text(self._build_service_menu_markdown(payload), encoding="utf-8")
        sales_playbook_path.write_text(self._build_sales_playbook_markdown(payload), encoding="utf-8")

        return {
            "lead_magnet": lead_magnet_path,
            "service_menu": service_menu_path,
            "sales_playbook": sales_playbook_path,
            "revenue_config": self.config_path,
        }

    def _build_lead_magnet_markdown(self, payload: dict[str, Any]) -> str:
        lead = payload.get("lead_magnet", {}) if isinstance(payload, dict) else {}
        return (
            f"# {lead.get('name', '行业情报周报')}\n\n"
            f"- 承诺：{lead.get('promise', '')}\n"
            f"- 交付方式：{lead.get('delivery', '')}\n\n"
            "## 领取方式\n\n"
            f"{lead.get('signup_call_to_action', '')}\n"
        )

    def _build_service_menu_markdown(self, payload: dict[str, Any]) -> str:
        offers = payload.get("core_offers", []) if isinstance(payload, dict) else []
        lines = [f"# {self.settings.brand_name} 服务菜单", ""]
        for item in offers:
            if not isinstance(item, dict):
                continue
            lines.append(f"## {str(item.get('name', '')).strip()}")
            lines.append("")
            lines.append(f"- 价格：{str(item.get('price', '')).strip()}")
            lines.append(f"- 适合谁：{str(item.get('target_customer', '')).strip()}")
            lines.append(f"- 交付：{str(item.get('delivery', '')).strip()}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_sales_playbook_markdown(self, payload: dict[str, Any]) -> str:
        playbook = payload.get("sales_playbook", {}) if isinstance(payload, dict) else {}
        questions = [str(item).strip() for item in playbook.get("qualification_questions", []) if str(item).strip()]
        script = [str(item).strip() for item in playbook.get("conversion_script", []) if str(item).strip()]
        lines = ["# 销售分诊脚本", "", "## 资格判断问题", ""]
        lines.extend(f"- {item}" for item in questions)
        lines.extend(["", "## 转化原则", ""])
        lines.extend(f"- {item}" for item in script)
        return "\n".join(lines).strip() + "\n"

    def _load_payload(self) -> dict[str, Any]:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(
                yaml.safe_dump(self.DEFAULT_CONFIG, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        try:
            payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            self.logger.warning("Failed to load revenue config, using defaults: %s", exc)
            payload = {}
        return payload if isinstance(payload, dict) else {}
