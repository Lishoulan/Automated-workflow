from __future__ import annotations

import re
import copy
from difflib import SequenceMatcher
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from toutiao_content_factory.agents.agency_roles import AgencyRoleLoader
from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import AppSettings, StyleProfile
from toutiao_content_factory.models import (
    CacheContext,
    DataReport,
    EditorialCouncilReport,
    GeneratedContent,
    QualityReviewReport,
)
from toutiao_content_factory.services.article_quality_checker import ArticleQualityChecker
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository


class QualityReviewCouncilAgent(BaseAgent):
    """Run an independent, post-polish quality audit for a finished article."""

    agent_name = "QualityReviewCouncilAgent"
    DEFAULT_ROLE_NAMES = (
        "quality-fact-checker",
        "quality-logic-auditor",
        "quality-structure-editor",
        "quality-originality-auditor",
        "quality-compliance-editor",
    )
    _FACT_FIELDS = (
        "topic_title",
        "core_data",
        "industry_trends",
        "growth_numbers",
        "companies",
        "supply_chain_keywords",
        "key_points",
        "watch_metrics",
        "evidence_notes",
        "risk_flags",
        "source_facts",
        "source_references",
        "source_records",
        "fact_evidence",
        "article_kind",
        "is_hot_topic",
    )
    _INTERNAL_LEAK_RE = re.compile(
        r"manual_paste|evergreen_pool|benchmark_hotspot|source_document|source_material|"
        r"editorial_council|quality_review|内部标签|素材标签|已跳过缓存|强制重新生成|"
        r"主体关系也不能省略|主体关系也要单独核对|能够直接回指|当前记录的核心内容是|"
        r"当前记录涉及|材料目前能支持的事实是|后续复盘可以先核对|"
        r"这些项目是后续复盘时可以核对的信号|质量审核专家团|发布就绪|DataReport",
        re.IGNORECASE,
    )
    _ABSOLUTE_CLAIMS = (
        "绝对",
        "完全没有风险",
        "一定成功",
        "保证收益",
        "必然上涨",
        "必然成功",
        "首创",
        "独家",
    )
    _CAUSAL_TOKENS = (
        "因为",
        "原因",
        "因此",
        "导致",
        "带来",
        "意味着",
        "取决于",
        "前提",
        "如果",
        "但",
        "然而",
        "反而",
        "不过",
        "风险",
    )
    _AI_CLICHES = (
        "综上所述",
        "不可否认",
        "值得注意的是",
        "不难发现",
        "众所周知",
        "毋庸置疑",
        "在当今快节奏的世界里",
        "由此可见",
    )
    _NARRATIVE_CHECKER_EXEMPTIONS = (
        "RULE_006",
        "RULE_009",
        "信息密度不足",
        "无法回指事实的模糊主体",
        "字数不足1500字",
    )

    def __init__(
        self,
        settings: AppSettings,
        prompt_repository: PromptRepository,
        openai_service: OpenAIContentService,
        logger: logging.Logger,
    ) -> None:
        super().__init__(settings, prompt_repository, openai_service, logger)
        roles_path = Path(settings.agency_roles_dir)
        if not roles_path.is_absolute():
            roles_path = settings.project_root / roles_path
        self.role_loader = AgencyRoleLoader(str(roles_path), logger)
        self.quality_checker = ArticleQualityChecker(wechat_mode=settings.wechat_auto_publish_mode)

    def run(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
        editorial_council: EditorialCouncilReport | None = None,
        revision_round: int = 0,
    ) -> QualityReviewReport:
        article = self._article(content)
        title = content.recommended_titles[0] if content.recommended_titles else ""
        quality_settings = getattr(self.settings, "quality_review", None)
        role_names = list(
            getattr(quality_settings, "roles", None) or self.DEFAULT_ROLE_NAMES
        )
        role_context = self.role_loader.get_role_context(role_names) or self._fallback_role_context(role_names)
        local_snapshot = self._local_snapshot(title, article, data_report)
        pending = []
        if self.settings.wechat_auto_publish_mode:
            from toutiao_content_factory.services.wechat_revision import pending_verifications
            pending = pending_verifications(content, data_report.fact_evidence)
        editorial_brief = self._editorial_brief(editorial_council)

        fact_payload = self._fact_payload(data_report)
        if self.settings.wechat_auto_publish_mode:
            from toutiao_content_factory.services.wechat_evidence import requirements
            fact_payload = {name: getattr(data_report, name) for name in
                            ("topic_title", "article_kind", "source_records", "fact_evidence")}
            fact_payload["source_records"] = [source for source in data_report.source_records
                                              if source.get("source_role") != "background"]
            editorial_brief = requirements(data_report.article_kind)["instruction"]
        review_style = self.style_digest(style)
        if self.settings.wechat_auto_publish_mode:
            review_style = (f"语气：{style.tone}。" + editorial_brief
                            + "正文目标2000～2200字，最低1750字；每段不超过180字。"
                            "结构围绕已披露事实、解释及证据边界，不强行讨论订单、利润或竞争优势。")
        prompt = self.render_prompt(
            "quality_review_council_prompt.md",
            platform=self.settings.platform,
            topic_title=data_report.topic_title,
            role_context=role_context,
            style_digest=review_style,
            article=article,
            title=title,
            data_json=self.json_text(fact_payload),
            editorial_brief=editorial_brief,
            local_snapshot=self.json_text(local_snapshot),
            cache_note=self.cache_note(cache_context),
        )
        if self.settings.platform == "wechat":
            # Describe the wire format without supplying a pre-filled verdict
            # and score that a small model can copy instead of auditing.
            sample_start = prompt.rfind('\n{\n  "article_version":')
            if sample_start >= 0:
                prompt = prompt[:sample_start]
            properties = {
                name: {"type": "integer", "minimum": 0, "maximum": 100}
                for name in ("overall_score", "fact_score", "logic_score", "structure_score", "originality_score", "compliance_score")
            }
            properties.update({
                name: {"type": "array", "items": {"type": "string"}}
                for name in ("hard_blockers", "fact_issues", "logic_issues", "structure_issues", "originality_issues",
                             "compliance_issues", "required_revisions", "optional_improvements")
            })
            properties["verdict"] = {"type": "string", "enum": ["approved", "needs_revision", "blocked"]}
            properties["chief_editor_conclusion"] = {"type": "string"}
            properties["role_reviews"] = {"type": "array", "items": {"type": "object"}}
            properties["issues"] = {"type": "array", "items": {"type": "object", "properties": {
                "requirement": {"type": "string"}, "paragraph": {"type": "string"}, "category": {"type": "string"},
                "target": {"enum": ["body", "title"]}, "verification": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}}}}}
            properties["issue_evaluations"] = {"type": "array", "items": {"type": "object", "properties": {
                "issue_id": {"type": "string"}, "status": {"enum": ["resolved", "still_present", "unverifiable"]},
                "after_quote": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "verification": {"type": "string"}}}}
            if self.settings.wechat_auto_publish_mode:
                from toutiao_content_factory.services.wechat_evidence import requirements
                prompt += "\n" + requirements(data_report.article_kind)["instruction"]
                prompt += "\n逐条使用fact_evidence核对数字、单位、统计对象和统计期；发布日期不是统计期。issues的requirement须对应意见数组，paragraph须为正文原段落。"
                prompt += "\n只把正文实际出现的表述列为稿件问题，不把出处元数据或写作要求当成正文。原文可核对的产品型号和技术缩写需按技术语境核验，不能仅因是缩写就当作自动化流程标签；真实流程标签仍须阻断。"
                prompt += "\n原文没有披露比较对象、测试方法或独立验证结果时，检查正文是否明确写出未披露且保留发言人、报道及预告归因。准确说明未知边界本身不是缺失事实，不要求补造具体对象、测试结果或市场竞争优势；正文声称已验证或偷换对象仍须阻断。"
                prompt += "\n每条必须修改的要求应能通过现有事实的补充、删除无依据断言或收窄表述完成。只针对成稿现存问题，不要求写出资料中不存在的信息。issues必须给出正文原段落和verification核验理由，事实问题及硬阻断提供匹配evidence_ids；标题问题target明确标为title，并引用标题原句。"
                prompt += "\n先独立检查现稿，再对待核实问题逐项返回issue_evaluations。历史要求中的示例数字不是事实，不得要求补造。事实及硬阻断需匹配fact_evidence编号；结构、表达和逻辑可留空编号，但须现稿原句、真实前后差异及核验理由。未修改正文或仅换措辞不能证明逻辑或结构问题解决。不确定则unverifiable。\n"
                prompt += self.json_text({"pending_verifications": pending})
            prompt += "\n输出审核结果，不要输出Schema本身。没有发现的问题返回空数组，不要编造问题。\n" + self.json_text({
                "type": "object", "properties": properties, "required": list(properties),
            })
        system_prompt = (
            "你是成稿质量审核总编，负责协调五名独立审校专家。"
            "审核对象是已经润色后的文章，不负责替作者补写事实。"
            "所有事实问题必须能回指输入数据；无法确认的内容要指出不确定性。"
            "只输出一个合法 JSON 对象，不要输出 Markdown。"
        )
        payload = self.request_json(system_prompt, prompt)
        if payload:
            report = QualityReviewReport.from_dict(payload)
            report.article_version = "post_polish"
            report.reviewed_char_count = len(re.sub(r"\s+", "", article))
            report.revision_round = max(0, int(revision_round))
            report.used_fallback = False
            if report.is_usable():
                self._apply_local_hard_gate(report, local_snapshot, article=article, title=title)
                if self.settings.wechat_auto_publish_mode:
                    self._complete_audit(report, payload, content, data_report, article, title, pending, local_snapshot)
                self.logger.info(
                    "Quality review council completed: verdict=%s score=%d blockers=%d",
                    report.verdict,
                    report.overall_score,
                    len(report.hard_blockers),
                )
                return report
            self.logger.warning("Quality review council returned an unusable report; using local audit.")

        report = self._fallback(
            content,
            data_report,
            revision_round=revision_round,
            role_names=role_names,
        )
        if self.settings.wechat_auto_publish_mode:
            report.audit_integrity = {"status": "model_unavailable", "reasons": ["model_unavailable"],
                                      "decision_category": "model_unavailable",
                                      "supplement_count": content.audit_supplement_count, "primary_report": payload}
            content.audit_history.append(copy.deepcopy(report.audit_integrity))
        return report

    def _complete_audit(self, report, primary, content, data_report, article, title, pending, local_snapshot):
        from toutiao_content_factory.services.wechat_revision import (
            audit_fact_material, bind_issues, manuscript_fingerprint, integrity_reasons, apply_supplement)
        facts = data_report.fact_evidence
        bind_issues(report, article, facts)
        reasons = integrity_reasons(report, article, title, facts, pending, primary)
        diagnostic = {"status": "incomplete" if reasons else "complete", "reasons": reasons,
                      "initial_reasons": list(reasons),
                      "primary_report": primary, "primary_checked_report": asdict(report),
                      "fingerprint": manuscript_fingerprint(article, title, audit_fact_material(data_report)),
                      "pending_verifications": pending, "supplement_count": content.audit_supplement_count}
        deadline = os.getenv("WECHAT_RUN_DEADLINE_EPOCH", "")
        remaining = float(deadline) - time.time() if deadline else float("inf")
        if reasons and content.audit_supplement_count < 1 and remaining >= 60 and self.openai_service.available:
            content.audit_supplement_count += 1
            diagnostic["supplement_count"] = content.audit_supplement_count
            repair_input = {"article": article, "title": title, "fact_evidence": facts,
                "incomplete_reasons": reasons, "pending_verifications": pending,
                "findings": [{key: issue.get(key) for key in ("issue_id", "category", "requirement", "paragraph", "target")}
                             for issue in report.issues]}
            prompt = ("仅补齐审核定位、核验及结论矛盾，不能重评打分、提取新事实或改写正文。历史示例数字不可变成必须写入的事实。"
                "每项findings返回finding_evaluations：issue_id,status(confirmed/not_applicable/unverifiable),target(body/title),"
                "current_quote(现稿或标题原句),evidence_ids,verification(具体理由)。每项pending_verifications返回"
                "issue_evaluations：issue_id,status(resolved/still_present/unverifiable),after_quote,evidence_ids,verification。"
                "事实问题及硬阻断必须匹配事实编号；结构表达逻辑编号可空，但要解释真实前后差异。未修改或仅换措辞不能解决逻辑结构问题。"
                "unverifiable不得放行。返回verdict(approved/needs_revision/blocked)及chief_editor_conclusion。只输出JSON。\n"
                + self.json_text(repair_input))
            try:
                supplement = self.openai_service.create_json_completion("你负责审核报告完整性补核。只输出JSON。", prompt,
                                                                       single_attempt=True, timeout_seconds=60)
                diagnostic["supplement_report"] = supplement
                # Apply to a copy so a failed repair cannot erase the primary blockers.
                repaired = copy.deepcopy(report)
                apply_supplement(repaired, supplement, article, title, facts)
                self._apply_local_hard_gate(repaired, local_snapshot, article=article, title=title)
                reasons = integrity_reasons(repaired, article, title, facts, pending, supplement)
                if not reasons:
                    report.__dict__.update(repaired.__dict__)
                diagnostic.update(status="incomplete" if reasons else "complete", reasons=reasons)
            except Exception as exc:
                diagnostic.update(status="model_unavailable", reasons=list(reasons) + ["supplement_failed"],
                                  supplement_error=type(exc).__name__)
                if isinstance(exc, ValueError):
                    diagnostic.update(status="incomplete", reasons=list(reasons) + ["invalid_supplement_evidence"],
                                      supplement_validation_reason=str(exc))
        elif reasons:
            diagnostic["supplement_skipped"] = "exhausted" if content.audit_supplement_count else "insufficient_budget_or_model"
        diagnostic["decision_category"] = (
            "model_unavailable" if diagnostic["status"] == "model_unavailable" else
            "audit_incomplete" if diagnostic["status"] != "complete" else
            "manuscript_issue" if report.needs_revision else "approved")
        report.audit_integrity = diagnostic
        content.audit_history.append(copy.deepcopy(diagnostic))

    @classmethod
    def _fact_payload(cls, data_report: DataReport) -> dict[str, Any]:
        """只向评审模型暴露事实候选字段。

        历史 bug：直接把 ``data_report.__dict__`` 全量塞进提示词，会把
        writing_brief / content_format / evidence_strength 这类内部写作参数
        一并喂给评审模型，模型据此判定「内部标签泄露」并阻断发布——而文章
        正文其实是干净的。这里按白名单只保留事实候选。
        """
        return {
            name: value
            for name in cls._FACT_FIELDS
            if (value := getattr(data_report, name, None)) not in (None, "", [], {})
        }

    def _fallback(
        self,
        content: GeneratedContent,
        data_report: DataReport,
        *,
        revision_round: int = 0,
        role_names: list[str] | None = None,
    ) -> QualityReviewReport:
        article = self._article(content)
        title = content.recommended_titles[0] if content.recommended_titles else ""
        is_hot_topic = bool(getattr(data_report, "is_hot_topic", False))
        local = self._local_snapshot(title, article, data_report)

        hard_blockers = list(local["hard_blockers"])
        fact_issues = list(local["fact_issues"])
        logic_issues = list(local["logic_issues"])
        structure_issues = list(local["structure_issues"])
        originality_issues = list(local["originality_issues"])
        compliance_issues = list(local["compliance_issues"])

        fact_score = int(local["fact_score"])
        logic_score = int(local["logic_score"])
        structure_score = int(local["structure_score"])
        originality_score = int(local["originality_score"])
        compliance_score = int(local["compliance_score"])
        overall_score = round(
            fact_score * 0.25
            + logic_score * 0.20
            + structure_score * 0.20
            + originality_score * 0.20
            + compliance_score * 0.15
        )

        quality_settings = getattr(self.settings, "quality_review", None)
        min_overall = int(getattr(quality_settings, "min_overall_score", 78))
        min_dimension = int(getattr(quality_settings, "min_dimension_score", 65))
        dimensions = (
            fact_score,
            logic_score,
            structure_score,
            originality_score,
            compliance_score,
        )
        needs_revision = bool(
            hard_blockers
            or overall_score < min_overall
            or min(dimensions) < min_dimension
        )
        verdict = "blocked" if hard_blockers else "needs_revision" if needs_revision else "approved"

        required_revisions = self.unique(
            [
                *hard_blockers,
                *fact_issues,
                *logic_issues,
                *structure_issues,
                *originality_issues,
                *compliance_issues,
            ]
        )[:8]
        optional_improvements = self.unique(
            (
                [
                    "保留具体人物、时间、地点和处理结果，避免把推断写成事实。",
                    "按事件经过、当事人处境和读者提醒组织段落。",
                    "结尾保留一个具体问题，方便读者表达自己的处理方式。",
                ]
                if self._content_mode(data_report) == "social_story"
                else [
                    "保留具体主体、时间点和数据来源表达，避免把推断写成事实。",
                    "让相邻段落之间有明确的因果、转折或递进关系。",
                    "结尾保留一个可继续观察的变量，避免口号式拔高。",
                ]
            )
        )[:5]
        if not needs_revision:
            required_revisions = []

        conclusion = (
            "本地规则审核未发现需要阻断的问题，文章可以进入后续发布审核。"
            if verdict == "approved"
            else "本地规则审核发现需要处理的问题；模型不可用时不擅自补写事实，建议先按清单返工。"
        )
        if is_hot_topic:
            conclusion += "本次为热点快评，按观点密度和时效性评估，不以缺少长篇来源材料直接判定不合格。"

        return QualityReviewReport(
            article_version="post_polish",
            overall_score=overall_score,
            fact_score=fact_score,
            logic_score=logic_score,
            structure_score=structure_score,
            originality_score=originality_score,
            compliance_score=compliance_score,
            verdict=verdict,
            hard_blockers=hard_blockers,
            fact_issues=fact_issues,
            logic_issues=logic_issues,
            structure_issues=structure_issues,
            originality_issues=originality_issues,
            compliance_issues=compliance_issues,
            required_revisions=required_revisions,
            optional_improvements=optional_improvements,
            role_reviews=[
                {
                    "role": role,
                    "judgment": "本地规则已完成保守检查，模型不可用时不扩展输入事实。",
                    "confidence": 0.55,
                }
                for role in (role_names or list(self.DEFAULT_ROLE_NAMES))
            ],
            chief_editor_conclusion=conclusion,
            reviewed_char_count=len(re.sub(r"\s+", "", article)),
            revision_round=max(0, int(revision_round)),
            used_fallback=True,
        )

    def _local_snapshot(self, title: str, article: str, data_report: DataReport) -> dict[str, Any]:
        is_hot_topic = bool(getattr(data_report, "is_hot_topic", False))
        narrative = self._content_mode(data_report) == "social_story"
        clean_article = re.sub(r"\s+", "", article)
        paragraphs = [item.strip() for item in article.split("\n\n") if item.strip()]
        checker = self.quality_checker.check_article(
            title,
            article,
            check_first_publish=not is_hot_topic,
        )
        checker_issues = self.unique(
            [
                str(item).strip()
                for item in checker.issues
                if str(item).strip()
                and not (is_hot_topic and "字数不足" in str(item))
            ]
        )
        if narrative:
            checker_issues = [
                issue
                for issue in checker_issues
                if not any(marker in issue for marker in self._NARRATIVE_CHECKER_EXEMPTIONS)
                and not (
                    "互动吸引力不足" in issue
                    and re.search(r"(?:你|换成是你|你会|怎么看|怎么处理).{0,40}[？?]", article)
                )
            ]

        expected_companies = [
            *(getattr(data_report, "focus_companies", []) or []),
            *(getattr(data_report, "companies", []) or []),
        ]
        integrity = self.quality_checker.check_editorial_integrity(
            title,
            article,
            expected_companies=expected_companies,
            require_body=True,
        )
        unsupported = self._unsupported_numeric_tokens(article, data_report)
        if self.settings.wechat_auto_publish_mode and data_report.fact_evidence:
            from toutiao_content_factory.services.wechat_evidence import unsupported_claims
            unsupported = unsupported_claims(article, data_report.fact_evidence)
        internal_leaks = self._INTERNAL_LEAK_RE.findall(article)
        hard_blockers: list[str] = list(integrity.get("hard_blockers", []))
        if not clean_article:
            hard_blockers.append("正文为空，不能进入发布流程。")
        elif narrative and len(clean_article) < 700:
            hard_blockers.append(
                f"社会故事正文仅{len(clean_article)}字，低于发布审核使用的700字底线。"
            )
        if unsupported:
            hard_blockers.append(
                f"正文存在未在数据报告中找到依据的数字：{'、'.join(unsupported[:6])}。"
            )
        if internal_leaks:
            hard_blockers.append("正文泄露了内部素材或流程标签，必须在发布前清除。")

        fact_issues: list[str] = []
        if unsupported:
            fact_issues.append(f"核对数字口径和来源：{'、'.join(unsupported[:6])}。")
        if not is_hot_topic and not (
            data_report.evidence_notes
            or data_report.growth_numbers
            or data_report.core_data
        ):
            fact_issues.append("正文缺少可回指的数据或公开材料依据。")
        if checker_issues and any("来源" in issue or "数字" in issue for issue in checker_issues):
            fact_issues.extend(issue for issue in checker_issues if issue not in fact_issues)

        logic_issues: list[str] = []
        if len(clean_article) >= 240 and not any(token in article for token in self._CAUSAL_TOKENS):
            logic_issues.append("正文缺少因果、转折或条件边界，论点之间可能只是并列。")
        if (
            not narrative
            and len(paragraphs) >= 4
            and not any(token in article for token in ("但", "然而", "风险", "前提", "如果"))
        ):
            logic_issues.append("正文缺少反方或失效条件，建议补充边界讨论。")

        structure_issues: list[str] = []
        if not is_hot_topic and len(paragraphs) < 5:
            structure_issues.append("正文段落少于5段，开头、展开和结尾层次不完整。")
        elif is_hot_topic and len(paragraphs) < 3:
            structure_issues.append("热点快评至少需要开头、观点展开和收束三个段落。")
        long_paragraphs = sum(1 for paragraph in paragraphs if len(re.sub(r"\s+", "", paragraph)) > 180)
        if long_paragraphs:
            structure_issues.append(f"有{long_paragraphs}个段落超过180字，建议拆短以适配手机阅读。")
        duplicate_count = self._duplicate_paragraph_count(paragraphs)
        if duplicate_count:
            structure_issues.append(f"发现{duplicate_count}处重复或高度相似段落，需合并去重。")

        originality_issues: list[str] = []
        cliché_hits = [phrase for phrase in self._AI_CLICHES if phrase in article]
        if cliché_hits:
            originality_issues.append(f"存在AI套话：{'、'.join(cliché_hits[:5])}。")
        if checker_issues and any("模板" in issue for issue in checker_issues):
            originality_issues.extend(issue for issue in checker_issues if "模板" in issue)
        if duplicate_count:
            originality_issues.append("重复段落会削弱文章的原创感和阅读节奏。")

        compliance_issues = self.unique(
            [*checker_issues, *integrity.get("issues", [])]
        )
        absolute_hits = [phrase for phrase in self._ABSOLUTE_CLAIMS if phrase in article or phrase in title]
        if absolute_hits:
            compliance_issues.append(f"存在未经限定的绝对化表达：{'、'.join(absolute_hits[:5])}。")
        if internal_leaks:
            compliance_issues.append("需删除内部来源标记、流程字段或素材占位符。")

        fact_score = max(25, 94 - len(unsupported) * 20)
        if fact_issues and not is_hot_topic:
            fact_score -= min(20, len(fact_issues) * 7)
        logic_score = 88 - min(32, len(logic_issues) * 16)
        structure_score = 94 - min(50, len(structure_issues) * 15)
        originality_score = 94 - min(55, len(originality_issues) * 18)
        compliance_score = 96 - min(80, len(compliance_issues) * 16)
        if hard_blockers:
            compliance_score = min(compliance_score, 35)

        return {
            "hard_blockers": self.unique(hard_blockers),
            "fact_issues": self.unique(fact_issues)[:6],
            "logic_issues": self.unique(logic_issues)[:6],
            "structure_issues": self.unique(structure_issues)[:6],
            "originality_issues": self.unique(originality_issues)[:6],
            "compliance_issues": self.unique(compliance_issues)[:8],
            "fact_score": max(0, min(100, fact_score)),
            "logic_score": max(0, min(100, logic_score)),
            "structure_score": max(0, min(100, structure_score)),
            "originality_score": max(0, min(100, originality_score)),
            "compliance_score": max(0, min(100, compliance_score)),
            "checker_score": checker.total_score,
            "editorial_integrity_issues": self.unique(integrity.get("issues", [])),
            "unsupported_numeric_tokens": unsupported,
            "paragraph_count": len(paragraphs),
            "duplicate_paragraph_count": duplicate_count,
        }

    # 只有能被代码复核的指控才允许一票否决。审核模型是「挑刺专家」，
    # 任何稿子都能找出「可能存在……」，若让它直接决定 blocked，全自动发布
    # 就永远过不了（2026-09-15 实测：23 篇全部被模型指控拦下，自动发布 0 条）。
    # 能被代码复核、且适合作为阻断理由的指控类型。
    # 注意「绝对化」不在其中：它属于可自动修复项（见 auto_repair_service），
    # 修完就不成立，用它阻断只会白白废掉一篇稿子。
    _VERIFIABLE_BLOCKER_HINTS: tuple[str, ...] = (
        "内部标签",
        "内部素材",
        "流程标签",
        "占位符",
        "数字",
        "口径",
        "空",
        "过短",
        "字数",
    )
    # 正文低于此字数视为实质性缺失，可核实的「过短」指控成立。
    _MIN_BODY_CHARS = 400

    def _verify_model_blockers(
        self,
        model_blockers: list[str],
        local_snapshot: dict[str, Any],
        article: str,
        title: str,
    ) -> tuple[list[str], list[str]]:
        """把模型给的阻断项按「能否被代码证实」拆成两组。

        返回 (confirmed, advisory)：
        - confirmed：正文里确实存在该问题的证据，允许阻断发布
        - advisory：模型的主观疑虑（如「可能存在误导性因果」），降级为
          待改进建议，降分但不阻止发布
        """
        confirmed: list[str] = []
        advisory: list[str] = []
        unsupported = {str(x) for x in local_snapshot.get("unsupported_numeric_tokens", [])}
        has_internal_leak = bool(self._INTERNAL_LEAK_RE.search(article))
        body_chars = len(re.sub(r"\s+", "", article))

        for raw in model_blockers:
            item = str(raw).strip()
            if not item:
                continue
            verifiable = False
            if any(hint in item for hint in ("内部标签", "内部素材", "流程标签", "占位符")):
                verifiable = has_internal_leak
            elif any(hint in item for hint in ("数字", "口径")):
                verifiable = bool(unsupported)
            elif any(hint in item for hint in ("空", "过短", "字数")):
                verifiable = body_chars < self._MIN_BODY_CHARS
            if verifiable:
                confirmed.append(item)
            else:
                advisory.append(item)
        return confirmed, advisory

    def _apply_local_hard_gate(
        self,
        report: QualityReviewReport,
        local_snapshot: dict[str, Any],
        *,
        article: str = "",
        title: str = "",
    ) -> None:
        """Prevent a model verdict from overriding deterministic hard blockers.

        同时做反向收口：模型提出但我们复核不出证据的阻断项，降级为建议，
        不再一票否决——否则自动发布永远无法通过。
        """
        model_original = [str(item).strip() for item in report.hard_blockers if str(item).strip()]
        confirmed, advisory = self._verify_model_blockers(
            model_original, local_snapshot, article, title
        )
        if advisory:
            self.logger.info(
                "Quality review: %d model blocker(s) downgraded to advisory (no verifiable evidence): %s",
                len(advisory),
                " | ".join(advisory)[:300],
            )
            report.required_revisions = self.unique(
                [*advisory, *report.required_revisions]
            )[:8]
            report.fact_issues = self.unique([*advisory[:2], *report.fact_issues])[:8]
        report.hard_blockers = confirmed

        blockers = [
            str(item).strip()
            for item in local_snapshot.get("hard_blockers", [])
            if str(item).strip()
        ]
        if not blockers:
            # 本地无确定性硬伤，且模型阻断项已按证据筛选过，重新判定 verdict。
            if report.verdict == "blocked" and not confirmed:
                report.verdict = "needs_revision"
                report.needs_revision = True
            return

        report.hard_blockers = self.unique([*blockers, *report.hard_blockers])
        report.fact_issues = self.unique(
            [*local_snapshot.get("fact_issues", []), *report.fact_issues]
        )[:8]
        report.compliance_issues = self.unique(
            [*local_snapshot.get("compliance_issues", []), *report.compliance_issues]
        )[:8]
        report.required_revisions = self.unique(
            [*report.hard_blockers, *report.required_revisions]
        )[:8]
        report.compliance_score = min(report.compliance_score, 35)
        report.overall_score = min(report.overall_score, 59)
        report.verdict = "blocked"
        report.needs_revision = True
        conclusion = "本地硬门禁发现确定性的事实或写作完整性问题，模型通过意见不能覆盖该阻断。"
        if report.chief_editor_conclusion:
            report.chief_editor_conclusion = f"{conclusion}{report.chief_editor_conclusion}"
        else:
            report.chief_editor_conclusion = conclusion

    def _duplicate_paragraph_count(self, paragraphs: list[str]) -> int:
        normalized = [re.sub(r"\W", "", item).lower() for item in paragraphs]
        count = 0
        for index, current in enumerate(normalized):
            if len(current) < 30:
                continue
            for previous in normalized[:index]:
                if len(previous) < 30:
                    continue
                if current == previous or SequenceMatcher(None, current, previous).ratio() >= 0.88:
                    count += 1
                    break
        return count

    def _article(self, content: GeneratedContent) -> str:
        return str(content.polished_article or content.article or "").strip()

    def _editorial_brief(self, report: EditorialCouncilReport | None) -> str:
        if report is None:
            return "暂无写作前编辑委员会报告。"
        return self.json_text(
            {
                "editorial_thesis": report.editorial_thesis,
                "originality_angle": report.originality_angle,
                "evidence_chain": report.evidence_chain[:6],
                "causal_chain": report.causal_chain[:6],
                "avoid_claims": report.avoid_claims[:6],
                "risk_boundaries": report.risk_boundaries[:6],
            }
        )

    def _fallback_role_context(self, role_names: list[str]) -> str:
        return "\n\n".join(
            f"【{name}】请独立指出文章中可验证的问题和具体修改动作。"
            for name in role_names
        )
