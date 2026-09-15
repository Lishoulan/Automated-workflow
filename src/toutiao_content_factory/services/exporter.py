from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import WorkflowResult
from toutiao_content_factory.services.article_sanitizer import clean_article_text
from toutiao_content_factory.services.auto_repair_service import repair_article
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.services.monetization_service import MonetizationService


class ExportService:
    def __init__(self, settings: AppSettings, logger) -> None:
        self.settings = settings
        self.output_dir = settings.output_dir
        self.logger = logger
        self.growth_ops_service = GrowthOpsService(settings, logger)
        self.monetization_service = MonetizationService(settings, logger)

    def export(self, result: WorkflowResult) -> dict[str, Path]:
        article_path = self.output_dir / "article.md"
        editorial_path = self.output_dir / "editorial_packet.md"
        titles_path = self.output_dir / "titles.txt"
        score_path = self.output_dir / "content_score.json"
        workflow_path = self.output_dir / "workflow_result.json"
        quality_review_path = self.output_dir / "quality_review_council.json"
        benchmark_path = self.output_dir / "benchmark_strategy.json"

        benchmark_strategy = self._build_benchmark_strategy(result)
        article_path.write_text(self._build_publish_article(result), encoding="utf-8")
        editorial_path.write_text(self._build_editorial_packet(result, benchmark_strategy), encoding="utf-8")
        titles_path.write_text(self._build_titles_package(result), encoding="utf-8")
        score_path.write_text(
            json.dumps(self._build_score_package(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        benchmark_path.write_text(
            json.dumps(benchmark_strategy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        workflow_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        quality_review_path.write_text(
            json.dumps(
                asdict(result.quality_review_council)
                if result.quality_review_council is not None
                else {"enabled": False, "verdict": "not_run"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        exported = {
            "article": article_path,
            "editorial_packet": editorial_path,
            "titles": titles_path,
            "content_score": score_path,
            "quality_review_council": quality_review_path,
            "benchmark_strategy": benchmark_path,
            "workflow_result": workflow_path,
        }
        self.logger.info("Export completed: %s", exported)
        return exported

    def _build_publish_article(self, result: WorkflowResult) -> str:
        article = result.content.polished_article.strip() or result.content.article.strip()
        # Re-sanitize at the file boundary because review/polish/model output
        # can reintroduce inline metadata after the agent-level cleanup.
        article = clean_article_text(article, min_word_count=0)
        # 最后一道确定性清理：无论正文由哪条路径产出（首写 / 润色 / 模型返工
        # 重建），都不允许把内部标签、提示词元叙述或绝对化断言带进发布文件。
        # 这一步也让「兜底发布」看到的就是即将发出去的真实文本。
        article = str(repair_article(article)["article"])
        return self.monetization_service.append_cta(article, platform_key="default")

    def _build_editorial_packet(self, result: WorkflowResult, benchmark_strategy: dict) -> str:
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
        quality_report = result.quality_review_council
        quality_review_text = "- 未运行"
        if quality_report is not None:
            quality_review_text = (
                f"- 结论：{quality_report.verdict}\n"
                f"- 综合质量分：{quality_report.overall_score}\n"
                f"- 事实/逻辑/结构/原创/合规："
                f"{quality_report.fact_score}/{quality_report.logic_score}/"
                f"{quality_report.structure_score}/{quality_report.originality_score}/"
                f"{quality_report.compliance_score}\n"
                f"- 硬阻断：{'; '.join(quality_report.hard_blockers) or '无'}\n"
                + "\n".join(f"- 必须修改：{item}" for item in quality_report.required_revisions[:6])
            )
        article = (
            content.polished_article or content.article
        ).strip()
        # 文章已在 writer_agent._finalize_article 中清洗过，不再重复清洗

        return (
            "# 编辑包\n\n"
            f"- 风格：{result.style_label}\n"
            f"- 切入角度：{content.article_angle}\n"
            f"- 对标赛道：{benchmark_strategy.get('track', '') or '未命中'}\n"
            f"- 参考账号：{'、'.join(benchmark_strategy.get('benchmark_accounts', [])[:4]) or '当前赛道高质量分析号'}\n"
            f"- 自有高表现样本：{'；'.join(benchmark_strategy.get('self_benchmark_titles', [])[:2]) or '暂无'}\n"
            f"- 缓存状态：{result.cache_status}\n"
            f"- 缓存说明：{content.cache_note or '无'}\n\n"
            "## 对标写法重点\n\n"
            f"- 核心写法：{benchmark_strategy.get('voice_summary', '') or '无'}\n"
            f"- 读者价值：{benchmark_strategy.get('reader_value', '') or '无'}\n"
            f"- 内部复用线索：{'；'.join(benchmark_strategy.get('self_benchmark_patterns', [])[:4]) or '暂无'}\n"
            f"- 开头规则：{'；'.join(benchmark_strategy.get('opening_rules', [])[:3]) or '无'}\n"
            f"- 正文结构：{'；'.join(benchmark_strategy.get('structure_rules', [])[:4]) or '无'}\n"
            f"- 规避项：{'；'.join(benchmark_strategy.get('avoid_patterns', [])[:4]) or '无'}\n\n"
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
            "## 文章质量审核专家团\n\n"
            f"{quality_review_text}\n\n"
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

    def _build_score_package(self, result: WorkflowResult) -> dict[str, object]:
        score = result.content_score
        return {
            "generated_at": result.created_at,
            "style_key": result.style_key,
            "style_label": result.style_label,
            "cache_status": result.cache_status,
            "model_used": result.model_used,
            "estimated_cost": result.estimated_cost,
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

    def _build_benchmark_strategy(self, result: WorkflowResult) -> dict[str, object]:
        focus_track = str(result.data_report.focus_industry or "").strip()
        combined = " ".join(
            [
                result.data_report.topic_title,
                result.data_report.focus_industry,
                *result.data_report.focus_keywords[:6],
                *result.data_report.supply_chain_keywords[:6],
                result.content.article_angle,
            ]
        )
        track = focus_track or self.growth_ops_service.infer_title_playbook_track(combined)
        context = self.growth_ops_service.get_benchmark_strategy_context(track)
        return {
            "generated_at": result.created_at,
            "track": context.get("track", track),
            "benchmark_accounts": context.get("benchmark_accounts", []),
            "voice_summary": context.get("voice_summary", ""),
            "reader_value": context.get("reader_value", ""),
            "top_keywords": context.get("top_keywords", []),
            "opening_rules": context.get("opening_rules", []),
            "structure_rules": context.get("structure_rules", []),
            "angle_rules": context.get("angle_rules", []),
            "review_focus": context.get("review_focus", []),
            "avoid_patterns": context.get("avoid_patterns", []),
            "example_titles": context.get("example_titles", [])[:5],
            "self_benchmark_titles": context.get("self_benchmark_titles", [])[:5],
            "self_benchmark_patterns": context.get("self_benchmark_patterns", [])[:6],
            "self_benchmark_notes": context.get("self_benchmark_notes", [])[:5],
            "self_benchmark_hits": context.get("self_benchmark_hits", [])[:3],
            "self_benchmark_summary": context.get("self_benchmark_summary", {}),
        }
