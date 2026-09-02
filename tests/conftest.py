from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from toutiao_content_factory.config import (
    AppSettings,
    AutomationSettings,
    ImageGenerationSettings,
    PublishSettings,
    ReviewSettings,
    StyleProfile,
    TopicCollectionSettings,
    WechatSettings,
)
from toutiao_content_factory.models import ExtractionResult, SourceDocument
from toutiao_content_factory.services.openai_service import OpenAIContentService


# ---------------------------------------------------------------------------
# Minimal AppSettings fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_settings(tmp_path: Path) -> AppSettings:
    """Create a minimal AppSettings with temp directories."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "prompts").mkdir()
    (project_root / "config").mkdir()
    (project_root / "config" / "bad_words.txt").write_text("# test\n", encoding="utf-8")

    return AppSettings(
        project_root=project_root,
        config_path=project_root / "config.yaml",
        account_key="default",
        account_slug="default",
        account_label="Default",
        is_default_account=True,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        prompts_dir=project_root / "prompts",
        logs_dir=tmp_path / "logs",
        cache_dir=tmp_path / "cache",
        openai_api_key=None,
        openai_base_url=None,
        openai_model="gpt-4.1-mini",
        temperature=0.7,
        max_input_chars=16000,
        article_word_target=1200,
        brand_name="测试品牌",
        industry_focus="新能源、机器人",
        brand_positioning="测试科技产业分析",
        audience="测试读者",
        platform="今日头条",
        logging_level="WARNING",
        default_style="deep_industry_analysis",
        title_count=20,
        recommended_title_count=3,
        cache_similarity_threshold=0.82,
        style_profiles={
            "deep_industry_analysis": StyleProfile(
                key="deep_industry_analysis",
                label="深度行业分析风",
                tone="冷静、专业",
                article_instruction="突出产业链",
                title_instruction="做出判断感",
                image_instruction="深色科技风",
                opening_instruction="开头先抛出反直觉判断",
            ),
        },
        topic_collection=TopicCollectionSettings(
            lookback_days=2,
            max_topic_age_hours=24,
            drop_topics_without_timestamp=True,
            max_ranked_items_per_source=4,
            max_items_per_source=12,
            max_export_topics=20,
            request_timeout_seconds=18,
            enabled_sources=[],
            focus_industries=[],
            keyword_map={},
            source_configs={},
        ),
        image_generation=ImageGenerationSettings(
            width=1280,
            height=720,
            mobile_width=1080,
            mobile_height=1920,
            backend="renderer",
            backend_priority="image2,local",
            api_mode="images",
            model_name="gpt-image-1",
            model_base_url="",
            model_api_key="",
            request_timeout_seconds=15.0,
            cover_size="1536x1024",
            infographic_size="1024x1536",
            max_generated_assets=2,
            quality="high",
            style="vivid",
            background="opaque",
            fallback_to_renderer=True,
            theme_name="dark_tech",
            primary_color="#0B1220",
            secondary_color="#122238",
            accent_color="#26D9FF",
            background_color="#060B14",
            card_color="#0F1B2D",
            text_color="#F5F8FF",
            muted_text_color="#91A4C2",
            font_path="",
            bold_font_path="",
            export_html=True,
            export_svg=True,
            export_png=True,
        ),
        automation=AutomationSettings(
            default_topic_rank=1,
            max_daily_topic_attempts=3,
            daily_run_times=[],
            archive_outputs=True,
            archive_dirname="runs",
            selected_topic_basename="selected_topic",
        ),
        review=ReviewSettings(
            auto_prepare=True,
            min_overall_score=72,
            max_ai_risk=45,
            min_auto_publish_overall_score=80,
            max_auto_publish_ai_risk=40,
            min_auto_publish_validation_score=80,
            min_auto_publish_readiness_score=70,
            publish_dirname="publish",
        ),
        publishing=PublishSettings(
            enabled=False,
            auto_publish=False,
            draft_only=False,
            platform="toutiao",
            base_url="https://mp.toutiao.com/",
            publish_url="",
            user_data_dir=".runtime/toutiao_profile",
            channel="msedge",
            executable_path="",
            headless=False,
            wait_timeout_ms=15000,
            history_filename="publish_history.json",
            allow_duplicate_topics=False,
            title_max_length=30,
            cover_mode="auto",
            selectors={},
        ),
        wechat=WechatSettings(
            enabled=False,
            auto_publish=False,
            toutiao_sync_delay_seconds=1800,
            app_id="",
            app_secret="",
            account_name="",
            author_name="",
            base_url="https://mp.weixin.qq.com/",
            api_base_url="https://api.weixin.qq.com",
            api_proxy="",
            title_max_length=64,
            digest_max_length=120,
            history_filename="wechat_publish_history.json",
            cover_strategy="text_cover",
            layout_backend="builtin",
            md2wechat_enabled=False,
            md2wechat_api_key="",
            md2wechat_base_url="https://www.md2wechat.com/api/v1",
            md2wechat_theme="default",
            md2wechat_font_size="medium",
            md2wechat_convert_version="v1",
            md2wechat_cli_path="",
            md2wechat_fallback_to_builtin=True,
            html_anything_enabled=False,
            html_anything_url="http://localhost:3000",
            html_anything_template="",
            html_anything_agent="claude",
            serv00_proxy_enabled=False,
            serv00_proxy_url="",
            serv00_proxy_secret="",
            editor_135_enabled=False,
            editor_135_base_url="https://www.135editor.com",
            editor_135_profile_dir="editor_135_profile",
            builtin_theme="tech_blue",
        ),
        account_profiles={},
        raw_account_profiles={},
        available_account_keys=["default"],
    )


# ---------------------------------------------------------------------------
# Mock OpenAI service
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_openai_service() -> MagicMock:
    """Return a mock OpenAIContentService that returns canned responses."""
    service = MagicMock(spec=OpenAIContentService)
    service.available = False
    service.estimated_cost = 0.0
    service.create_json_completion.return_value = {"result": "mocked"}
    return service


# ---------------------------------------------------------------------------
# Temp output directory
# ---------------------------------------------------------------------------


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    d = tmp_path / "output"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Sample SourceDocument
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_source_document() -> SourceDocument:
    return SourceDocument(
        source_type="rss",
        content="宁德时代发布2024年财报，储能业务同比增长67%。比亚迪新能源销量突破300万辆。"
        "人形机器人赛道加速，优必选科技完成B轮融资。"
        "储能装机量达50GWh，同比增长40%。",
        title="新能源与机器人行业周报",
        metadata={
            "topic_title": "新能源与机器人行业动态",
            "primary_industry": "新能源",
            "primary_keywords": "储能、新能源、人形机器人",
        },
    )


# ---------------------------------------------------------------------------
# Sample ExtractionResult
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_extraction_result() -> ExtractionResult:
    return ExtractionResult(
        merged_text="宁德时代储能业务增长67%，比亚迪销量300万辆。",
        core_data=["储能业务同比增长67%", "销量突破300万辆"],
        industry_trends=["储能装机量持续增长"],
        growth_numbers=["67%", "300万辆", "50GWh", "40%"],
        companies=["宁德时代", "比亚迪", "优必选科技"],
        supply_chain_keywords=["储能", "新能源", "人形机器人"],
        key_points=["储能业务高增长", "新能源销量突破"],
        focus_industry="新能源",
        focus_keywords=["储能", "新能源"],
        focus_companies=["宁德时代", "比亚迪"],
        topic_title="新能源与机器人行业动态",
    )


# ---------------------------------------------------------------------------
# Logger fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def logger() -> logging.Logger:
    return logging.getLogger("test")
