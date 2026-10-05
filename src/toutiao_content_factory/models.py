from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SourceDocument:
    source_type: str
    content: str
    path: Path | None = None
    title: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    merged_text: str
    core_data: list[str]
    industry_trends: list[str]
    growth_numbers: list[str]
    companies: list[str]
    supply_chain_keywords: list[str]
    key_points: list[str]
    focus_industry: str = ""
    focus_keywords: list[str] = field(default_factory=list)
    focus_companies: list[str] = field(default_factory=list)
    topic_title: str = ""
    selection_reason: str = ""
    suggested_angle: str = ""
    follow_up_focus: str = ""
    writing_brief: str = ""
    topic_type: str = ""
    topic_type_label: str = ""
    content_format: str = ""
    content_format_label: str = ""
    format_brief: str = ""
    paragraph_instruction: str = ""
    evidence_strength: int = 0
    topic_quality_score: int = 0
    topic_quality_flags: list[str] = field(default_factory=list)
    word_target: int = 0
    evidence_items: list[dict[str, str]] = field(default_factory=list)
    source_summaries: list[dict[str, str]] = field(default_factory=list)
    # 实时热榜二创标记：来自头条热榜/百度热搜的标题型话题，放宽证据型质量门禁
    is_hot_topic: bool = False
    # 模块 C 合并新增：演进规则提取结果（向下兼容，旧序列化文件无此字段时默认空列表）
    extracted_rules: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractionResult":
        return cls(
            merged_text=str(payload.get("merged_text", "")),
            core_data=list(payload.get("core_data", [])),
            industry_trends=list(payload.get("industry_trends", [])),
            growth_numbers=list(payload.get("growth_numbers", [])),
            companies=list(payload.get("companies", [])),
            supply_chain_keywords=list(payload.get("supply_chain_keywords", [])),
            key_points=list(payload.get("key_points", [])),
            focus_industry=str(payload.get("focus_industry", "")),
            focus_keywords=list(payload.get("focus_keywords", [])),
            focus_companies=list(payload.get("focus_companies", [])),
            topic_title=str(payload.get("topic_title", "")),
            selection_reason=str(payload.get("selection_reason", "")),
            suggested_angle=str(payload.get("suggested_angle", "")),
            follow_up_focus=str(payload.get("follow_up_focus", "")),
            writing_brief=str(payload.get("writing_brief", "")),
            topic_type=str(payload.get("topic_type", "")),
            topic_type_label=str(payload.get("topic_type_label", "")),
            content_format=str(payload.get("content_format", "")),
            content_format_label=str(payload.get("content_format_label", "")),
            format_brief=str(payload.get("format_brief", "")),
            paragraph_instruction=str(payload.get("paragraph_instruction", "")),
            evidence_strength=int(payload.get("evidence_strength", 0)),
            topic_quality_score=int(payload.get("topic_quality_score", 0)),
            topic_quality_flags=list(payload.get("topic_quality_flags", [])),
            word_target=int(payload.get("word_target", 0)),
            evidence_items=list(payload.get("evidence_items", [])),
            source_summaries=list(payload.get("source_summaries", [])),
            is_hot_topic=bool(payload.get("is_hot_topic", False)),
            extracted_rules=list(payload.get("extracted_rules", [])),
        )


@dataclass
class TrendReport:
    hot_trends: list[str] = field(default_factory=list)
    platform_topics: list[str] = field(default_factory=list)
    audience_hooks: list[str] = field(default_factory=list)
    recommended_angle: str = ""
    timing_judgment: str = ""
    competitive_edges: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TrendReport":
        return cls(
            hot_trends=list(payload.get("hot_trends", [])),
            platform_topics=list(payload.get("platform_topics", [])),
            audience_hooks=list(payload.get("audience_hooks", [])),
            recommended_angle=str(payload.get("recommended_angle", "")),
            timing_judgment=str(payload.get("timing_judgment", "")),
            competitive_edges=list(payload.get("competitive_edges", [])),
        )


