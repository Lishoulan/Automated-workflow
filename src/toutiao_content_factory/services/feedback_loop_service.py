"""Enhanced feedback loop — data collection, evolution analysis, prompt updating.

Closes the loop between published article performance and future content generation:
1. Collect real performance data from creator dashboards (via PerformanceService)
2. Normalize data into EvolutionAgent-compatible format
3. Run EvolutionAgent to extract actionable writing rules
4. Auto-update prompt templates (title_prompt_v2.md, article_prompt_v2.md) with Few-Shot examples
5. Update self_benchmark_articles.yaml with new high-performer patterns
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from toutiao_content_factory.agents.evolution_agent import EvolutionAgent
from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.database.models import Article, Topic
from toutiao_content_factory.database.service import DatabaseService
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.performance_service import PerformanceService
from toutiao_content_factory.services.prompt_repository import PromptRepository

logger = logging.getLogger(__name__)


class FeedbackLoopService:
    """Orchestrates the full data feedback loop.

    Steps:
    1. Collect performance data from the creator center (via Playwright or local records)
    2. Normalize and store in EvolutionAgent-compatible format
    3. Run EvolutionAgent to extract actionable rules
    4. Update prompt templates with Few-Shot examples from top performers
    5. Update self_benchmark_articles.yaml
    """

    def __init__(
        self,
        settings: AppSettings,
        logger: logging.Logger,
        prompt_repository: PromptRepository | None = None,
        openai_service: OpenAIContentService | None = None,
        database_service: DatabaseService | None = None,
    ):
        self.settings = settings
        self.logger = logger
        self.prompt_repo = prompt_repository or PromptRepository(settings.prompts_dir)
        self.openai_service = openai_service
        self.db = database_service
        self.performance_service = PerformanceService(settings, logger)
        self.evolution_agent = EvolutionAgent(
            settings, self.prompt_repo,
            openai_service or OpenAIContentService(settings, logger),
            logger,
        ) if openai_service or True else None
        self.growth_ops_dir = settings.output_dir / "growth_ops"
        self.growth_ops_dir.mkdir(parents=True, exist_ok=True)

    # ====================================================================
    # Main entry point
    # ====================================================================

    def run_full_loop(self) -> dict[str, Any]:
        """Execute the complete feedback loop."""
        self.logger.info("=== Starting full feedback loop ===")

        # Step 1: Collect performance data
        performance_data = self._collect_performance()
        if not performance_data:
            self.logger.warning("No performance data collected, using local run records instead.")
            performance_data = self._build_performance_from_local_records()

        # Step 2: Normalize into EvolutionAgent format
        metrics_path = self._normalize_for_evolution(performance_data)

        # Step 3: Run EvolutionAgent
        evolution_result = self._run_evolution(metrics_path)
        rules = evolution_result.get("rules", "")

        # Step 4: Update prompt templates
        top_articles = self._extract_top_performers(performance_data)
        if top_articles:
            self._update_prompt_templates(top_articles, rules)

        # Step 5: Update self-benchmark
        self._update_self_benchmark(top_articles)

        summary = {
            "status": "ok",
            "articles_collected": len(performance_data.get("records", [])),
            "rules_generated": bool(rules),
            "prompts_updated": bool(top_articles),
            "benchmark_updated": len(top_articles),
            "metrics_path": str(metrics_path) if metrics_path else "",
        }
        self.logger.info("Feedback loop summary: %s", summary)
        return summary

    # ====================================================================
    # Step 1: Collect performance
    # ====================================================================

    def _collect_performance(self) -> dict[str, Any]:
        """Collect performance data from creator dashboards."""
        try:
            result = self.performance_service.collect_performance_data()
            self.logger.info("Collected %d performance records", len(result.get("records", [])))
            return result
        except Exception as exc:
            self.logger.warning("Performance collection failed: %s", exc)
            return {}

    def _build_performance_from_local_records(self) -> dict[str, Any]:
        """Build performance data from local database/run records when live collection fails."""
        records: list[dict[str, Any]] = []
        if self.db and self.db.available:
            articles = self.db.list_articles(limit=50)
            for article in articles:
                records.append({
                    "title": article.title,
                    "article": article.article_text or article.polished_article,
                    "summary": article.summary,
                    "topic_title": article.topic_title,
                    "roi_score": max(1, article.overall_score),
                    "platform": article.platform,
                    "created_at": article.created_at.isoformat() if article.created_at else "",
                })

        # If no database, read from run directories
        if not records:
            runs_dir = self.settings.output_dir / "runs"
            if runs_dir.exists():
                for run_dir in sorted(runs_dir.iterdir(), reverse=True)[:30]:
                    if run_dir.is_dir():
                        record = self._read_run_record(run_dir)
                        if record:
                            records.append(record)

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "records": records,
            "platforms": {},
            "errors": [],
        }

    def _read_run_record(self, run_dir: Path) -> dict[str, Any] | None:
        """Read a single run's data from its output directory."""
        try:
            workflow_path = run_dir / "workflow_result.json"
            if not workflow_path.exists():
                workflow_path = run_dir / "workflow.json"
            if not workflow_path.exists():
                return None

            data = json.loads(workflow_path.read_text(encoding="utf-8"))
            title = ""
            for key in ("recommended_titles", "titles"):
                titles = data.get(key, [])
                if titles and isinstance(titles, list) and titles[0]:
                    title = titles[0]
                    break

            return {
                "title": title or data.get("title", run_dir.name),
                "article": data.get("article", "") or data.get("polished_article", ""),
                "summary": data.get("summary", ""),
                "topic_title": data.get("topic_title", ""),
                "roi_score": float(data.get("overall_score", 50)),
                "platform": data.get("platform", "toutiao"),
                "created_at": data.get("created_at", ""),
            }
        except Exception:
            return None

    # ====================================================================
    # Step 2: Normalize for EvolutionAgent
    # ====================================================================

    def _normalize_for_evolution(self, performance_data: dict[str, Any]) -> Path | None:
        """Convert performance data into the format EvolutionAgent expects."""
        records = performance_data.get("records", [])
        if not records:
            return None

        # Transform to EvolutionAgent-compatible format
        evolution_records = []
        for record in records:
            roi_score = self._compute_roi_score(record)
            evolution_records.append({
                "title": record.get("title", ""),
                "article": record.get("article", "") or record.get("article_text", ""),
                "summary": record.get("summary", ""),
                "topic_title": record.get("topic_title", ""),
                "roi_score": roi_score,
                "impression_count": record.get("impression_count", 0),
                "read_count": record.get("read_count", 0),
                "ctr": record.get("ctr", 0),
                "platform": record.get("platform", "toutiao"),
            })

        evolution_records.sort(key=lambda r: r["roi_score"], reverse=True)

        # Save to the path EvolutionAgent looks for
        metrics_path = self.settings.automation_dir / "performance_metrics.json"
        metrics_path.write_text(
            json.dumps(evolution_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.info("Normalized %d records for EvolutionAgent at %s", len(evolution_records), metrics_path)
        return metrics_path

    def _compute_roi_score(self, record: dict[str, Any]) -> float:
        """Compute a normalized ROI score from available metrics."""
        read_count = int(record.get("read_count", 0) or 0)
        impression_count = int(record.get("impression_count", 0) or 0)
        ctr = float(record.get("ctr", 0) or 0)
        overall_score = int(record.get("overall_score", 0) or 0)

        if read_count > 0:
            return float(read_count + impression_count * 0.1 + ctr * 1000)
        if overall_score > 0:
            return float(overall_score)
        return 50.0

    # ====================================================================
    # Step 3: Run EvolutionAgent
    # ====================================================================

    def _run_evolution(self, metrics_path: Path | None) -> dict[str, Any]:
        """Run EvolutionAgent to extract actionable rules."""
        if not metrics_path or not metrics_path.exists():
            return {"status": "no_metrics", "rules": ""}

        try:
            result = self.evolution_agent.run()
            if result.get("status") == "ok":
                rules_path = self.evolution_agent.best_practices_rule_path()
                rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
                self.logger.info("EvolutionAgent generated %d chars of rules", len(rules))
                return {"status": "ok", "rules": rules, "path": str(rules_path)}
            return {"status": result.get("status", "unknown"), "rules": ""}
        except Exception as exc:
            self.logger.warning("EvolutionAgent run failed: %s", exc)
            return {"status": "error", "rules": ""}

    # ====================================================================
    # Step 4: Update prompt templates
    # ====================================================================

    def _extract_top_performers(
        self,
        performance_data: dict[str, Any],
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Extract the top-k performing articles."""
        records = performance_data.get("records", [])
        if not records:
            return []
        scored = [(self._compute_roi_score(r), r) for r in records]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k] if r.get("title")]

    def _update_prompt_templates(
        self,
        top_articles: list[dict[str, Any]],
        evolution_rules: str,
    ) -> None:
        """Update title and article prompt templates with Few-Shot examples."""
        if not top_articles:
            return

        # Build Few-Shot injection blocks
        title_few_shot = self._build_title_few_shot(top_articles)
        style_notes = self._build_style_notes(top_articles)
        rules_section = f"\n## EvolutionAgent \u89c4\u5219\n{evolution_rules}\n" if evolution_rules else ""

        # Update title_prompt_v2.md
        title_prompt_path = self.settings.prompts_dir / "title_prompt_v2.md"
        if title_prompt_path.exists():
            content = title_prompt_path.read_text(encoding="utf-8")
            content = self._inject_or_update_section(
                content,
                "## \u53c2\u8003\uff1a\u6700\u8fd1\u7684\u9ad8\u70b9\u51fb\u7387\u6807\u9898",
                title_few_shot,
            )
            content = self._inject_or_update_section(
                content,
                "## EvolutionAgent \u89c4\u5219",
                rules_section,
            )
            title_prompt_path.write_text(content, encoding="utf-8")
            self.logger.info("Updated title prompt template at %s", title_prompt_path)

        # Update article_prompt_v2.md
        article_prompt_path = self.settings.prompts_dir / "article_prompt_v2.md"
        if article_prompt_path.exists() and style_notes:
            content = article_prompt_path.read_text(encoding="utf-8")
            content = self._inject_or_update_section(
                content,
                "## \u53c2\u8003\uff1a\u6700\u8fd1\u7684\u9ad8\u8868\u73b0\u7bc7\u7ae0",
                style_notes,
            )
            article_prompt_path.write_text(content, encoding="utf-8")
            self.logger.info("Updated article prompt template at %s", article_prompt_path)

    def _build_title_few_shot(self, top_articles: list[dict[str, Any]]) -> str:
        """Build Few-Shot injection for title prompt."""
        lines = [
            "\n## \u53c2\u8003\uff1a\u6700\u8fd1\u7684\u9ad8\u70b9\u51fb\u7387\u6807\u9898\uff08\u8bf7\u5089\u4eff\u5176\u98ce\u683c\u548c\u7ed3\u6784\u4f46\u4e0d\u91cd\u590d\uff09\n",
        ]
        for i, article in enumerate(top_articles, 1):
            title = article.get("title", "")
            read_count = article.get("read_count", 0)
            ctr = article.get("ctr", 0)
            if title:
                lines.append(f"{i}. {title}")
                if read_count or ctr:
                    lines.append(f"   \u6570\u636e\uff1a\u9605\u8bfb {read_count} | CTR {ctr:.1%}" if ctr else f"   \u6570\u636e\uff1a\u9605\u8bfb {read_count}")
                lines.append("")
        return "\n".join(lines)

    def _build_style_notes(self, top_articles: list[dict[str, Any]]) -> str:
        """Build style notes for article prompt from top performers."""
        lines = [
            "\n## \u53c2\u8003\uff1a\u6700\u8fd1\u7684\u9ad8\u8868\u73b0\u7bc7\u7ae0\uff08\u8bf7\u5089\u4eff\u5176\u6210\u529f\u6a21\u5f0f\u4f46\u4e0d\u91cd\u590d\u5185\u5bb9\uff09\n",
        ]
        for i, article in enumerate(top_articles, 1):
            title = article.get("title", "")
            summary = article.get("summary", "")
            if title:
                lines.append(f"### \u9ad8\u8868\u73b0\u7bc7\u7ae0 {i}")
                lines.append(f"- \u6807\u9898\uff1a{title}")
                if summary:
                    lines.append(f"- \u6982\u8ff0\uff1a{summary[:200]}")
                lines.append("")
        return "\n".join(lines)

    def _inject_or_update_section(
        self,
        content: str,
        section_title: str,
        section_body: str,
    ) -> str:
        """Replace an existing section in a prompt file, or append it."""
        if not section_body.strip():
            return content

        mark = section_title.strip()
        if mark in content:
            # Replace everything from mark to the next section (or end)
            pattern = re.compile(
                re.escape(mark) + r".*?(?=\n## |\Z)",
                re.DOTALL,
            )
            content = pattern.sub(section_body.strip(), content)
        else:
            content += "\n" + section_body.strip() + "\n"
        return content

    # ====================================================================
    # Step 5: Update self_benchmark_articles.yaml
    # ====================================================================

    def _update_self_benchmark(self, top_articles: list[dict[str, Any]]) -> None:
        """Add top performers to the self-benchmark YAML file."""
        if not top_articles:
            return

        benchmark_path = self.settings.project_root / "branding" / "self_benchmark_articles.yaml"
        if not benchmark_path.exists():
            benchmark_path.parent.mkdir(parents=True, exist_ok=True)
            benchmark_data: dict[str, Any] = {
                "version": 1,
                "updated_at": datetime.now().strftime("%Y-%m-%d"),
                "articles": [],
            }
        else:
            benchmark_data = yaml.safe_load(benchmark_path.read_text(encoding="utf-8")) or {
                "version": 1,
                "updated_at": datetime.now().strftime("%Y-%m-%d"),
                "articles": [],
            }

        existing_titles = {a.get("title", "") for a in benchmark_data.get("articles", [])}
        new_count = 0
        for article in top_articles:
            title = article.get("title", "")
            if not title or title in existing_titles:
                continue

            benchmark_data.setdefault("articles", []).append({
                "key": self._make_key(title),
                "enabled": True,
                "track": "auto_generated",
                "account_key": self.settings.account_key or "default",
                "platform": article.get("platform", "toutiao"),
                "published_at": datetime.now().strftime("%Y-%m-%d"),
                "title": title,
                "run_dir": "",
                "observed_metrics": {
                    "impression_count": article.get("impression_count", 0),
                    "read_count": article.get("read_count", 0),
                },
                "keywords": [],
                "notes": [],
                "inferred_patterns": [],
            })
            existing_titles.add(title)
            new_count += 1

        if new_count > 0:
            # Keep only the most recent N articles
            max_articles = 30
            articles = benchmark_data["articles"]
            articles.sort(key=lambda a: a.get("published_at", ""), reverse=True)
            benchmark_data["articles"] = articles[:max_articles]
            benchmark_data["updated_at"] = datetime.now().strftime("%Y-%m-%d")

            benchmark_path.write_text(
                yaml.dump(benchmark_data, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            self.logger.info("Added %d new articles to self-benchmark at %s", new_count, benchmark_path)
        else:
            self.logger.info("No new articles to add to self-benchmark")

    def _make_key(self, title: str) -> str:
        """Generate a URL-friendly key from a title."""
        key = re.sub(r"[^\w\u4e00-\u9fff]", "_", title.lower())
        key = re.sub(r"_+", "_", key).strip("_")
        return key[:60] or f"article_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # ====================================================================
    # Utility: generate current injection strings for agents
    # ====================================================================

    def get_title_few_shot(self) -> str:
        """Generate the current Few-Shot injection for TitleAgent."""
        metrics_path = self.settings.automation_dir / "performance_metrics.json"
        if not metrics_path.exists():
            return ""
        try:
            records = json.loads(metrics_path.read_text(encoding="utf-8"))
            return self._build_title_few_shot(records[:3])
        except Exception:
            return ""

    def get_writer_few_shot(self) -> str:
        """Generate the current Few-Shot injection for WriterAgent."""
        metrics_path = self.settings.automation_dir / "performance_metrics.json"
        if not metrics_path.exists():
            return ""
        try:
            records = json.loads(metrics_path.read_text(encoding="utf-8"))
            return self._build_style_notes(records[:3])
        except Exception:
            return ""
