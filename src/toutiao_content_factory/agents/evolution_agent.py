from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


class EvolutionAgent(BaseAgent):
    agent_name = "EvolutionAgent"
    TOP_K = 3
    METRICS_FILENAME = "performance_metrics.json"

    def run(self) -> dict[str, Any]:
        metrics_path = self._resolve_metrics_path()
        if metrics_path is None:
            self.logger.debug("EvolutionAgent skipped: %s not found.", self.METRICS_FILENAME)
            self._ensure_seed_metrics()
            return {"status": "missing_metrics"}

        records = self._load_records(metrics_path)
        if not records:
            self.logger.info("EvolutionAgent skipped: no valid performance records in %s.", metrics_path)
            return {"status": "empty_metrics", "metrics_path": str(metrics_path)}

        ranked = sorted(records, key=lambda item: item["roi_score"], reverse=True)
        top_records = ranked[: self.TOP_K]
        bottom_records = list(reversed(ranked[-self.TOP_K :]))
        rules = self._summarize_rules(top_records, bottom_records)
        if not rules:
            rules = self._fallback_rules(top_records, bottom_records)

        self._write_rules(rules)
        return {
            "status": "ok",
            "metrics_path": str(metrics_path),
            "rule_path": str(self.best_practices_rule_path()),
            "top_titles": [item.get("title", "") for item in top_records],
            "bottom_titles": [item.get("title", "") for item in bottom_records],
        }

    def _resolve_metrics_path(self) -> Path | None:
        candidates = [
            self.settings.project_root / self.METRICS_FILENAME,
            self.settings.output_dir / self.METRICS_FILENAME,
            self.settings.automation_dir / self.METRICS_FILENAME,
            self.settings.automation_dir / "feedback" / self.METRICS_FILENAME,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _ensure_seed_metrics(self) -> None:
        """Create an empty seed metrics file so future runs can find and append to it."""
        seed_path = self.settings.automation_dir / self.METRICS_FILENAME
        seed_path.parent.mkdir(parents=True, exist_ok=True)
        if not seed_path.exists():
            seed_path.write_text('{"articles": []}', encoding="utf-8")
            self.logger.info("EvolutionAgent created seed metrics file at %s", seed_path)

    def _load_records(self, path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Failed to read performance metrics from %s: %s", path, exc)
            return []

        raw_records: list[Any]
        if isinstance(payload, list):
            raw_records = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("articles"), list):
                raw_records = payload["articles"]
            elif isinstance(payload.get("records"), list):
                raw_records = payload["records"]
            elif isinstance(payload.get("items"), list):
                raw_records = payload["items"]
            else:
                raw_records = [payload]
        else:
            return []

        result: list[dict[str, Any]] = []
        for index, item in enumerate(raw_records):
            if not isinstance(item, dict):
                continue
            roi_score = self._to_float(item.get("roi_score"))
            if roi_score is None:
                continue
            normalized = dict(item)
            normalized["roi_score"] = roi_score
            normalized["title"] = self._pick_first_text(
                item.get("title"),
                item.get("article_title"),
                item.get("headline"),
                item.get("topic_title"),
                fallback=f"record_{index + 1}",
            )
            normalized["summary"] = self._pick_first_text(
                item.get("summary"),
                item.get("article_summary"),
                item.get("desc"),
                item.get("description"),
            )
            normalized["article"] = self._pick_first_text(
                item.get("article"),
                item.get("content"),
                item.get("body"),
                item.get("text"),
            )
            normalized["reason"] = self._pick_first_text(
                item.get("selection_reason"),
                item.get("note"),
                item.get("remarks"),
            )
            result.append(normalized)
        return result

    def _summarize_rules(
        self,
        top_records: list[dict[str, Any]],
        bottom_records: list[dict[str, Any]],
    ) -> str:
        payload = self.request_json(
            self._system_prompt(),
            self._user_prompt(top_records, bottom_records),
        )
        if not payload:
            return ""

        rules = payload.get("Actionable_Rules")
        if isinstance(rules, list):
            lines = [str(item).strip() for item in rules if str(item).strip()]
            return "\n".join(lines[:10]).strip()
        if isinstance(rules, str):
            return rules.strip()
        return ""

    def _system_prompt(self) -> str:
        return (
            "你是 EvolutionAgent，只输出 JSON。"
            " 你要对比爆款与弃子，提炼可直接执行的内容生产规则。"
            " 禁止空话，禁止复述样本，规则必须简短、具体、可操作。"
            " 请重点分析标题吸引力、争议感、信息密度、段落疏密、数字使用、开头钩子。"
            " 输出字段必须包含 Actionable_Rules，值为字符串数组。"
        )

    def _user_prompt(
        self,
        top_records: list[dict[str, Any]],
        bottom_records: list[dict[str, Any]],
    ) -> str:
        return (
            "请比较以下两组文章的流量反馈。\n"
            "任务：先判断前三篇为什么受欢迎，再指出后三篇的问题，最后提炼出一份精简的 Actionable_Rules。\n"
            "要求：\n"
            "1. 规则必须是明天就能直接执行的写作/标题指令。\n"
            "2. 每条规则单独成行，尽量用命令句，例如“开头必须用疑问句”。\n"
            "3. 规则数量控制在 5 到 10 条。\n"
            "4. 不要输出解释性长文，只返回 JSON。\n\n"
            f"Top 3 爆款：\n{self.json_text(top_records)}\n\n"
            f"Bottom 3 弃子：\n{self.json_text(bottom_records)}"
        )

    def _fallback_rules(
        self,
        top_records: list[dict[str, Any]],
        bottom_records: list[dict[str, Any]],
    ) -> str:
        top_has_digits = any(any(char.isdigit() for char in item.get("title", "")) for item in top_records)
        bottom_dense = any(len(item.get("article", "")) >= 600 and "\n\n" not in item.get("article", "") for item in bottom_records)
        rules = [
            "标题前 14 个字必须出现明确冲突、反差或判断，不要平铺直叙。",
            "开头前两句必须给出问题、反常识判断或具体结果，禁止空泛导语。",
            "正文每段控制在 2 到 3 句，避免大段密集排版。",
            "至少加入一个具体数字、金额或比例，提升可信度和传播感。",
            "结尾必须抛出一个具体问题，引导读者站队或补充观点。",
        ]
        if top_has_digits:
            rules.append("标题或开头优先使用具体数字，尤其是金额、涨跌幅、订单量、时间点。")
        if bottom_dense:
            rules.append("长文必须用更短段落和更明显的小标题切开，避免信息墙。")
        return "\n".join(rules[:10])

    def _write_rules(self, rules: str) -> None:
        path = self.best_practices_rule_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rules.strip(), encoding="utf-8")
        self.logger.info("EvolutionAgent wrote best practices rules to %s", path)

    def _to_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _pick_first_text(self, *values: Any, fallback: str = "") -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return fallback