@dataclass
class DataReport:
    core_data: list[str] = field(default_factory=list)
    industry_trends: list[str] = field(default_factory=list)
    growth_numbers: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    supply_chain_keywords: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)
    watch_metrics: list[str] = field(default_factory=list)
    evidence_notes: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    focus_industry: str = ""
    focus_keywords: list[str] = field(default_factory=list)
    focus_companies: list[str] = field(default_factory=list)
    topic_title: str = ""
    selection_reason: str = ""
    suggested_angle: str = ""
    follow_up_focus: str = ""
    writing_brief: str = ""
    topic_type: str = ""
    topic_type_label: str = ""
    content_format: str = ""
    content_format_label: str = ""
    format_brief: str = ""
    paragraph_instruction: str = ""
    evidence_strength: int = 0
    topic_quality_score: int = 0
    topic_quality_flags: list[str] = field(default_factory=list)
    word_target: int = 0
    is_hot_topic: bool = False
    # Clean, source-derived sentences retained for deterministic generation
    # when the model provider is unavailable.
    source_facts: list[str] = field(default_factory=list)

    # Attribution is context, not additional factual evidence or a score boost.
    source_references: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DataReport":
        return cls(
            core_data=list(payload.get("core_data", [])),
            industry_trends=list(payload.get("industry_trends", [])),
            growth_numbers=list(payload.get("growth_numbers", [])),
            companies=list(payload.get("companies", [])),
            supply_chain_keywords=list(payload.get("supply_chain_keywords", [])),
            key_points=list(payload.get("key_points", [])),
            watch_metrics=list(payload.get("watch_metrics", [])),
            evidence_notes=list(payload.get("evidence_notes", [])),
            risk_flags=list(payload.get("risk_flags", [])),
            focus_industry=str(payload.get("focus_industry", "")),
            focus_keywords=list(payload.get("focus_keywords", [])),
            focus_companies=list(payload.get("focus_companies", [])),
            topic_title=str(payload.get("topic_title", "")),
            selection_reason=str(payload.get("selection_reason", "")),
            suggested_angle=str(payload.get("suggested_angle", "")),
            follow_up_focus=str(payload.get("follow_up_focus", "")),
            writing_brief=str(payload.get("writing_brief", "")),
            topic_type=str(payload.get("topic_type", "")),
            topic_type_label=str(payload.get("topic_type_label", "")),
            content_format=str(payload.get("content_format", "")),
            content_format_label=str(payload.get("content_format_label", "")),
            format_brief=str(payload.get("format_brief", "")),
            paragraph_instruction=str(payload.get("paragraph_instruction", "")),
            evidence_strength=int(payload.get("evidence_strength", 0)),
            topic_quality_score=int(payload.get("topic_quality_score", 0)),
            topic_quality_flags=list(payload.get("topic_quality_flags", [])),
            word_target=int(payload.get("word_target", 0)),
            is_hot_topic=bool(payload.get("is_hot_topic", False)),
            source_facts=list(payload.get("source_facts", [])),
            source_references=list(payload.get("source_references", [])),
        )


@dataclass
class ContentScore:
    overall_score: int = 0
    title_attraction: int = 0
    information_density: int = 0
    save_potential: int = 0
    completion_rate_potential: int = 0
    ai_generated_risk: int = 0
    reasoning: list[str] = field(default_factory=list)
    revision_suggestions: list[str] = field(default_factory=list)
    pass_status: str = "draft"
    publish_readiness_score: int = 0
    publish_risk_flags: list[str] = field(default_factory=list)
    publish_recommendation: str = "manual_review"
    publish_notes: list[str] = field(default_factory=list)
    # 模块 B 合并新增：内容守卫标记（向下兼容，旧序列化文件无此字段时默认空列表）
    guard_flags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContentScore":
        return cls(
            overall_score=int(payload.get("overall_score", 0)),
            title_attraction=int(payload.get("title_attraction", 0)),
            information_density=int(payload.get("information_density", 0)),
            save_potential=int(payload.get("save_potential", 0)),
            completion_rate_potential=int(payload.get("completion_rate_potential", 0)),
            ai_generated_risk=int(payload.get("ai_generated_risk", 0)),
            reasoning=list(payload.get("reasoning", [])),
            revision_suggestions=list(payload.get("revision_suggestions", [])),
            pass_status=str(payload.get("pass_status", "draft")),
            publish_readiness_score=int(payload.get("publish_readiness_score", 0)),
            publish_risk_flags=list(payload.get("publish_risk_flags", [])),
            publish_recommendation=str(payload.get("publish_recommendation", "manual_review")),
            publish_notes=list(payload.get("publish_notes", [])),
            guard_flags=list(payload.get("guard_flags", [])),
        )


