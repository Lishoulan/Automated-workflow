from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.loaders.document_loader import DocumentLoader
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.cache_service import CacheService
from toutiao_content_factory.services.compliance_service import check_daily_post_limit
from toutiao_content_factory.services.exporter import ExportService
from toutiao_content_factory.services.extractor import SignalExtractor
from toutiao_content_factory.services.notification_service import NotificationService
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.pipeline_state_manager import PipelineStateManager
from toutiao_content_factory.services.prompt_repository import PromptRepository


class ContentFactoryPipeline:
    def __init__(
        self,
        settings: AppSettings,
        logger: logging.Logger,
        force_mock: bool = False,
        style_override: str | None = None,
        refresh_cache: bool = False,
        fast_mode: bool = False,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self.style_override = style_override
        self.refresh_cache = refresh_cache
        self.fast_mode = fast_mode
        self.loader = DocumentLoader(settings.input_dir, settings.max_input_chars)
        self.extractor = SignalExtractor(include_source_provenance=settings.wechat_auto_publish_mode)
        self.prompt_repository = PromptRepository(settings.prompts_dir)
        self.openai_service = OpenAIContentService(settings, logger, force_mock=force_mock)
        self.workflow = AgentWorkflowService(settings, self.prompt_repository, self.openai_service, logger)
        self.cache_service = CacheService(settings.cache_dir, settings.cache_similarity_threshold, logger)
        self.exporter = ExportService(settings, logger)
        self.notification_service = NotificationService(
            asdict(settings),
            fallback_log_dir=str(settings.output_dir / "automation"),
        )
        self.state_manager = PipelineStateManager(
            settings.output_dir,
            notification_service=self.notification_service,
        )

    def run(self, paste_text: str | None = None, include_input_dir: bool = True, *, source_records: list[dict] | None = None) -> dict[str, str]:
        if include_input_dir:
            self.logger.info("开始读取输入目录: %s", self.settings.input_dir)
        else:
            self.logger.info("本次运行跳过 input 目录，仅处理即时输入文本。")
        documents = self.loader.load(paste_text=paste_text, include_input_dir=include_input_dir)
        self.logger.info("已加载 %s 份输入材料。", len(documents))

        extraction = self.extractor.extract(documents)
        if self.settings.wechat_auto_publish_mode:
            from toutiao_content_factory.services.wechat_evidence import build_facts, classify_kind, requirements
            extraction.source_records = list(source_records or [])
            extraction.fact_evidence = build_facts(extraction.source_records)
            extraction.article_kind = classify_kind(extraction.topic_type, extraction.fact_evidence)
            brief = requirements(extraction.article_kind)
            for field in ("suggested_angle", "follow_up_focus", "writing_brief"):
                setattr(extraction, field, brief[field])
        self.logger.info(
            "提取完成：核心数据 %s 条，趋势 %s 条，公司 %s 个。",
            len(extraction.core_data),
            len(extraction.industry_trends),
            len(extraction.companies),
        )

        style = self.settings.get_style(self.style_override)
        cache_context = self.cache_service.inspect(
            extraction.merged_text,
            style.key,
            # Retry held WeChat articles under the current generation and audit
            # policy; a cached rejection cannot substitute for a new attempt.
            refresh_cache=self.refresh_cache or self.settings.wechat_auto_publish_mode,
        )
        self.logger.info("缓存状态: %s", cache_context.status)

        if cache_context.exact_hit and cache_context.cached_result is not None:
            result = cache_context.cached_result
            result.cache_status = "exact_hit"
            result.content.cache_note = cache_context.note
        else:
            is_fast_mode = self.fast_mode or self.settings.fast_mode
            result = self.workflow.run(
                extraction,
                style.key,
                cache_context,
                fast_mode=is_fast_mode,
                skip_review_agent=self.settings.skip_review_agent or is_fast_mode,
                skip_publish_review=self.settings.skip_publish_review or is_fast_mode,
            )
            self.cache_service.save(result, extraction.merged_text)

        exported = self.exporter.export(result)
        return {name: str(path) for name, path in exported.items()}

    def save_as_draft(self, run_dir: Path) -> dict[str, Any]:
        run_dir = Path(run_dir)
        draft_info: dict[str, Any] = {
            "run_dir": str(run_dir),
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "title": "",
            "summary": "",
            "status": "draft",
        }
        title_path = run_dir / self.settings.review.publish_dirname / "title.txt"
        if title_path.exists():
            try:
                draft_info["title"] = title_path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                pass
        article_path = run_dir / "article.md"
        if article_path.exists():
            try:
                text = article_path.read_text(encoding="utf-8", errors="replace").strip()
                draft_info["summary"] = text[:200] if text else ""
            except Exception:
                pass
        score_path = run_dir / "content_score.json"
        if score_path.exists():
            try:
                score = json.loads(score_path.read_text(encoding="utf-8"))
                scores = score.get("scores", {})
                draft_info["overall_score"] = scores.get("overall_score", 0)
                draft_info["ai_risk_score"] = scores.get("ai_generated_risk", 0)
            except Exception:
                pass
        draft_path = run_dir / "draft_status.json"
        draft_path.write_text(
            json.dumps(draft_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_name = run_dir.name
        self.state_manager.save_state(run_name, PipelineStateManager.STATE_DRAFTED)
        self.logger.info("草稿已保存: %s", run_dir)
        return draft_info

    def publish_draft(self, run_dir: Path) -> dict[str, Any]:
        run_dir = Path(run_dir)
        draft_path = run_dir / "draft_status.json"
        if not draft_path.exists():
            return {"status": "error", "message": f"草稿不存在: {run_dir}"}
        try:
            draft_info = json.loads(draft_path.read_text(encoding="utf-8"))
        except Exception:
            return {"status": "error", "message": f"草稿文件读取失败: {run_dir}"}
        history_path = run_dir.parent / self.settings.publishing.history_filename
        if not check_daily_post_limit(history_path, platform="toutiao"):
            return {"status": "blocked", "message": "合规限制：今日发布数量已达上限，请明日再发布。"}
        run_name = run_dir.name
        self.state_manager.save_state(run_name, PipelineStateManager.STATE_PUBLISHING)
        article_title = draft_info.get("title", "")
        self.state_manager.save_state(run_name, PipelineStateManager.STATE_PUBLISHED)
        draft_info["status"] = "published"
        draft_info["published_at"] = datetime.now().isoformat(timespec="seconds")
        draft_path.write_text(
            json.dumps(draft_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.notification_service.notify_success(
            platform="今日头条",
            article_title=article_title,
            run_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        self.logger.info("草稿已发布: %s", run_dir)
        return draft_info

    def publish_with_human_approval(
        self,
        paste_text: str | None = None,
        include_input_dir: bool = True,
    ) -> dict[str, Any]:
        auto_publish = bool(self.settings.publishing.auto_publish) or bool(
            os.getenv("TOUTIAO_AUTO_PUBLISH", "").strip() == "1"
        )
        exported = self.run(paste_text=paste_text, include_input_dir=include_input_dir)
        output_dir = self.settings.output_dir
        draft_info = self.save_as_draft(output_dir)
        if auto_publish:
            self.notification_service._broadcast(
                "【今日头条】草稿待确认发布",
                f"《{draft_info.get('title', '未知标题')}》已生成草稿，请前往后台确认发布。",
            )
            self.logger.info("自动发布模式：草稿已保存，等待人工确认发布")
        else:
            self.logger.info("草稿已保存，等待人工确认发布")
        return {
            "exported": exported,
            "draft": draft_info,
            "requires_approval": True,
        }

    def close(self) -> None:
        self.openai_service.close()
