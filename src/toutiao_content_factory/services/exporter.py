from __future__ import annotations

import json
from pathlib import Path

from toutiao_content_factory.models import ImageArtifacts, WorkflowResult
from toutiao_content_factory.services.article_sanitizer import clean_article_text


class ExportService:
    def __init__(self, output_dir: Path, logger) -> None:
        self.output_dir = output_dir
        self.logger = logger

    def export(self, result: WorkflowResult, image_artifacts: ImageArtifacts | None = None) -> dict[str, Path]:
        article_path = self.output_dir / "article.md"
        editorial_path = self.output_dir / "editorial_packet.md"
        titles_path = self.output_dir / "titles.txt"
        cover_prompt_path = self.output_dir / "cover_prompt.txt"
        infographic_path = self.output_dir / "infographic_prompts.txt"
        score_path = self.output_dir / "content_score.json"
        workflow_path = self.output_dir / "workflow_result.json"

        article_path.write_text(self._build_publish_article(result), encoding="utf-8")
        editorial_path.write_text(self._build_editorial_packet(result), encoding="utf-8")
        titles_path.write_text(self._build_titles_package(result), encoding="utf-8")
        cover_prompt_path.write_text(result.content.cover_prompt.strip() + "\n", encoding="utf-8")
        infographic_path.write_text(self._build_infographic_package(result), encoding="utf-8")
        score_path.write_text(
            json.dumps(self._build_score_package(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        workflow_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        exported = {
            "article": article_path,
            "editorial_packet": editorial_path,
            "titles": titles_path,
            "cover_prompt": cover_prompt_path,
            "infographic_prompts": infographic_path,
            "content_score": score_path,
            "workflow_result": workflow_path,
        }
        if image_artifacts is not None:
            exported["cover_png"] = image_artifacts.cover_png
            for index, path in enumerate(image_artifacts.infographic_pngs, start=1):
                exported[f"infographic_{index}_png"] = path
            if image_artifacts.cover_svg is not None:
                exported["cover_svg"] = image_artifacts.cover_svg
            for index, path in enumerate(image_artifacts.infographic_svgs, start=1):
                exported[f"infographic_{index}_svg"] = path
            for index, path in enumerate(image_artifacts.html_files, start=1):
                exported[f"layout_{index}_html"] = path
        self.logger.info("Export completed: %s", exported)
        return exported

    def _build_publish_article(self, result: WorkflowResult) -> str:
        article = result.content.polished_article.strip() or result.content.article.strip()
        title_candidates = result.content.recommended_titles + result.content.titles
        clean_article = clean_article_text(article, title_candidates=title_candidates)
        return (clean_article or article) + "\n"

    def _build_editorial_packet(self, result: WorkflowResult) -> str:
        content = result.content
        score = result.content_score
        recommended_titles = "\n".join(
            f"{index}. {title}" for index, title in enumerate(content.recommended_titles, start=1)
        ) or "1. 暂无推荐标题"
        optimized_opening = "\n".join(
            f"{index}. {line}" for index, line in enumerate(content.optimized_opening_lines, start=1)
        ) or "1. 暂无开头优化建议"
        tags = " ".join(content.tags) or "无"
        topics = "\n".join(f"- {item}" for item in content.recommended_topics) or "- 无"
        review_notes = "\n".join(f"- {item}" for item in content.review_notes) or "- 无"
        validation_notes = "\n".join(f"- {item}" for item in content.validation_notes) or "- 无"
        publish_risks = "\n".join(f"- {item}" for item in score.publish_risk_flags) or "- 无"
        publish_notes = "\n".join(f"- {item}" for item in score.publish_notes) or "- 无"
        article = clean_article_text(
            (content.polished_article or content.article).strip(),
            title_candidates=content.recommended_titles + content.titles,
        )

        return (
            "# 编辑包\n\n"
            f"- 风格：{result.style_label}\n"
            f"- 切入角度：{content.article_angle}\n"
            f"- 缓存状态：{result.cache_status}\n"
            f"- 缓存说明：{content.cache_note or '无'}\n\n"
            "## 推荐标题\n\n"
            f"{recommended_titles}\n\n"
            "## 开头前三句优化\n\n"
            f"{optimized_opening}\n\n"
            "## 摘要\n\n"
            f"{content.summary.strip()}\n\n"
            "## 头条标签\n\n"
            f"{tags}\n\n"
            "## 推荐话题\n\n"
            f"{topics}\n\n"
            "## 正文成稿\n\n"
            f"{article}\n\n"
            "## 评论区互动\n\n"
            f"{content.comment_prompt.strip()}\n\n"
            "## 正文校验\n\n"
            f"- 校验分：{content.validation_score}\n"
            f"{validation_notes}\n\n"
            "## 评分摘要\n\n"
            f"- 综合得分：{score.overall_score}\n"
            f"- 标题吸引力：{score.title_attraction}\n"
            f"- 信息密度：{score.information_density}\n"
            f"- 收藏潜力：{score.save_potential}\n"
            f"- 完读率潜力：{score.completion_rate_potential}\n"
            f"- AI 痕迹风险：{score.ai_generated_risk}\n"
            f"- 内容审核结论：{score.pass_status}\n\n"
            "## 发布审核摘要\n\n"
            f"- 发布就绪分：{score.publish_readiness_score}\n"
            f"- 发布建议：{score.publish_recommendation}\n\n"
            "### 发布风险\n\n"
            f"{publish_risks}\n\n"
            "### 发布备注\n\n"
            f"{publish_notes}\n\n"
            "## 发布前检查\n\n"
            f"{review_notes}\n"
        )

    def _build_titles_package(self, result: WorkflowResult) -> str:
        content = result.content
        all_titles = "\n".join(f"{index}. {title}" for index, title in enumerate(content.titles, start=1))
        recommended = "\n".join(
            f"{index}. {title}" for index, title in enumerate(content.recommended_titles, start=1)
        )
        return f"全部标题：\n{all_titles}\n\n推荐标题：\n{recommended}\n"

    def _build_infographic_package(self, result: WorkflowResult) -> str:
        lines = [f"{index}. {item}" for index, item in enumerate(result.content.infographic_prompts, start=1)]
        if result.data_report.growth_numbers:
            lines.append("")
            lines.append("可用数据抓手：")
            lines.extend(f"- {item}" for item in result.data_report.growth_numbers[:5])
        return "\n".join(lines) + "\n"

    def _build_score_package(self, result: WorkflowResult) -> dict[str, object]:
        score = result.content_score
        return {
            "generated_at": result.created_at,
            "style_key": result.style_key,
            "style_label": result.style_label,
            "cache_status": result.cache_status,
            "model_used": result.model_used,
            "score_scale_note": "除 ai_generated_risk 外，其余分数越高越好；ai_generated_risk 越低越好。",
            "scores": {
                "overall_score": score.overall_score,
                "title_attraction": score.title_attraction,
                "information_density": score.information_density,
                "save_potential": score.save_potential,
                "completion_rate_potential": score.completion_rate_potential,
                "ai_generated_risk": score.ai_generated_risk,
                "publish_readiness_score": score.publish_readiness_score,
                "validation_score": result.content.validation_score,
            },
            "reasoning": score.reasoning,
            "revision_suggestions": score.revision_suggestions,
            "pass_status": score.pass_status,
            "validation_notes": result.content.validation_notes,
            "publish_risk_flags": score.publish_risk_flags,
            "publish_recommendation": score.publish_recommendation,
            "publish_notes": score.publish_notes,
        }