@dataclass
class GeneratedContent:
    article: str = ""
    polished_article: str = ""
    summary: str = ""
    comment_prompt: str = ""
    article_angle: str = ""
    style_name: str = ""
    titles: list[str] = field(default_factory=list)
    recommended_titles: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    recommended_topics: list[str] = field(default_factory=list)
    cover_prompt: str = ""
    infographic_prompts: list[str] = field(default_factory=list)
    opening_lines: list[str] = field(default_factory=list)
    optimized_opening_lines: list[str] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)
    cache_note: str = ""
    validation_notes: list[str] = field(default_factory=list)
    validation_score: int = 0
    content_format: str = ""
    content_format_label: str = ""
    # 2026-09-17（D1/D2）：最终标题来自哪一层池（model / fallback / emergency），
    # 以及那次标题选择的**结构化**校验结果（status / pass_count / reason_summary …）。
    # 只加这两个字段，不再另建一套平行的统计结构。
    selected_title_source: str = ""
    title_validation: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GeneratedContent":
        return cls(
            article=str(payload.get("article", "")),
            polished_article=str(payload.get("polished_article", "")),
            summary=str(payload.get("summary", "")),
            comment_prompt=str(payload.get("comment_prompt", "")),
            article_angle=str(payload.get("article_angle", "")),
            style_name=str(payload.get("style_name", "")),
            titles=list(payload.get("titles", [])),
            recommended_titles=list(payload.get("recommended_titles", [])),
            tags=list(payload.get("tags", [])),
            recommended_topics=list(payload.get("recommended_topics", [])),
            cover_prompt=str(payload.get("cover_prompt", "")),
            infographic_prompts=list(payload.get("infographic_prompts", [])),
            opening_lines=list(payload.get("opening_lines", [])),
            optimized_opening_lines=list(payload.get("optimized_opening_lines", [])),
            review_notes=list(payload.get("review_notes", [])),
            cache_note=str(payload.get("cache_note", "")),
            validation_notes=list(payload.get("validation_notes", [])),
            validation_score=int(payload.get("validation_score", 0)),
            content_format=str(payload.get("content_format", "")),
            content_format_label=str(payload.get("content_format_label", "")),
            selected_title_source=str(payload.get("selected_title_source", "") or ""),
            title_validation=dict(payload.get("title_validation") or {}),
        )


@dataclass
class WorkflowResult:
    extraction: ExtractionResult
    trend_report: TrendReport
    data_report: DataReport
    content: GeneratedContent
    content_score: ContentScore
    style_key: str
    style_label: str
    cache_key: str
    cache_status: str
    model_used: str
    created_at: str
    estimated_cost: float = 0.0
    content_id: str = ""
    commercial_intent: str = "medium"
    content_mix: str = "professional"
    cta_variant: str = ""
    cta_source_code: str = ""
    title_variant: str = ""
    cover_variant: str = ""
    platform_variant: str = ""
    topic_bucket: str = ""
    editorial_council: EditorialCouncilReport | None = None
    quality_review_council: QualityReviewReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkflowResult":
        result = cls(
            extraction=ExtractionResult.from_dict(payload.get("extraction", {})),
            trend_report=TrendReport.from_dict(payload.get("trend_report", {})),
            data_report=DataReport.from_dict(payload.get("data_report", {})),
            content=GeneratedContent.from_dict(payload.get("content", {})),
            content_score=ContentScore.from_dict(payload.get("content_score", {})),
            style_key=str(payload.get("style_key", "")),
            style_label=str(payload.get("style_label", "")),
            cache_key=str(payload.get("cache_key", "")),
            cache_status=str(payload.get("cache_status", "miss")),
            model_used=str(payload.get("model_used", "")),
            created_at=str(payload.get("created_at", "")),
            estimated_cost=float(payload.get("estimated_cost", 0.0) or 0.0),
            content_id=str(payload.get("content_id", "")),
            commercial_intent=str(payload.get("commercial_intent", "medium")),
            content_mix=str(payload.get("content_mix", "professional")),
            cta_variant=str(payload.get("cta_variant", "")),
            cta_source_code=str(payload.get("cta_source_code", "")),
            title_variant=str(payload.get("title_variant", "")),
            cover_variant=str(payload.get("cover_variant", "")),
            platform_variant=str(payload.get("platform_variant", "")),
            topic_bucket=str(payload.get("topic_bucket", "")),
        )
        if isinstance(payload.get("editorial_council"), dict):
            result.editorial_council = EditorialCouncilReport.from_dict(payload["editorial_council"])
        if isinstance(payload.get("quality_review_council"), dict):
            result.quality_review_council = QualityReviewReport.from_dict(payload["quality_review_council"])
        return result


