from __future__ import annotations

"""配置加载模块 — 优先级与覆盖规则
========================================

配置值的解析优先级（高 → 低）：

1. **账号级环境变量** — 格式 ``{BASE_NAME}__{ACCOUNT_SLUG}``（如 ``BRAND_NAME__POLITICS_NOTES``），
   仅对对应账号生效，优先级最高。
2. **全局环境变量** — 标准格式（如 ``OPENAI_API_KEY``、``TOUTIAO_AUTO_PUBLISH``），
   对所有账号生效，但可被账号级变量覆盖。
3. **账号覆盖（config.yaml → accounts → {key}）** — 账号配置中除 meta 字段
   （label / input_dir / output_dir / logs_dir / cache_dir / browser_profile_dir / enabled）
   外的字段会深度合并到全局配置之上。
4. **全局 config.yaml** — 项目根目录的 config.yaml 提供所有默认值。
5. **代码内硬编码默认值** — ``_default_config_payload()`` 等函数提供的兜底值。

合并与覆盖规则
--------------
- ``.env`` 文件通过 ``python-dotenv`` 在启动时加载到 ``os.environ``，作为环境变量的来源之一。
- ``config.yaml`` 中的值作为"基础层"，环境变量作为"覆盖层"。
- 账号级配置通过 ``_deep_merge()`` 深度合并到全局配置之上，非 meta 字段逐层覆盖。
- 环境变量（包括 .env 注入的）始终优先于 config.yaml 中的同名配置。
- 对于布尔标志，使用 ``_env_flag()`` / ``_env_flag_scoped()`` 解析，支持 ``1/true/yes/on``。

简而言之：环境变量 > config.yaml；账号级 > 全局级。
"""

import copy
import json
import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_EXPERT_COUNCIL_ROLES = [
    "evidence-auditor",
    "industry-strategist",
    "reader-editor",
    "devils-advocate",
    "originality-editor",
]
DEFAULT_QUALITY_REVIEW_COUNCIL_ROLES = [
    "quality-fact-checker",
    "quality-logic-auditor",
    "quality-structure-editor",
    "quality-originality-auditor",
    "quality-compliance-editor",
]
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def resolve_default_font_path(variant: str = "regular") -> str:
    """根据当前操作系统返回默认中文字体路径。

    Args:
        variant: "regular" 或 "bold"，对应常规/粗体字体。
    """
    system = platform.system()
    if system == "Windows":
        if variant == "bold":
            return "C:/Windows/Fonts/msyhbd.ttc"
        return "C:/Windows/Fonts/msyh.ttc"
    elif system == "Darwin":
        if variant == "bold":
            return "/System/Library/Fonts/STHeiti Medium.ttc"
        return "/System/Library/Fonts/STHeiti Light.ttc"
    else:  # Linux 及其他
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        ]
        bold_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        ]
        search_list = bold_candidates if variant == "bold" else candidates
        for path in search_list:
            if Path(path).exists():
                return path
        return search_list[0]

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - optional dependency fallback
    yaml = None


DEFAULT_ACCOUNT_KEY = "default"
ACCOUNT_META_KEYS = {"label", "input_dir", "output_dir", "logs_dir", "cache_dir", "browser_profile_dir", "enabled"}
ACCOUNT_SLUG_PATTERN = re.compile(r"[^a-zA-Z0-9]+")


def normalize_account_key(raw_value: str | None) -> str:
    value = (raw_value or DEFAULT_ACCOUNT_KEY).strip()
    return value or DEFAULT_ACCOUNT_KEY


def sanitize_account_slug(raw_value: str | None) -> str:
    normalized = normalize_account_key(raw_value)
    sanitized = ACCOUNT_SLUG_PATTERN.sub("-", normalized).strip("-").lower()
    return sanitized or DEFAULT_ACCOUNT_KEY


def account_env_name(base_name: str, account_key: str | None) -> str:
    slug = sanitize_account_slug(account_key).replace("-", "_").upper()
    return f"{base_name}__{slug}"


def _env_value_scoped(name: str, account_key: str | None, default: str) -> str:
    scoped_value = os.getenv(account_env_name(name, account_key))
    if scoped_value is not None:
        return scoped_value
    return default


