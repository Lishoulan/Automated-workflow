from __future__ import annotations

import re

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.config import StyleProfile
from toutiao_content_factory.models import CacheContext, DataReport, GeneratedContent, TrendReport


class PolishAgent(BaseAgent):
    agent_name = "PolishAgent"

    def run(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        style: StyleProfile,
        cache_context: CacheContext,
        revision_focus: list[str] | None = None,
    ) -> dict[str, object]:
        prompt = self.render_prompt(
            "polish_prompt.md",
            platform=self.settings.platform,
            brand_name=self.settings.brand_name,
            audience=self.settings.audience,
            style_digest=self.style_digest(style),
            cache_note=self.cache_note(cache_context),
            revision_focus=self._format_revision_focus(revision_focus),
            trend_json=self.json_text(trend_report.__dict__),
            data_json=self.json_text(data_report.__dict__),
            article=content.article,
            summary=content.summary,
        )
        payload = self.request_json("你是 PolishAgent，只输出 JSON。", prompt)
        if payload:
            polished_article = self.strip_prompt_leakage(str(payload.get("polished_article", "")).strip())
            if polished_article:
                local_notes, local_score = self._validate_article(polished_article, data_report, trend_report)
                validation_notes = self.unique(
                    self._clean_list(payload.get("validation_notes"), 8) + local_notes
                )[:8]
                validation_score = max(self._bound_score(payload.get("validation_score", 0)), local_score)
                return {
                    "polished_article": polished_article,
                    "validation_notes": validation_notes,
                    "validation_score": validation_score,
                }
        return self._fallback(content, trend_report, data_report, revision_focus=revision_focus)

    def _fallback(
        self,
        content: GeneratedContent,
        trend_report: TrendReport,
        data_report: DataReport,
        revision_focus: list[str] | None = None,
    ) -> dict[str, object]:
        polished_article = self._polish_article(
            content.article,
            data_report,
            trend_report,
            revision_focus=revision_focus,
        )
        validation_notes, validation_score = self._validate_article(polished_article, data_report, trend_report)
        return {
            "polished_article": self.strip_prompt_leakage(polished_article),
            "validation_notes": validation_notes,
            "validation_score": validation_score,
        }

    def _polish_article(
        self,
        article: str,
        data_report: DataReport,
        trend_report: TrendReport,
        revision_focus: list[str] | None = None,
    ) -> str:
        blocks = [block.strip() for block in article.split("\n\n") if block.strip()]
        cleaned_blocks: list[str] = []
        for index, block in enumerate(blocks):
            block = re.sub(r"^#{1,6}\s*", "", block)
            block = re.sub(r"^\d+\.\s*", "", block)
            block = re.sub(r"^[-*]\s+", "", block)
            block = re.sub(r"\n{2,}", "\n", block)
            if self._is_instruction_block(block):
                continue
            if not block:
                continue
            if index > 0 and len(block) < 18:
                continue
            cleaned_blocks.append(block)

        polished_blocks: list[str] = []
        for block in cleaned_blocks:
            paragraph = re.sub(r"\s+", " ", block).strip()
            paragraph = paragraph.replace("**", "").replace("__", "")
            paragraph = paragraph.replace("### ", "").replace("## ", "").replace("# ", "")
            paragraph = self._soften_template_phrases(paragraph)
            if self._is_instruction_block(paragraph):
                continue
            polished_blocks.extend(self._split_long_paragraph(paragraph))

        polished_blocks = [item for item in polished_blocks if item]
        if revision_focus and self._should_emphasize_source(revision_focus) and not self._has_source_anchor("\n\n".join(polished_blocks)):
            anchor = self._build_source_anchor(data_report, trend_report)
            if anchor:
                polished_blocks.insert(1 if len(polished_blocks) > 1 else 0, anchor)

        return "\n\n".join(polished_blocks).strip()

    def _validate_article(
        self,
        article: str,
        data_report: DataReport,
        trend_report: TrendReport,
    ) -> tuple[list[str], int]:
        notes: list[str] = []
        hit_count = 0
        article_text = article.lower()
        paragraphs = [item.strip() for item in article.split("\n\n") if item.strip()]
        max_paragraph_length = max((len(item) for item in paragraphs), default=0)

        evidence_hits = self._count_hits(article_text, data_report.growth_numbers[:4])
        company_hits = self._count_hits(article_text, data_report.companies[:4])
        keyword_hits = self._count_hits(article_text, data_report.supply_chain_keywords[:5])

        if evidence_hits:
            hit_count += 1
            notes.append(f"正文已落入 {evidence_hits} 个核心数字或增速信号。")
        else:
            notes.append("正文缺少明确数字锚点，建议至少保留 1 到 2 个关键增长或规模数据。")

        if company_hits:
            hit_count += 1
            notes.append(f"正文已点名 {company_hits} 个公司或项目主体，主体感更清楚。")
        else:
            notes.append("正文主体感偏弱，建议点名公司、项目或订单方，避免写成泛行业评论。")

        if keyword_hits >= 2:
            hit_count += 1
            notes.append("正文已覆盖关键产业链环节，不只是复述新闻事件。")
        else:
            notes.append("正文对产业链位置的展开还不够，建议补充上游零部件或下游场景位置。")

        if trend_report.recommended_angle and trend_report.recommended_angle.lower() in article_text:
            hit_count += 1
            notes.append("核心切入角度已经在正文中落地。")
        else:
            notes.append("推荐切入角度没有被充分展开，后续可继续强化主线判断。")

        if self._has_source_anchor(article):
            hit_count += 1
            notes.append("正文带有公开材料或来源感表达，证据更稳定。")
        else:
            notes.append("建议补一句公开材料、公告或机构统计的出处感表达。")

        if max_paragraph_length and max_paragraph_length <= 160:
            hit_count += 1
            notes.append("段落长度适合移动端阅读，首屏压力较小。")
        else:
            notes.append("仍有较长段落，建议继续拆短，提升头条移动端完读率。")

        score = min(100, 38 + hit_count * 10)
        return self.unique(notes)[:8], score

    def _split_long_paragraph(self, paragraph: str) -> list[str]:
        paragraph = paragraph.strip()
        if not paragraph:
            return []
        paragraph = paragraph.replace("；", "。")
        sentences = [item.strip() for item in re.split(r"(?<=[。！？])", paragraph) if item.strip()]
        if len(paragraph) <= 150 and len(sentences) <= 3:
            return [paragraph]

        result: list[str] = []
        current = ""
        for sentence in sentences:
            next_chunk = f"{current}{sentence}"
            if current and len(next_chunk) > 120:
                result.append(current.strip())
                current = sentence
            else:
                current = next_chunk
        if current.strip():
            result.append(current.strip())
        return result or [paragraph]

    def _soften_template_phrases(self, paragraph: str) -> str:
        replacements = {
            "换句话说，": "",
            "不难发现，": "",
            "需要指出的是，": "",
            "从某种意义上说，": "",
            "真正值得看的是": "更该盯的是",
            "值得关注的是": "更关键的是",
        }
        result = paragraph
        for source, target in replacements.items():
            result = result.replace(source, target)
        return result.strip()

    def _build_source_anchor(self, data_report: DataReport, trend_report: TrendReport) -> str:
        for note in data_report.evidence_notes:
            text = note.strip().lstrip("- ").strip()
            if not text:
                continue
            if "：" in text:
                text = text.split("：", 1)[1].strip()
            if text:
                if text[-1] not in "。！？!?":
                    text += "。"
                return f"从公开材料看，{text}"
        if data_report.growth_numbers:
            return f"目前至少已经明确的数字信号包括 {'、'.join(data_report.growth_numbers[:2])}。"
        if trend_report.timing_judgment:
            return f"从当前公开节奏看，{trend_report.timing_judgment.rstrip('。')}。"
        return ""

    def _should_emphasize_source(self, revision_focus: list[str]) -> bool:
        joined = " ".join(revision_focus)
        return any(token in joined for token in ("出处", "证据", "来源", "公开材料"))

    def _format_revision_focus(self, revision_focus: list[str] | None) -> str:
        if not revision_focus:
            return "无额外修订重点，默认提升证据感、段落节奏和人味。"
        return "\n".join(f"- {item}" for item in revision_focus[:6])

    def _count_hits(self, article_text: str, candidates: list[str]) -> int:
        hits = 0
        for item in candidates:
            token = str(item).strip().lower()
            if token and token in article_text:
                hits += 1
        return hits

    def _has_source_anchor(self, article: str) -> bool:
        markers = ("据", "数据显示", "公开材料", "财报", "公告", "机构统计", "报告")
        return any(marker in article for marker in markers)

    def _is_instruction_block(self, text: str) -> bool:
        lowered = text.lower()
        markers = (
            "请围绕这条产业线索",
            "请重点分析",
            "自动生成输入",
            "匹配赛道",
            "匹配关键词",
            "可确认数字",
            "标题：",
            "来源：",
            "发布日期：",
            "原文链接：",
            "摘要：",
            "补充片段：",
        )
        return any(marker.lower() in lowered for marker in markers)

    def _clean_list(self, payload: object, limit: int) -> list[str]:
        if not isinstance(payload, list):
            return []
        return self.unique([str(item).strip() for item in payload if str(item).strip()])[:limit]

    def _bound_score(self, value: object) -> int:
        try:
            score = int(value)
        except (TypeError, ValueError):
            score = 0
        return max(0, min(100, score))