@dataclass
class ArticlePerformance:
    url: str
    title: str
    publish_date: str
    views: int  # 阅读量（真正的浏览量，头条管理页"阅读"字段）
    interactions: int
    roi_score: float
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    impression_count: int = 0  # 展现量（头条管理页"展现"字段，推荐曝光次数）
    # Compatibility fields used by the feedback and growth pipelines. Keep
    # ``views`` as the canonical source value for older persisted records.
    read_count: int = 0
    ctr: float = 0.0
    platform_revenue: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArticlePerformance":
        like_count = int(payload.get("like_count", 0) or 0)
        comment_count = int(payload.get("comment_count", 0) or 0)
        share_count = int(payload.get("share_count", 0) or 0)
        interactions_raw = payload.get("interactions")
        if interactions_raw is None or interactions_raw == 0:
            interactions = like_count + comment_count + share_count
        else:
            interactions = int(interactions_raw or 0)
        views = int(payload.get("views", payload.get("read_count", 0)) or 0)
        impression_count = int(payload.get("impression_count", 0) or 0)
        read_count = int(payload.get("read_count", views) or 0)
        ctr_raw = payload.get("ctr")
        ctr = float(ctr_raw or 0.0)
        if ctr <= 0 and impression_count > 0 and read_count > 0:
            ctr = round(read_count * 100 / impression_count, 2)
        return cls(
            url=str(payload.get("url", "")),
            title=str(payload.get("title", "")),
            publish_date=str(payload.get("publish_date", "")),
            views=views,
            interactions=interactions,
            roi_score=float(payload.get("roi_score", 0.0) or 0.0),
            like_count=like_count,
            comment_count=comment_count,
            share_count=share_count,
            impression_count=impression_count,
            read_count=read_count,
            ctr=ctr,
            platform_revenue=(
                float(payload.get("platform_revenue"))
                if payload.get("platform_revenue") is not None
                else None
            ),
        )


@dataclass
class PublishRecord:
    published_at: str
    platform: str
    title: str
    run_dir: str
    topic_title: str
    topic_url: str
    status: str
    notes: list[str] = field(default_factory=list)
    external_url: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishRecord":
        return cls(
            published_at=str(payload.get("published_at", "")),
            platform=str(payload.get("platform", "")),
            title=str(payload.get("title", "")),
            run_dir=str(payload.get("run_dir", "")),
            topic_title=str(payload.get("topic_title", "")),
            topic_url=str(payload.get("topic_url", "")),
            status=str(payload.get("status", "")),
            notes=list(payload.get("notes", [])),
            external_url=str(payload.get("external_url", "")),
        )


@dataclass
class CacheContext:
    status: str = "miss"
    cache_key: str = ""
    matched_key: str = ""
    similarity: float = 0.0
    avoid_titles: list[str] = field(default_factory=list)
    avoid_angle: str = ""
    cached_result: WorkflowResult | None = None
    note: str = ""

    @property
    def exact_hit(self) -> bool:
        return self.status == "exact_hit"

    @property
    def similar_hit(self) -> bool:
        return self.status == "similar_hit"


