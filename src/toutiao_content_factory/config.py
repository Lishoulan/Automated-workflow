from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
ACCOUNT_META_KEYS = {"label", "input_dir", "output_dir", "logs_dir", "cache_dir", "browser_profile_dir"}
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


@dataclass(frozen=True)
class TopicCollectionSettings:
    lookback_days: int
    max_topic_age_hours: int
    drop_topics_without_timestamp: bool
    max_ranked_items_per_source: int
    max_items_per_source: int
    max_export_topics: int
    request_timeout_seconds: int
    enabled_sources: list[str]
    focus_industries: list[str]
    keyword_map: dict[str, list[str]]
    source_configs: dict[str, dict[str, Any]]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TopicCollectionSettings":
        return cls(
            lookback_days=int(payload.get("lookback_days", 7)),
            max_topic_age_hours=int(payload.get("max_topic_age_hours", 72)),
            drop_topics_without_timestamp=bool(payload.get("drop_topics_without_timestamp", False)),
            max_ranked_items_per_source=int(payload.get("max_ranked_items_per_source", 4)),
            max_items_per_source=int(payload.get("max_items_per_source", 12)),
            max_export_topics=int(payload.get("max_export_topics", 20)),
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
    api_mode: str
    model_name: str
    model_base_url: str
    model_api_key: str
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
            backend=str(payload.get("backend", "renderer")),
            api_mode=str(payload.get("api_mode", "images")),
            model_name=str(payload.get("model_name", "gpt-image-1")),
            model_base_url=str(payload.get("model_base_url", "")),
            model_api_key=str(payload.get("model_api_key", "")),
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
            font_path=str(payload.get("font_path", "C:/Windows/Fonts/msyh.ttc")),
            bold_font_path=str(payload.get("bold_font_path", "C:/Windows/Fonts/msyhbd.ttc")),
            export_html=bool(payload.get("export_html", True)),
            export_svg=bool(payload.get("export_svg", True)),
            export_png=bool(payload.get("export_png", True)),
        )


@dataclass(frozen=True)
class AutomationSettings:
    default_topic_rank: int
    archive_outputs: bool
    archive_dirname: str
    selected_topic_basename: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AutomationSettings":
        return cls(
            default_topic_rank=int(payload.get("default_topic_rank", 1)),
            archive_outputs=bool(payload.get("archive_outputs", True)),
            archive_dirname=str(payload.get("archive_dirname", "runs")),
            selected_topic_basename=str(payload.get("selected_topic_basename", "selected_topic")),
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
        )

    def qualifies_for_auto_publish(
        self,
        overall_score: int,
        ai_risk: int,
        validation_score: int,
        publish_readiness_score: int,
    ) -> bool:
        return (
            overall_score >= self.min_auto_publish_overall_score
            and ai_risk <= self.max_auto_publish_ai_risk
            and validation_score >= self.min_auto_publish_validation_score
            and publish_readiness_score >= self.min_auto_publish_readiness_score
        )


@dataclass(frozen=True)
class PublishSettings:
    enabled: bool
    auto_publish: bool
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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishSettings":
        selectors = {
            str(key): [str(item) for item in value]
            for key, value in dict(payload.get("selectors", {})).items()
        }
        return cls(
            enabled=bool(payload.get("enabled", False)),
            auto_publish=bool(payload.get("auto_publish", False)),
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
        )


@dataclass(frozen=True)
class AccountProfile:
    key: str
    slug: str
    label: str
    input_dir: str
    output_dir: str
    logs_dir: str
    cache_dir: str
    browser_profile_dir: str
    overrides: dict[str, Any]

    @classmethod
    def from_dict(cls, key: str, payload: dict[str, Any]) -> "AccountProfile":
        slug = sanitize_account_slug(key)
        return cls(
            key=normalize_account_key(key),
            slug=slug,
            label=str(payload.get("label", key)),
            input_dir=str(payload.get("input_dir", "")).strip(),
            output_dir=str(payload.get("output_dir", "")).strip(),
            logs_dir=str(payload.get("logs_dir", "")).strip(),
            cache_dir=str(payload.get("cache_dir", "")).strip(),
            browser_profile_dir=str(payload.get("browser_profile_dir", "")).strip(),
            overrides={str(name): copy.deepcopy(value) for name, value in payload.items() if str(name) not in ACCOUNT_META_KEYS},
        )


