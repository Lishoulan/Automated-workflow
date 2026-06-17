from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.openai_service import OpenAIContentService


# 规则类别枚举：覆盖标题、开头、段落、数据、冲突、结尾、去AI味
RULE_CATEGORIES = (
    "opening_hook",        # 开头钩子
    "title_attraction",    # 标题吸引力
    "paragraph_density",   # 段落疏密
    "data_anchor",         # 数据锚定
    "conflict_design",     # 冲突/争议设计
    "closing_interaction", # 结尾互动
    "anti_ai_flavor",      # 去AI味
)

# 结构化规则 schema 版本
RULE_SCHEMA_VERSION = "2.0"


class EvolutionAgent(BaseAgent):
    agent_name = "EvolutionAgent"
    TOP_K = 3
    METRICS_FILENAME = "performance_metrics.json"
    RULES_FILENAME = "best_practices_rule.json"
    MAX_RULES = 10

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
        rules_payload = self._summarize_rules(top_records, bottom_records)
        if not rules_payload:
            rules_payload = self._fallback_rules(top_records, bottom_records)

        self._write_rules(rules_payload)
        return {
            "status": "ok",
            "metrics_path": str(metrics_path),
            "rule_path": str(self.best_practices_rule_json_path()),
            "rule_count": len(rules_payload.get("rules", [])),
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
    ) -> dict[str, Any]:
        """调用 LLM 提炼结构化规则，返回符合 schema 的 dict。失败返回空 dict。"""
        payload = self.request_json(
            self._system_prompt(),
            self._user_prompt(top_records, bottom_records),
        )
        if not payload:
            return {}

        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, list):
            return {}

        cleaned_rules: list[dict[str, Any]] = []
        for index, item in enumerate(raw_rules):
            if not isinstance(item, dict):
                continue
            rule = self._normalize_rule(item, index + 1)
            if rule:
                cleaned_rules.append(rule)
            if len(cleaned_rules) >= self.MAX_RULES:
                break

        if not cleaned_rules:
            return {}

        # 按 penalty_weight 降序、priority 升序排序，确保高权重规则靠前
        cleaned_rules.sort(key=lambda r: (-float(r.get("penalty_weight", 0.5)), int(r.get("priority", 99))))

        return {
            "version": RULE_SCHEMA_VERSION,
            "generated_at": date.today().isoformat(),
            "source": "EvolutionAgent",
            "rule_count": len(cleaned_rules),
            "rules": cleaned_rules,
        }

    def _normalize_rule(self, item: dict[str, Any], index: int) -> dict[str, Any] | None:
        """将 LLM 输出的单条规则归一化为符合 schema 的结构。"""
        title = str(item.get("title") or item.get("rule") or "").strip()
        if not title:
            return None

        category = str(item.get("category", "")).strip()
        if category not in RULE_CATEGORIES:
            # 尝试根据标题关键词推断类别
            category = self._infer_category(title)

        trigger = item.get("trigger_conditions")
        if not isinstance(trigger, list):
            trigger = [str(trigger).strip()] if trigger else ["通用"]

        industries = item.get("applicable_industries")
        if not isinstance(industries, list):
            industries = ["通用"]

        formats = item.get("applicable_formats")
        if not isinstance(formats, list):
            formats = ["通用"]

        # 惩罚权重归一化到 [0, 1]
        try:
            penalty = float(item.get("penalty_weight", 0.6))
        except (TypeError, ValueError):
            penalty = 0.6
        penalty = max(0.0, min(1.0, penalty))

        try:
            priority = int(item.get("priority", index))
        except (TypeError, ValueError):
            priority = index

        return {
            "rule_id": f"R{index:03d}",
            "category": category,
            "title": title,
            "trigger_conditions": [str(t).strip() for t in trigger if str(t).strip()],
            "applicable_industries": [str(i).strip() for i in industries if str(i).strip()],
            "applicable_formats": [str(f).strip() for f in formats if str(f).strip()],
            "positive_example": str(item.get("positive_example", "")).strip(),
            "negative_example": str(item.get("negative_example", "")).strip(),
            "penalty_weight": round(penalty, 2),
            "priority": priority,
            "evidence": str(item.get("evidence", "")).strip(),
        }

    def _infer_category(self, title: str) -> str:
        """根据规则标题关键词推断类别。"""
        lowered = title.lower()
        if any(k in title for k in ("开头", "钩子", "第一句", "前两句")):
            return "opening_hook"
        if any(k in title for k in ("标题", "前14字", "前14个字")):
            return "title_attraction"
        if any(k in title for k in ("段落", "排版", "疏密", "短段落")):
            return "paragraph_density"
        if any(k in title for k in ("数字", "数据", "金额", "比例", "锚定")):
            return "data_anchor"
        if any(k in title for k in ("冲突", "争议", "反差", "反常识")):
            return "conflict_design"
        if any(k in title for k in ("结尾", "互动", "提问", "站队")):
            return "closing_interaction"
        if any(k in title for k in ("AI味", "套话", "模板", "营销腔")):
            return "anti_ai_flavor"
        return "opening_hook"

    def _system_prompt(self) -> str:
        categories_text = "、".join(RULE_CATEGORIES)
        return (
            "你是 EvolutionAgent，只输出 JSON。"
            " 你要对比爆款与弃子，提炼可直接执行的结构化内容生产规则。"
            " 禁止空话，禁止复述样本，规则必须简短、具体、可操作。\n"
            " 请重点分析标题吸引力、争议感、信息密度、段落疏密、数字使用、开头钩子。\n"
            f" 规则类别必须从以下枚举中选择：{categories_text}。\n"
            " 输出格式必须是如下 JSON 结构：\n"
            "{\n"
            '  "rules": [\n'
            "    {\n"
            '      "category": "规则类别",\n'
            '      "title": "简短可执行的规则指令",\n'
            '      "trigger_conditions": ["触发该规则的条件"],\n'
            '      "applicable_industries": ["适用行业，如新能源/储能/机器人/通用"],\n'
            '      "applicable_formats": ["适用体裁，如deep_analysis/quick_commentary/通用"],\n'
            '      "positive_example": "符合规则的正面示例",\n'
            '      "negative_example": "违反规则的负面示例",\n'
            '      "penalty_weight": 0.0到1.0之间的惩罚权重，越高越重要,\n'
            '      "priority": 1到10的优先级，越小越优先,\n'
            '      "evidence": "来自爆款/弃子对比的简短证据"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            " 要求：\n"
            "1. 规则数量控制在 5 到 10 条。\n"
            "2. penalty_weight 反映该规则对流量影响的程度，核心规则应在 0.7 以上。\n"
            "3. 每条规则必须有明确的正面示例和负面示例。\n"
            "4. applicable_industries 至少包含一个具体行业或'通用'。\n"
            "5. 不要输出解释性长文，只返回 JSON。"
        )

    def _user_prompt(
        self,
        top_records: list[dict[str, Any]],
        bottom_records: list[dict[str, Any]],
    ) -> str:
        return (
            "请比较以下两组文章的流量反馈。\n"
            "任务：先判断前三篇为什么受欢迎，再指出后三篇的问题，最后提炼出一份结构化的 rules 数组。\n"
            "要求：\n"
            "1. 规则必须是明天就能直接执行的写作/标题指令。\n"
            "2. 每条规则用命令句，例如'开头必须用疑问句'。\n"
            "3. 规则数量控制在 5 到 10 条。\n"
            "4. 高权重规则（penalty_weight >= 0.8）必须是爆款与弃子差异最显著的维度。\n"
            "5. 不要输出解释性长文，只返回 JSON。\n\n"
            f"Top 3 爆款：\n{self.json_text(top_records)}\n\n"
            f"Bottom 3 弃子：\n{self.json_text(bottom_records)}"
        )

    def _fallback_rules(
        self,
        top_records: list[dict[str, Any]],
        bottom_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """本地兜底规则：当 LLM 不可用时生成结构化规则。"""
        top_has_digits = any(any(char.isdigit() for char in item.get("title", "")) for item in top_records)
        bottom_dense = any(
            len(item.get("article", "")) >= 600 and "\n\n" not in item.get("article", "")
            for item in bottom_records
        )

        rules: list[dict[str, Any]] = [
            {
                "rule_id": "R001",
                "category": "title_attraction",
                "title": "标题前 14 个字必须出现明确冲突、反差或判断，不要平铺直叙。",
                "trigger_conditions": ["所有文章"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["通用"],
                "positive_example": "订单翻了三倍，利润为什么还在跌？",
                "negative_example": "关于新能源行业发展的几点思考",
                "penalty_weight": 0.9,
                "priority": 1,
                "evidence": "爆款标题普遍在前14字内制造冲突，弃子标题多为平铺直叙。",
            },
            {
                "rule_id": "R002",
                "category": "opening_hook",
                "title": "开头前两句必须给出问题、反常识判断或具体结果，禁止空泛导语。",
                "trigger_conditions": ["所有文章", "content_format != quick_commentary"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["deep_analysis", "data_recap", "project_breakdown"],
                "positive_example": "储能并网容量同比涨了210%，但行业龙头毛利率反而下滑了4个百分点。",
                "negative_example": "近年来，新能源行业取得了长足发展，值得关注。",
                "penalty_weight": 0.85,
                "priority": 2,
                "evidence": "爆款开头均在前两句抛出反差数据或反直觉判断。",
            },
            {
                "rule_id": "R003",
                "category": "paragraph_density",
                "title": "正文每段控制在 2 到 3 句，避免大段密集排版。",
                "trigger_conditions": ["所有文章"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["通用"],
                "positive_example": "先落到数据层面。储能并网容量同比涨了210%。\n\n但真正值得拆开的，是利润归属。",
                "negative_example": "先落到数据层面。储能并网容量同比涨了210%。但真正值得拆开的，是利润归属。这意味着上游材料端……（连续200字不分段）",
                "penalty_weight": 0.7,
                "priority": 3,
                "evidence": "弃子文章普遍存在大段密集排版，完读率低。",
            },
            {
                "rule_id": "R004",
                "category": "data_anchor",
                "title": "至少加入一个具体数字、金额或比例，提升可信度和传播感。",
                "trigger_conditions": ["所有文章"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["通用"],
                "positive_example": "根据公告，该公司Q3营收同比增长47.2%，达到18.6亿元。",
                "negative_example": "该公司业绩表现亮眼，增长显著。",
                "penalty_weight": 0.8,
                "priority": 4,
                "evidence": "爆款文章均包含至少3个具体数据锚点。",
            },
            {
                "rule_id": "R005",
                "category": "closing_interaction",
                "title": "结尾必须抛出一个具体问题，引导读者站队或补充观点。",
                "trigger_conditions": ["所有文章"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["通用"],
                "positive_example": "你觉得这轮储能扩产，利润会先流向集成商还是零部件厂商？",
                "negative_example": "以上就是本文的全部内容，感谢阅读。",
                "penalty_weight": 0.65,
                "priority": 5,
                "evidence": "爆款结尾均设置具体争议问题，弃子多为总结式收束。",
            },
        ]

        if top_has_digits:
            rules.append({
                "rule_id": "R006",
                "category": "title_attraction",
                "title": "标题或开头优先使用具体数字，尤其是金额、涨跌幅、订单量、时间点。",
                "trigger_conditions": ["标题或开头包含数字时"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["通用"],
                "positive_example": "180亿融资砸进人形机器人，量产还要等多久？",
                "negative_example": "人形机器人行业迎来重大融资进展",
                "penalty_weight": 0.75,
                "priority": 6,
                "evidence": "爆款标题中含数字的比例显著高于弃子。",
            })

        if bottom_dense:
            rules.append({
                "rule_id": "R007",
                "category": "paragraph_density",
                "title": "长文必须用更短段落和更明显的小标题切开，避免信息墙。",
                "trigger_conditions": ["正文超过600字"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["deep_analysis", "project_breakdown"],
                "positive_example": "### 热度在前，兑现在后\n\n先看数据。Q3并网容量涨了210%。\n\n### 看着提速，实则分化\n\n但毛利率反而下滑了4个百分点。",
                "negative_example": "（连续600字无小标题无分段的信息墙）",
                "penalty_weight": 0.7,
                "priority": 7,
                "evidence": "弃子文章存在大段密集排版，缺乏小标题切分。",
            })

        return {
            "version": RULE_SCHEMA_VERSION,
            "generated_at": date.today().isoformat(),
            "source": "EvolutionAgent.fallback",
            "rule_count": len(rules[: self.MAX_RULES]),
            "rules": rules[: self.MAX_RULES],
        }

    def _write_rules(self, rules_payload: dict[str, Any]) -> None:
        """将结构化规则写入 JSON 文件，同时同步写入 .txt 格式（模块 C 合并新增）。

        - .json 文件保留原有 schema（含 version/rules/penalty_weight 等），供 BaseAgent.select_top_rules 使用
        - .txt 文件写入精简 JSON 数组格式（rule_id/category/rule/weight/trigger_patterns），
          供 WriterAgent._load_active_rules 使用，符合模块 C 集成协议
        """
        # 1. 写入结构化 .json（原有逻辑，保持不变）
        path = self.best_practices_rule_json_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(rules_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.info(
            "EvolutionAgent wrote %d structured rules to %s",
            rules_payload.get("rule_count", 0),
            path,
        )

        # 2. 模块 C 合并新增：同步写入 .txt 精简 JSON 数组格式
        txt_rules: list[dict[str, Any]] = []
        for rule in rules_payload.get("rules", []):
            if not isinstance(rule, dict):
                continue
            txt_rules.append({
                "rule_id": str(rule.get("rule_id", "")),
                "category": str(rule.get("category", "")),
                "rule": str(rule.get("title", rule.get("rule", ""))),
                "weight": float(rule.get("penalty_weight", 0.5)),
                "trigger_patterns": list(rule.get("trigger_conditions", [])),
            })
        txt_path = self.best_practices_rule_path()
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(
            json.dumps(txt_rules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.info(
            "EvolutionAgent synced %d rules to %s (txt format)",
            len(txt_rules),
            txt_path,
        )

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