@dataclass
class TopicCandidate:
    title: str
    url: str
    source: str
    source_type: str
    published_at: str = ""
    snippet: str = ""
    summary: str = ""
    matched_industries: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    extracted_numbers: list[str] = field(default_factory=list)
    heat_value: int = 0  # 平台原始热度值（头条热榜 HotValue / 百度热搜 hotScore）
    heat_score: int = 0
    read_score: int = 0
    save_score: int = 0
    discussion_score: int = 0
    total_score: int = 0
    title_potential_score: int = 0
    title_playbook_track: str = ""
    title_potential_reasons: list[str] = field(default_factory=list)
    is_high_read: bool = False
    is_high_save: bool = False
    is_high_discussion: bool = False
    topic_type: str = ""
    topic_type_label: str = ""
    evidence_strength: int = 0
    quality_score: int = 0
    quality_flags: list[str] = field(default_factory=list)
    recommended_format: str = ""
    recommended_format_label: str = ""
    rank: int = 0
    commercial_intent: str = "medium"
    content_mix: str = "professional"
    cta_variant: str = ""
    cta_source_code: str = ""
    title_variant: str = ""
    cover_variant: str = ""
    platform_variant: str = ""
    topic_bucket: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TopicCandidate":
        return cls(
            title=str(payload.get("title", "")),
            url=str(payload.get("url", "")),
            source=str(payload.get("source", "")),
            source_type=str(payload.get("source_type", "")),
            published_at=str(payload.get("published_at", "")),
            snippet=str(payload.get("snippet", "")),
            summary=str(payload.get("summary", "")),
            matched_industries=list(payload.get("matched_industries", [])),
            matched_keywords=list(payload.get("matched_keywords", [])),
            extracted_numbers=list(payload.get("extracted_numbers", [])),
            heat_value=int(payload.get("heat_value", 0) or 0),
            heat_score=int(payload.get("heat_score", 0)),
            read_score=int(payload.get("read_score", 0)),
            save_score=int(payload.get("save_score", 0)),
            discussion_score=int(payload.get("discussion_score", 0)),
            total_score=int(payload.get("total_score", 0)),
            title_potential_score=int(payload.get("title_potential_score", 0)),
            title_playbook_track=str(payload.get("title_playbook_track", "")),
            title_potential_reasons=list(payload.get("title_potential_reasons", [])),
            is_high_read=bool(payload.get("is_high_read", False)),
            is_high_save=bool(payload.get("is_high_save", False)),
            is_high_discussion=bool(payload.get("is_high_discussion", False)),
            topic_type=str(payload.get("topic_type", "")),
            topic_type_label=str(payload.get("topic_type_label", "")),
            evidence_strength=int(payload.get("evidence_strength", 0)),
            quality_score=int(payload.get("quality_score", 0)),
            quality_flags=list(payload.get("quality_flags", [])),
            recommended_format=str(payload.get("recommended_format", "")),
            recommended_format_label=str(payload.get("recommended_format_label", "")),
            rank=int(payload.get("rank", 0)),
            commercial_intent=str(payload.get("commercial_intent", "medium")),
            content_mix=str(payload.get("content_mix", "professional")),
            cta_variant=str(payload.get("cta_variant", "")),
            cta_source_code=str(payload.get("cta_source_code", "")),
            title_variant=str(payload.get("title_variant", "")),
            cover_variant=str(payload.get("cover_variant", "")),
            platform_variant=str(payload.get("platform_variant", "")),
            topic_bucket=str(payload.get("topic_bucket", "")),
        )


@dataclass
class DailyTopicsReport:
    generated_at: str
    lookback_days: int
    max_topic_age_hours: int
    focus_industries: list[str]
    total_raw_items: int
    total_selected_items: int
    topics: list[TopicCandidate] = field(default_factory=list)
    source_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed_sources: list[dict[str, str]] = field(default_factory=list)
    filtered_out: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DailyTopicsReport":
        return cls(
            generated_at=str(payload.get("generated_at", "")),
            lookback_days=int(payload.get("lookback_days", 0)),
            max_topic_age_hours=int(payload.get("max_topic_age_hours", 0)),
            focus_industries=list(payload.get("focus_industries", [])),
            total_raw_items=int(payload.get("total_raw_items", 0)),
            total_selected_items=int(payload.get("total_selected_items", 0)),
            topics=[TopicCandidate.from_dict(item) for item in payload.get("topics", [])],
            source_stats=dict(payload.get("source_stats", {})),
            failed_sources=list(payload.get("failed_sources", [])),
            filtered_out=dict(payload.get("filtered_out", {})),
        )


@dataclass
class VisualMetric:
    label: str
    value: float
    unit: str = ""
    note: str = ""


@dataclass
class VisualBrief:
    cover_title: str
    cover_subtitle: str
    article_title: str
    summary: str
    style_name: str
    cover_prompt_hint: str = ""
    infographic_prompt_hints: list[str] = field(default_factory=list)
    key_metrics: list[VisualMetric] = field(default_factory=list)
    trend_labels: list[str] = field(default_factory=list)
    timeline_points: list[str] = field(default_factory=list)
    chain_nodes: list[str] = field(default_factory=list)
    topic_highlights: list[str] = field(default_factory=list)
    score_snapshot: dict[str, int] = field(default_factory=dict)


@dataclass
class ImageArtifacts:
    cover_png: Path
    infographic_pngs: list[Path]
    cover_svg: Path | None = None
    infographic_svgs: list[Path] = field(default_factory=list)
    html_files: list[Path] = field(default_factory=list)


