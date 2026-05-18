from __future__ import annotations

import logging

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.loaders.document_loader import DocumentLoader
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.cache_service import CacheService
from toutiao_content_factory.services.exporter import ExportService
from toutiao_content_factory.services.extractor import SignalExtractor
from toutiao_content_factory.services.image_service import ImageGenerationService
from toutiao_content_factory.services.openai_service import OpenAIContentService
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
        self.extractor = SignalExtractor()
        self.prompt_repository = PromptRepository(settings.prompts_dir)
        self.openai_service = OpenAIContentService(settings, logger, force_mock=force_mock)
        self.workflow = AgentWorkflowService(settings, self.prompt_repository, self.openai_service, logger)
        self.cache_service = CacheService(settings.cache_dir, settings.cache_similarity_threshold, logger)
        self.image_service = ImageGenerationService(settings, logger)
        self.exporter = ExportService(settings.output_dir, logger)

    def run(self, paste_text: str | None = None, include_input_dir: bool = True) -> dict[str, str]:
        if include_input_dir:
            self.logger.info("开始读取输入目录: %s", self.settings.input_dir)
        else:
            self.logger.info("本次运行跳过 input 目录，仅处理即时输入文本。")
        documents = self.loader.load(paste_text=paste_text, include_input_dir=include_input_dir)
        self.logger.info("已加载 %s 份输入材料。", len(documents))

        extraction = self.extractor.extract(documents)
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
            refresh_cache=self.refresh_cache,
        )
        self.logger.info("缓存状态: %s", cache_context.status)

        if cache_context.exact_hit and cache_context.cached_result is not None:
            result = cache_context.cached_result
            result.cache_status = "exact_hit"
            result.content.cache_note = cache_context.note
        else:
            result = self.workflow.run(
                extraction,
                style.key,
                cache_context,
                fast_mode=self.fast_mode or self.settings.fast_mode,
                skip_review_agent=self.settings.skip_review_agent or self.fast_mode or self.settings.fast_mode,
                skip_publish_review=self.settings.skip_publish_review or self.fast_mode or self.settings.fast_mode,
                skip_image_prompt_agent=self.fast_mode or self.settings.fast_mode,
            )
            self.cache_service.save(result, extraction.merged_text)

        image_artifacts = None
        if not (self.settings.skip_image_generation or self.fast_mode or self.settings.fast_mode):
            image_artifacts = self.image_service.generate(result)
        exported = self.exporter.export(result, image_artifacts=image_artifacts)
        return {name: str(path) for name, path in exported.items()}