@dataclass(frozen=True)
class StyleProfile:
    key: str
    label: str
    tone: str
    article_instruction: str
    title_instruction: str
    image_instruction: str
    opening_instruction: str

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
    account_profiles: dict[str, AccountProfile]
    raw_account_profiles: dict[str, dict[str, Any]]
    available_account_keys: list[str]
    fast_mode: bool = False
    skip_image_generation: bool = False
    skip_review_agent: bool = False
    skip_publish_review: bool = False

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
    def account_runtime_dir(self) -> Path:
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
    load_dotenv(project_root / ".env")

    config_path = project_root / "config.yaml"
    config_payload = _load_config_payload(config_path)
    styles = _load_style_profiles(config_payload.get("styles", {}))
    raw_account_profiles = _load_account_profiles(config_payload.get("accounts", {}))
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

    model_config = merged_payload.get("model", {})
    writing_config = merged_payload.get("writing", {})
    industry_config = merged_payload.get("industry", {})
    topic_config = TopicCollectionSettings.from_dict(merged_payload.get("topic_collection", {}))
    image_config = ImageGenerationSettings.from_dict(merged_payload.get("image_generation", {}))
    automation_config = AutomationSettings.from_dict(merged_payload.get("automation", {}))
    review_config = ReviewSettings.from_dict(merged_payload.get("review", {}))
    publishing_config = PublishSettings.from_dict(merged_payload.get("publishing", {}))

    default_style = os.getenv("DEFAULT_STYLE", str(writing_config.get("default_style", "deep_industry_analysis")))
    is_default_account = account_profile.key == DEFAULT_ACCOUNT_KEY
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

    return AppSettings(
        project_root=project_root,
        config_path=config_path,
        account_key=account_profile.key,
        account_slug=account_profile.slug,
        account_label=account_profile.label,
        is_default_account=is_default_account,
        input_dir=input_dir,
        output_dir=output_dir,
        prompts_dir=project_root / "prompts",
        logs_dir=logs_dir,
        cache_dir=cache_dir,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_base_url=os.getenv("OPENAI_BASE_URL") or str(model_config.get("base_url", "")).strip() or None,
        openai_model=os.getenv("OPENAI_MODEL", str(model_config.get("name", "gpt-4.1-mini"))),
        temperature=float(os.getenv("OPENAI_TEMPERATURE", str(model_config.get("temperature", 0.7)))),
        max_input_chars=int(os.getenv("MAX_INPUT_CHARS", str(writing_config.get("max_input_chars", 16000)))),
        article_word_target=int(
            os.getenv("ARTICLE_WORD_TARGET", str(writing_config.get("article_word_target", 1200)))
        ),
        brand_name=os.getenv("BRAND_NAME", str(industry_config.get("brand_name", "手蓝数据笔记"))),
        industry_focus=os.getenv(
            "INDUSTRY_FOCUS",
            str(industry_config.get("focus", "新能源、机器人、智能装备、储能")),
        ),
        audience=os.getenv(
            "TARGET_AUDIENCE",
            str(industry_config.get("audience", "科技行业从业者与关注产业升级的读者")),
        ),
        platform=os.getenv("TARGET_PLATFORM", str(industry_config.get("platform", "今日头条"))),
        logging_level=os.getenv("LOG_LEVEL", "INFO"),
        default_style=default_style if default_style in styles else "deep_industry_analysis",
        title_count=int(os.getenv("TITLE_COUNT", str(writing_config.get("title_count", 20)))),
        recommended_title_count=int(
            os.getenv("RECOMMENDED_TITLE_COUNT", str(writing_config.get("recommended_title_count", 3)))
        ),
        cache_similarity_threshold=float(
            os.getenv(
                "CACHE_SIMILARITY_THRESHOLD",
                str(writing_config.get("cache_similarity_threshold", 0.82)),
            )
        ),
        style_profiles=styles,
        topic_collection=topic_config,
        image_generation=ImageGenerationSettings(
            width=image_config.width,
            height=image_config.height,
            mobile_width=image_config.mobile_width,
            mobile_height=image_config.mobile_height,
            backend=os.getenv("IMAGE_BACKEND", image_config.backend),
            api_mode=os.getenv("IMAGE_API_MODE", image_config.api_mode),
            model_name=os.getenv("IMAGE_MODEL", image_config.model_name),
            model_base_url=os.getenv("IMAGE_BASE_URL", image_config.model_base_url),
            model_api_key=os.getenv("IMAGE_API_KEY", image_config.model_api_key),
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
        ),
        automation=automation_config,
        review=ReviewSettings(
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
        ),
        publishing=PublishSettings(
            enabled=_env_flag("TOUTIAO_PUBLISH_ENABLED", publishing_config.enabled),
            auto_publish=_env_flag("TOUTIAO_AUTO_PUBLISH", publishing_config.auto_publish),
            platform=os.getenv("TOUTIAO_PUBLISH_PLATFORM", publishing_config.platform),
            base_url=os.getenv("TOUTIAO_PUBLISH_BASE_URL", publishing_config.base_url),
            publish_url=os.getenv("TOUTIAO_PUBLISH_URL", publishing_config.publish_url),
            user_data_dir=browser_profile_value,
            channel=os.getenv("TOUTIAO_PUBLISH_CHANNEL", publishing_config.channel),
            executable_path=os.getenv("TOUTIAO_BROWSER_EXECUTABLE", publishing_config.executable_path),
            headless=_env_flag("TOUTIAO_PUBLISH_HEADLESS", publishing_config.headless),
            wait_timeout_ms=int(
                os.getenv("TOUTIAO_PUBLISH_TIMEOUT_MS", str(publishing_config.wait_timeout_ms))
            ),
            history_filename=publish_history_value,
            allow_duplicate_topics=_env_flag(
                "TOUTIAO_ALLOW_DUPLICATE_TOPICS",
                publishing_config.allow_duplicate_topics,
            ),
            title_max_length=int(
                os.getenv("TOUTIAO_TITLE_MAX_LENGTH", str(publishing_config.title_max_length))
            ),
            cover_mode=os.getenv("TOUTIAO_PUBLISH_COVER_MODE", publishing_config.cover_mode),
            selectors=publishing_config.selectors,
        ),
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
        fast_mode=_env_flag("TOUTIAO_FAST_MODE", False),
        skip_image_generation=_env_flag("TOUTIAO_SKIP_IMAGE_GENERATION", False),
        skip_review_agent=_env_flag("TOUTIAO_SKIP_REVIEW_AGENT", False),
        skip_publish_review=_env_flag("TOUTIAO_SKIP_PUBLISH_REVIEW", False),
    )


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
            "article_word_target": 1200,
            "max_input_chars": 16000,
            "default_style": "deep_industry_analysis",
            "title_count": 20,
            "recommended_title_count": 3,
            "cache_similarity_threshold": 0.82,
        },
        "industry": {
            "brand_name": "手蓝数据笔记",
            "focus": "新能源、机器人、智能装备、储能",
            "platform": "今日头条",
            "audience": "科技行业从业者与关注产业升级的读者",
        },
        "topic_collection": {
            "lookback_days": 7,
            "max_topic_age_hours": 72,
            "drop_topics_without_timestamp": False,
            "max_ranked_items_per_source": 4,
            "max_items_per_source": 12,
            "max_export_topics": 20,
            "request_timeout_seconds": 18,
            "enabled_sources": [
                "google_news",
                "36kr",
                "zhidx",
                "ofweek",
                "gg_robot",
            ],
            "focus_industries": [
                "humanoid_robot",
                "new_energy",
                "energy_storage",
                "ai_hardware",
                "industrial_automation",
            ],
            "keyword_map": {
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
                ],
                "energy_storage": [
                    "储能",
                    "工商业储能",
                    "PCS",
                    "EMS",
                    "逆变器",
                    "电网侧储能",
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
                "google_news": {
                    "type": "google_rss",
                    "urls": [
                        "https://news.google.com/rss/search?q=%E4%BA%BA%E5%BD%A2%E6%9C%BA%E5%99%A8%E4%BA%BA&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
                        "https://news.google.com/rss/search?q=%E6%96%B0%E8%83%BD%E6%BA%90&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
                        "https://news.google.com/rss/search?q=%E5%82%A8%E8%83%BD&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
                        "https://news.google.com/rss/search?q=AI%E7%A1%AC%E4%BB%B6&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
                        "https://news.google.com/rss/search?q=%E5%B7%A5%E4%B8%9A%E8%87%AA%E5%8A%A8%E5%8C%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
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
            },
        },
        "image_generation": {
            "width": 1280,
            "height": 720,
            "mobile_width": 1080,
            "mobile_height": 1920,
            "backend": "renderer",
            "api_mode": "images",
            "model_name": "gpt-image-1",
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
            "font_path": "C:/Windows/Fonts/msyh.ttc",
            "bold_font_path": "C:/Windows/Fonts/msyhbd.ttc",
            "export_html": True,
            "export_svg": True,
            "export_png": True,
        },
        "automation": {
            "default_topic_rank": 1,
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
        "styles": _default_style_payloads(),
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