@dataclass
class InsightReport:
    """深度洞察报告"""
    main_insight: str = ""
    supporting_data: list[str] = field(default_factory=list)
    alternative_angles: list[str] = field(default_factory=list)
    counterarguments: list[str] = field(default_factory=list)
    prediction: str = ""
    confidence_score: float = 0.0
    # Editorial council extensions. Defaults keep older persisted reports compatible.
    causal_chain: list[str] = field(default_factory=list)
    industry_chain: list[str] = field(default_factory=list)
    originality_angle: str = ""
    evidence_gaps: list[str] = field(default_factory=list)
    risk_boundaries: list[str] = field(default_factory=list)
    expert_perspectives: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InsightReport":
        return cls(
            main_insight=str(payload.get("main_insight", "")),
            supporting_data=list(payload.get("supporting_data", [])),
            alternative_angles=list(payload.get("alternative_angles", [])),
            counterarguments=list(payload.get("counterarguments", [])),
            prediction=str(payload.get("prediction", "")),
            confidence_score=float(payload.get("confidence_score", 0.0)),
            causal_chain=list(payload.get("causal_chain", [])),
            industry_chain=list(payload.get("industry_chain", [])),
            originality_angle=str(payload.get("originality_angle", "")),
            evidence_gaps=list(payload.get("evidence_gaps", [])),
            risk_boundaries=list(payload.get("risk_boundaries", [])),
            expert_perspectives=list(payload.get("expert_perspectives", [])),
        )