class TopicCollectionSettings(BaseModel):
    """话题采集配置模型，带数据校验。"""
    model_config = ConfigDict(frozen=True)

    lookback_days: int
    max_topic_age_hours: int
    drop_topics_without_timestamp: bool
    max_ranked_items_per_source: int
    max_items_per_source: int
    max_export_topics: int
    request_timeout_seconds: int
    # TODO: sports_notes 账号的 enabled_sources 当前为空，需要添加体育新闻源
    #   （如懂球帝、虎扑、腾讯体育 RSS 等），目前仅靠 keyword_map 过滤。
    enabled_sources: list[str]
    focus_industries: list[str]
    keyword_map: dict[str, list[str]]
    source_configs: dict[str, dict[str, Any]]

    @field_validator("enabled_sources", mode="before")
    @classmethod
    def enabled_sources_must_be_list(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            raise ValueError(f"enabled_sources must be a list, got {type(v).__name__}: {v!r}")
        return [str(item) for item in v]

    @field_validator("focus_industries", mode="before")
    @classmethod
    def focus_industries_must_be_list(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            raise ValueError(f"focus_industries must be a list, got {type(v).__name__}: {v!r}")
        return [str(item) for item in v]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TopicCollectionSettings":
        return cls(
            lookback_days=int(payload.get("lookback_days", 2)),
            max_topic_age_hours=int(payload.get("max_topic_age_hours", 24)),
            drop_topics_without_timestamp=bool(payload.get("drop_topics_without_timestamp", True)),
            max_ranked_items_per_source=int(payload.get("max_ranked_items_per_source", 4)),
            max_items_per_source=int(payload.get("max_items_per_source", 24)),
            max_export_topics=int(payload.get("max_export_topics", 40)),
            request_timeout_seconds=int(payload.get("request_timeout_seconds", 18)),
            enabled_sources=[str(item) for item in payload.get("enabled_sources", [])],
            focus_industries=[str(item) for item in payload.get("focus_industries", [])],
            keyword_map={
                str(key): [str(item) for item in value]
                for key, value in dict(payload.get("keyword_map", {})).items()
            },
            source_configs={
                str(key): dict(value)
                for key, value in dict(payload.get("sources", {})).items()
            },
        )


@dataclass(frozen=True)
class ImageGenerationSettings:
    width: int
    height: int
    mobile_width: int
    mobile_height: int
    backend: str
    backend_priority: str
    api_mode: str
    model_name: str
    model_base_url: str
    model_api_key: str
    request_timeout_seconds: float
    cover_size: str
    infographic_size: str
    max_generated_assets: int
    quality: str
    style: str
    background: str
    fallback_to_renderer: bool
    theme_name: str
    primary_color: str
    secondary_color: str
    accent_color: str
    background_color: str
    card_color: str
    text_color: str
    muted_text_color: str
    font_path: str
    bold_font_path: str
    export_html: bool
    export_svg: bool
    export_png: bool

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ImageGenerationSettings":
        return cls(
            width=int(payload.get("width", 1280)),
            height=int(payload.get("height", 720)),
            mobile_width=int(payload.get("mobile_width", 1080)),
            mobile_height=int(payload.get("mobile_height", 1920)),
            backend=str(payload.get("backend", "image2")),
            backend_priority=str(payload.get("backend_priority", "image2,local")),
            api_mode=str(payload.get("api_mode", "images")),
            model_name=str(payload.get("model_name", "gpt-image-2")),
            model_base_url=str(payload.get("model_base_url", "")),
            model_api_key=str(payload.get("model_api_key", "")),
            request_timeout_seconds=float(payload.get("request_timeout_seconds", 180)),
            cover_size=str(payload.get("cover_size", "1536x1024")),
            infographic_size=str(payload.get("infographic_size", "1024x1536")),
            max_generated_assets=int(payload.get("max_generated_assets", 2)),
            quality=str(payload.get("quality", "high")),
            style=str(payload.get("style", "vivid")),
            background=str(payload.get("background", "opaque")),
            fallback_to_renderer=bool(payload.get("fallback_to_renderer", True)),
            theme_name=str(payload.get("theme_name", "dark_tech")),
            primary_color=str(payload.get("primary_color", "#0B1220")),
            secondary_color=str(payload.get("secondary_color", "#122238")),
            accent_color=str(payload.get("accent_color", "#26D9FF")),
            background_color=str(payload.get("background_color", "#060B14")),
            card_color=str(payload.get("card_color", "#0F1B2D")),
            text_color=str(payload.get("text_color", "#F5F8FF")),
            muted_text_color=str(payload.get("muted_text_color", "#91A4C2")),
            font_path=str(payload.get("font_path", "") or resolve_default_font_path("regular")),
            bold_font_path=str(payload.get("bold_font_path", "") or resolve_default_font_path("bold")),
            export_html=bool(payload.get("export_html", True)),
            export_svg=bool(payload.get("export_svg", True)),
            export_png=bool(payload.get("export_png", True)),
        )


@dataclass(frozen=True)
class AutomationSettings:
    default_topic_rank: int
    max_daily_topic_attempts: int
    daily_run_times: list[str]
    weekend_run_times: list[str] = field(default_factory=list)
    archive_outputs: bool = True
    archive_dirname: str = "runs"
    selected_topic_basename: str = "selected_topic"
    # 本地通道「当日兜底窗口」：>=0 时，本地在当天该小时之前不做发布。
    # 目的：让云端（生产通道）先跑完它的窗口（北京 07:00/12:30/18:00，
    # 周末 08:00），本地只在窗口之后作为兜底补发，避免本地抢在云端前面发一篇。
    # -1 表示不限制（云端 CI 应显式设为 -1）。
    local_fallback_after_hour: int = -1

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AutomationSettings":
        return cls(
            default_topic_rank=int(payload.get("default_topic_rank", 1)),
            max_daily_topic_attempts=int(payload.get("max_daily_topic_attempts", 5)),
            daily_run_times=[str(item).strip() for item in payload.get("daily_run_times", []) if str(item).strip()],
            weekend_run_times=[str(item).strip() for item in payload.get("weekend_run_times", []) if str(item).strip()],
            archive_outputs=bool(payload.get("archive_outputs", True)),
            archive_dirname=str(payload.get("archive_dirname", "runs")),
            selected_topic_basename=str(payload.get("selected_topic_basename", "selected_topic")),
            local_fallback_after_hour=int(payload.get("local_fallback_after_hour", -1)),
        )


@dataclass(frozen=True)
class ReviewSettings:
    auto_prepare: bool
    min_overall_score: int
    max_ai_risk: int
    min_auto_publish_overall_score: int
    max_auto_publish_ai_risk: int
    min_auto_publish_validation_score: int
    min_auto_publish_readiness_score: int
    publish_dirname: str
    # 热榜快评专用自动发布门槛：标题型热点无原文素材，信息密度天然低于深度文。
    # 单独放宽门槛，让热点及时发布起量（仍拦截明显低质内容）。
    hot_topic_min_auto_publish_overall_score: int = 65
    hot_topic_max_auto_publish_ai_risk: int = 50
    hot_topic_min_auto_publish_readiness_score: int = 50

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewSettings":
        return cls(
            auto_prepare=bool(payload.get("auto_prepare", True)),
            min_overall_score=int(payload.get("min_overall_score", 72)),
            max_ai_risk=int(payload.get("max_ai_risk", 45)),
            min_auto_publish_overall_score=int(
                payload.get("min_auto_publish_overall_score", payload.get("min_overall_score", 72))
            ),
            max_auto_publish_ai_risk=int(
                payload.get("max_auto_publish_ai_risk", payload.get("max_ai_risk", 45))
            ),
            min_auto_publish_validation_score=int(payload.get("min_auto_publish_validation_score", 80)),
            min_auto_publish_readiness_score=int(payload.get("min_auto_publish_readiness_score", 70)),
            publish_dirname=str(payload.get("publish_dirname", "publish")),
            hot_topic_min_auto_publish_overall_score=int(
                payload.get("hot_topic_min_auto_publish_overall_score", 65)
            ),
            hot_topic_max_auto_publish_ai_risk=int(
                payload.get("hot_topic_max_auto_publish_ai_risk", 50)
            ),
            hot_topic_min_auto_publish_readiness_score=int(
                payload.get("hot_topic_min_auto_publish_readiness_score", 50)
            ),
        )

    def qualifies_for_auto_publish(
        self,
        overall_score: int,
        ai_risk: int,
        validation_score: int,
        publish_readiness_score: int,
        is_hot_topic: bool = False,
    ) -> bool:
        if is_hot_topic:
            # 热榜快评：标题型热点无原文素材，信息密度天然偏低，
            # 使用放宽后的门槛，让热点及时发布起量。
            return (
                overall_score >= self.hot_topic_min_auto_publish_overall_score
                and ai_risk <= self.hot_topic_max_auto_publish_ai_risk
                and validation_score >= self.min_auto_publish_validation_score
                and publish_readiness_score >= self.hot_topic_min_auto_publish_readiness_score
            )
        return (
            overall_score >= self.min_auto_publish_overall_score
            and ai_risk <= self.max_auto_publish_ai_risk
            and validation_score >= self.min_auto_publish_validation_score
            and publish_readiness_score >= self.min_auto_publish_readiness_score
        )


@dataclass(frozen=True)
class QualityReviewSettings:
    """独立文章质量审核委员会的运行策略。"""

    enabled: bool = True
    roles: list[str] = field(default_factory=lambda: list(DEFAULT_QUALITY_REVIEW_COUNCIL_ROLES))
    auto_revise: bool = True
    max_auto_revisions: int = 1
    min_overall_score: int = 78
    min_dimension_score: int = 65

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QualityReviewSettings":
        payload = payload if isinstance(payload, dict) else {}
        raw_roles = payload.get("roles", DEFAULT_QUALITY_REVIEW_COUNCIL_ROLES)
        roles = (
            [str(item).strip() for item in raw_roles if str(item).strip()]
            if isinstance(raw_roles, list)
            else list(DEFAULT_QUALITY_REVIEW_COUNCIL_ROLES)
        )
        return cls(
            enabled=bool(payload.get("enabled", True)),
            roles=roles or list(DEFAULT_QUALITY_REVIEW_COUNCIL_ROLES),
            auto_revise=bool(payload.get("auto_revise", True)),
            max_auto_revisions=max(0, min(3, int(payload.get("max_auto_revisions", 1)))),
            min_overall_score=max(0, min(100, int(payload.get("min_overall_score", 78)))),
            min_dimension_score=max(0, min(100, int(payload.get("min_dimension_score", 65)))),
        )


@dataclass(frozen=True)
class PublishSettings:
    enabled: bool
    auto_publish: bool
    draft_only: bool
    platform: str
    base_url: str
    publish_url: str
    user_data_dir: str
    channel: str
    executable_path: str
    headless: bool
    wait_timeout_ms: int
    history_filename: str
    allow_duplicate_topics: bool
    title_max_length: int
    cover_mode: str
    selectors: dict[str, list[str]]
    # Add evidence-backed images inside the Toutiao article body.
    inline_images_enabled: bool = True
    weitoutiao_enabled: bool = False
    weitoutiao_publish_url: str = ""
    weitoutiao_selectors: dict[str, list[str]] = field(default_factory=dict)
    # 微头条高频化（2026-08-27）：每日微头条目标条数（含图文伴生 1 条），
    # 超出部分由「独立微头条选题+写作」能力补齐（weitoutiao_standalone_enabled 时生效）。
    weitoutiao_daily_target: int = 1
    weitoutiao_standalone_enabled: bool = False
    # Publish one standalone WeiToutiao before the heavier article pipeline in each slot.
    weitoutiao_priority_enabled: bool = False
    # 微头条错峰（2026-09-04）：图文发布成功后是否「立即」发伴生微头条。
    # 关闭后伴生动作交给后续时段的 standalone top-up（already_published 分支），
    # 图文与微头条之间天然拉开 8-12 小时，避免同一窗口连发触发灌水风控。
    weitoutiao_companion_enabled: bool = True
    # Allow deterministic short-content generation when the model gateway is transiently unavailable.
    weitoutiao_local_fallback_enabled: bool = True
    # 独立微头条人设（hot_comment / warm / sanhuo / shoulan），按序轮换
    weitoutiao_personas: list[str] = field(default_factory=lambda: ["hot_comment", "warm"])

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishSettings":
        selectors = {
            str(key): [str(item) for item in value]
            for key, value in dict(payload.get("selectors", {})).items()
        }
        weitoutiao_selectors = {
            str(key): [str(item) for item in value]
            for key, value in dict(payload.get("weitoutiao_selectors", {})).items()
        }
        raw_personas = payload.get("weitoutiao_personas", ["hot_comment", "warm"])
        personas = (
            [str(item).strip() for item in raw_personas if str(item).strip()]
            if isinstance(raw_personas, list)
            else ["hot_comment", "warm"]
        )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            auto_publish=bool(payload.get("auto_publish", False)),
            draft_only=bool(payload.get("draft_only", False)),
            platform=str(payload.get("platform", "toutiao")),
            base_url=str(payload.get("base_url", "https://mp.toutiao.com/")),
            publish_url=str(payload.get("publish_url", "")),
            user_data_dir=str(payload.get("user_data_dir", ".runtime/toutiao_profile")),
            channel=str(payload.get("channel", "msedge")),
            executable_path=str(payload.get("executable_path", "")),
            headless=bool(payload.get("headless", False)),
            wait_timeout_ms=int(payload.get("wait_timeout_ms", 15000)),
            history_filename=str(payload.get("history_filename", "publish_history.json")),
            allow_duplicate_topics=bool(payload.get("allow_duplicate_topics", False)),
            title_max_length=int(payload.get("title_max_length", 30)),
            cover_mode=str(payload.get("cover_mode", "auto")),
            selectors=selectors,
            inline_images_enabled=bool(payload.get("inline_images_enabled", True)),
            weitoutiao_enabled=bool(payload.get("weitoutiao_enabled", False)),
            weitoutiao_publish_url=str(payload.get("weitoutiao_publish_url", "")),
            weitoutiao_selectors=weitoutiao_selectors,
            weitoutiao_daily_target=int(payload.get("weitoutiao_daily_target", 1)),
            weitoutiao_standalone_enabled=bool(payload.get("weitoutiao_standalone_enabled", False)),
            weitoutiao_priority_enabled=bool(payload.get("weitoutiao_priority_enabled", False)),
            weitoutiao_companion_enabled=bool(payload.get("weitoutiao_companion_enabled", True)),
            weitoutiao_local_fallback_enabled=bool(payload.get("weitoutiao_local_fallback_enabled", True)),
            weitoutiao_personas=personas,
        )


@dataclass(frozen=True)
class WechatSettings:
    enabled: bool
    auto_publish: bool
    toutiao_sync_delay_seconds: int
    app_id: str
    app_secret: str
    account_name: str
    author_name: str
    base_url: str
    api_base_url: str
    api_proxy: str
    title_max_length: int
    digest_max_length: int
    history_filename: str
    cover_strategy: str
    layout_backend: str
    md2wechat_enabled: bool
    md2wechat_api_key: str
    md2wechat_base_url: str
    md2wechat_theme: str
    md2wechat_font_size: str
    md2wechat_convert_version: str
    md2wechat_cli_path: str
    md2wechat_fallback_to_builtin: bool
    html_anything_enabled: bool
    html_anything_url: str
    html_anything_template: str
    html_anything_agent: str
    serv00_proxy_enabled: bool
    serv00_proxy_url: str
    serv00_proxy_secret: str
    editor_135_enabled: bool
    editor_135_base_url: str
    editor_135_profile_dir: str
    builtin_theme: str
    draft_only: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WechatSettings":
        return cls(
            enabled=bool(payload.get("enabled", False)),
            auto_publish=bool(payload.get("auto_publish", False)),
            toutiao_sync_delay_seconds=int(payload.get("toutiao_sync_delay_seconds", 1800)),
            app_id=str(payload.get("app_id", "")).strip(),
            app_secret=str(payload.get("app_secret", "")).strip(),
            account_name=str(payload.get("account_name", "")).strip(),
            author_name=str(payload.get("author_name", "")).strip(),
            base_url=str(payload.get("base_url", "https://mp.weixin.qq.com/")).strip(),
            api_base_url=str(payload.get("api_base_url", "https://api.weixin.qq.com")).strip(),
            api_proxy=str(payload.get("api_proxy", "")).strip(),
            title_max_length=int(payload.get("title_max_length", 64)),
            digest_max_length=int(payload.get("digest_max_length", 120)),
            history_filename=str(payload.get("history_filename", "wechat_publish_history.json")),
            cover_strategy=str(payload.get("cover_strategy", "text_cover")),
            layout_backend=str(payload.get("layout_backend", "builtin")).strip() or "builtin",
            md2wechat_enabled=bool(payload.get("md2wechat_enabled", False)),
            md2wechat_api_key=str(payload.get("md2wechat_api_key", "")).strip(),
            md2wechat_base_url=str(payload.get("md2wechat_base_url", "https://www.md2wechat.com/api/v1")).strip(),
            md2wechat_theme=str(payload.get("md2wechat_theme", "default")).strip() or "default",
            md2wechat_font_size=str(payload.get("md2wechat_font_size", "medium")).strip() or "medium",
            md2wechat_convert_version=str(payload.get("md2wechat_convert_version", "v1")).strip() or "v1",
            md2wechat_cli_path=str(payload.get("md2wechat_cli_path", "")).strip(),
            md2wechat_fallback_to_builtin=bool(payload.get("md2wechat_fallback_to_builtin", True)),
            html_anything_enabled=bool(payload.get("html_anything_enabled", False)),
            html_anything_url=str(payload.get("html_anything_url", "http://localhost:3000")).strip(),
            html_anything_template=str(payload.get("html_anything_template", "")).strip(),
            html_anything_agent=str(payload.get("html_anything_agent", "claude")).strip(),
            serv00_proxy_enabled=bool(payload.get("serv00_proxy_enabled", False)),
            serv00_proxy_url=str(payload.get("serv00_proxy_url", "")).strip(),
            serv00_proxy_secret=str(payload.get("serv00_proxy_secret", "")).strip(),
            # Retain the legacy field for config compatibility, but the 135
            # Editor integration is retired and cannot be enabled again.
            editor_135_enabled=False,
            editor_135_base_url=str(payload.get("editor_135_base_url", "https://www.135editor.com")).strip(),
            editor_135_profile_dir=str(payload.get("editor_135_profile_dir", "editor_135_profile")).strip(),
            builtin_theme=str(payload.get("builtin_theme", "tech_blue")).strip() or "tech_blue",
            draft_only=bool(payload.get("draft_only", False)),
        )



class AccountProfile(BaseModel):
    """账号配置模型，带数据校验。"""
    model_config = ConfigDict(frozen=True)

    key: str
    slug: str
    label: str
    enabled: bool
    input_dir: str
    output_dir: str
    logs_dir: str
    cache_dir: str
    browser_profile_dir: str
    overrides: dict[str, Any]

    @field_validator("key")
    @classmethod
    def key_must_be_valid_identifier(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", v):
            raise ValueError(
                f"Account key must be a valid English identifier (letters, digits, underscores), got: {v!r}"
            )
        return v

    @classmethod
    def from_dict(cls, key: str, payload: dict[str, Any]) -> "AccountProfile":
        slug = sanitize_account_slug(key)
        return cls(
            key=normalize_account_key(key),
            slug=slug,
            label=str(payload.get("label", key)),
            enabled=bool(payload.get("enabled", True)),
            input_dir=str(payload.get("input_dir", "")).strip(),
            output_dir=str(payload.get("output_dir", "")).strip(),
            logs_dir=str(payload.get("logs_dir", "")).strip(),
            cache_dir=str(payload.get("cache_dir", "")).strip(),
            browser_profile_dir=str(payload.get("browser_profile_dir", "")).strip(),
            overrides={str(name): copy.deepcopy(value) for name, value in payload.items() if str(name) not in ACCOUNT_META_KEYS},
        )


class StyleProfile(BaseModel):
    """写作风格配置模型。"""
    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    tone: str
    article_instruction: str
    title_instruction: str
    image_instruction: str
    opening_instruction: str
    # 内容领域。决定 WriterAgent 用哪一套写作机制与读者框架。
    #
    # ⚠️ 2026-09-18：新增字段，默认 "industry"，既有 4 个风格因此**行为完全不变**。
    #
    # 为什么需要它：`_writer_system_prompt` 与 `article_prompt_v2.md` 里写死了
    # 「中文科技产业文章」「产业链拆解」「3 个数据锚点」「时间线锚定」这类要求。
    # style 只能调语气和节奏，改不了领域 —— 于是社会故事类账号会被写成
    # 「产业分析腔的家庭故事」。这个字段就是用来切换那一层的。
    #
    # 取值：
    #   - "industry"（默认）：科技/产业分析写法，即现有全部风格。
    #   - "narrative"：人间故事写法。事实边界、反虚构、改写距离这些**安全约束一律保留**，
    #     只把「产业链/数据密度」换成「人物处境/选择与后果」。
    content_domain: str = "industry"

    @classmethod
    def from_dict(cls, key: str, payload: dict[str, Any]) -> "StyleProfile":
        return cls(
            key=key,
            label=str(payload.get("label", key)),
            tone=str(payload.get("tone", "")),
            article_instruction=str(payload.get("article_instruction", "")),
            title_instruction=str(payload.get("title_instruction", "")),
            image_instruction=str(payload.get("image_instruction", "")),
            opening_instruction=str(payload.get("opening_instruction", "")),
            content_domain=str(payload.get("content_domain", "industry") or "industry"),
        )


# ============================================================================
# 模块 A/B/C 合并新增配置结构（全部遵循 .get 兼容规则，向下兼容）
# ============================================================================


@dataclass(frozen=True)
class RPAResilienceSettings:
    """模块 A：RPA 浏览器发布稳定性配置。"""
    max_retries: int = 3
    retry_backoff_seconds: int = 2
    popup_defense_enabled: bool = True
    navigation_timeout_ms: int = 30000
    stale_element_retry: int = 2
    crash_recovery_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "RPAResilienceSettings":
        if not data:
            return cls()
        return cls(
            max_retries=data.get("max_retries", 3),
            retry_backoff_seconds=data.get("retry_backoff_seconds", 2),
            popup_defense_enabled=data.get("popup_defense_enabled", True),
            navigation_timeout_ms=data.get("navigation_timeout_ms", 30000),
            stale_element_retry=data.get("stale_element_retry", 2),
            crash_recovery_enabled=data.get("crash_recovery_enabled", True),
        )


@dataclass(frozen=True)
class ContentGuardSettings:
    """模块 B：内容安全守卫与查重配置。"""
    sensitive_words_path: str = "config/bad_words.txt"
    ai_cliche_words_path: str = "config/ai_cliche_words.txt"
    simhash_threshold: float = 0.82
    dedup_cache_size: int = 5000
    scan_timeout_seconds: int = 10
    parallel_scan_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "ContentGuardSettings":
        if not data:
            return cls()
        return cls(
            sensitive_words_path=data.get("sensitive_words_path", "config/bad_words.txt"),
            ai_cliche_words_path=data.get("ai_cliche_words_path", "config/ai_cliche_words.txt"),
            simhash_threshold=data.get("simhash_threshold", 0.82),
            dedup_cache_size=data.get("dedup_cache_size", 5000),
            scan_timeout_seconds=data.get("scan_timeout_seconds", 10),
            parallel_scan_enabled=data.get("parallel_scan_enabled", True),
        )


@dataclass(frozen=True)
class EvolutionSettings:
    """模块 C：自我演进闭环与规则动态注入配置。"""
    best_practices_rule_path: str = "config/best_practices_rule.txt"
    rule_injection_weight: float = 0.3
    auto_rule_refresh: bool = True
    rule_cache_ttl_seconds: int = 3600
    min_rule_confidence: float = 0.6

    @classmethod
    def from_dict(cls, data: dict) -> "EvolutionSettings":
        if not data:
            return cls()
        return cls(
            best_practices_rule_path=data.get("best_practices_rule_path", "config/best_practices_rule.txt"),
            rule_injection_weight=data.get("rule_injection_weight", 0.3),
            auto_rule_refresh=data.get("auto_rule_refresh", True),
            rule_cache_ttl_seconds=data.get("rule_cache_ttl_seconds", 3600),
            min_rule_confidence=data.get("min_rule_confidence", 0.6),
        )


@dataclass(frozen=True)
class RevenueOptimizationSettings:
    """收益归因驱动的选题冷却策略。

    Defaults are intentionally conservative: the policy cannot remove a topic
    until enough mature performance observations have accumulated.
    """

    enabled: bool = True
    adaptive_topic_selection: bool = False
    lookback_days: int = 30
    min_samples: int = 10
    weak_rpm_threshold: float = 0.0
    weak_read_rate_threshold: float = 3.0
    cooldown_weak_ratio: float = 0.5
    stop_weak_ratio: float = 0.8
    cooldown_penalty: int = 12

    @classmethod
    def from_dict(cls, data: dict) -> "RevenueOptimizationSettings":
        payload = data if isinstance(data, dict) else {}

        def _int_value(key: str, default: int, env_name: str) -> int:
            raw = os.getenv(env_name, payload.get(key, default))
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        def _float_value(key: str, default: float, env_name: str) -> float:
            raw = os.getenv(env_name, payload.get(key, default))
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return default
            return value if value == value and abs(value) != float("inf") else default

        cooldown_ratio = max(0.0, min(1.0, _float_value("cooldown_weak_ratio", 0.5, "REVENUE_COOLDOWN_WEAK_RATIO")))
        stop_ratio = max(cooldown_ratio, min(1.0, _float_value("stop_weak_ratio", 0.8, "REVENUE_STOP_WEAK_RATIO")))
        return cls(
            enabled=_env_flag("REVENUE_OPTIMIZATION_ENABLED", bool(payload.get("enabled", True))),
            adaptive_topic_selection=bool(payload.get("adaptive_topic_selection", False)),
            lookback_days=max(1, _int_value("lookback_days", 30, "REVENUE_OPTIMIZATION_LOOKBACK_DAYS")),
            min_samples=max(1, _int_value("min_samples", 10, "REVENUE_OPTIMIZATION_MIN_SAMPLES")),
            weak_rpm_threshold=_float_value("weak_rpm_threshold", 0.0, "REVENUE_WEAK_RPM_THRESHOLD"),
            weak_read_rate_threshold=max(
                0.0,
                _float_value("weak_read_rate_threshold", 3.0, "REVENUE_WEAK_READ_RATE_THRESHOLD"),
            ),
            cooldown_weak_ratio=cooldown_ratio,
            stop_weak_ratio=stop_ratio,
            cooldown_penalty=max(0, _int_value("cooldown_penalty", 12, "REVENUE_COOLDOWN_PENALTY")),
        )


@dataclass(frozen=True)
class IterationOverrides:
    """基于发布数据观察的内容迭代调整项。

    由 ``config/iteration_overrides/<account>.json`` 加载，覆盖各 Agent 的默认配置，
    实现基于真实读者反馈的迭代调整。所有字段均带默认值（向下兼容）：
    当调整项文件不存在或字段缺失时，使用默认空值，不影响现有流程。
    """
    # 标题模板权重，如 {"judgment": 0.4, "contrast": 0.3, "data_hook": 0.3}
    title_template_weights: dict = field(default_factory=dict)
    # 互动话术模板权重，如 {"stance": 0.5, "variable_query": 0.2, "counterfactual": 0.2, "experience": 0.1}
    engagement_template_weights: dict = field(default_factory=dict)
    # 话题优先级，如 {"robot": 0.4, "ai_chip": 0.3, "new_energy": 0.3}
    topic_priorities: dict = field(default_factory=dict)
    # 首段结构，如 "judgment_first" | "data_first" | "scene_first"
    opening_structure: str = ""
    # 调整证据（各调整项的证据说明）
    adjustment_evidence: dict = field(default_factory=dict)
    # 整体置信度
    adjustment_confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "IterationOverrides":
        if not data:
            return cls()
        return cls(
            title_template_weights=dict(data.get("title_template_weights", {}) or {}),
            engagement_template_weights=dict(data.get("engagement_template_weights", {}) or {}),
            topic_priorities=dict(data.get("topic_priorities", {}) or {}),
            opening_structure=str(data.get("opening_structure", "") or ""),
            adjustment_evidence=dict(data.get("adjustment_evidence", {}) or {}),
            adjustment_confidence=float(data.get("adjustment_confidence", 0.0) or 0.0),
        )


@dataclass
class AppSettings:
    project_root: Path
    config_path: Path
    account_key: str
    account_slug: str
    account_label: str
    is_default_account: bool
    input_dir: Path
    output_dir: Path
    prompts_dir: Path
    logs_dir: Path
    cache_dir: Path
    openai_api_key: str | None
    openai_base_url: str | None
    openai_model: str
    temperature: float
    max_input_chars: int
    article_word_target: int
    brand_name: str
    industry_focus: str
    audience: str
    brand_positioning: str
    platform: str
    logging_level: str
    default_style: str
    title_count: int
    recommended_title_count: int
    cache_similarity_threshold: float
    style_profiles: dict[str, StyleProfile]
    topic_collection: TopicCollectionSettings
    image_generation: ImageGenerationSettings
    automation: AutomationSettings
    review: ReviewSettings
    publishing: PublishSettings
    wechat: WechatSettings
    account_profiles: dict[str, AccountProfile]
    raw_account_profiles: dict[str, dict[str, Any]]
    available_account_keys: list[str]
    quality_review: QualityReviewSettings = field(default_factory=QualityReviewSettings)
    fast_mode: bool = False
    skip_image_generation: bool = False
    skip_review_agent: bool = False
    skip_publish_review: bool = False
    # 热榜/标题型热点是否豁免「素材事实不足」门槛。
    #
    # ⚠️ 2026-09-18：默认 False = **保持既有严格行为**（开自动发布时，即使
    # is_hot_topic=True 也拦）。这一点在 tests/test_agent_workflow.py 的
    # `test_abort_rejects_insufficient_hot_topic_for_auto_publish` 里被显式断言过，
    # 说明是**有意的策略**而不是笔误，所以不能直接放宽。
    #
    # 但这条策略对社会故事类账号是走不通的：热榜类社会新闻（例如
    # 「男子高空作业绳子被2楼老人剪断」）在采集时**只有标题、没有正文**，
    # 永远不可能满足「≥6 条事实且 ≥320 字」，于是每个候选都被拦，
    # 日更直接报「Daily workflow did not produce any runnable candidates」。
    # 实测：换赛道后 4 个候选全部以 insufficient_source_facts 被跳过。
    #
    # 所以做成显式开关：默认不动任何既有账号；社会故事类账号在账号配置里
    # 打开它（`hot_topic_evidence_exempt: true`），把「热点二创不受证据门槛限制」
    # 这条已有注释里的意图真正生效。
    hot_topic_evidence_exempt: bool = False
    enhanced_insights_enabled: bool = False
    enhanced_insights_use_multi_source: bool = False
    enhanced_insights_use_historical: bool = False
    enhanced_insights_use_data_mining: bool = False
    expert_council_enabled: bool = True
    expert_council_roles: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXPERT_COUNCIL_ROLES)
    )
    # ---- One-Person Company AI Services ----
    deerflow_enabled: bool = False
    deerflow_base_url: str = "http://deerflow:8001"
    letta_enabled: bool = False
    letta_base_url: str = "http://letta:8283"
    letta_agent_id: str = ""
    prompt_optimizer_enabled: bool = False
    prompt_optimizer_base_url: str = "http://prompt-optimizer:8501"
    ruflo_enabled: bool = False
    ruflo_base_url: str = "http://ruflo:8002"
    agency_roles_dir: str = "config/agency_roles"
    # Multi-platform publishing
    bilibili_enabled: bool = False
    zhihu_enabled: bool = False
    xiaohongshu_enabled: bool = False
    youtube_enabled: bool = False
    # Engagement agent
    engagement_enabled: bool = False
    engagement_max_daily_likes: int = 50
    engagement_max_daily_comments: int = 20
    engagement_comment_style: str = "professional"
    # Monetization
    monetization_enabled: bool = False
    cps_enabled: bool = False
    cpe_enabled: bool = False
    # Matrix operations
    matrix_enabled: bool = False
    # ---- 模块 A/B/C 合并新增配置字段（带默认值，向下兼容） ----
    rpa_resilience: RPAResilienceSettings = field(default_factory=RPAResilienceSettings)
    content_guard: ContentGuardSettings = field(default_factory=ContentGuardSettings)
    evolution: EvolutionSettings = field(default_factory=EvolutionSettings)
    revenue_optimization: RevenueOptimizationSettings = field(default_factory=RevenueOptimizationSettings)
    # 基于发布数据观察的内容迭代调整项（默认空对象，向下兼容）
    iteration_overrides: IterationOverrides = field(default_factory=IterationOverrides)

    def ensure_directories(self) -> None:
        for directory in (
            self.input_dir,
            self.output_dir,
            self.prompts_dir,
            self.logs_dir,
            self.logs_dir / "scheduled",
            self.cache_dir,
            self.output_dir / "automation",
            self.output_dir / self.automation.archive_dirname,
            self.account_runtime_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def get_style(self, style_key: str | None = None) -> StyleProfile:
        key = style_key or self.default_style
        return self.style_profiles.get(key, self.style_profiles[self.default_style])

    @property
    def content_domain(self) -> str:
        """当前账号的内容领域（industry / narrative）。

        提示词里把「你是科技产业编辑」「标题要像头条科技分析号」写死成了领域定位，
        `PromptRepository` 按这个值决定是否替换成叙事措辞。
        ⚠️ 2026-09-18：原来只有 WriterAgent 显式传了 `content_domain`，
        标题/润色/审核等 agent 全部落回默认 `industry` —— 于是标题依旧被套进科技框架。
        现在由 `BaseAgent.render_prompt` 统一从这里取，agent 不需要各自记得传。
        """
        try:
            return str(getattr(self.get_style(), "content_domain", "industry") or "industry")
        except Exception:  # noqa: BLE001 - 配置异常不应阻断提示词渲染
            return "industry"

    @property
    def wechat_auto_publish_mode(self) -> bool:
        return self.platform == "wechat" and self.wechat.enabled and self.wechat.auto_publish

    @property
    def account_runtime_dir(self) -> Path:
        if self.platform == "wechat" and os.environ.get("WECHAT_EXECUTION_HOST") == "fixed":
            fixed_root = Path(os.environ.get("WECHAT_FIXED_DEVICE_DIR", ""))
            if not fixed_root.is_absolute():
                raise ValueError("Fixed WeChat execution requires an absolute WECHAT_FIXED_DEVICE_DIR")
            return fixed_root / self.account_slug
        if self.is_default_account:
            return self.project_root / ".runtime"
        return self.project_root / ".runtime" / "accounts" / self.account_slug

    @property
    def automation_dir(self) -> Path:
        return self.output_dir / "automation"

    @property
    def scheduled_logs_dir(self) -> Path:
        return self.logs_dir / "scheduled"

    @property
    def runs_dir(self) -> Path:
        return self.output_dir / self.automation.archive_dirname


def load_settings(project_root: Path, account_key: str | None = None) -> AppSettings:
    env_path = project_root / ".env"
    if not env_path.exists():
        import sys
        in_github_actions = str(os.getenv("GITHUB_ACTIONS", "")).strip().lower() == "true"
        if in_github_actions:
            print(
                f"[INFO] .env file not found at {env_path}. "
                "Continuing because GitHub Actions injects secrets via workflow env.",
                file=sys.stderr,
            )
        else:
            print(
                f"[WARNING] .env file not found at {env_path}. "
                "Copy .env.example to .env and fill in your API keys.",
                file=sys.stderr,
            )
    load_dotenv(env_path)

    config_path = project_root / "config.yaml"
    config_payload = _load_config_payload(config_path)
    styles = _load_style_profiles(config_payload.get("styles", {}))
    all_account_profiles = _load_account_profiles(config_payload.get("accounts", {}))
    raw_account_profiles = {
        key: profile for key, profile in all_account_profiles.items() if profile.enabled
    }
    resolved_account_key = normalize_account_key(account_key or os.getenv("TOUTIAO_ACCOUNT"))
    account_profile = raw_account_profiles.get(resolved_account_key)
    if account_profile is None and resolved_account_key != DEFAULT_ACCOUNT_KEY:
        raise ValueError(
            f"Unknown account key: {resolved_account_key}. "
            f"Available accounts: {', '.join(sorted(raw_account_profiles))}"
        )
    if account_profile is None:
        account_profile = raw_account_profiles[DEFAULT_ACCOUNT_KEY]
    merged_payload = copy.deepcopy(config_payload)
    if account_profile.overrides:
        _deep_merge(merged_payload, account_profile.overrides)

    # Load sub-configs via helpers
    topic_config = TopicCollectionSettings.from_dict(merged_payload.get("topic_collection", {}))
    image_config = ImageGenerationSettings.from_dict(merged_payload.get("image_generation", {}))
    automation_config = AutomationSettings.from_dict(merged_payload.get("automation", {}))
    review_config = ReviewSettings.from_dict(merged_payload.get("review", {}))
    publishing_config = PublishSettings.from_dict(merged_payload.get("publishing", {}))
    wechat_config = WechatSettings.from_dict(merged_payload.get("wechat", {}))
    quality_review_config = QualityReviewSettings.from_dict(
        merged_payload.get("quality_review_council", {})
    )

    # 模块 A/B/C 合并新增配置加载（全部通过 from_dict 兜底默认值，向下兼容）
    rpa_resilience_config = RPAResilienceSettings.from_dict(merged_payload.get("rpa_resilience", {}))
    content_guard_config = ContentGuardSettings.from_dict(merged_payload.get("content_guard", {}))
    evolution_config = EvolutionSettings.from_dict(merged_payload.get("evolution", {}))
    revenue_optimization_config = RevenueOptimizationSettings.from_dict(
        merged_payload.get("revenue_optimization", {})
    )

    # 基于发布数据观察的迭代调整项：从 config/iteration_overrides/<account>.json 加载
    # 文件不存在时使用空默认值，不影响现有流程
    iteration_overrides_config = _load_iteration_overrides(
        project_root, account_profile.key, account_profile.slug
    )

    is_default_account = account_profile.key == DEFAULT_ACCOUNT_KEY

    # 写作风格的取值优先级：账号级 writing.default_style > DEFAULT_STYLE 环境变量 > 全局。
    #
    # ⚠️ 2026-09-18：这里原来**只读全局** `writing.default_style` 且让环境变量无条件压过它，
    # config.yaml 里每个账号自带的 `writing.default_style` 被静默忽略
    # （robot_notes=tech_media、sports_notes=viral_science …全都不生效）。
    # 本项目的 .env 又设了 `DEFAULT_STYLE=tech_media`，于是**所有账号用的是同一个风格**，
    # 「按账号换写作风格」这件事实际不存在。
    #
    # 现在改为账号级优先、环境变量作兜底：
    #   - 单账号使用者：账号没配风格时，.env 的 DEFAULT_STYLE 照旧生效（行为不变）；
    #   - 多账号：每个账号各用各的风格，不需要同步改 .env；
    #   - 想强制覆盖某个账号：用账号级变量 `DEFAULT_STYLE__<SLUG>`。
    account_writing = account_profile.overrides.get("writing")
    account_default_style = ""
    if isinstance(account_writing, dict):
        account_default_style = str(account_writing.get("default_style", "") or "").strip()
    scoped_default_style = (
        os.getenv(account_env_name("DEFAULT_STYLE", account_profile.key), "") or ""
    ).strip()
    global_default_style = str(
        merged_payload.get("writing", {}).get("default_style", "deep_industry_analysis")
    )
    default_style = (
        scoped_default_style
        or account_default_style
        or os.getenv("DEFAULT_STYLE", "")
        or global_default_style
    )

    # Resolve account paths
    paths = _load_account_paths(project_root, account_profile, is_default_account, publishing_config)

    # Build final settings using helper functions
    model_overrides = _load_model_config(merged_payload)
    writing_overrides = _load_writing_config(merged_payload, account_profile.key)
    automation_final = _load_automation_config(merged_payload, automation_config)
    review_final = _load_review_config(merged_payload, review_config)
    publishing_final = _load_publishing_config_final(merged_payload, account_profile, is_default_account, automation_config, publishing_config, paths)
    wechat_final = _load_wechat_config_final(merged_payload, account_profile, wechat_config)
    image_final = _load_image_config(merged_payload, image_config)
    quality_review_final = _load_quality_review_config(merged_payload, quality_review_config)
    if writing_overrides["platform"] == "wechat" and _env_flag_scoped(
        "WECHAT_TECH_TOPICS_ONLY", account_profile.key, False
    ):
        topic_config = topic_config.model_copy(update={
            "focus_industries": [
                "humanoid_robot", "new_energy", "energy_storage", "ai_hardware",
                "industrial_automation", "ai_applications",
            ],
            "keyword_map": {**topic_config.keyword_map, "ai_applications": [
                "人工智能", "大模型", "生成式AI", "智能体", "AI应用", "机器学习",
            ]},
        })
    expert_council_payload = merged_payload.get("expert_council", {})
    if not isinstance(expert_council_payload, dict):
        expert_council_payload = {}
    expert_council_roles = expert_council_payload.get("roles", DEFAULT_EXPERT_COUNCIL_ROLES)
    if not isinstance(expert_council_roles, list):
        expert_council_roles = list(DEFAULT_EXPERT_COUNCIL_ROLES)
    expert_council_roles = [str(role).strip() for role in expert_council_roles if str(role).strip()]
    if not expert_council_roles:
        expert_council_roles = list(DEFAULT_EXPERT_COUNCIL_ROLES)

    return AppSettings(
        project_root=project_root,
        config_path=config_path,
        account_key=account_profile.key,
        account_slug=account_profile.slug,
        account_label=account_profile.label,
        is_default_account=is_default_account,
        input_dir=paths["input_dir"],
        output_dir=paths["output_dir"],
        prompts_dir=project_root / "prompts",
        logs_dir=paths["logs_dir"],
        cache_dir=paths["cache_dir"],
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_base_url=model_overrides["openai_base_url"],
        openai_model=model_overrides["openai_model"],
        temperature=model_overrides["temperature"],
        max_input_chars=writing_overrides["max_input_chars"],
        article_word_target=writing_overrides["article_word_target"],
        brand_name=writing_overrides["brand_name"],
        industry_focus=writing_overrides["industry_focus"],
        audience=writing_overrides["audience"],
        brand_positioning=writing_overrides["brand_positioning"],
        platform=writing_overrides["platform"],
        logging_level=os.getenv("LOG_LEVEL", "INFO"),
        default_style=default_style if default_style in styles else "deep_industry_analysis",
        title_count=writing_overrides["title_count"],
        recommended_title_count=writing_overrides["recommended_title_count"],
        cache_similarity_threshold=writing_overrides["cache_similarity_threshold"],
        style_profiles=styles,
        topic_collection=topic_config,
        image_generation=image_final,
        automation=automation_final,
        review=review_final,
        publishing=publishing_final,
        wechat=wechat_final,
        account_profiles=raw_account_profiles,
        raw_account_profiles={
            key: {
                "label": profile.label,
                "input_dir": profile.input_dir,
                "output_dir": profile.output_dir,
                "logs_dir": profile.logs_dir,
                "cache_dir": profile.cache_dir,
                "browser_profile_dir": profile.browser_profile_dir,
                **copy.deepcopy(profile.overrides),
            }
            for key, profile in raw_account_profiles.items()
        },
        available_account_keys=sorted(raw_account_profiles),
        quality_review=quality_review_final,
        fast_mode=_env_flag("TOUTIAO_FAST_MODE", False),
        skip_image_generation=_env_flag("TOUTIAO_SKIP_IMAGE_GENERATION", False),
        skip_review_agent=_env_flag("TOUTIAO_SKIP_REVIEW_AGENT", False),
        skip_publish_review=_env_flag("TOUTIAO_SKIP_PUBLISH_REVIEW", False),
        # 账号级开关（也为环境变量留了口子）。默认 False，既有账号行为不变。
        hot_topic_evidence_exempt=_env_flag_scoped(
            "TOUTIAO_HOT_TOPIC_EVIDENCE_EXEMPT",
            account_profile.key,
            bool(account_profile.overrides.get("hot_topic_evidence_exempt", False)),
        ),
        enhanced_insights_enabled=_env_flag("TOUTIAO_ENHANCED_INSIGHTS_ENABLED", bool(merged_payload.get("enhanced_insights", {}).get("enabled", False))),
        enhanced_insights_use_multi_source=_env_flag("TOUTIAO_ENHANCED_INSIGHTS_USE_MULTI_SOURCE", bool(merged_payload.get("enhanced_insights", {}).get("multi_source", False))),
        enhanced_insights_use_historical=_env_flag("TOUTIAO_ENHANCED_INSIGHTS_USE_HISTORICAL", bool(merged_payload.get("enhanced_insights", {}).get("historical", False))),
        enhanced_insights_use_data_mining=_env_flag("TOUTIAO_ENHANCED_INSIGHTS_USE_DATA_MINING", bool(merged_payload.get("enhanced_insights", {}).get("data_mining", False))),
        expert_council_enabled=_env_flag(
            "TOUTIAO_EXPERT_COUNCIL_ENABLED",
            bool(expert_council_payload.get("enabled", True)),
        ),
        expert_council_roles=expert_council_roles,
        # One-Person Company AI Services
        deerflow_enabled=_env_flag("DEERFLOW_ENABLED", False),
        deerflow_base_url=os.getenv("DEERFLOW_BASE_URL", "http://deerflow:8001"),
        letta_enabled=_env_flag("LETTA_ENABLED", False),
        letta_base_url=os.getenv("LETTA_BASE_URL", "http://letta:8283"),
        letta_agent_id=os.getenv("LETTA_AGENT_ID", ""),
        prompt_optimizer_enabled=_env_flag("PROMPT_OPTIMIZER_ENABLED", False),
        prompt_optimizer_base_url=os.getenv("PROMPT_OPTIMIZER_BASE_URL", "http://prompt-optimizer:8501"),
        ruflo_enabled=_env_flag("RUFLO_ENABLED", False),
        ruflo_base_url=os.getenv("RUFLO_BASE_URL", "http://ruflo:8002"),
        agency_roles_dir=os.getenv("AGENCY_ROLES_DIR", "config/agency_roles"),
        # Multi-platform publishing
        bilibili_enabled=_env_flag("BILIBILI_ENABLED", False),
        zhihu_enabled=_env_flag("ZHIHU_ENABLED", False),
        xiaohongshu_enabled=_env_flag("XIAOHONGSHU_ENABLED", False),
        youtube_enabled=_env_flag("YOUTUBE_ENABLED", False),
        # Engagement agent
        engagement_enabled=_env_flag("ENGAGEMENT_ENABLED", False),
        engagement_max_daily_likes=int(os.getenv("ENGAGEMENT_MAX_DAILY_LIKES", "50")),
        engagement_max_daily_comments=int(os.getenv("ENGAGEMENT_MAX_DAILY_COMMENTS", "20")),
        engagement_comment_style=os.getenv("ENGAGEMENT_COMMENT_STYLE", "professional"),
        # Monetization
        monetization_enabled=_env_flag("MONETIZATION_ENABLED", False),
        cps_enabled=_env_flag("CPS_ENABLED", False),
        cpe_enabled=_env_flag("CPE_ENABLED", False),
        # Matrix operations
        matrix_enabled=_env_flag("MATRIX_ENABLED", False),
        # 模块 A/B/C 合并新增配置
        rpa_resilience=rpa_resilience_config,
        content_guard=content_guard_config,
        evolution=evolution_config,
        revenue_optimization=revenue_optimization_config,
        # 内容迭代调整项
        iteration_overrides=iteration_overrides_config,
    )




def _load_model_config(merged_payload: dict[str, Any]) -> dict[str, Any]:
    model_config = merged_payload.get("model", {})
    return {
        "openai_base_url": os.getenv("OPENAI_BASE_URL") or str(model_config.get("base_url", "")).strip() or None,
        "openai_model": os.getenv("OPENAI_MODEL", str(model_config.get("name", "gpt-4.1-mini"))),
        "temperature": float(os.getenv("OPENAI_TEMPERATURE", str(model_config.get("temperature", 0.7)))),
    }


def _load_writing_config(merged_payload: dict[str, Any], account_key: str) -> dict[str, Any]:
    writing_config = merged_payload.get("writing", {})
    industry_config = merged_payload.get("industry", {})
    return {
        "max_input_chars": int(os.getenv("MAX_INPUT_CHARS", str(writing_config.get("max_input_chars", 16000)))),
        "article_word_target": int(
            os.getenv("ARTICLE_WORD_TARGET", str(writing_config.get("article_word_target", 2000)))
        ),
        "brand_name": _env_value_scoped(
            "BRAND_NAME",
            account_key,
            str(industry_config.get("brand_name", "手蓝数据笔记")),
        ),
        "industry_focus": _env_value_scoped(
            "INDUSTRY_FOCUS",
            account_key,
            str(industry_config.get("focus", "新能源、机器人、智能装备、储能")),
        ),
        "audience": _env_value_scoped(
            "TARGET_AUDIENCE",
            account_key,
            str(industry_config.get("audience", "科技行业从业者与关注产业升级的读者")),
        ),
        "brand_positioning": _env_value_scoped(
            "BRAND_POSITIONING",
            account_key,
            str(
                industry_config.get(
                    "positioning",
                    "聚焦科技商业、AI产业与先进制造，用数据拆热点，用产业链看趋势兑现。",
                )
            ),
        ),
        "platform": _env_value_scoped(
            "TARGET_PLATFORM",
            account_key,
            str(industry_config.get("platform", "今日头条")),
        ),
        "title_count": int(os.getenv("TITLE_COUNT", str(writing_config.get("title_count", 20)))),
        "recommended_title_count": int(
            os.getenv("RECOMMENDED_TITLE_COUNT", str(writing_config.get("recommended_title_count", 3)))
        ),
        "cache_similarity_threshold": float(
            os.getenv(
                "CACHE_SIMILARITY_THRESHOLD",
                str(writing_config.get("cache_similarity_threshold", 0.82)),
            )
        ),
    }


def _load_automation_config(merged_payload: dict[str, Any], automation_config: AutomationSettings) -> AutomationSettings:
    return AutomationSettings(
        default_topic_rank=int(
            os.getenv("TOUTIAO_DEFAULT_TOPIC_RANK", str(automation_config.default_topic_rank))
        ),
        max_daily_topic_attempts=int(
            os.getenv(
                "TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS",
                str(automation_config.max_daily_topic_attempts),
            )
        ),
        daily_run_times=automation_config.daily_run_times,
        weekend_run_times=automation_config.weekend_run_times,
        archive_outputs=automation_config.archive_outputs,
        archive_dirname=automation_config.archive_dirname,
        selected_topic_basename=automation_config.selected_topic_basename,
        local_fallback_after_hour=int(
            os.getenv(
                "TOUTIAO_LOCAL_FALLBACK_AFTER_HOUR",
                str(automation_config.local_fallback_after_hour),
            )
        ),
    )


def _load_review_config(merged_payload: dict[str, Any], review_config: ReviewSettings) -> ReviewSettings:
    return ReviewSettings(
        auto_prepare=_env_flag("TOUTIAO_REVIEW_AUTO_PREPARE", review_config.auto_prepare),
        min_overall_score=int(
            os.getenv("TOUTIAO_REVIEW_MIN_OVERALL_SCORE", str(review_config.min_overall_score))
        ),
        max_ai_risk=int(os.getenv("TOUTIAO_REVIEW_MAX_AI_RISK", str(review_config.max_ai_risk))),
        min_auto_publish_overall_score=int(
            os.getenv(
                "TOUTIAO_AUTO_PUBLISH_MIN_OVERALL_SCORE",
                str(review_config.min_auto_publish_overall_score),
            )
        ),
        max_auto_publish_ai_risk=int(
            os.getenv(
                "TOUTIAO_AUTO_PUBLISH_MAX_AI_RISK",
                str(review_config.max_auto_publish_ai_risk),
            )
        ),
        min_auto_publish_validation_score=int(
            os.getenv(
                "TOUTIAO_AUTO_PUBLISH_MIN_VALIDATION_SCORE",
                str(review_config.min_auto_publish_validation_score),
            )
        ),
        min_auto_publish_readiness_score=int(
            os.getenv(
                "TOUTIAO_AUTO_PUBLISH_MIN_READINESS_SCORE",
                str(review_config.min_auto_publish_readiness_score),
            )
        ),
        publish_dirname=os.getenv("TOUTIAO_REVIEW_PUBLISH_DIR", review_config.publish_dirname),
        hot_topic_min_auto_publish_overall_score=int(
            os.getenv(
                "TOUTIAO_HOT_TOPIC_MIN_AUTO_PUBLISH_OVERALL_SCORE",
                str(review_config.hot_topic_min_auto_publish_overall_score),
            )
        ),
        hot_topic_max_auto_publish_ai_risk=int(
            os.getenv(
                "TOUTIAO_HOT_TOPIC_MAX_AUTO_PUBLISH_AI_RISK",
                str(review_config.hot_topic_max_auto_publish_ai_risk),
            )
        ),
        hot_topic_min_auto_publish_readiness_score=int(
            os.getenv(
                "TOUTIAO_HOT_TOPIC_MIN_AUTO_PUBLISH_READINESS_SCORE",
                str(review_config.hot_topic_min_auto_publish_readiness_score),
            )
        ),
    )


def _load_quality_review_config(
    merged_payload: dict[str, Any],
    quality_review_config: QualityReviewSettings,
) -> QualityReviewSettings:
    roles_value = os.getenv("TOUTIAO_QUALITY_REVIEW_COUNCIL_ROLES", "").strip()
    roles = (
        [item.strip() for item in roles_value.split(",") if item.strip()]
        if roles_value
        else list(quality_review_config.roles)
    )
    return QualityReviewSettings(
        enabled=_env_flag("TOUTIAO_QUALITY_REVIEW_COUNCIL_ENABLED", quality_review_config.enabled),
        roles=roles or list(DEFAULT_QUALITY_REVIEW_COUNCIL_ROLES),
        auto_revise=_env_flag("TOUTIAO_QUALITY_REVIEW_AUTO_REVISE", quality_review_config.auto_revise),
        max_auto_revisions=max(
            0,
            min(
                3,
                int(
                    os.getenv(
                        "TOUTIAO_QUALITY_REVIEW_MAX_AUTO_REVISIONS",
                        str(quality_review_config.max_auto_revisions),
                    )
                ),
            ),
        ),
        min_overall_score=max(
            0,
            min(
                100,
                int(os.getenv("TOUTIAO_QUALITY_REVIEW_MIN_SCORE", str(quality_review_config.min_overall_score))),
            ),
        ),
        min_dimension_score=max(
            0,
            min(
                100,
                int(
                    os.getenv(
                        "TOUTIAO_QUALITY_REVIEW_MIN_DIMENSION_SCORE",
                        str(quality_review_config.min_dimension_score),
                    )
                ),
            ),
        ),
    )


def _load_account_paths(
    project_root: Path,
    account_profile: AccountProfile,
    is_default_account: bool,
    publishing_config: PublishSettings,
) -> dict[str, Any]:
    input_dir = _resolve_account_path(
        project_root=project_root,
        account_slug=account_profile.slug,
        configured_value=account_profile.input_dir,
        default_relative="input" if is_default_account else f"input/accounts/{account_profile.slug}",
    )
    output_dir = _resolve_account_path(
        project_root=project_root,
        account_slug=account_profile.slug,
        configured_value=account_profile.output_dir,
        default_relative="output" if is_default_account else f"output/accounts/{account_profile.slug}",
    )
    logs_dir = _resolve_account_path(
        project_root=project_root,
        account_slug=account_profile.slug,
        configured_value=account_profile.logs_dir,
        default_relative="logs" if is_default_account else f"logs/accounts/{account_profile.slug}",
    )
    cache_dir = _resolve_account_path(
        project_root=project_root,
        account_slug=account_profile.slug,
        configured_value=account_profile.cache_dir,
        default_relative="cache" if is_default_account else f"cache/accounts/{account_profile.slug}",
    )
    default_profile_relative = (
        ".runtime/toutiao_profile"
        if is_default_account
        else f".runtime/accounts/{account_profile.slug}/toutiao_profile"
    )
    account_browser_profile = account_profile.browser_profile_dir or default_profile_relative
    scoped_browser_profile = os.getenv(account_env_name("TOUTIAO_BROWSER_PROFILE_DIR", account_profile.key))
    scoped_publish_history = os.getenv(account_env_name("TOUTIAO_PUBLISH_HISTORY_FILE", account_profile.key))
    browser_profile_value = (
        scoped_browser_profile
        or (os.getenv("TOUTIAO_BROWSER_PROFILE_DIR") if is_default_account else None)
        or account_browser_profile
        or publishing_config.user_data_dir
    )
    publish_history_value = (
        scoped_publish_history
        or (os.getenv("TOUTIAO_PUBLISH_HISTORY_FILE") if is_default_account else None)
        or publishing_config.history_filename
    )
    return {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "logs_dir": logs_dir,
        "cache_dir": cache_dir,
        "browser_profile_value": browser_profile_value,
        "publish_history_value": publish_history_value,
    }


def _warn_legacy_draft_only_env() -> None:
    """旧名 `TOUTIAO_DRAFT_ONLY` 对发布配置**没有作用**，只设它时要显式提醒。

    2026-09-16 核实的事实（别再用"死变量"这种含糊说法）：
    - 这里真正读取的是 `TOUTIAO_PUBLISH_DRAFT_ONLY`；
    - `TOUTIAO_DRAFT_ONLY` 被 `scripts/cloud_daily_run.py`（降级路径设 1）与
      `scripts/preview_fallback.py`（设 0）当作**脚本间信号量**在用，
      workflow 里也是两个名字同时导出；
    - 所以只有「只设旧名、没设新名」时才存在"以为开了草稿模式其实没开"的坑。

    这里只打一条警告，不改变任何行为 —— 把旧名接成别名会**改变云端行为**
    （`cloud_daily_run.py` 用 setdefault 把它默认设成 1，一旦生效云端会整体转草稿）。
    """
    legacy = os.getenv("TOUTIAO_DRAFT_ONLY")
    if legacy is None or os.getenv("TOUTIAO_PUBLISH_DRAFT_ONLY") is not None:
        return
    import logging

    logging.getLogger(__name__).warning(
        "TOUTIAO_DRAFT_ONLY=%s 不会生效：发布开关读的是 TOUTIAO_PUBLISH_DRAFT_ONLY。"
        "两个名字语义不同（前者是脚本间信号量）；要真正开草稿模式请设 "
        "TOUTIAO_PUBLISH_DRAFT_ONLY=1。",
        legacy,
    )


def _load_publishing_config_final(
    merged_payload: dict[str, Any],
    account_profile: AccountProfile,
    is_default_account: bool,
    automation_config: AutomationSettings,
    publishing_config: PublishSettings,
    paths: dict[str, Any],
) -> PublishSettings:
    _warn_legacy_draft_only_env()
    return PublishSettings(
        enabled=_env_flag("TOUTIAO_PUBLISH_ENABLED", publishing_config.enabled),
        auto_publish=_env_flag("TOUTIAO_AUTO_PUBLISH", publishing_config.auto_publish),
        draft_only=_env_flag("TOUTIAO_PUBLISH_DRAFT_ONLY", publishing_config.draft_only),
        platform=os.getenv("TOUTIAO_PUBLISH_PLATFORM", publishing_config.platform),
        base_url=os.getenv("TOUTIAO_PUBLISH_BASE_URL", publishing_config.base_url),
        publish_url=os.getenv("TOUTIAO_PUBLISH_URL", publishing_config.publish_url),
        user_data_dir=paths["browser_profile_value"],
        channel=os.getenv("TOUTIAO_PUBLISH_CHANNEL", publishing_config.channel),
        executable_path=os.getenv("TOUTIAO_BROWSER_EXECUTABLE", publishing_config.executable_path),
        headless=_env_flag("TOUTIAO_PUBLISH_HEADLESS", publishing_config.headless),
        wait_timeout_ms=int(
            os.getenv("TOUTIAO_PUBLISH_TIMEOUT_MS", str(publishing_config.wait_timeout_ms))
        ),
        history_filename=paths["publish_history_value"],
        allow_duplicate_topics=_env_flag(
            "TOUTIAO_ALLOW_DUPLICATE_TOPICS",
            publishing_config.allow_duplicate_topics,
        ),
        title_max_length=int(
            os.getenv("TOUTIAO_TITLE_MAX_LENGTH", str(publishing_config.title_max_length))
        ),
        cover_mode=os.getenv("TOUTIAO_PUBLISH_COVER_MODE", publishing_config.cover_mode),
        selectors=publishing_config.selectors,
        inline_images_enabled=_env_flag(
            "TOUTIAO_INLINE_IMAGES_ENABLED",
            publishing_config.inline_images_enabled,
        ),
        weitoutiao_enabled=_env_flag("TOUTIAO_WEITOUTIAO_ENABLED", publishing_config.weitoutiao_enabled),
        weitoutiao_publish_url=os.getenv("TOUTIAO_WEITOUTIAO_PUBLISH_URL", publishing_config.weitoutiao_publish_url),
        weitoutiao_selectors=publishing_config.weitoutiao_selectors,
        weitoutiao_daily_target=int(
            os.getenv("TOUTIAO_WEITOUTIAO_DAILY_TARGET", str(publishing_config.weitoutiao_daily_target))
        ),
        weitoutiao_standalone_enabled=_env_flag(
            "TOUTIAO_WEITOUTIAO_STANDALONE_ENABLED",
            publishing_config.weitoutiao_standalone_enabled,
        ),
        weitoutiao_priority_enabled=_env_flag(
            "TOUTIAO_WEITOUTIAO_PRIORITY_ENABLED",
            publishing_config.weitoutiao_priority_enabled,
        ),
        weitoutiao_companion_enabled=_env_flag(
            "TOUTIAO_WEITOUTIAO_COMPANION_ENABLED",
            publishing_config.weitoutiao_companion_enabled,
        ),
        weitoutiao_local_fallback_enabled=_env_flag(
            "TOUTIAO_WEITOUTIAO_LOCAL_FALLBACK_ENABLED",
            publishing_config.weitoutiao_local_fallback_enabled,
        ),
        weitoutiao_personas=_resolve_weitoutiao_personas(
            account_profile, publishing_config
        ),
    )


def _resolve_weitoutiao_personas(
    account_profile: AccountProfile,
    publishing_config: PublishSettings,
) -> list[str]:
    """微头条人设的取值优先级：账号级环境变量 > 账号级配置 > 全局环境变量 > 全局配置。

    ⚠️ 2026-09-18：这里原来只读全局（`TOUTIAO_WEITOUTIAO_PERSONAS` 环境变量，
    否则全局 config.yaml 的 `publishing.weitoutiao_personas`），账号级
    `publishing.weitoutiao_personas` 写了也不生效 —— 和 default_style 是同一个毛病。

    对社会故事类账号这是硬需求：它需要用 `story_comment` 人设（就事实讲人情、给读者
    实在提醒），而机器人账号用的是 `hot_comment/warm/sanhuo`。共用一套人设会让
    story_notes 发出来的微头条是科技热点评论腔，跟账号定位完全不符。
    """
    account_publishing = account_profile.overrides.get("publishing")
    account_personas: list[str] = []
    if isinstance(account_publishing, dict):
        raw = account_publishing.get("weitoutiao_personas")
        if isinstance(raw, (list, tuple)):
            account_personas = [str(item).strip() for item in raw if str(item).strip()]

    scoped = (
        os.getenv(
            account_env_name("TOUTIAO_WEITOUTIAO_PERSONAS", account_profile.key), ""
        )
        or ""
    ).strip()
    if scoped:
        return [item.strip() for item in scoped.split(",") if item.strip()]
    if account_personas:
        return account_personas

    raw_env = os.getenv("TOUTIAO_WEITOUTIAO_PERSONAS", "").strip()
    if raw_env:
        return [item.strip() for item in raw_env.split(",") if item.strip()]
    return list(publishing_config.weitoutiao_personas)


def _load_wechat_config_final(
    merged_payload: dict[str, Any],
    account_profile: AccountProfile,
    wechat_config: WechatSettings,
) -> WechatSettings:
    return WechatSettings(
        enabled=_env_flag_scoped("WECHAT_ENABLED", account_profile.key, wechat_config.enabled),
        auto_publish=_env_flag_scoped("WECHAT_AUTO_PUBLISH", account_profile.key, wechat_config.auto_publish),
        draft_only=_env_flag_scoped("WECHAT_DRAFT_ONLY", account_profile.key, wechat_config.draft_only),
        toutiao_sync_delay_seconds=int(
            _env_value_scoped(
                "WECHAT_TOUTIAO_SYNC_DELAY",
                account_profile.key,
                str(wechat_config.toutiao_sync_delay_seconds),
            )
        ),
        app_id=os.getenv("WECHAT_APP_ID", wechat_config.app_id),
        app_secret=os.getenv("WECHAT_APP_SECRET", wechat_config.app_secret),
        account_name=os.getenv("WECHAT_ACCOUNT_NAME", wechat_config.account_name),
        author_name=os.getenv("WECHAT_AUTHOR_NAME", wechat_config.author_name),
        base_url=os.getenv("WECHAT_BASE_URL", wechat_config.base_url),
        api_base_url=os.getenv("WECHAT_API_BASE_URL", wechat_config.api_base_url),
        api_proxy=os.getenv("WECHAT_API_PROXY", wechat_config.api_proxy),
        title_max_length=int(os.getenv("WECHAT_TITLE_MAX_LENGTH", str(wechat_config.title_max_length))),
        digest_max_length=int(os.getenv("WECHAT_DIGEST_MAX_LENGTH", str(wechat_config.digest_max_length))),
        history_filename=os.getenv("WECHAT_HISTORY_FILENAME", wechat_config.history_filename),
        cover_strategy=os.getenv("WECHAT_COVER_STRATEGY", wechat_config.cover_strategy),
        layout_backend=os.getenv("WECHAT_LAYOUT_BACKEND", wechat_config.layout_backend),
        md2wechat_enabled=_env_flag("WECHAT_MD2WECHAT_ENABLED", wechat_config.md2wechat_enabled),
        md2wechat_api_key=os.getenv("MD2WECHAT_API_KEY", wechat_config.md2wechat_api_key),
        md2wechat_base_url=os.getenv("MD2WECHAT_BASE_URL", wechat_config.md2wechat_base_url),
        md2wechat_theme=os.getenv("MD2WECHAT_THEME", wechat_config.md2wechat_theme),
        md2wechat_font_size=os.getenv("MD2WECHAT_FONT_SIZE", wechat_config.md2wechat_font_size),
        md2wechat_convert_version=os.getenv(
            "MD2WECHAT_CONVERT_VERSION",
            wechat_config.md2wechat_convert_version,
        ),
        md2wechat_cli_path=os.getenv("MD2WECHAT_CLI_PATH", wechat_config.md2wechat_cli_path),
        md2wechat_fallback_to_builtin=_env_flag(
            "MD2WECHAT_FALLBACK_TO_BUILTIN",
            wechat_config.md2wechat_fallback_to_builtin,
        ),
        html_anything_enabled=_env_flag("HTML_ANYTHING_ENABLED", wechat_config.html_anything_enabled),
        html_anything_url=os.getenv("HTML_ANYTHING_URL", wechat_config.html_anything_url),
        html_anything_template=os.getenv("HTML_ANYTHING_TEMPLATE", wechat_config.html_anything_template),
        html_anything_agent=os.getenv("HTML_ANYTHING_AGENT", wechat_config.html_anything_agent),
        serv00_proxy_enabled=_env_flag("WECHAT_SERV00_PROXY_ENABLED", wechat_config.serv00_proxy_enabled),
        serv00_proxy_url=os.getenv("WECHAT_SERV00_PROXY_URL", wechat_config.serv00_proxy_url),
        serv00_proxy_secret=os.getenv("WECHAT_SERV00_PROXY_SECRET", wechat_config.serv00_proxy_secret),
        # Retain the legacy field for config compatibility, but never allow
        # an old .env value, account override, or Secret to re-enable 135.
        editor_135_enabled=False,
        editor_135_base_url=os.getenv("WECHAT_EDITOR_135_BASE_URL", wechat_config.editor_135_base_url),
        editor_135_profile_dir=os.getenv("WECHAT_EDITOR_135_PROFILE_DIR", wechat_config.editor_135_profile_dir),
        builtin_theme=os.getenv("WECHAT_BUILTIN_THEME", wechat_config.builtin_theme),
    )


def _load_image_config(merged_payload: dict[str, Any], image_config: ImageGenerationSettings) -> ImageGenerationSettings:
    return ImageGenerationSettings(
        width=image_config.width,
        height=image_config.height,
        mobile_width=image_config.mobile_width,
        mobile_height=image_config.mobile_height,
        backend=os.getenv("IMAGE_BACKEND", image_config.backend),
        backend_priority=os.getenv("IMAGE_BACKEND_PRIORITY", image_config.backend_priority),
        api_mode=os.getenv("IMAGE_API_MODE", image_config.api_mode),
        model_name=os.getenv("IMAGE_MODEL", image_config.model_name),
        model_base_url=os.getenv("IMAGE_BASE_URL", image_config.model_base_url),
        model_api_key=os.getenv("IMAGE_API_KEY", image_config.model_api_key),
        request_timeout_seconds=float(
            os.getenv("IMAGE_REQUEST_TIMEOUT_SECONDS", str(image_config.request_timeout_seconds))
        ),
        cover_size=os.getenv("IMAGE_COVER_SIZE", image_config.cover_size),
        infographic_size=os.getenv("IMAGE_INFOGRAPHIC_SIZE", image_config.infographic_size),
        max_generated_assets=int(
            os.getenv("IMAGE_MAX_ASSETS", str(image_config.max_generated_assets))
        ),
        quality=os.getenv("IMAGE_QUALITY", image_config.quality),
        style=os.getenv("IMAGE_STYLE", image_config.style),
        background=os.getenv("IMAGE_BACKGROUND", image_config.background),
        fallback_to_renderer=_env_flag("IMAGE_FALLBACK_TO_RENDERER", image_config.fallback_to_renderer),
        theme_name=image_config.theme_name,
        primary_color=image_config.primary_color,
        secondary_color=image_config.secondary_color,
        accent_color=image_config.accent_color,
        background_color=image_config.background_color,
        card_color=image_config.card_color,
        text_color=image_config.text_color,
        muted_text_color=image_config.muted_text_color,
        font_path=image_config.font_path,
        bold_font_path=image_config.bold_font_path,
        export_html=image_config.export_html,
        export_svg=image_config.export_svg,
        export_png=image_config.export_png,
    )

def _load_iteration_overrides(
    project_root: Path,
    account_key: str,
    account_slug: str,
) -> IterationOverrides:
    """从 ``config/iteration_overrides/<account>.json`` 加载迭代调整项。

    账号名可能是 ``robot_notes`` 或 ``robot-notes``，因此同时尝试下划线/连字符
    变体。文件不存在时返回空 ``IterationOverrides``（不影响现有流程）；
    文件格式错误时同样返回空默认值，避免调整项加载失败导致整个配置加载失败。
    """
    base_dir = project_root / "config" / "iteration_overrides"
    candidates: list[str] = []
    seen: set[str] = set()
    for name in (
        account_key,
        account_slug,
        account_key.replace("-", "_"),
        account_key.replace("_", "-"),
    ):
        normalized = str(name or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    for name in candidates:
        path = base_dir / f"{name}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            # 调整项文件格式错误不应导致整个配置加载失败
            import sys
            print(
                f"[WARNING] Failed to load iteration overrides from {path}: {exc}. "
                "Falling back to empty overrides.",
                file=sys.stderr,
            )
            return IterationOverrides()
        if isinstance(payload, dict):
            return IterationOverrides.from_dict(payload)
        return IterationOverrides()
    return IterationOverrides()


def _load_config_payload(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return _default_config_payload()

    text = config_path.read_text(encoding="utf-8").strip()
    if not text:
        return _default_config_payload()

    if yaml is not None:
        payload = yaml.safe_load(text) or {}
    else:
        payload = json.loads(text)

    merged = _default_config_payload()
    _deep_merge(merged, payload)
    return merged


def _load_style_profiles(payload: dict[str, Any]) -> dict[str, StyleProfile]:
    base = _default_style_payloads()
    _deep_merge(base, payload)
    profiles = {key: StyleProfile.from_dict(key, value) for key, value in base.items()}
    if "deep_industry_analysis" not in profiles:
        profiles["deep_industry_analysis"] = StyleProfile.from_dict(
            "deep_industry_analysis",
            _default_style_payloads()["deep_industry_analysis"],
        )
    return profiles


def _load_account_profiles(payload: dict[str, Any]) -> dict[str, AccountProfile]:
    profiles: dict[str, AccountProfile] = {
        DEFAULT_ACCOUNT_KEY: AccountProfile.from_dict(DEFAULT_ACCOUNT_KEY, {})
    }
    if not isinstance(payload, dict):
        return profiles
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        normalized_key = normalize_account_key(str(key))
        profiles[normalized_key] = AccountProfile.from_dict(normalized_key, value)
    return profiles


def _resolve_account_path(
    project_root: Path,
    account_slug: str,
    configured_value: str,
    default_relative: str,
) -> Path:
    raw_value = configured_value.strip() if configured_value else ""
    if not raw_value:
        raw_value = default_relative
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _default_config_payload() -> dict[str, Any]:
    return {
        "model": {
            "name": "gpt-4.1-mini",
            "base_url": "",
            "temperature": 0.7,
        },
        "writing": {
            "article_word_target": 2000,
            "max_input_chars": 16000,
            "default_style": "deep_industry_analysis",
            "title_count": 20,
            "recommended_title_count": 3,
            "cache_similarity_threshold": 0.82,
        },
        "industry": {
            "brand_name": "手蓝数据笔记",
            "focus": "科技商业、AI产业、先进制造（机器人、新能源、智能装备、储能）",
            "platform": "今日头条",
            "audience": "关注科技商业与产业升级的从业者、投资者与决策者",
            "positioning": "聚焦科技商业、AI产业与先进制造，用数据拆热点，用产业链看趋势兑现。",
        },
        "topic_collection": {
            "lookback_days": 2,
            "max_topic_age_hours": 12,
            "drop_topics_without_timestamp": True,
            "max_ranked_items_per_source": 4,
            "max_items_per_source": 24,
            "max_export_topics": 40,
            "request_timeout_seconds": 22,
            "enabled_sources": [
                "toutiao_hot",
                "baidu_hot",
                "36kr",
                "zhidx",
                "ofweek",
                "chuneng_ofweek",
                "newenergy_ofweek",
                "gg_robot",
                "ithome",
                "solidot",
            ],
            "focus_industries": [
                "hot_topic",
                "humanoid_robot",
                "new_energy",
                "energy_storage",
                "ai_hardware",
                "industrial_automation",
            ],
            "keyword_map": {
                "hot_topic": [
                    "热点",
                    "热搜",
                    "热议",
                    "爆火",
                    "刷屏",
                    "官宣",
                    "突发",
                    "通报",
                    "回应",
                    "反转",
                ],
                "humanoid_robot": [
                    "人形机器人",
                    "具身智能",
                    "灵巧手",
                    "减速器",
                    "执行器",
                    "无框力矩电机",
                    "丝杠",
                ],
                "new_energy": [
                    "新能源",
                    "光伏",
                    "风电",
                    "锂电",
                    "电池",
                    "充电",
                    "新能源汽车",
                    "动力电池",
                    "固态电池",
                    "钠电",
                    "充换电",
                    "光储",
                    "电池回收",
                ],
                "energy_storage": [
                    "储能",
                    "工商业储能",
                    "PCS",
                    "EMS",
                    "逆变器",
                    "电网侧储能",
                    "储能电站",
                    "储能电池",
                    "大储",
                    "户储",
                    "抽水蓄能",
                    "液流电池",
                    "源网荷储",
                    "构网型储能",
                ],
                "ai_hardware": [
                    "AI硬件",
                    "算力",
                    "服务器",
                    "GPU",
                    "芯片",
                    "边缘计算",
                    "CPO",
                ],
                "industrial_automation": [
                    "工业自动化",
                    "工业机器人",
                    "机器视觉",
                    "PLC",
                    "伺服",
                    "传感器",
                    "工业母机",
                ],
            },
            "sources": {
                "toutiao_hot": {
                    "type": "hot_board",
                    "urls": [
                        "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                    ],
                },
                "baidu_hot": {
                    "type": "baidu_hot",
                    "urls": [
                        "https://top.baidu.com/board?tab=realtime",
                    ],
                },
                "ithome": {
                    "type": "rss",
                    "urls": [
                        "https://www.ithome.com/rss/",
                    ],
                },
                "solidot": {
                    "type": "rss",
                    "urls": [
                        "https://www.solidot.org/index.rss",
                    ],
                },
                "36kr": {
                    "type": "rss",
                    "urls": [
                        "https://36kr.com/feed-article",
                        "https://36kr.com/feed-newsflash",
                    ],
                },
                "zhidx": {
                    "type": "html",
                    "urls": [
                        "https://www.zhidx.com/",
                        "https://www.zhidx.com/p/category/%E6%9C%BA%E5%99%A8%E4%BA%BA%E6%97%A0%E4%BA%BA%E6%9C%BA",
                        "https://www.zhidx.com/p/category/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD",
                    ],
                },
                "ofweek": {
                    "type": "html",
                    "urls": [
                        "https://robot.ofweek.com/CATList-8321200-8100-robot.html",
                        "https://robot.ofweek.com/CAT-898890-humanoidRobot.html",
                        "https://chuneng.ofweek.com/",
                        "https://nev.ofweek.com/",
                        "https://www.ofweek.com/ai/",
                    ],
                },
                "gg_robot": {
                    "type": "html",
                    "urls": [
                        "https://www.gg-robot.com/",
                        "https://www.gg-robot.com/meeting.html",
                        "https://www.gg-robot.com/home/aggregator/?act=report",
                    ],
                },
                "chuneng_ofweek": {
                    "type": "html",
                    "same_domain_only": True,
                    "urls": [
                        "https://chuneng.ofweek.com/",
                    ],
                },
                "newenergy_ofweek": {
                    "type": "html",
                    "same_domain_only": True,
                    "urls": [
                        "https://nev.ofweek.com/",
                    ],
                },
            },
        },
        "image_generation": {
            "width": 1280,
            "height": 720,
            "mobile_width": 1080,
            "mobile_height": 1920,
            "backend": "image2",
            "api_mode": "images",
            "model_name": "gpt-image-2",
            "model_base_url": "",
            "model_api_key": "",
            "cover_size": "1536x1024",
            "infographic_size": "1024x1536",
            "max_generated_assets": 2,
            "quality": "high",
            "style": "vivid",
            "background": "opaque",
            "fallback_to_renderer": True,
            "theme_name": "dark_tech",
            "primary_color": "#0B1220",
            "secondary_color": "#122238",
            "accent_color": "#26D9FF",
            "background_color": "#060B14",
            "card_color": "#0F1B2D",
            "text_color": "#F5F8FF",
            "muted_text_color": "#91A4C2",
            "font_path": resolve_default_font_path("regular"),
            "bold_font_path": resolve_default_font_path("bold"),
            "export_html": True,
            "export_svg": True,
            "export_png": True,
        },
        "automation": {
            "default_topic_rank": 1,
            "max_daily_topic_attempts": 5,
            "daily_run_times": [],
            "weekend_run_times": [],
            "archive_outputs": True,
            "archive_dirname": "runs",
            "selected_topic_basename": "selected_topic",
        },
        "review": {
            "auto_prepare": True,
            "min_overall_score": 72,
            "max_ai_risk": 45,
            "min_auto_publish_overall_score": 80,
            "max_auto_publish_ai_risk": 40,
            "min_auto_publish_validation_score": 80,
            "min_auto_publish_readiness_score": 70,
            "hot_topic_min_auto_publish_overall_score": 65,
            "hot_topic_max_auto_publish_ai_risk": 50,
            "hot_topic_min_auto_publish_readiness_score": 50,
            "publish_dirname": "publish",
        },
        "publishing": {
            "enabled": False,
            "auto_publish": False,
            "platform": "toutiao",
            "base_url": "https://mp.toutiao.com/",
            "publish_url": "",
            "user_data_dir": ".runtime/toutiao_profile",
            "channel": "msedge",
            "executable_path": "",
            "headless": False,
            "wait_timeout_ms": 15000,
            "history_filename": "publish_history.json",
            "allow_duplicate_topics": False,
            "title_max_length": 30,
            "cover_mode": "none",
            "inline_images_enabled": True,
            "weitoutiao_enabled": False,
            "weitoutiao_publish_url": "https://mp.toutiao.com/profile_v4/weitoutiao/publish",
            "weitoutiao_daily_target": 1,
            "weitoutiao_standalone_enabled": False,
            "weitoutiao_priority_enabled": False,
            "weitoutiao_local_fallback_enabled": True,
            "weitoutiao_personas": ["hot_comment", "warm"],
            "weitoutiao_selectors": {
                "editor": [
                    "div.ProseMirror",
                    "div[contenteditable='true'][data-placeholder*='微头条']",
                    "div[contenteditable='true'][data-placeholder*='正文']",
                    "div[contenteditable='true']",
                ],
                "publish_button": [
                    "button:has-text('发布')",
                    "button:has-text('发表')",
                    "button:has-text('发布微头条')",
                ],
                "success_indicator": [
                    "text=发布成功",
                    "text=提交成功",
                    "text=审核中",
                    "text=已发布",
                ],
            },
            "selectors": {
                "new_post_button": [
                    "a:has-text('发文')",
                    "button:has-text('发文')",
                    "a:has-text('写文章')",
                    "button:has-text('写文章')",
                    "text=发布文章",
                ],
                "title_input": [
                    "textarea[placeholder*='标题']",
                    "input[placeholder*='标题']",
                    "input[placeholder*='请输入标题']",
                ],
                "article_editor": [
                    "div.ProseMirror",
                    "div[contenteditable='true'][data-placeholder*='正文']",
                    "div[contenteditable='true']",
                ],
                "cover_upload": [
                    "input[type='file'][accept*='image']",
                    "input[type='file']",
                ],
                "publish_button": [
                    "button:has-text('发布')",
                    "button:has-text('确认发布')",
                    "button:has-text('发表')",
                    "text=发布",
                ],
                "confirm_publish_button": [
                    "button:has-text('确认')",
                    "button:has-text('继续发布')",
                    "button:has-text('我知道了')",
                ],
                "success_indicator": [
                    "text=发布成功",
                    "text=提交成功",
                    "text=审核中",
                    "text=已提交",
                ],
            },
        },
        "wechat": {
            "enabled": False,
            "auto_publish": False,
            "toutiao_sync_delay_seconds": 1800,
            "app_id": "",
            "app_secret": "",
            "account_name": "公众号名称",
            "author_name": "手蓝",
            "base_url": "https://mp.weixin.qq.com/",
            "api_base_url": "https://api.weixin.qq.com",
            "api_proxy": "",
            "title_max_length": 64,
            "digest_max_length": 120,
            "history_filename": "wechat_publish_history.json",
            "cover_strategy": "text_cover",
            "layout_backend": "builtin",
            "md2wechat_enabled": False,
            "md2wechat_api_key": "",
            "md2wechat_base_url": "https://www.md2wechat.com/api/v1",
            "md2wechat_theme": "default",
            "md2wechat_font_size": "medium",
            "md2wechat_convert_version": "v1",
            "md2wechat_cli_path": "",
            "md2wechat_fallback_to_builtin": True,
            "html_anything_enabled": False,
            "html_anything_url": "http://localhost:3000",
            "html_anything_template": "",
            "html_anything_agent": "claude",
            "serv00_proxy_enabled": False,
            "serv00_proxy_url": "",
            "serv00_proxy_secret": "",
            "editor_135_enabled": False,
            "editor_135_base_url": "https://www.135editor.com",
            "editor_135_profile_dir": ".runtime/editor_135_profile",
        },
        "styles": _default_style_payloads(),
        "enhanced_insights": {
            "enabled": False,
            "multi_source": False,
            "historical": False,
            "data_mining": False,
        },
        "expert_council": {
            "enabled": True,
            "roles": list(DEFAULT_EXPERT_COUNCIL_ROLES),
        },
        "quality_review_council": {
            "enabled": True,
            "roles": list(DEFAULT_QUALITY_REVIEW_COUNCIL_ROLES),
            "auto_revise": True,
            "max_auto_revisions": 1,
            "min_overall_score": 78,
            "min_dimension_score": 65,
        },
    }


def _default_style_payloads() -> dict[str, dict[str, str]]:
    return {
        "deep_industry_analysis": {
            "label": "深度行业分析风",
            "tone": "冷静、专业、强调产业链兑现和环节分化。",
            "article_instruction": "突出产业链位置、订单验证、交付效率、盈利兑现。",
            "title_instruction": "做出判断感和分化感，避免纯新闻播报标题。",
            "image_instruction": "深色科技风，图表感强，突出产业链和数据节点。",
            "opening_instruction": "开头先抛出一个反直觉判断，再给出数据证据。",
        },
        "tech_media": {
            "label": "科技媒体风",
            "tone": "节奏快、信息密度高、兼顾可读性。",
            "article_instruction": "像科技媒体深稿，强调事件与趋势的连接。",
            "title_instruction": "标题兼顾传播性和信息量，避免标题党。",
            "image_instruction": "媒体封面感，主体明确，适合头条点击。",
            "opening_instruction": "前三句快速交代变化、原因和最值得看的点。",
        },
        "finance_report": {
            "label": "财经研报风",
            "tone": "偏研报、偏结论先行、强调数据和盈利模型。",
            "article_instruction": "结构接近券商行业点评，突出量化指标和风险提示。",
            "title_instruction": "标题更像行业判断或投资框架总结。",
            "image_instruction": "图表、数据卡片、深蓝配色，强调专业度。",
            "opening_instruction": "第一句直接下结论，第二句给关键数据，第三句给配置视角。",
        },
        "viral_science": {
            "label": "爆款科普风",
            "tone": "易懂、反差强、适合非专业读者转发收藏。",
            "article_instruction": "用通俗解释复杂技术，但保留关键数字和产业判断。",
            "title_instruction": "标题可有反问、对比、悬念，但不能低质夸张。",
            "image_instruction": "视觉冲击更强，主体更直观，适合移动端封面。",
            "opening_instruction": "前三句要有悬念和反差，让读者愿意继续看。",
        },
        # 面向中老年读者的社会故事/人间观察类账号（大妈文赛道）。
        #
        # ⚠️ 合规是这个风格的第一约束，不是可选项。头条的创作规范里两条红线
        # 直接冲着这个赛道来：
        #   1.「故事诱导营销」——编造与事实不符的故事、以讲述自身经历或社会事件
        #      的形式吸引点击，属于违规。
        #   2.《人工智能生成合成内容标识办法》2025-09-01 起强制 AI 内容打标，
        #      且头条 2026-09 正在专项治理 AI 低质内容与虚构故事。
        # 所以这里的写法是**真实事件 + 观察解读**：素材必须是可追溯的真实报道或
        # 公开事件，文章做分析、对比和给读者的提醒，而不是冒充当事人自述。
        # `article_instruction` 里那两句禁止性要求是硬约束，改动前请先看
        # docs/ 下社会故事赛道的合规说明。
        "family_story": {
            "label": "人间故事观察风",
            "content_domain": "narrative",
            "tone": "讲人话、有生活细节、有共情但不煽情，像一位见多识广的长者在慢慢说事。",
            "article_instruction": (
                "从真实事件里的具体人物处境切入，还原事实、补充背景，再给出对读者的"
                "提醒或判断。禁止编造第一人称亲历故事，禁止虚构不存在的当事人自述；"
                "所有情节必须能追溯到素材来源。"
            ),
            "title_instruction": (
                "用具体的人、具体的选择、具体的结果做标题，让读者一眼看到与自己相关；"
                "不要用悬念和省略号故弄玄虚，不要用极限用语。"
            ),
            "image_instruction": "生活场景感、光线温暖、人物主体清晰，避免强烈的科技与图表风格。",
            "opening_instruction": "开头先给一个具体的生活场景或一句实在话，让读者立刻代入。",
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_flag_scoped(name: str, account_key: str | None, default: bool) -> bool:
    scoped_value = os.getenv(account_env_name(name, account_key))
    if scoped_value is not None:
        return scoped_value.strip().lower() in {"1", "true", "yes", "on"}
    return _env_flag(name, default)
