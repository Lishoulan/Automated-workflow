from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate
from toutiao_content_factory.services.automation import TopicAutomationService
from toutiao_content_factory.services.pipeline import ContentFactoryPipeline
from toutiao_content_factory.services.review_service import ReviewWorkflowService
from toutiao_content_factory.services.topic_collection import TopicCollectionService
from toutiao_content_factory.services.topic_exporter import TopicExportService
from toutiao_content_factory.services.openai_service import OpenAIContentService
from toutiao_content_factory.services.prompt_repository import PromptRepository
from toutiao_content_factory.services.agent_workflow import AgentWorkflowService
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.agents import BenchmarkMonitorAgent, SampleCollectorAgent
from toutiao_content_factory.services.wechat_service import WechatPublishService
from toutiao_content_factory.services.wechat_launch_service import WechatLaunchService
from toutiao_content_factory.services.image_generation_service import ImageGenerationService


class ContentFactoryAppService:
    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.topic_automation = TopicAutomationService(settings, logger)
        self.review_service = ReviewWorkflowService(settings, logger)
        self.wechat_service = WechatPublishService(settings, logger)
        self.wechat_launch_service = WechatLaunchService(settings, logger)
        self.image_generation_service = ImageGenerationService(settings, logger)
        self.growth_ops_service = GrowthOpsService(settings, logger)

    def collect_topics(self) -> dict[str, str]:
        topic_collector = TopicCollectionService(self.settings, self.logger)
        topic_exporter = TopicExportService(self.settings.output_dir, self.logger)
        topic_report = topic_collector.collect_sync()
        exported: dict[str, str] = {}
        benchmark_platform = self._resolve_benchmark_platform_for_workflow()
        benchmark_track = self._resolve_benchmark_track_for_account()
        try:
            benchmark_exports = self.collect_benchmark_hot_topics(
                platform=benchmark_platform,
                track=benchmark_track,
                limit=max(8, min(20, int(self.settings.topic_collection.max_export_topics) * 2)),
            )
            exported.update(benchmark_exports)
            benchmark_json_raw = str(benchmark_exports.get("benchmark_hot_topics_json", "")).strip()
            if benchmark_json_raw:
                benchmark_json_path = Path(benchmark_json_raw)
            else:
                benchmark_json_path = None
            if benchmark_json_path is not None and benchmark_json_path.exists():
                topic_report, merge_summary_path = self._merge_benchmark_topics_into_daily_report(
                    topic_report,
                    benchmark_json_path,
                    topic_collector,
                    platform=benchmark_platform,
                    track=benchmark_track,
                )
                exported["benchmark_topic_merge"] = str(merge_summary_path)
        except Exception as exc:
            self.logger.warning(
                "Benchmark hotspot refresh skipped for account=%s platform=%s track=%s: %s",
                self.settings.account_key,
                benchmark_platform,
                benchmark_track or "all",
                exc,
            )
        filtered_topics = self.topic_automation.filter_topics_for_account(topic_report.topics)
        if len(filtered_topics) != len(topic_report.topics):
            for index, topic in enumerate(filtered_topics, start=1):
                topic.rank = index
            topic_report.topics = filtered_topics
            topic_report.total_selected_items = len(filtered_topics)
        topic_exports = topic_exporter.export(topic_report)
        exported.update({name: str(path) for name, path in topic_exports.items()})
        return exported

    def _resolve_benchmark_platform_for_workflow(self) -> str:
        platform_text = " ".join(
            item
            for item in (
                str(self.settings.platform or "").strip(),
                self.settings.account_label,
                self.settings.brand_name,
            )
            if item
        ).lower()
        if any(token in platform_text for token in ("wechat", "公众号", "微信", "official_account", "weixin")):
            return "wechat"
        if any(token in platform_text for token in ("toutiao", "今日头条")):
            return "toutiao"
        platform_value = str(self.settings.platform or "").strip().lower()
        if platform_value in {"wechat", "公众号", "official_account", "weixin"}:
            return "wechat"
        if platform_value in {"toutiao", "今日头条"}:
            return "toutiao"

        combined = " ".join(
            item
            for item in (
                str(self.settings.platform or "").strip(),
                self.settings.account_label,
                self.settings.brand_name,
            )
            if item
        ).lower()
        if "wechat" in combined or "公众号" in combined or "微信" in combined:
            return "wechat"
        return "toutiao"

    def _resolve_benchmark_track_for_account(self) -> str | None:
        clean_focus_industries = {
            str(item).strip() for item in self.settings.topic_collection.focus_industries if str(item).strip()
        }
        if self.settings.account_key == "default":
            return None
        if clean_focus_industries and clean_focus_industries.issubset({"humanoid_robot", "ai_hardware", "industrial_automation"}):
            return "AI机器人"
        if clean_focus_industries and clean_focus_industries.issubset({"energy_storage", "new_energy"}):
            return "新能源储能"
        if clean_focus_industries and clean_focus_industries.issubset({"international"}):
            return "国际热点"
        if clean_focus_industries and clean_focus_industries.issubset({"sports"}):
            return "体育赛事"
        focus_industries = {str(item).strip() for item in self.settings.topic_collection.focus_industries if str(item).strip()}
        robot_industries = {"humanoid_robot", "ai_hardware", "industrial_automation"}
        energy_industries = {"energy_storage", "new_energy"}
        international_industries = {"international"}
        sports_industries = {"sports"}

        if self.settings.account_key == "default":
            return None
        if focus_industries and focus_industries.issubset(robot_industries):
            return "AI机器人"
        if focus_industries and focus_industries.issubset(energy_industries):
            return "新能源储能"
        if focus_industries and focus_industries.issubset(international_industries):
            return "国际热点"
        if focus_industries and focus_industries.issubset(sports_industries):
            return "体育赛事"

        inferred = self.growth_ops_service.infer_title_playbook_track(
            " ".join(
                item
                for item in (
                    self.settings.account_key,
                    self.settings.account_label,
                    self.settings.brand_name,
                    self.settings.industry_focus,
                )
                if item
            )
        )
        return inferred or None

    def _merge_benchmark_topics_into_daily_report(
        self,
        report: DailyTopicsReport,
        benchmark_json_path: Path,
        topic_collector: TopicCollectionService,
        *,
        platform: str,
        track: str | None,
    ) -> tuple[DailyTopicsReport, Path]:
        payload = json.loads(benchmark_json_path.read_text(encoding="utf-8"))
        benchmark_topics = self._load_benchmark_topic_candidates(payload)
        if not benchmark_topics:
            merge_summary_path = self._write_benchmark_merge_summary(
                platform=platform,
                track=track,
                base_report=report,
                merged_report=report,
                benchmark_topics=[],
                source_payload=payload,
            )
            return report, merge_summary_path

        filtered_out = dict(report.filtered_out or {})
        for key in ("missing_timestamp", "stale", "industry_mismatch", "weak_focus", "low_quality", "duplicate", "source_overflow", "relaxed_added"):
            filtered_out.setdefault(key, 0)

        merged_candidates = topic_collector._dedupe_topics([*report.topics, *benchmark_topics], filtered_out)
        ranked_candidates = topic_collector._rank_topics(merged_candidates)
        final_candidates = topic_collector._apply_source_cap(ranked_candidates, filtered_out)[
            : self.settings.topic_collection.max_export_topics
        ]
        for index, item in enumerate(final_candidates, start=1):
            item.rank = index

        merged_source_stats = dict(report.source_stats or {})
        for source_name, stats in payload.get("source_stats", {}).items():
            if not isinstance(stats, dict):
                continue
            merged_source_stats[f"benchmark::{source_name}"] = dict(stats)

        merged_failed_sources = list(report.failed_sources or [])
        for item in payload.get("failed_sources", []):
            if isinstance(item, dict):
                merged_failed_sources.append(
                    {
                        "source": f"benchmark::{item.get('source', '')}",
                        "error": str(item.get("error", "")),
                    }
                )

        merged_report = DailyTopicsReport(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            lookback_days=report.lookback_days,
            max_topic_age_hours=report.max_topic_age_hours,
            focus_industries=list(report.focus_industries),
            total_raw_items=int(report.total_raw_items) + len(benchmark_topics),
            total_selected_items=len(final_candidates),
            topics=final_candidates,
            source_stats=merged_source_stats,
            failed_sources=merged_failed_sources,
            filtered_out=filtered_out,
        )
        merge_summary_path = self._write_benchmark_merge_summary(
            platform=platform,
            track=track,
            base_report=report,
            merged_report=merged_report,
            benchmark_topics=benchmark_topics,
            source_payload=payload,
        )
        return merged_report, merge_summary_path

    def _load_benchmark_topic_candidates(self, payload: dict[str, object]) -> list[TopicCandidate]:
        topics: list[TopicCandidate] = []
        for row in payload.get("topics", []):
            if not isinstance(row, dict):
                continue
            account_name = str(row.get("benchmark_account_name", "")).strip() or "对标账号"
            title = str(row.get("title", "")).strip()
            url = str(row.get("url", "")).strip()
            if not title or not url:
                continue
            title_track = str(row.get("benchmark_track", "")).strip() or self.growth_ops_service.infer_title_playbook_track(title)
            matched_industries = [str(item).strip() for item in row.get("matched_industries", []) if str(item).strip()]
            matched_keywords = [str(item).strip() for item in row.get("matched_keywords", []) if str(item).strip()]
            quality_flags = [str(item).strip() for item in row.get("quality_flags", []) if str(item).strip()]
            if "benchmark_hotspot" not in quality_flags:
                quality_flags.append("benchmark_hotspot")
            title_reasons = [str(item).strip() for item in row.get("title_potential_reasons", []) if str(item).strip()]
            if "来自对标账号最新高热内容" not in title_reasons:
                title_reasons.insert(0, "来自对标账号最新高热内容")

            base_total_score = int(row.get("total_score", 0))
            benchmark_bonus = 12
            topics.append(
                TopicCandidate(
                    title=title,
                    url=url,
                    source=f"对标热点/{account_name}",
                    source_type="benchmark_hotspot",
                    published_at=str(row.get("published_at", "")).strip(),
                    snippet=str(row.get("snippet", "")).strip(),
                    summary=str(row.get("summary", "")).strip(),
                    matched_industries=matched_industries,
                    matched_keywords=matched_keywords,
                    extracted_numbers=[str(item).strip() for item in row.get("extracted_numbers", []) if str(item).strip()],
                    heat_score=int(row.get("heat_score", 0)),
                    read_score=int(row.get("read_score", 0)),
                    save_score=int(row.get("save_score", 0)),
                    discussion_score=int(row.get("discussion_score", 0)),
                    total_score=base_total_score + benchmark_bonus,
                    title_potential_score=int(row.get("title_potential_score", 0)),
                    title_playbook_track=title_track,
                    title_potential_reasons=title_reasons,
                    is_high_read=int(row.get("read_score", 0)) >= 70,
                    is_high_save=int(row.get("save_score", 0)) >= 70,
                    is_high_discussion=int(row.get("discussion_score", 0)) >= 70,
                    topic_type=str(row.get("topic_type", "")).strip(),
                    topic_type_label=str(row.get("topic_type_label", "")).strip(),
                    evidence_strength=max(55, int(row.get("quality_score", 0))),
                    quality_score=max(60, int(row.get("quality_score", 0))),
                    quality_flags=quality_flags,
                    recommended_format=str(row.get("recommended_format", "")).strip(),
                    recommended_format_label=str(row.get("recommended_format_label", "")).strip(),
                )
            )
        return topics

    def _write_benchmark_merge_summary(
        self,
        *,
        platform: str,
        track: str | None,
        base_report: DailyTopicsReport,
        merged_report: DailyTopicsReport,
        benchmark_topics: list[TopicCandidate],
        source_payload: dict[str, object],
    ) -> Path:
        summary_path = self.settings.output_dir / "automation" / "benchmark_topic_merge.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "account_label": self.settings.account_label,
            "platform": platform,
            "track": track or "",
            "base_topic_count": len(base_report.topics),
            "benchmark_topic_count": len(benchmark_topics),
            "merged_topic_count": len(merged_report.topics),
            "benchmark_source_count": int(source_payload.get("source_count", 0) or 0),
            "benchmark_accounts": source_payload.get("accounts", []),
            "top_titles": [
                {
                    "rank": item.rank,
                    "title": item.title,
                    "source": item.source,
                    "track": item.title_playbook_track,
                    "total_score": item.total_score,
                }
                for item in merged_report.topics[:10]
            ],
        }
        summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary_path

    def manual_run(
        self,
        paste_text: str | None = None,
        style_override: str | None = None,
        dry_run: bool = False,
        refresh_cache: bool = False,
        include_input_dir: bool = True,
        fast_mode: bool = False,
    ) -> dict[str, str]:
        pipeline = ContentFactoryPipeline(
            settings=self.settings,
            logger=self.logger,
            force_mock=dry_run,
            style_override=style_override,
            refresh_cache=refresh_cache,
            fast_mode=fast_mode,
        )
        try:
            return pipeline.run(paste_text=paste_text, include_input_dir=include_input_dir)
        finally:
            pipeline.close()

    def auto_run(
        self,
        style_override: str | None = None,
        dry_run: bool = False,
        refresh_cache: bool = False,
        topic_rank: int | None = None,
        topic_title: str | None = None,
        collect_if_missing: bool = True,
        force_collect: bool = False,
        no_archive: bool = False,
        fast_mode: bool = False,
    ) -> dict[str, str]:
        exported_files: dict[str, str] = {}
        topics_path = self.settings.output_dir / "daily_topics.json"
        should_collect = force_collect or (collect_if_missing and not topics_path.exists())
        if should_collect:
            exported_files.update(self.collect_topics())

        try:
            selection = self.topic_automation.prepare_auto_run(
                rank=topic_rank,
                title_query=topic_title,
                allow_generated_duplicates=refresh_cache or dry_run,
            )
        except (FileNotFoundError, ValueError) as exc:
            if not self._should_retry_topic_collection(exc, already_collected=should_collect):
                raise
            self.logger.warning("Topic selection failed, recollecting topics once: %s", exc)
            exported_files.update(self.collect_topics())
            selection = self.topic_automation.prepare_auto_run(
                rank=topic_rank,
                title_query=topic_title,
                allow_generated_duplicates=refresh_cache or dry_run,
            )
        exported_files.update({name: str(path) for name, path in selection.exported_files.items()})

        content_exports = self.manual_run(
            paste_text=selection.input_text,
            style_override=style_override,
            dry_run=dry_run,
            refresh_cache=refresh_cache,
            include_input_dir=False,
            fast_mode=fast_mode,
        )
        exported_files.update(content_exports)

        if self.settings.automation.archive_outputs and not no_archive:
            archived_files = self.topic_automation.archive_run(exported_files, selected_topic=selection.topic)
            exported_files.update({name: str(path) for name, path in archived_files.items()})
            archive_root = archived_files.get("archive_root")
            if self.settings.review.auto_prepare and isinstance(archive_root, Path):
                review_exports = self.review_service.prepare_review(archive_root)
                exported_files.update({name: str(path) for name, path in review_exports.items()})

        return exported_files

    def _should_retry_topic_collection(self, exc: Exception, already_collected: bool) -> bool:
        if already_collected:
            return False
        message = str(exc)
        retry_markers = (
            "daily_topics.json not found",
            "daily_topics.json contains no available topics",
            "No unpublished topics are available for today",
        )
        return any(marker in message for marker in retry_markers)

    def daily_run(
        self,
        style_override: str | None = None,
        dry_run: bool = False,
        refresh_cache: bool = False,
        topic_rank: int | None = None,
        topic_title: str | None = None,
        no_archive: bool = False,
        fast_mode: bool = False,
    ) -> dict[str, str]:
        exported_files = self.collect_topics()
        benchmark_refresh_exports: dict[str, str] = {}
        try:
            topic_report = self.topic_automation._load_daily_topics()
            refreshed = self.growth_ops_service.collect_self_benchmark_samples_from_report(
                topic_report,
                account_key=self.settings.account_key,
                top_n=10,
                source_path=str(exported_files.get("daily_topics", self.settings.output_dir / "daily_topics.json")),
            )
            if refreshed:
                benchmark_refresh_exports = {name: str(path) for name, path in refreshed.items()}
                exported_files.update(benchmark_refresh_exports)
        except Exception as exc:
            self.logger.warning("Self-benchmark refresh skipped for daily run: %s", exc)

        candidate_specs = self._build_daily_candidate_specs(
            topic_rank=topic_rank,
            topic_title=topic_title,
            refresh_cache=refresh_cache,
            dry_run=dry_run,
        )

        if not candidate_specs:
            raise ValueError("No unpublished topics are available for today.")

        losing_attempts: list[dict[str, object]] = []
        best_attempt: dict[str, object] | None = None

        for attempt_index, candidate in enumerate(candidate_specs, start=1):
            self.logger.info(
                "Daily workflow attempt %s/%s: %s",
                attempt_index,
                len(candidate_specs),
                candidate["title"],
            )
            attempt_exports = self.auto_run(
                style_override=style_override,
                dry_run=dry_run,
                refresh_cache=refresh_cache,
                topic_title=str(candidate["title"]),
                collect_if_missing=False,
                force_collect=False,
                no_archive=no_archive,
                fast_mode=fast_mode,
            )
            exported_files.update(attempt_exports)

            run_dir = self._resolve_run_dir_from_exports(attempt_exports)
            score_snapshot = self._load_run_score_snapshot(run_dir)
            qualifies = self._qualifies_for_auto_approval(score_snapshot)
            attempt_record = {
                "attempt_index": attempt_index,
                "candidate_rank": int(candidate["rank"]),
                "topic_title": str(candidate["title"]),
                "topic_url": str(candidate["url"]),
                "run_dir": str(run_dir) if run_dir else "",
                "score_snapshot": score_snapshot,
                "qualified_for_auto_approval": qualifies,
            }

            if best_attempt is None or self._is_better_attempt(attempt_record, best_attempt):
                best_attempt = {
                    **attempt_record,
                    "exported_files": dict(attempt_exports),
                }

            if no_archive or run_dir is None:
                attempt_record["status"] = "generated"
                report_exports = self._write_daily_workflow_report(
                    status="success",
                    final_attempt=attempt_record,
                    attempts=[attempt_record],
                    supporting_exports=benchmark_refresh_exports,
                )
                exported_files.update(report_exports)
                return exported_files

            if qualifies:
                approve_exports = self.approve_run(
                    str(run_dir),
                    selected_title_index=1,
                    review_notes="Auto-approved by the daily automation workflow.",
                    auto_publish=False,
                )
                attempt_exports.update(approve_exports)
                exported_files.update(approve_exports)
                platform_exports, platform_results, platform_errors = self._run_daily_platform_automation(
                    run_dir,
                    dry_run=dry_run,
                )
                workflow_status = self._summarize_platform_workflow_status(platform_results, platform_errors)
                attempt_exports.update(platform_exports)
                exported_files.update(platform_exports)

                if losing_attempts:
                    self._mark_attempts_skipped(losing_attempts)

                final_attempt = {
                    **attempt_record,
                    "status": workflow_status if platform_errors else self._summarize_distribution_status(platform_results),
                    "selected_title": self._load_selected_title(run_dir),
                    "platform_results": platform_results,
                    "platform_errors": platform_errors,
                }
                report_exports = self._write_daily_workflow_report(
                    status=workflow_status,
                    final_attempt=final_attempt,
                    attempts=[*losing_attempts, final_attempt],
                    supporting_exports=benchmark_refresh_exports,
                )
                exported_files.update(report_exports)
                return exported_files

            attempt_record["status"] = "candidate_rejected"
            losing_attempts.append(attempt_record)

        if best_attempt is None:
            raise ValueError("Daily workflow did not produce any runnable candidates.")

        skipped_attempts = [item for item in losing_attempts if item.get("run_dir") != best_attempt.get("run_dir")]
        if skipped_attempts:
            self._mark_attempts_skipped(skipped_attempts)

        final_attempt = {
            key: value
            for key, value in best_attempt.items()
            if key != "exported_files"
        }
        final_attempt["status"] = "review_required"
        final_attempt["selected_title"] = self._load_selected_title(self._path_or_none(best_attempt.get("run_dir")))
        exported_files.update(dict(best_attempt.get("exported_files", {})))
        report_exports = self._write_daily_workflow_report(
            status="review_required",
            final_attempt=final_attempt,
            attempts=[*skipped_attempts, final_attempt],
            supporting_exports=benchmark_refresh_exports,
        )
        exported_files.update(report_exports)
        return exported_files

    def prepare_review(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.prepare_review(resolved)
        return {name: str(path) for name, path in result.items()}

    def approve_run(
        self,
        run_dir: str | None = None,
        selected_title: str | None = None,
        selected_title_index: int | None = None,
        review_notes: str | None = None,
        auto_publish: bool = False,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.approve_review(
            resolved,
            selected_title=selected_title,
            title_index=selected_title_index,
            reviewer_notes=review_notes,
            auto_publish=auto_publish,
        )
        return {name: str(path) for name, path in result.items()}

    def reject_run(self, run_dir: str | None = None, review_notes: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.reject_review(resolved, reviewer_notes=review_notes)
        return {name: str(path) for name, path in result.items()}

    def publish_run(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.review_service.publish_run(resolved)
        return {name: str(path) for name, path in result.items()}

    def publish_latest_approved(self, unpublished_only: bool = True, today_only: bool = True) -> dict[str, str]:
        resolved = self.review_service.resolve_latest_approved_run(
            unpublished_only=unpublished_only,
            today_only=today_only,
        )
        result = self.review_service.publish_run(resolved)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def publish_wechat_run(self, run_dir: str | None = None, submit: bool = False) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.wechat_service.publish_run(resolved, submit=submit)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def publish_wechat_latest_approved(
        self,
        unpublished_only: bool = False,
        today_only: bool = True,
        submit: bool = False,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_latest_approved_run(
            unpublished_only=unpublished_only,
            today_only=today_only,
        )
        result = self.wechat_service.publish_run(resolved, submit=submit)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def approve_latest(
        self,
        selected_title: str | None = None,
        selected_title_index: int | None = None,
        review_notes: str | None = None,
        auto_publish: bool = False,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_latest_pending_run()
        result = self.review_service.approve_review(
            resolved,
            selected_title=selected_title,
            title_index=selected_title_index,
            reviewer_notes=review_notes,
            auto_publish=auto_publish,
        )
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def initialize_publish_login(self) -> dict[str, str]:
        result = self.review_service.publish_service.initialize_login_session()
        return {name: str(value) for name, value in result.items()}

    def initialize_wechat_publish_login(self) -> dict[str, str]:
        result = self.wechat_service.initialize_login_session()
        return {name: str(value) for name, value in result.items()}

    def prepare_wechat_launch_day(self, day_index: int) -> dict[str, str]:
        result = self.wechat_launch_service.prepare_day(day_index)
        return {name: str(path) for name, path in result.items()}

    def prepare_wechat_launch_week(self) -> dict[str, str]:
        result = self.wechat_launch_service.prepare_week()
        return {name: str(path) for name, path in result.items()}

    def generate_images_for_run(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.image_generation_service.generate_for_run(resolved)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def collect_publish_preflight(self) -> dict[str, str]:
        result = self.review_service.publish_service.export_preflight_summary()
        return {name: str(value) for name, value in result.items()}

    def build_benchmark_report(self) -> dict[str, str]:
        result = self.growth_ops_service.build_benchmark_report()
        return {name: str(path) for name, path in result.items()}

    def collect_benchmark_hot_topics(
        self,
        *,
        platform: str | None = None,
        track: str | None = None,
        limit: int = 20,
    ) -> dict[str, str]:
        agent = BenchmarkMonitorAgent(self.settings, self.logger)
        result = agent.run(platform=platform, track=track, limit=limit)
        return {name: str(path) for name, path in result.items()}

    def collect_self_benchmark_samples(
        self,
        account_key: str | None = None,
        track: str | None = None,
        top_n: int = 10,
    ) -> dict[str, str]:
        prompt_repository = PromptRepository(self.settings.prompts_dir)
        openai_service = OpenAIContentService(self.settings, self.logger)
        try:
            agent = SampleCollectorAgent(self.settings, prompt_repository, openai_service, self.logger)
            result = agent.run(
                account_key=account_key or self.settings.account_key,
                track=track,
                top_n=top_n,
            )
            return {name: str(path) for name, path in result.items()}
        finally:
            openai_service.close()

    def backfill_performance_ledger(self) -> dict[str, str]:
        result = self.growth_ops_service.backfill_performance_ledger()
        return {name: str(path) for name, path in result.items()}

    def record_performance(
        self,
        run_dir: str | None = None,
        *,
        platform: str,
        snapshot_date: str | None = None,
        impression_count: int | None = None,
        read_count: int | None = None,
        recommendation_read_count: int | None = None,
        like_count: int | None = None,
        wow_count: int | None = None,
        share_count: int | None = None,
        comment_count: int | None = None,
        follower_gain: int | None = None,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        result = self.growth_ops_service.record_performance(
            run_dir=resolved,
            platform=platform,
            snapshot_date=snapshot_date,
            impression_count=impression_count,
            read_count=read_count,
            recommendation_read_count=recommendation_read_count,
            like_count=like_count,
            wow_count=wow_count,
            share_count=share_count,
            comment_count=comment_count,
            follower_gain=follower_gain,
        )
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def build_performance_report(self) -> dict[str, str]:
        result = self.growth_ops_service.build_performance_report()
        return {name: str(path) for name, path in result.items()}

    def evaluate_latest_run_publish_readiness(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        workflow = self.review_service._load_workflow(resolved)
        prompt_repository = PromptRepository(self.settings.prompts_dir)
        openai_service = OpenAIContentService(self.settings, self.logger)
        try:
            workflow_service = AgentWorkflowService(self.settings, prompt_repository, openai_service, self.logger)
            preflight_summary = self.review_service.publish_service.collect_preflight_summary()
            publish_review = workflow_service.publish_review_agent.run(
                workflow.content,
                workflow.data_report,
                self.settings.get_style(workflow.style_key),
                workflow.content_score,
                preflight_summary=preflight_summary,
            )
            workflow.content_score.publish_readiness_score = int(publish_review.get("publish_readiness_score", 0))
            workflow.content_score.publish_risk_flags = list(publish_review.get("publish_risk_flags", []))
            workflow.content_score.publish_recommendation = str(
                publish_review.get("publish_recommendation", "manual_review")
            )
            workflow.content_score.publish_notes = list(publish_review.get("publish_notes", []))
            workflow_path = resolved / "workflow_result.json"
            score_path = resolved / "content_score.json"
            workflow_path.write_text(json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            score_payload = json.loads(score_path.read_text(encoding="utf-8")) if score_path.exists() else {}
            score_payload["scores"] = {
                **dict(score_payload.get("scores", {})),
                "publish_readiness_score": workflow.content_score.publish_readiness_score,
                "validation_score": workflow.content.validation_score,
            }
            score_payload["publish_risk_flags"] = workflow.content_score.publish_risk_flags
            score_payload["publish_recommendation"] = workflow.content_score.publish_recommendation
            score_payload["publish_notes"] = workflow.content_score.publish_notes
            score_payload["preflight_summary"] = preflight_summary
            score_path.write_text(json.dumps(score_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                "workflow_result": str(workflow_path),
                "content_score": str(score_path),
            }
        finally:
            openai_service.close()

    def resolve_run_dir(self, run_dir: str | None = None) -> Path:
        return self.review_service.resolve_run_dir(run_dir)

    def _build_daily_candidate_specs(
        self,
        *,
        topic_rank: int | None,
        topic_title: str | None,
        refresh_cache: bool,
        dry_run: bool,
    ) -> list[dict[str, object]]:
        report = self.topic_automation._load_daily_topics()
        available_topics = self.topic_automation._available_topics(
            report.topics,
            allow_generated_duplicates=refresh_cache or dry_run,
        )
        if not available_topics:
            return []

        if topic_title:
            normalized_query = self.topic_automation._normalize_text(topic_title)
            for available_rank, topic in enumerate(available_topics, start=1):
                if normalized_query in self.topic_automation._normalize_text(topic.title):
                    return [
                        {
                            "rank": available_rank,
                            "title": topic.title,
                            "url": topic.url,
                        }
                    ]
            raise ValueError(f"No unpublished topic matched title query: {topic_title}")

        if topic_rank is not None:
            if topic_rank < 1:
                raise ValueError("--topic-rank must be greater than or equal to 1.")
            if topic_rank > len(available_topics):
                raise ValueError(
                    f"Only {len(available_topics)} unpublished topics remain, cannot select rank {topic_rank}."
                )
            topic = available_topics[topic_rank - 1]
            return [
                {
                    "rank": topic_rank,
                    "title": topic.title,
                    "url": topic.url,
                }
            ]

        max_attempts = max(1, int(self.settings.automation.max_daily_topic_attempts))
        return [
            {
                "rank": available_rank,
                "title": topic.title,
                "url": topic.url,
            }
            for available_rank, topic in enumerate(available_topics[:max_attempts], start=1)
        ]

    def _resolve_run_dir_from_exports(self, exported_files: dict[str, str]) -> Path | None:
        run_dir_candidates = [
            exported_files.get("archive_root"),
            exported_files.get("resolved_run_dir"),
        ]
        review_status_path = exported_files.get("review_status")
        if review_status_path:
            run_dir_candidates.append(str(Path(review_status_path).resolve().parent))

        publish_result_path = exported_files.get("publish_result")
        if publish_result_path:
            run_dir_candidates.append(str(Path(publish_result_path).resolve().parent.parent))

        for raw_path in run_dir_candidates:
            if not raw_path:
                continue
            candidate = Path(raw_path).resolve()
            if candidate.is_dir():
                return candidate
        return None

    def _load_run_score_snapshot(self, run_dir: Path | None) -> dict[str, int]:
        if run_dir is None:
            return {}
        score_path = run_dir / "content_score.json"
        if not score_path.exists():
            return {}
        payload = json.loads(score_path.read_text(encoding="utf-8"))
        raw_scores = payload.get("scores", {}) if isinstance(payload, dict) else {}
        return {
            "overall_score": int(raw_scores.get("overall_score", 0)),
            "ai_generated_risk": int(raw_scores.get("ai_generated_risk", 100)),
            "validation_score": int(raw_scores.get("validation_score", 0)),
            "publish_readiness_score": int(raw_scores.get("publish_readiness_score", 0)),
        }

    def _qualifies_for_auto_approval(self, score_snapshot: dict[str, int]) -> bool:
        if not score_snapshot:
            return False
        return self.settings.review.qualifies_for_auto_publish(
            overall_score=int(score_snapshot.get("overall_score", 0)),
            ai_risk=int(score_snapshot.get("ai_generated_risk", 100)),
            validation_score=int(score_snapshot.get("validation_score", 0)),
            publish_readiness_score=int(score_snapshot.get("publish_readiness_score", 0)),
        )

    def _is_better_attempt(self, candidate: dict[str, object], current_best: dict[str, object]) -> bool:
        candidate_scores = dict(candidate.get("score_snapshot", {}))
        current_scores = dict(current_best.get("score_snapshot", {}))
        candidate_total = (
            int(candidate_scores.get("overall_score", 0))
            + int(candidate_scores.get("validation_score", 0))
            + int(candidate_scores.get("publish_readiness_score", 0))
            - int(candidate_scores.get("ai_generated_risk", 100))
        )
        current_total = (
            int(current_scores.get("overall_score", 0))
            + int(current_scores.get("validation_score", 0))
            + int(current_scores.get("publish_readiness_score", 0))
            - int(current_scores.get("ai_generated_risk", 100))
        )
        if candidate_total != current_total:
            return candidate_total > current_total
        return int(candidate.get("candidate_rank", 9999)) < int(current_best.get("candidate_rank", 9999))

    def _run_daily_platform_automation(
        self,
        run_dir: Path,
        *,
        dry_run: bool,
    ) -> tuple[dict[str, str], dict[str, dict[str, object]], list[str]]:
        if dry_run:
            return {}, {}, []

        exported_files: dict[str, str] = {}
        platform_results: dict[str, dict[str, object]] = {}
        platform_errors: list[str] = []

        if not self.settings.skip_image_generation:
            try:
                image_exports = self.image_generation_service.generate_for_run(run_dir)
                exported_files.update({name: str(path) for name, path in image_exports.items()})
            except Exception as exc:
                message = f"Image generation failed before publish: {exc}"
                platform_errors.append(message)
                self.logger.exception(message)

        if self.settings.publishing.enabled and self.settings.publishing.auto_publish:
            try:
                toutiao_exports = self.publish_run(str(run_dir))
                exported_files.update(toutiao_exports)
                platform_results["toutiao"] = self._load_toutiao_publish_snapshot(run_dir)
            except Exception as exc:
                message = f"Toutiao publish failed: {exc}"
                platform_errors.append(message)
                platform_results["toutiao"] = {"status": "failed", "error": str(exc)}
                self.logger.exception(message)

        if self.settings.wechat.enabled:
            try:
                wechat_exports = self.publish_wechat_run(str(run_dir), submit=self.settings.wechat.auto_publish)
                exported_files.update(wechat_exports)
                platform_results["wechat"] = self._load_wechat_publish_snapshot(run_dir)
                snapshot_path = self._write_latest_wechat_automation_snapshot(run_dir)
                exported_files["wechat_automation_result"] = str(snapshot_path)
            except Exception as exc:
                message = f"WeChat publish failed: {exc}"
                platform_errors.append(message)
                platform_results["wechat"] = {"status": "failed", "error": str(exc)}
                self.logger.exception(message)

        return exported_files, platform_results, platform_errors

    def _load_toutiao_publish_snapshot(self, run_dir: Path) -> dict[str, object]:
        result_path = run_dir / self.settings.review.publish_dirname / "publish_result.json"
        if not result_path.exists():
            return {"status": "missing"}
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"status": "invalid"}
        return {
            "status": str(payload.get("status", "")).strip() or "published",
            "external_url": str(payload.get("external_url", "")).strip(),
        }

    def _load_wechat_publish_snapshot(self, run_dir: Path) -> dict[str, object]:
        result_path = run_dir / self.settings.review.publish_dirname / "wechat" / "wechat_publish_result.json"
        if not result_path.exists():
            return {"status": "missing"}
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"status": "invalid"}
        status = str(payload.get("status", "")).strip()
        submit_status = str(payload.get("submit_status", "")).strip()
        external_url = str(payload.get("external_url", "")).strip()
        draft_media_id = str(payload.get("draft_media_id", "")).strip()
        if not status:
            if submit_status or external_url:
                status = "submitted"
            elif draft_media_id:
                status = "draft_created"
        return {
            "status": status or "draft_created",
            "submit_requested": bool(payload.get("submit_requested", False)),
            "submit_status": submit_status,
            "external_url": external_url,
            "draft_media_id": draft_media_id,
        }

    def _write_latest_wechat_automation_snapshot(self, run_dir: Path) -> Path:
        source_path = run_dir / self.settings.review.publish_dirname / "wechat" / "wechat_publish_result.json"
        snapshot_path = self.settings.automation_dir / "last_wechat_publish_result.json"
        if source_path.exists():
            snapshot_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        return snapshot_path

    def _summarize_platform_workflow_status(
        self,
        platform_results: dict[str, dict[str, object]],
        platform_errors: list[str],
    ) -> str:
        if not platform_errors:
            return "success"
        if self._count_successful_platform_results(platform_results) > 0:
            return "platform_partial_success"
        return "platform_publish_failed"

    def _count_successful_platform_results(self, platform_results: dict[str, dict[str, object]]) -> int:
        successful_statuses = {"published", "submitted", "draft_created"}
        count = 0
        for payload in platform_results.values():
            status = str(payload.get("status", "")).strip()
            if status in successful_statuses:
                count += 1
        return count

    def _summarize_distribution_status(self, platform_results: dict[str, dict[str, object]]) -> str:
        if not platform_results:
            return "approved"
        statuses = {str(item.get("status", "")).strip() for item in platform_results.values()}
        if "submitted" in statuses or "published" in statuses:
            return "published"
        if "draft_created" in statuses:
            return "draft_created"
        return "approved"

    def _mark_attempts_skipped(self, attempts: list[dict[str, object]]) -> None:
        for attempt in attempts:
            run_dir = self._path_or_none(attempt.get("run_dir"))
            if run_dir is None:
                continue
            reason = self._build_skip_reason(dict(attempt.get("score_snapshot", {})))
            self.review_service.mark_skipped_review(run_dir, reviewer_notes=reason)
            attempt["status"] = "skipped"

    def _build_skip_reason(self, score_snapshot: dict[str, int]) -> str:
        if not score_snapshot:
            return "Skipped by the daily automation fallback logic."
        return (
            "Skipped by the daily automation fallback logic because the score thresholds were not met: "
            f"overall={int(score_snapshot.get('overall_score', 0))}, "
            f"validation={int(score_snapshot.get('validation_score', 0))}, "
            f"publish_readiness={int(score_snapshot.get('publish_readiness_score', 0))}, "
            f"ai_risk={int(score_snapshot.get('ai_generated_risk', 100))}."
        )

    def _write_daily_workflow_report(
        self,
        *,
        status: str,
        final_attempt: dict[str, object],
        attempts: list[dict[str, object]],
        supporting_exports: dict[str, str] | None = None,
    ) -> dict[str, str]:
        report_payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "account_slug": self.settings.account_slug,
            "status": status,
            "max_daily_topic_attempts": self.settings.automation.max_daily_topic_attempts,
            "final_attempt": final_attempt,
            "attempts": attempts,
        }
        if supporting_exports:
            report_payload["supporting_exports"] = dict(supporting_exports)
        report_path = self.settings.automation_dir / "last_daily_workflow_report.json"
        report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"workflow_report": str(report_path)}

    def _load_selected_title(self, run_dir: Path | None) -> str:
        if run_dir is None:
            return ""
        status_path = run_dir / "review_status.json"
        if not status_path.exists():
            return ""
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        return str(payload.get("selected_title", "")).strip()

    def _path_or_none(self, raw_path: object) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(str(raw_path)).resolve()
        if candidate.exists():
            return candidate
        return None
