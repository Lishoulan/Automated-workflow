from __future__ import annotations

import re
from difflib import SequenceMatcher
import logging
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
    _INTERNAL_LEAK_RE = re.compile(
        r"manual_paste|evergreen_pool|benchmark_hotspot|source_document|source_material|"
        r"editorial_council|quality_review|内部标签|素材标签",
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
        self.quality_checker = ArticleQualityChecker()

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
        editorial_brief = self._editorial_brief(editorial_council)

        prompt = self.render_prompt(
            "quality_review_council_prompt.md",
            platform=self.settings.platform,
            topic_title=data_report.topic_title,
            role_context=role_context,
            style_digest=self.style_digest(style),
            article=article,
            title=title,
            data_json=self.json_text(data_report.__dict__),
            editorial_brief=editorial_brief,
            local_snapshot=self.json_text(local_snapshot),
            cache_note=self.cache_note(cache_context),
        )
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
                self._apply_local_hard_gate(report, local_snapshot)
                self.logger.info(
                    "Quality review council completed: verdict=%s score=%d blockers=%d",
                    report.verdict,
                    report.overall_score,
                    len(report.hard_blockers),
                )
                return report
            self.logger.warning("Quality review council returned an unusable report; using local audit.")

        return self._fallback(
            content,
            data_report,
            revision_round=revision_round,
            role_names=role_names,
        )

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
            [
                "保留具体主体、时间点和数据来源表达，避免把推断写成事实。",
                "让相邻段落之间有明确的因果、转折或递进关系。",
                "结尾保留一个可继续观察的变量，避免口号式拔高。",
            ]
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

        unsupported = self._unsupported_numeric_tokens(article, data_report)
        internal_leaks = self._INTERNAL_LEAK_RE.findall(article)
        hard_blockers: list[str] = []
        if not clean_article:
            hard_blockers.append("正文为空，不能进入发布流程。")
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
        if len(paragraphs) >= 4 and not any(token in article for token in ("但", "然而", "风险", "前提", "如果")):
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

        compliance_issues = list(checker_issues)
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
            "unsupported_numeric_tokens": unsupported,
            "paragraph_count": len(paragraphs),
            "duplicate_paragraph_count": duplicate_count,
        }

    def _apply_local_hard_gate(
        self,
        report: QualityReviewReport,
        local_snapshot: dict[str, Any],
    ) -> None:
        """Prevent a model verdict from overriding deterministic hard blockers."""
        blockers = [
            str(item).strip()
            for item in local_snapshot.get("hard_blockers", [])
            if str(item).strip()
        ]
        if not blockers:
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
        conclusion = "本地硬门禁发现未核实事实或内部标签泄露，模型通过意见不能覆盖该阻断。"
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
