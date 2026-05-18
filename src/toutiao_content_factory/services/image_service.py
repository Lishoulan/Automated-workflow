from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import DailyTopicsReport, ImageArtifacts, VisualBrief, VisualMetric, WorkflowResult
from toutiao_content_factory.renderers import ImageRenderer
from toutiao_content_factory.services.image_model_service import ModelImageGenerationService


class ImageGenerationService:
    NUMBER_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")

    def __init__(self, settings: AppSettings, logger=None) -> None:
        self.settings = settings
        self.image_dir = settings.project_root / "images"
        self.templates_dir = settings.project_root / "templates"
        self.renderer = ImageRenderer(settings.image_generation)
        self.logger = logger
        self.model_generator = ModelImageGenerationService(settings.image_generation, logger) if logger else None
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def generate(self, workflow_result: WorkflowResult) -> ImageArtifacts:
        daily_topics = self._load_daily_topics()
        brief = self._build_visual_brief(workflow_result, daily_topics)
        artifacts = self._generate_artifacts(brief)
        self._render_html_briefs(brief, artifacts)
        return artifacts

    def _generate_artifacts(self, brief: VisualBrief) -> ImageArtifacts:
        backend = self.settings.image_generation.backend.strip().lower()
        if backend in {"openai", "model", "image", "image2", "image2.0"} and self.model_generator is not None:
            try:
                return self.model_generator.generate(brief, self.image_dir)
            except Exception as exc:
                if not self.settings.image_generation.fallback_to_renderer:
                    raise
                if self.logger is not None:
                    self.logger.warning("Image model backend failed, falling back to renderer: %s", exc)
        return self.renderer.render(brief, self.image_dir)

    def _load_daily_topics(self) -> DailyTopicsReport | None:
        path = self.settings.output_dir / "daily_topics.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DailyTopicsReport.from_dict(payload)

    def _build_visual_brief(self, result: WorkflowResult, topics: DailyTopicsReport | None) -> VisualBrief:
        metrics = self._build_metrics(result, topics)
        trend_labels = result.data_report.watch_metrics[:4] or result.data_report.key_points[:4]
        timeline_points = self._build_timeline_points(result)
        chain_nodes = result.data_report.supply_chain_keywords[:5] or [
            "energy storage",
            "system integration",
            "reducer",
            "motor",
            "overseas expansion",
        ]
        topic_highlights = self._build_topic_highlights(topics)
        recommended_title = (
            result.content.recommended_titles[0]
            if result.content.recommended_titles
            else "Where is the real profit pool in the industry chain?"
        )
        subtitle = (
            result.content.optimized_opening_lines[1]
            if len(result.content.optimized_opening_lines) > 1
            else result.content.summary
        )

        return VisualBrief(
            cover_title=recommended_title,
            cover_subtitle=subtitle,
            article_title=recommended_title,
            summary=result.content.summary,
            style_name=result.style_label,
            key_metrics=metrics,
            trend_labels=trend_labels,
            timeline_points=timeline_points,
            chain_nodes=chain_nodes,
            topic_highlights=topic_highlights,
            score_snapshot={
                "overall_score": result.content_score.overall_score,
                "title_attraction": result.content_score.title_attraction,
                "information_density": result.content_score.information_density,
                "save_potential": result.content_score.save_potential,
                "completion_rate": result.content_score.completion_rate_potential,
                "ai_risk": result.content_score.ai_generated_risk,
            },
        )

    def _build_metrics(self, result: WorkflowResult, topics: DailyTopicsReport | None) -> list[VisualMetric]:
        metrics: list[VisualMetric] = []
        for raw in result.data_report.growth_numbers[:4]:
            number, unit = self._split_number(raw)
            if number is None:
                continue
            metrics.append(VisualMetric(label=self._metric_label(raw), value=number, unit=unit))

        if topics:
            metrics.append(VisualMetric(label="selected_topics", value=float(topics.total_selected_items), unit=""))

        if not metrics:
            metrics = [
                VisualMetric(label="overall_score", value=float(result.content_score.overall_score), unit=""),
                VisualMetric(label="info_density", value=float(result.content_score.information_density), unit=""),
                VisualMetric(label="title_hook", value=float(result.content_score.title_attraction), unit=""),
            ]
        return metrics[:4]

    def _split_number(self, raw: str) -> tuple[float | None, str]:
        match = self.NUMBER_PATTERN.search(raw)
        if not match:
            return None, ""
        number = float(match.group(1))
        suffix = raw[match.end():].strip()
        return number, suffix or ""

    def _metric_label(self, raw: str) -> str:
        normalized = raw.lower()
        if "%" in raw:
            return raw[:12]
        if any(token in normalized for token in ("亿", "bn", "billion", "million", "万")):
            return "market_value"
        return raw[:10]

    def _build_timeline_points(self, result: WorkflowResult) -> list[str]:
        points = [
            "Attention shifts from isolated news to sustained industry discussion.",
            "Orders, pilot projects and production schedules start linking up.",
            "Profit pools split between key components and system integration.",
            "Overseas delivery and local service become the next battleground.",
        ]
        if result.trend_report.timing_judgment:
            points.insert(1, result.trend_report.timing_judgment)
        return points[:5]

    def _build_topic_highlights(self, topics: DailyTopicsReport | None) -> list[str]:
        if topics and topics.topics:
            return [item.title for item in topics.topics[:5]]
        return [
            "Demand conversion is accelerating",
            "Funding remains active in embodied intelligence",
            "Key component positioning is getting tighter",
            "Compute and industrial scenarios are merging faster",
        ]

    def _render_html_briefs(self, brief: VisualBrief, artifacts: ImageArtifacts) -> None:
        template = self.jinja_env.get_template("infographic.html.j2")
        html_specs: list[tuple[str, str, str, list[dict[str, object]], str]] = [
            (
                "cover_layout.html",
                brief.cover_title,
                brief.cover_subtitle,
                [
                    {"heading": "Core Metrics", "type": "metrics", "entries": [metric.__dict__ for metric in brief.key_metrics[:3]]},
                    {"heading": "Chain Nodes", "type": "chips", "entries": brief.chain_nodes[:6]},
                    {"heading": "Trend Signals", "type": "timeline", "entries": brief.trend_labels[:5]},
                ],
                "Cover Layout",
            ),
        ]

        max_assets = max(1, min(self.settings.image_generation.max_generated_assets, 3))
        if max_assets >= 2:
            html_specs.append(
                (
                    "infographic_layout_1.html",
                    "One image to understand the core growth chain",
                    brief.summary,
                    [
                        {"heading": "Core Metrics", "type": "metrics", "entries": [metric.__dict__ for metric in brief.key_metrics[:4]]},
                        {"heading": "Trend Notes", "type": "timeline", "entries": brief.timeline_points[:5]},
                        {"heading": "Keywords", "type": "chips", "entries": brief.chain_nodes[:6]},
                    ],
                    "Infographic Layout 1",
                )
            )
        if max_assets >= 3:
            html_specs.append(
                (
                    "infographic_layout_2.html",
                    "Trend map: heat, chain position and judgment",
                    brief.cover_subtitle,
                    [
                        {"heading": "Hot Topics", "type": "timeline", "entries": brief.topic_highlights[:5]},
                        {"heading": "Chain Nodes", "type": "chips", "entries": brief.chain_nodes[:6]},
                        {
                            "heading": "Score Snapshot",
                            "type": "metrics",
                            "entries": [
                                {"label": key, "value": value, "unit": "", "note": ""}
                                for key, value in list(brief.score_snapshot.items())[:4]
                                if isinstance(value, int)
                            ],
                        },
                    ],
                    "Infographic Layout 2",
                )
            )

        html_files: list[Path] = []
        for filename, title, subtitle, sections, footer_left in html_specs:
            target = self.image_dir / filename
            rendered = template.render(
                title=title,
                eyebrow="AI Content Factory",
                subtitle=subtitle,
                sections=sections,
                footer_left=footer_left,
                footer_right=f"Style: {brief.style_name}",
                primary_color=self.settings.image_generation.primary_color,
                secondary_color=self.settings.image_generation.secondary_color,
                accent_color=self.settings.image_generation.accent_color,
                background_color=self.settings.image_generation.background_color,
                card_color=self.settings.image_generation.card_color,
                text_color=self.settings.image_generation.text_color,
                muted_text_color=self.settings.image_generation.muted_text_color,
            )
            target.write_text(rendered, encoding="utf-8")
            html_files.append(target)

        expected_paths = {self.image_dir / spec[0] for spec in html_specs}
        for stale_name in ("cover_layout.html", "infographic_layout_1.html", "infographic_layout_2.html"):
            stale_path = self.image_dir / stale_name
            if stale_path in expected_paths:
                continue
            if stale_path.exists():
                stale_path.unlink()

        artifacts.html_files = html_files