@dataclass
class EditorialCouncilReport:
    """多专家编辑委员会的结构化裁决结果。"""

    topic_title: str = ""
    editorial_thesis: str = ""
    originality_angle: str = ""
    evidence_chain: list[str] = field(default_factory=list)
    causal_chain: list[str] = field(default_factory=list)
    industry_chain: list[str] = field(default_factory=list)
    counterarguments: list[str] = field(default_factory=list)
    risk_boundaries: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    avoid_claims: list[str] = field(default_factory=list)
    perspectives: list[dict[str, Any]] = field(default_factory=list)
    chief_editor_note: str = ""
    confidence_score: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EditorialCouncilReport":
        if not isinstance(payload, dict):
            return cls()

        chief_editor = payload.get("chief_editor")
        chief = chief_editor if isinstance(chief_editor, dict) else payload

        def text_list(value: Any) -> list[str]:
            if isinstance(value, (list, tuple)):
                return [str(item).strip() for item in value if str(item).strip()]
            if value is None:
                return []
            text = str(value).strip()
            return [text] if text else []

        def dict_list(value: Any) -> list[dict[str, Any]]:
            if not isinstance(value, list):
                return []
            return [dict(item) for item in value if isinstance(item, dict)]

        def first_text(*keys: str) -> str:
            for key in keys:
                value = chief.get(key, payload.get(key, ""))
                text = str(value or "").strip()
                if text:
                    return text
            return ""

        try:
            confidence = float(payload.get("confidence_score", chief.get("confidence_score", 0.0)) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        return cls(
            topic_title=str(payload.get("topic_title", "")).strip(),
            editorial_thesis=first_text("editorial_thesis", "thesis", "main_insight"),
            originality_angle=first_text("originality_angle", "distinctive_angle"),
            evidence_chain=text_list(payload.get("evidence_chain", chief.get("evidence_chain", []))),
            causal_chain=text_list(payload.get("causal_chain", chief.get("causal_chain", []))),
            industry_chain=text_list(payload.get("industry_chain", chief.get("industry_chain", []))),
            counterarguments=text_list(payload.get("counterarguments", chief.get("counterarguments", []))),
            risk_boundaries=text_list(payload.get("risk_boundaries", chief.get("risk_boundaries", []))),
            evidence_gaps=text_list(payload.get("evidence_gaps", chief.get("evidence_gaps", []))),
            follow_up_questions=text_list(payload.get("follow_up_questions", chief.get("follow_up_questions", []))),
            avoid_claims=text_list(payload.get("avoid_claims", chief.get("avoid_claims", []))),
            perspectives=dict_list(payload.get("perspectives", [])),
            chief_editor_note=first_text("chief_editor_note", "editorial_note", "note"),
            confidence_score=max(0.0, min(1.0, confidence)),
        )

    def is_usable(self) -> bool:
        return bool(
            self.editorial_thesis.strip()
            and (self.evidence_chain or self.causal_chain or self.originality_angle)
        )

    def to_insight_report(self) -> InsightReport:
        alternative_angles: list[str] = []
        for perspective in self.perspectives:
            for key in ("alternative_angle", "angle", "judgment"):
                value = str(perspective.get(key, "")).strip()
                if value and value not in alternative_angles:
                    alternative_angles.append(value)
                    break
        if self.originality_angle and self.originality_angle not in alternative_angles:
            alternative_angles.insert(0, self.originality_angle)

        prediction = self.follow_up_questions[0] if self.follow_up_questions else "继续观察证据是否兑现。"
        return InsightReport(
            main_insight=self.editorial_thesis,
            supporting_data=self.evidence_chain,
            alternative_angles=alternative_angles[:5],
            counterarguments=self.counterarguments,
            prediction=prediction,
            confidence_score=self.confidence_score,
            causal_chain=self.causal_chain,
            industry_chain=self.industry_chain,
            originality_angle=self.originality_angle,
            evidence_gaps=self.evidence_gaps,
            risk_boundaries=self.risk_boundaries,
            expert_perspectives=self.perspectives,
        )


@dataclass
class QualityReviewReport:
    """文章成稿质量审核专家团的结构化报告。

    This report is deliberately separate from :class:`ContentScore`: the
    latter estimates distribution potential, while this report records an
    independent editorial gate and its actionable findings.
    """

    article_version: str = "post_polish"
    overall_score: int = 0
    fact_score: int = 0
    logic_score: int = 0
    structure_score: int = 0
    originality_score: int = 0
    compliance_score: int = 0
    verdict: str = "needs_revision"
    needs_revision: bool = True
    hard_blockers: list[str] = field(default_factory=list)
    fact_issues: list[str] = field(default_factory=list)
    logic_issues: list[str] = field(default_factory=list)
    structure_issues: list[str] = field(default_factory=list)
    originality_issues: list[str] = field(default_factory=list)
    compliance_issues: list[str] = field(default_factory=list)
    required_revisions: list[str] = field(default_factory=list)
    optional_improvements: list[str] = field(default_factory=list)
    role_reviews: list[dict[str, Any]] = field(default_factory=list)
    chief_editor_conclusion: str = ""
    reviewed_char_count: int = 0
    revision_round: int = 0
    used_fallback: bool = False

    def __post_init__(self) -> None:
        for name in (
            "overall_score",
            "fact_score",
            "logic_score",
            "structure_score",
            "originality_score",
            "compliance_score",
        ):
            value = getattr(self, name, 0)
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = 0
            setattr(self, name, max(0, min(100, value)))

        verdict = str(self.verdict or "needs_revision").strip().lower()
        verdict_aliases = {
            "pass": "approved",
            "ready": "approved",
            "approve": "approved",
            "通过": "approved",
            "阻断": "blocked",
            "block": "blocked",
            "reject": "blocked",
            "驳回": "blocked",
            "返工": "needs_revision",
        }
        self.verdict = verdict_aliases.get(verdict, verdict)
        if self.verdict not in {"approved", "needs_revision", "blocked"}:
            self.verdict = "needs_revision"
        if self.hard_blockers:
            self.verdict = "blocked"
        self.needs_revision = self.verdict != "approved"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "QualityReviewReport":
        if not isinstance(payload, dict):
            return cls()

        chief_editor = payload.get("chief_editor")
        chief = chief_editor if isinstance(chief_editor, dict) else {}

        def text_list(*keys: str) -> list[str]:
            for key in keys:
                value = payload.get(key, chief.get(key))
                if isinstance(value, (list, tuple)):
                    return [str(item).strip() for item in value if str(item).strip()]
                if value is not None and str(value).strip():
                    return [str(value).strip()]
            return []

        def dict_list(*keys: str) -> list[dict[str, Any]]:
            for key in keys:
                value = payload.get(key, chief.get(key))
                if isinstance(value, list):
                    return [dict(item) for item in value if isinstance(item, dict)]
            return []

        def text_value(*keys: str) -> str:
            for key in keys:
                value = payload.get(key, chief.get(key, ""))
                if value is not None and str(value).strip():
                    return str(value).strip()
            return ""

        def score_value(key: str) -> int:
            value = payload.get(key, chief.get(key, 0))
            try:
                # 模型常把分数写成浮点或浮点字符串（82.5 / "82.5"）。
                # 直接 int() 遇到 "82.5" 会抛 ValueError 并静默退回 0，
                # 把一次正常评分变成「全维度 0 分」，进而触发阻断。
                # 先转 float 再取整，对整数输入结果完全不变。
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        verdict = text_value("verdict", "decision") or "needs_revision"
        return cls(
            article_version=text_value("article_version") or "post_polish",
            overall_score=score_value("overall_score"),
            fact_score=score_value("fact_score"),
            logic_score=score_value("logic_score"),
            structure_score=score_value("structure_score"),
            originality_score=score_value("originality_score"),
            compliance_score=score_value("compliance_score"),
            verdict=verdict,
            hard_blockers=text_list("hard_blockers", "blocking_issues"),
            fact_issues=text_list("fact_issues", "factual_issues", "evidence_issues"),
            logic_issues=text_list("logic_issues", "causal_issues"),
            structure_issues=text_list("structure_issues", "readability_issues"),
            originality_issues=text_list("originality_issues", "ai_style_issues"),
            compliance_issues=text_list("compliance_issues", "risk_issues"),
            required_revisions=text_list("required_revisions", "must_fix"),
            optional_improvements=text_list("optional_improvements", "suggestions"),
            role_reviews=dict_list("role_reviews", "perspectives"),
            chief_editor_conclusion=text_value(
                "chief_editor_conclusion", "chief_editor_note", "conclusion", "summary"
            ),
            reviewed_char_count=score_value("reviewed_char_count"),
            revision_round=score_value("revision_round"),
            used_fallback=bool(payload.get("used_fallback", False)),
        )

    def has_valid_scores(self) -> bool:
        """五个维度分是否构成一次可信的评估。

        模型偶尔会照抄提示词里的占位值，导致所有维度同时为 0。这种报告并不
        代表真实审核结论（历史上正是它把正常成稿判成 blocked、并连带把
        publish_readiness_score 压到 0，使自动发布整体停摆），因此必须视为
        不可用，回落到本地规则审核。
        """
        dimensions = (
            self.fact_score,
            self.logic_score,
            self.structure_score,
            self.originality_score,
            self.compliance_score,
        )
        return any(score > 0 for score in dimensions)

    def is_usable(self) -> bool:
        if not self.has_valid_scores():
            return False
        return bool(
            self.chief_editor_conclusion
            or self.role_reviews
            or self.hard_blockers
            or any(
                (
                    self.fact_issues,
                    self.logic_issues,
                    self.structure_issues,
                    self.originality_issues,
                    self.compliance_issues,
                    self.required_revisions,
                )
            )
        )


@dataclass
class DataInsightReport:
    """数据洞察报告"""
    extracted_metrics: list[dict] = field(default_factory=list)
    trends: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    correlations: list[str] = field(default_factory=list)
    data_conclusions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DataInsightReport":
        return cls(
            extracted_metrics=list(payload.get("extracted_metrics", [])),
            trends=list(payload.get("trends", [])),
            anomalies=list(payload.get("anomalies", [])),
            correlations=list(payload.get("correlations", [])),
            data_conclusions=list(payload.get("data_conclusions", [])),
        )


@dataclass
class MultiSourceReport:
    """多源信息聚合报告（为未来准备）"""
    primary_sources: list[SourceDocument] = field(default_factory=list)
    secondary_sources: list[SourceDocument] = field(default_factory=list)
    consensus_points: list[str] = field(default_factory=list)
    controversy_points: list[str] = field(default_factory=list)
    overlooked_signals: list[str] = field(default_factory=list)
    source_credibility: dict[str, float] = field(default_factory=dict)
    synthesis_summary: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MultiSourceReport":
        return cls(
            primary_sources=[SourceDocument(**d) for d in payload.get("primary_sources", [])],
            secondary_sources=[SourceDocument(**d) for d in payload.get("secondary_sources", [])],
            consensus_points=list(payload.get("consensus_points", [])),
            controversy_points=list(payload.get("controversy_points", [])),
            overlooked_signals=list(payload.get("overlooked_signals", [])),
            source_credibility=dict(payload.get("source_credibility", {})),
            synthesis_summary=str(payload.get("synthesis_summary", "")),
        )


@dataclass
class HistoricalComparison:
    """历史对比报告（为未来准备）"""
    historical_events: list[dict] = field(default_factory=list)
    similarities: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    historical_outcomes: list[str] = field(default_factory=list)
    historical_insights: list[str] = field(default_factory=list)
    future_prediction: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HistoricalComparison":
        return cls(
            historical_events=list(payload.get("historical_events", [])),
            similarities=list(payload.get("similarities", [])),
            differences=list(payload.get("differences", [])),
            historical_outcomes=list(payload.get("historical_outcomes", [])),
            historical_insights=list(payload.get("historical_insights", [])),
            future_prediction=str(payload.get("future_prediction", "")),
        )
