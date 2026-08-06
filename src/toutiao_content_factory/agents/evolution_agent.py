from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
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

# evolution_state.json / evolution_proposals.json 相关常量
EVOLUTION_STATE_FILENAME = "evolution_state.json"
EVOLUTION_PROPOSALS_FILENAME = "evolution_proposals.json"
ITERATION_SUGGESTIONS_FILENAME = "iteration_suggestions.json"
PROPOSAL_SUPPORT_THRESHOLD = 3

# 迭代建议 category → 规则 category 映射
# topic_priority / publish_time_slot 无对应写作规则类别，将写入提案
SUGGESTION_TO_RULE_CATEGORY: dict[str, str] = {
    "title_template": "title_attraction",
    "engagement_template_weight": "closing_interaction",
}


class EvolutionAgent(BaseAgent):
    agent_name = "EvolutionAgent"
    TOP_K = 3
    METRICS_FILENAME = "performance_metrics.json"
    RULES_FILENAME = "best_practices_rule.json"
    MAX_RULES = 10

    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        super().__init__(settings, prompt_repository, openai_service, logger)
        self._account: str = "default"

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

    # ==================================================================
    # 迭代建议接入（Task 10）
    # ==================================================================

    def run_full_loop(self, account: str = "default") -> dict[str, Any]:
        """执行完整的演化循环，整合迭代建议与待审提案。

        流程：
            1. 评估 ``evolution_proposals.json`` 中的待审提案（连续 3 次支持则应用）
            2. 调用 ``run()`` 生成结构化规则
            3. 将生成的规则同步到 ``evolution_state.json``（保留 penalty_weight 修改和 change_log）
            4. 读取 ``iteration_suggestions.json``，与现有规则对比：
               方向一致则直接更新，方向冲突则写入提案

        Args:
            account: 账号标识，用于定位 ``.runtime/accounts/{account}/`` 目录。

        Returns:
            包含各步骤执行结果的字典。
        """
        self._account = account or "default"

        # Step 1: 评估待审提案（SubTask 10.5）
        proposals_result = self._evaluate_pending_proposals()

        # Step 2: 执行原有规则生成
        run_result = self.run()

        # Step 3: 同步规则到 evolution_state.json（保留已有 change_log 和权重修改）
        if run_result.get("status") == "ok":
            self._sync_rules_to_state()

        # Step 4: 处理迭代建议（SubTask 10.1 - 10.4）
        suggestions_result = self._process_iteration_suggestions()

        return {
            "status": "ok",
            "account": self._account,
            "run_result": run_result,
            "proposals_applied": proposals_result.get("applied", 0),
            "proposals_pending": proposals_result.get("pending", 0),
            "suggestions_applied": suggestions_result.get("applied", 0),
            "suggestions_proposed": suggestions_result.get("proposed", 0),
        }

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    def _account_runtime_dir(self) -> Path:
        """返回账号运行时目录：``.runtime/accounts/{account}/``"""
        return self.settings.project_root / ".runtime" / "accounts" / self._account

    def _evolution_state_path(self) -> Path:
        """返回 evolution_state.json 路径。"""
        return self._account_runtime_dir() / EVOLUTION_STATE_FILENAME

    def _evolution_proposals_path(self) -> Path:
        """返回 evolution_proposals.json 路径。"""
        return self._account_runtime_dir() / EVOLUTION_PROPOSALS_FILENAME

    def _iteration_suggestions_path(self) -> Path:
        """返回 iteration_suggestions.json 路径。"""
        return self._account_runtime_dir() / ITERATION_SUGGESTIONS_FILENAME

    # ------------------------------------------------------------------
    # evolution_state.json 读写
    # ------------------------------------------------------------------

    def _load_evolution_state(self) -> dict[str, Any]:
        """读取 ``evolution_state.json``，返回含 ``rules`` 和 ``change_log`` 的 dict。

        文件不存在或格式错误时返回空骨架，不抛异常。
        """
        path = self._evolution_state_path()
        if not path.exists():
            return {"version": RULE_SCHEMA_VERSION, "rules": [], "change_log": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("读取 evolution_state.json 失败 %s: %s", path, exc)
            return {"version": RULE_SCHEMA_VERSION, "rules": [], "change_log": []}
        if not isinstance(payload, dict):
            return {"version": RULE_SCHEMA_VERSION, "rules": [], "change_log": []}
        if not isinstance(payload.get("rules"), list):
            payload["rules"] = []
        if not isinstance(payload.get("change_log"), list):
            payload["change_log"] = []
        return payload

    def _save_evolution_state(self, state: dict[str, Any]) -> None:
        """写入 ``evolution_state.json``。"""
        path = self._evolution_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.info("EvolutionAgent 已更新 evolution_state.json: %s", path)

    def _sync_rules_to_state(self) -> None:
        """将 ``best_practices_rule.json`` 中的规则同步到 ``evolution_state.json``。

        同步策略：
            - 保留现有 ``change_log``
            - 按 ``rule_id`` 匹配：更新规则内容，保留 ``penalty_weight``（可能被建议修改过）
            - 无匹配的新规则直接添加
        """
        rules_payload = self.read_best_practices_rules_json()
        if not rules_payload:
            return

        state = self._load_evolution_state()
        new_rules = list(rules_payload.get("rules", []))
        old_rules: list[dict[str, Any]] = state.get("rules", [])

        # 构建 rule_id → penalty_weight 映射（保留被建议修改过的权重）
        old_weights: dict[str, float] = {}
        for rule in old_rules:
            if isinstance(rule, dict):
                rule_id = str(rule.get("rule_id", ""))
                if rule_id:
                    try:
                        old_weights[rule_id] = float(rule.get("penalty_weight", 0.5))
                    except (TypeError, ValueError):
                        pass

        merged: list[dict[str, Any]] = []
        for new_rule in new_rules:
            if not isinstance(new_rule, dict):
                continue
            merged_rule = dict(new_rule)
            rule_id = str(new_rule.get("rule_id", ""))
            if rule_id in old_weights:
                merged_rule["penalty_weight"] = old_weights[rule_id]
            merged.append(merged_rule)

        state["version"] = RULE_SCHEMA_VERSION
        state["rules"] = merged
        self._save_evolution_state(state)

    # ------------------------------------------------------------------
    # evolution_proposals.json 读写
    # ------------------------------------------------------------------

    def _load_proposals(self) -> list[dict[str, Any]]:
        """读取 ``evolution_proposals.json``，返回提案列表。文件不存在时返回空列表。"""
        path = self._evolution_proposals_path()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("读取 evolution_proposals.json 失败 %s: %s", path, exc)
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _save_proposals(self, proposals: list[dict[str, Any]]) -> None:
        """写入 ``evolution_proposals.json``。"""
        path = self._evolution_proposals_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(proposals, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # iteration_suggestions.json 读取（SubTask 10.1）
    # ------------------------------------------------------------------

    def _load_iteration_suggestions(self) -> list[dict[str, Any]]:
        """读取 ``iteration_suggestions.json``，返回建议列表。

        文件不存在或为空时记录日志并返回空列表，不报错。
        """
        path = self._iteration_suggestions_path()
        if not path.exists():
            self.logger.debug("iteration_suggestions.json 不存在，跳过: %s", path)
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("读取 iteration_suggestions.json 失败 %s: %s", path, exc)
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    # ------------------------------------------------------------------
    # 待审提案评估（SubTask 10.5）
    # ------------------------------------------------------------------

    def _evaluate_pending_proposals(self) -> dict[str, Any]:
        """评估待审提案：连续 3 次数据支持则应用，否则保留并更新支持计数。

        每次调用时，如果 ``iteration_suggestions.json`` 中存在与提案相同
        ``category`` 的建议，则递增提案的 ``support_count``。

        Returns:
            含 ``applied``（已应用数）和 ``pending``（保留待审数）的字典。
        """
        proposals = self._load_proposals()
        if not proposals:
            return {"applied": 0, "pending": 0}

        suggestions = self._load_iteration_suggestions()
        state = self._load_evolution_state()

        applied = 0
        remaining: list[dict[str, Any]] = []

        for proposal in proposals:
            suggestion = proposal.get("suggestion")
            if not isinstance(suggestion, dict):
                continue

            # 检查 iteration_suggestions.json 中是否有相同建议
            if self._find_matching_suggestion(suggestion, suggestions) is not None:
                proposal["support_count"] = int(proposal.get("support_count", 0)) + 1

            if int(proposal.get("support_count", 0)) >= PROPOSAL_SUPPORT_THRESHOLD:
                self._apply_proposal_to_state(proposal, state)
                applied += 1
            else:
                remaining.append(proposal)

        self._save_proposals(remaining)
        if applied > 0:
            self._save_evolution_state(state)

        self.logger.info(
            "EvolutionAgent 评估提案：应用 %d 条，保留 %d 条待审",
            applied,
            len(remaining),
        )
        return {"applied": applied, "pending": len(remaining)}

    def _find_matching_suggestion(
        self, suggestion: dict[str, Any], suggestions: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """在建议列表中查找与给定建议匹配的项（按 ``category`` 匹配）。"""
        target_category = str(suggestion.get("category", "")).strip()
        if not target_category:
            return None
        for item in suggestions:
            if str(item.get("category", "")).strip() == target_category:
                return item
        return None

    def _apply_proposal_to_state(
        self, proposal: dict[str, Any], state: dict[str, Any]
    ) -> None:
        """将连续 3 次支持的提案应用到 ``evolution_state.json``。"""
        suggestion = proposal.get("suggestion")
        if not isinstance(suggestion, dict):
            return

        category = str(suggestion.get("category", "")).strip()
        suggested_value = str(suggestion.get("suggested_value", ""))
        evidence = str(suggestion.get("evidence", ""))

        rules: list[dict[str, Any]] = state.get("rules", [])
        rule_category = SUGGESTION_TO_RULE_CATEGORY.get(category, "")

        target_rule: dict[str, Any] | None = None
        if rule_category:
            for rule in rules:
                if isinstance(rule, dict) and rule.get("category") == rule_category:
                    target_rule = rule
                    break

        timestamp = datetime.now().isoformat(timespec="seconds")

        if target_rule is not None:
            old_value = str(target_rule.get("penalty_weight", ""))
            new_weight = self._compute_new_weight(
                suggested_value, target_rule.get("penalty_weight", 0.5)
            )
            target_rule["penalty_weight"] = new_weight
            new_value = str(new_weight)
        else:
            # 无匹配规则，作为新规则添加
            old_value = ""
            new_rule: dict[str, Any] = {
                "rule_id": f"R{len(rules) + 1:03d}",
                "category": rule_category or category,
                "title": suggested_value,
                "trigger_conditions": ["通用"],
                "applicable_industries": ["通用"],
                "applicable_formats": ["通用"],
                "positive_example": "",
                "negative_example": "",
                "penalty_weight": 0.7,
                "priority": len(rules) + 1,
                "evidence": evidence,
            }
            rules.append(new_rule)
            new_value = suggested_value

        state["change_log"].append({
            "timestamp": timestamp,
            "category": category,
            "old_value": old_value,
            "new_value": new_value,
            "source": "monthly_review",
            "evidence": evidence,
        })

    # ------------------------------------------------------------------
    # 迭代建议处理（SubTask 10.1 - 10.4）
    # ------------------------------------------------------------------

    def _process_iteration_suggestions(self) -> dict[str, Any]:
        """读取迭代建议，与现有规则对比后应用或写入提案。

        - 方向一致：直接更新 ``evolution_state.json`` 并记录 ``change_log``
        - 方向冲突或无匹配规则：写入 ``evolution_proposals.json``

        Returns:
            含 ``applied``（直接应用数）和 ``proposed``（写入提案数）的字典。
        """
        suggestions = self._load_iteration_suggestions()
        if not suggestions:
            self.logger.info("无迭代建议可处理")
            return {"applied": 0, "proposed": 0}

        state = self._load_evolution_state()
        existing_proposals = self._load_proposals()

        applied = 0
        proposed = 0

        for suggestion in suggestions:
            category = str(suggestion.get("category", "")).strip()
            if not category:
                continue

            rule_category = SUGGESTION_TO_RULE_CATEGORY.get(category, "")
            matching_rule: dict[str, Any] | None = None
            if rule_category:
                for rule in state.get("rules", []):
                    if isinstance(rule, dict) and rule.get("category") == rule_category:
                        matching_rule = rule
                        break

            if matching_rule is None:
                # 无匹配规则 → 写入提案（SubTask 10.4）
                self._add_proposal(
                    suggestion, None, "无对应类别的现有规则", existing_proposals
                )
                proposed += 1
                continue

            # 判断方向是否一致（SubTask 10.2）
            if self._is_direction_consistent(suggestion, matching_rule):
                # 方向一致 → 直接更新（SubTask 10.3）
                self._apply_suggestion_to_state(suggestion, matching_rule, state)
                applied += 1
            else:
                # 方向冲突 → 写入提案（SubTask 10.4）
                self._add_proposal(
                    suggestion,
                    matching_rule,
                    "建议方向与现有规则不一致",
                    existing_proposals,
                )
                proposed += 1

        if applied > 0:
            self._save_evolution_state(state)
        if proposed > 0:
            self._save_proposals(existing_proposals)

        self.logger.info(
            "EvolutionAgent 处理迭代建议：直接应用 %d 条，写入提案 %d 条",
            applied,
            proposed,
        )
        return {"applied": applied, "proposed": proposed}

    def _is_direction_consistent(
        self, suggestion: dict[str, Any], rule: dict[str, Any]
    ) -> bool:
        """判断建议方向是否与现有规则一致（SubTask 10.2）。

        启发式判断：从建议的 ``suggested_value`` 中提取关键中文词汇，
        检查是否在规则的 ``title`` / ``positive_example`` / ``evidence`` 中出现。
        无法提取关键词时默认方向一致（同类规则）。
        """
        suggested_value = str(suggestion.get("suggested_value", ""))
        rule_text = " ".join([
            str(rule.get("title", "")),
            str(rule.get("positive_example", "")),
            str(rule.get("evidence", "")),
        ])

        key_terms = self._extract_key_terms(suggested_value)
        if not key_terms:
            return True

        return any(term in rule_text for term in key_terms)

    def _extract_key_terms(self, text: str) -> list[str]:
        """从文本中提取关键中文词汇（2 字以上），过滤常见停用词。"""
        if not text:
            return []
        terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        stop_words = {
            "建议", "使用", "增加", "提升", "优先级", "权重", "模板",
            "时段", "发布", "内容", "产出", "频次", "当前", "平均",
        }
        return [t for t in terms if t not in stop_words]

    def _apply_suggestion_to_state(
        self,
        suggestion: dict[str, Any],
        rule: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        """将建议直接应用到 ``evolution_state.json`` 的规则中，并记录 ``change_log``（SubTask 10.3）。

        ``change_log`` 格式：::

            {"timestamp": "...", "category": "...", "old_value": "...",
             "new_value": "...", "source": "monthly_review", "evidence": "..."}
        """
        category = str(suggestion.get("category", "")).strip()
        suggested_value = str(suggestion.get("suggested_value", ""))
        evidence = str(suggestion.get("evidence", ""))

        old_weight = rule.get("penalty_weight", 0.5)
        new_weight = self._compute_new_weight(suggested_value, old_weight)
        rule["penalty_weight"] = new_weight

        state["change_log"].append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "category": category,
            "old_value": str(old_weight),
            "new_value": str(new_weight),
            "source": "monthly_review",
            "evidence": evidence,
        })

    def _compute_new_weight(self, suggested_value: str, current_weight: float) -> float:
        """从建议值中提取目标权重，或基于当前权重递增。

        对于含数字的建议（如 ``站队式:0.5``），提取数字作为新权重；
        否则在当前权重基础上 +0.05，上限 1.0。
        """
        match = re.search(r"(\d+\.?\d*)", suggested_value)
        if match:
            try:
                value = float(match.group(1))
                if 0.0 < value <= 1.0:
                    return round(value, 2)
            except ValueError:
                pass

        try:
            current = float(current_weight)
        except (TypeError, ValueError):
            current = 0.5
        return round(min(1.0, current + 0.05), 2)

    def _add_proposal(
        self,
        suggestion: dict[str, Any],
        current_rule: dict[str, Any] | None,
        conflict_reason: str,
        proposals: list[dict[str, Any]],
    ) -> None:
        """将冲突建议作为提案追加到 ``proposals`` 列表（SubTask 10.4）。

        每条提案含字段：``suggestion`` / ``current_rule`` / ``conflict_reason`` /
        ``pending_since`` / ``support_count``。

        若已存在相同 ``category`` 的提案，则更新内容并保留 ``support_count``。
        """
        category = str(suggestion.get("category", "")).strip()
        for existing in proposals:
            existing_suggestion = existing.get("suggestion")
            if isinstance(existing_suggestion, dict) and \
                    str(existing_suggestion.get("category", "")).strip() == category:
                existing["suggestion"] = suggestion
                if current_rule is not None:
                    existing["current_rule"] = current_rule
                existing["conflict_reason"] = conflict_reason
                return

        proposal: dict[str, Any] = {
            "suggestion": suggestion,
            "current_rule": current_rule if current_rule is not None else {},
            "conflict_reason": conflict_reason,
            "pending_since": datetime.now().isoformat(timespec="seconds"),
            "support_count": 0,
        }
        proposals.append(proposal)
