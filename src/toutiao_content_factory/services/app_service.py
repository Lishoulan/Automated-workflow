from __future__ import annotations

import json
import logging
import os
import shutil
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path


def _safe_json_loads(text: str, *, default: dict | list | None = None) -> dict | list | None:
    """安全解析JSON字符串，解析失败返回默认值。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return default

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.exceptions import LowQualityAbortException, PlatformAuthError
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
from toutiao_content_factory.services.wechat_service import WechatIpWhitelistBlockedException, WechatPublishService
from toutiao_content_factory.services.wechat_launch_service import WechatLaunchService
from toutiao_content_factory.services.image_generation_service import ImageGenerationService
from toutiao_content_factory.services.topic_pool_service import TopicPoolService
from toutiao_content_factory.services.platform_health_service import PlatformHealthService
from toutiao_content_factory.services.content_guard_service import ContentGuardService
from toutiao_content_factory.services.notification_service import NotificationService
from toutiao_content_factory.services.circuit_breaker import PlatformCircuitBreaker
from toutiao_content_factory.services.company_os_service import CompanyOSService
from toutiao_content_factory.services.lead_ops_service import LeadOpsService
from toutiao_content_factory.services.monetization_service import MonetizationService
from toutiao_content_factory.services.revenue_engine_service import RevenueEngineService


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
        self.topic_pool_service = TopicPoolService(settings, logger)
        self.content_guard_service = ContentGuardService(settings, logger)
        self.company_os_service = CompanyOSService(settings, logger)
        self.lead_ops_service = LeadOpsService(settings, logger)
        self.monetization_service = MonetizationService(settings, logger)
        self.revenue_engine_service = RevenueEngineService(settings, logger)
        self.notification_service = NotificationService(dict(os.environ), fallback_log_dir=str(self.settings.output_dir / "automation"))
        if not self.notification_service.is_configured:
            self.logger.debug(
                "No notification channels configured. Set at least one of: "
                "BARK_URL, FEISHU_WEBHOOK_URL, DINGTALK_WEBHOOK_URL, WECOM_WEBHOOK_URL. "
                "All notifications will be silently dropped."
            )
        self.platform_health_service = PlatformHealthService(
            settings,
            logger,
            toutiao_publish_service=self.review_service.publish_service,
            wechat_publish_service=self.wechat_service,
            notification_service=self.notification_service,
        )
        self.circuit_breaker = PlatformCircuitBreaker()

    def collect_topics(self) -> dict[str, str]:
        topic_collector = TopicCollectionService(self.settings, self.logger)
        topic_exporter = TopicExportService(
            self.settings.output_dir,
            self.logger,
            topic_pool_service=self.topic_pool_service,
        )
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
        payload = _safe_json_loads(benchmark_json_path.read_text(encoding="utf-8"), default={})
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
        # 轻量级健康检查：blocking 时优雅退出，warnings 时继续执行
        if not dry_run:
            try:
                health = self.platform_health_service.quick_health_check()
            except Exception as exc:
                self.logger.warning("auto_run quick_health_check failed (non-fatal): %s", exc)
            else:
                blocking_platforms = [str(p) for p in health.get("blocking_platforms", []) if str(p).strip()]
                if blocking_platforms:
                    self.logger.error(
                        "auto_run blocked by platform health check: %s",
                        ", ".join(blocking_platforms),
                    )
                    return {"status": "blocked", "blocking_platforms": ", ".join(blocking_platforms)}
                warnings = health.get("blocking_flags", [])
                if warnings:
                    self.logger.warning(
                        "auto_run health check warnings: %s",
                        "; ".join(str(w) for w in warnings[:5]),
                    )

        # 前置健康检查：如果平台认证失败则通知并终止
        if not dry_run:
            self._preflight_health_check()

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
        if selection is None:
            self.logger.warning("No eligible topics found. Attempting to recollect.")
            if not should_collect:
                exported_files.update(self.collect_topics())
                selection = self.topic_automation.prepare_auto_run(
                    rank=topic_rank,
                    title_query=topic_title,
                    allow_generated_duplicates=True,
                )
            if selection is None:
                self.notification_service._broadcast(
                    "【自动运行】无可用选题",
                    f"账号: {self.settings.account_label}\n"
                    f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                    f"原因: 话题池为空或所有话题已发布\n"
                    f"建议: 检查话题源是否正常，或手动添加选题",
                )
                raise ValueError("No eligible topics available after re-collection.")
        guard_result = self.content_guard_service.evaluate_candidate_topic(
            topic_title=selection.topic.title,
            topic_url=selection.topic.url,
        )
        if bool(guard_result.get("blocked")):
            raise ValueError(
                "Selected topic is too similar to a recent published run. "
                f"Matched: {guard_result.get('matched_title', '')} "
                f"(similarity={guard_result.get('similarity', 0.0)})."
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
                guard_report = self.content_guard_service.evaluate_run(archive_root)
                exported_files["content_guard_report"] = str(archive_root / "content_guard_report.json")
                if guard_report.get("blocked"):
                    self.logger.warning(
                        "Generated run is too similar to a recent published run: matched=%s similarity=%s",
                        guard_report.get("matched_title", ""),
                        guard_report.get("similarity", 0.0),
                    )

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
        skip_wait: bool = False,
    ) -> dict[str, str]:
        if not self._acquire_daily_run_lock():
            self.logger.warning("Another daily_run is already in progress. Skipping this invocation.")
            return {"status": "skipped", "reason": "another_daily_run_in_progress"}
        try:
            return self._daily_run_impl(
                style_override=style_override,
                dry_run=dry_run,
                refresh_cache=refresh_cache,
                topic_rank=topic_rank,
                topic_title=topic_title,
                no_archive=no_archive,
                fast_mode=fast_mode,
                skip_wait=skip_wait,
            )
        finally:
            self._release_daily_run_lock()

    def _daily_run_impl(
        self,
        style_override: str | None = None,
        dry_run: bool = False,
        refresh_cache: bool = False,
        topic_rank: int | None = None,
        topic_title: str | None = None,
        no_archive: bool = False,
        fast_mode: bool = False,
        skip_wait: bool = False,
    ) -> dict[str, str]:
        # 轻量级健康检查：blocking 时优雅退出，warnings 时继续执行
        if not dry_run:
            try:
                health = self.platform_health_service.quick_health_check()
            except Exception as exc:
                self.logger.warning("daily_run quick_health_check failed (non-fatal): %s", exc)
            else:
                blocking_platforms = [str(p) for p in health.get("blocking_platforms", []) if str(p).strip()]
                if blocking_platforms:
                    self.logger.error(
                        "daily_run blocked by platform health check: %s",
                        ", ".join(blocking_platforms),
                    )
                    return {"status": "blocked", "blocking_platforms": ", ".join(blocking_platforms)}
                warnings = health.get("blocking_flags", [])
                if warnings:
                    self.logger.warning(
                        "daily_run health check warnings: %s",
                        "; ".join(str(w) for w in warnings[:5]),
                    )

        self._cleanup_stale_runs()
        exported_files = self.collect_topics()
        self._heartbeat_daily_run_lock()
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

        auth_health = self.platform_health_service.ensure_publish_ready()
        platform_health_path = str(auth_health.get("platform_health", "")).strip()
        if platform_health_path:
            exported_files["platform_health"] = platform_health_path
        blocking_platforms = [str(item) for item in auth_health.get("blocking_platforms", []) if str(item).strip()]
        if blocking_platforms:
            self._degrade_blocked_platforms_to_draft(blocking_platforms)
            message = (
                "Publish login precheck failed, degraded to draft mode. "
                f"blocked_platforms={', '.join(blocking_platforms)}; "
                f"account={self.settings.account_key}; "
                f"platform_health={platform_health_path or 'N/A'}"
            )
            self.logger.warning(message)
            self._notify_platform_auth_failure(
                blocking_platforms=blocking_platforms,
                platform_health_path=platform_health_path,
                auth_health=auth_health,
            )

        candidate_specs = self._build_daily_candidate_specs(
            topic_rank=topic_rank,
            topic_title=topic_title,
            refresh_cache=refresh_cache,
            dry_run=dry_run,
        )
        self._heartbeat_daily_run_lock()

        if not candidate_specs:
            self.logger.info("No unpublished topics available. Attempting to refresh topic pool.")
            try:
                self.collect_topics()
                candidate_specs = self._build_daily_candidate_specs(
                    topic_rank=topic_rank,
                    topic_title=topic_title,
                    refresh_cache=True,
                    dry_run=dry_run,
                )
            except Exception as exc:
                self.logger.warning("Topic re-collection also failed: %s", exc)

            if not candidate_specs:
                self.notification_service._broadcast(
                    "【定时任务】今日无可用选题",
                    f"账号: {self.settings.account_label}\n"
                    f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                    f"原因: 话题池为空或所有话题已发布\n"
                    f"建议: 检查话题源是否正常，或手动添加选题",
                )
                return {
                    "status": "no_topics",
                    "message": "No eligible topics available. Will retry at next scheduled time.",
                }

        losing_attempts: list[dict[str, object]] = []
        best_attempt: dict[str, object] | None = None

        for attempt_index, candidate in enumerate(candidate_specs, start=1):
            self.logger.info(
                "Daily workflow attempt %s/%s: %s",
                attempt_index,
                len(candidate_specs),
                candidate["title"],
            )
            try:
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
            except LowQualityAbortException as exc:
                self.logger.info(
                    "Daily workflow skipped low-quality candidate %s/%s: %s",
                    attempt_index,
                    len(candidate_specs),
                    exc,
                )
                losing_attempts.append(
                    {
                        "attempt_index": attempt_index,
                        "candidate_rank": int(candidate["rank"]),
                        "topic_title": str(candidate["title"]),
                        "topic_url": str(candidate["url"]),
                        "run_dir": "",
                        "score_snapshot": {},
                        "qualified_for_auto_approval": False,
                        "status": "low_quality_skipped",
                        "skip_reason": str(exc),
                    }
                )
                continue
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
                    skip_wait=skip_wait,
                )
                self._heartbeat_daily_run_lock()
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

    def publish_run(
        self,
        run_dir: str | None = None,
        draft_only: bool = False,
        *,
        declare_first_release: bool = True,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        previous_draft_only = self.settings.publishing.draft_only
        object.__setattr__(self.settings.publishing, "draft_only", bool(draft_only))
        try:
            result = self.review_service.publish_run(resolved, declare_first_release=declare_first_release)
        finally:
            object.__setattr__(self.settings.publishing, "draft_only", previous_draft_only)
        return {name: str(path) for name, path in result.items()}

    def publish_latest_approved(
        self,
        unpublished_only: bool = True,
        today_only: bool = True,
        draft_only: bool = False,
    ) -> dict[str, str]:
        resolved = self.review_service.resolve_latest_approved_run(
            unpublished_only=unpublished_only,
            today_only=today_only,
        )
        previous_draft_only = self.settings.publishing.draft_only
        object.__setattr__(self.settings.publishing, "draft_only", bool(draft_only))
        try:
            result = self.review_service.publish_run(resolved)
        finally:
            object.__setattr__(self.settings.publishing, "draft_only", previous_draft_only)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def reset_account_state(self) -> dict[str, str]:
        automation_dir = self.settings.automation_dir
        automation_dir.mkdir(parents=True, exist_ok=True)
        cleaned: dict[str, str] = {}
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        for name in [
            "scheduled_status.json",
            "platform_health.json",
            "publish_preflight.json",
            "wechat_publish_preflight.json",
            "last_wechat_publish_result.json",
        ]:
            path = automation_dir / name
            if path.exists():
                backup = path.with_suffix(path.suffix + f".{timestamp}.bak")
                backup.write_text(path.read_text(encoding="utf-8-sig", errors="replace"), encoding="utf-8")
                cleaned[f"backup_{name}"] = str(backup)

        clean_payloads = {
            "scheduled_status.json": {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "last_run_time": datetime.now().isoformat(timespec="seconds"),
                "account_key": self.settings.account_key,
                "account_slug": self.settings.account_slug,
                "status": "reset",
                "overall_status": "idle",
                "overall_message": "Account automation state was reset.",
                "message": "Account automation state was reset.",
                "platforms": {
                    "toutiao": {"status": "idle", "last_run": "", "message": ""},
                    "wechat": {"status": "idle", "last_run": "", "message": ""},
                },
            },
            "platform_health.json": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "account_key": self.settings.account_key,
                "account_slug": self.settings.account_slug,
                "platforms": {},
            },
            "publish_preflight.json": {},
            "wechat_publish_preflight.json": {},
            "last_wechat_publish_result.json": {},
        }
        for name, payload in clean_payloads.items():
            target = automation_dir / name
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            cleaned[name.replace('.', '_')] = str(target)

        repaired_runs = 0
        for run_dir in self.settings.runs_dir.iterdir() if self.settings.runs_dir.exists() else []:
            if not run_dir.is_dir():
                continue
            publish_dir = run_dir / self.settings.review.publish_dirname
            if not publish_dir.exists():
                continue
            result_path = publish_dir / "publish_result.json"
            if not result_path.exists():
                continue
            try:
                json.loads(result_path.read_text(encoding="utf-8-sig"))
            except Exception:
                broken_backup = result_path.with_suffix(result_path.suffix + f".{timestamp}.bak")
                broken_backup.write_text(result_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                result_path.unlink(missing_ok=True)
                repaired_runs += 1

        cleaned["repaired_publish_results"] = str(repaired_runs)
        return cleaned

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

    def initialize_135editor_login(self) -> dict[str, str]:
        result = self.wechat_service.initialize_135editor_login_session()
        return {name: str(value) for name, value in result.items()}

    def retry_platform_publish(self, run_dir: str | None, platform: str) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        platform_key = str(platform or "").strip().lower()
        if platform_key not in {"toutiao", "wechat"}:
            raise ValueError(f"Unsupported retry platform: {platform}")

        if platform_key == "toutiao":
            result = self.review_service.publish_run(resolved)
            platform_snapshot = self._load_toutiao_publish_snapshot(resolved)
        else:
            result = self.wechat_service.publish_run(resolved, submit=self.settings.wechat.auto_publish)
            platform_snapshot = self._load_wechat_publish_snapshot(resolved)
            self._write_latest_wechat_automation_snapshot(resolved)

        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        status_path = self._write_retry_publish_status(
            run_dir=resolved,
            platform=platform_key,
            platform_snapshot=platform_snapshot,
            exported=exported,
        )
        exported["scheduled_status"] = str(status_path)
        return exported

    def prepare_wechat_launch_day(self, day_index: int) -> dict[str, str]:
        result = self.wechat_launch_service.prepare_day(day_index)
        return {name: str(path) for name, path in result.items()}

    def prepare_wechat_launch_week(self) -> dict[str, str]:
        result = self.wechat_launch_service.prepare_week()
        return {name: str(path) for name, path in result.items()}

    def generate_images_for_run(self, run_dir: str | None = None) -> dict[str, str]:
        resolved = self.review_service.resolve_run_dir(run_dir)
        if (not self.settings.wechat.enabled) and str(self.settings.publishing.cover_mode or "auto").strip().lower() == "none":
            publish_dir = resolved / self.settings.review.publish_dirname
            skipped_path = publish_dir / "image_generation_skipped.json"
            skipped_payload = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "account_key": self.settings.account_key,
                "run_dir": str(resolved),
                "reason": "Current workflow uses Toutiao no-cover mode and has no WeChat image consumer enabled.",
            }
            skipped_path.write_text(json.dumps(skipped_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                "resolved_run_dir": str(resolved),
                "image_generation_skipped": str(skipped_path),
            }
        include_infographics = bool(self.settings.wechat.enabled)
        result = self.image_generation_service.generate_for_run(resolved, include_infographics=include_infographics)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def audit_account_artifacts(self) -> dict[str, str]:
        configured_slugs = {profile.slug for profile in self.settings.account_profiles.values()}
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "configured_account_slugs": sorted(configured_slugs),
            "artifacts": {},
        }
        roots = {
            "output_accounts": self.settings.project_root / "output" / "accounts",
            "logs_accounts": self.settings.project_root / "logs" / "accounts",
            "runtime_accounts": self.settings.project_root / ".runtime" / "accounts",
            "cache_accounts": self.settings.project_root / "cache" / "accounts",
            "input_accounts": self.settings.project_root / "input" / "accounts",
        }
        stale_candidates = []
        for label, root in roots.items():
            entries = []
            if root.exists():
                for item in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
                    is_configured = item.name in configured_slugs
                    entries.append(
                        {
                            "name": item.name,
                            "path": str(item.resolve()),
                            "configured": is_configured,
                            "last_modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat(timespec="seconds"),
                        }
                    )
                    if not is_configured:
                        stale_candidates.append(str(item.resolve()))
            report["artifacts"][label] = entries
        report["stale_paths"] = sorted(set(stale_candidates))
        report_path = self.settings.automation_dir / "account_artifact_audit.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"account_artifact_audit": str(report_path)}

    def cleanup_stale_account_artifacts(self) -> dict[str, str]:
        configured_slugs = {profile.slug for profile in self.settings.account_profiles.values()}
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        trash_root = self.settings.project_root / ".cleanup_trash" / timestamp
        trash_root.mkdir(parents=True, exist_ok=True)
        moved = []
        roots = [
            self.settings.project_root / "output" / "accounts",
            self.settings.project_root / "logs" / "accounts",
            self.settings.project_root / ".runtime" / "accounts",
            self.settings.project_root / "cache" / "accounts",
            self.settings.project_root / "input" / "accounts",
        ]
        for root in roots:
            if not root.exists():
                continue
            for item in [p for p in root.iterdir() if p.is_dir() and p.name not in configured_slugs]:
                target = trash_root / item.relative_to(self.settings.project_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                item.rename(target)
                moved.append({"from": str(item.resolve()), "to": str(target.resolve())})
        result_path = self.settings.automation_dir / "account_artifact_cleanup.json"
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "configured_account_slugs": sorted(configured_slugs),
            "trash_root": str(trash_root.resolve()),
            "moved": moved,
        }
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"account_artifact_cleanup": str(result_path), "cleanup_trash_root": str(trash_root.resolve())}

    def collect_publish_preflight(self) -> dict[str, str]:
        result = self.review_service.publish_service.export_preflight_summary()
        return {name: str(value) for name, value in result.items()}

    def collect_platform_health(self) -> dict[str, str]:
        result = self.platform_health_service.collect()
        return {name: str(value) for name, value in result.items()}

    def build_benchmark_report(self) -> dict[str, str]:
        result = self.growth_ops_service.build_benchmark_report()
        return {name: str(path) for name, path in result.items()}

    def build_company_os(self) -> dict[str, str]:
        result = self.company_os_service.build_company_os()
        return {name: str(path) for name, path in result.items()}

    def build_revenue_engine(self) -> dict[str, str]:
        benchmark_report = self.company_os_service._load_json_if_exists(self.settings.output_dir / "growth_ops" / "benchmark_report.json")
        self_benchmark_report = self.company_os_service._load_json_if_exists(
            self.settings.output_dir / "growth_ops" / "self_benchmark_report.json"
        )
        integrations = self.company_os_service._load_integrations()
        result = self.revenue_engine_service.build_assets(
            integrations=integrations,
            benchmark_report=benchmark_report,
            self_benchmark_report=self_benchmark_report,
        )
        recommendation_result = self.lead_ops_service.enrich_generated_leads_with_recommendations()
        proposal_draft_result = self.lead_ops_service.auto_draft_generated_proposals()
        lead_assets = self.lead_ops_service.ensure_assets()
        payload = {name: str(path) for name, path in result.items()}
        payload.update({name: str(path) for name, path in lead_assets.items()})
        payload["enriched_generated_leads"] = str(recommendation_result.get("updated", 0))
        payload["auto_drafted_proposals"] = str(proposal_draft_result.get("drafted", 0))
        payload["auto_drafted_proposal_report"] = json.dumps(proposal_draft_result, ensure_ascii=False)
        return payload

    def run_profit_loop(self) -> dict[str, str]:
        benchmark_report = self.company_os_service._load_json_if_exists(self.settings.output_dir / "growth_ops" / "benchmark_report.json")
        self_benchmark_report = self.company_os_service._load_json_if_exists(
            self.settings.output_dir / "growth_ops" / "self_benchmark_report.json"
        )
        integrations = self.company_os_service._load_integrations()
        result = self.revenue_engine_service.run_profit_loop(
            integrations=integrations,
            benchmark_report=benchmark_report,
            self_benchmark_report=self_benchmark_report,
        )
        payload = {name: str(path) for name, path in result.items()}
        return payload

    def execute_profit_actions(self, *, action_types: tuple[str, ...] = (), limit: int = 0) -> dict[str, str]:
        benchmark_report = self.company_os_service._load_json_if_exists(self.settings.output_dir / "growth_ops" / "benchmark_report.json")
        self_benchmark_report = self.company_os_service._load_json_if_exists(
            self.settings.output_dir / "growth_ops" / "self_benchmark_report.json"
        )
        integrations = self.company_os_service._load_integrations()
        result = self.revenue_engine_service.execute_profit_actions(
            action_types=action_types,
            limit=limit,
            integrations=integrations,
            benchmark_report=benchmark_report,
            self_benchmark_report=self_benchmark_report,
        )
        return {name: str(path) for name, path in result.items()}

    def update_sponsor_outreach_status(
        self,
        *,
        action_key: str,
        status: str,
        notes: str = "",
        next_follow_up_at: str = "",
        deal_value: float | None = None,
    ) -> dict[str, str]:
        result = self.revenue_engine_service.update_sponsor_outreach_status(
            action_key=action_key,
            status=status,
            notes=notes,
            next_follow_up_at=next_follow_up_at,
            deal_value=deal_value,
        )
        return {name: str(path) for name, path in result.items()}

    def update_newsletter_subscriber_status(
        self,
        *,
        subscriber_key: str,
        status: str,
        notes: str = "",
        next_follow_up_at: str = "",
        member_revenue: float | None = None,
    ) -> dict[str, str]:
        result = self.revenue_engine_service.update_newsletter_subscriber_status(
            subscriber_key=subscriber_key,
            status=status,
            notes=notes,
            next_follow_up_at=next_follow_up_at,
            member_revenue=member_revenue,
        )
        return {name: str(path) for name, path in result.items()}

    def ensure_lead_assets(self) -> dict[str, str]:
        result = self.lead_ops_service.ensure_assets()
        return {name: str(path) for name, path in result.items()}

    def record_lead(
        self,
        *,
        name: str,
        contact: str,
        intent: str,
        source: str,
        notes: str = "",
        status: str = "new",
        estimated_value: float = 0.0,
        next_follow_up_at: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.record_lead(
            name=name,
            contact=contact,
            intent=intent,
            source=source,
            notes=notes,
            status=status,
            estimated_value=estimated_value,
            next_follow_up_at=next_follow_up_at,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_entry_id"] = str(entry.get("id", ""))
        return result

    def update_lead(
        self,
        *,
        lead_id: str,
        status: str | None = None,
        notes: str | None = None,
        estimated_value: float | None = None,
        actual_value: float | None = None,
        next_follow_up_at: str | None = None,
    ) -> dict[str, str]:
        entry = self.lead_ops_service.update_lead(
            lead_id=lead_id,
            status=status,
            notes=notes,
            estimated_value=estimated_value,
            actual_value=actual_value,
            next_follow_up_at=next_follow_up_at,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_entry_id"] = str(entry.get("id", ""))
        return result

    def generate_lead_proposal(
        self,
        *,
        lead_id: str,
        offer_name: str = "",
        offer_key: str = "",
        custom_price: str = "",
        custom_scope: str = "",
        next_follow_up_at: str = "",
        mark_as_sent: bool = True,
        auto_generated: bool = False,
    ) -> dict[str, str]:
        result = self.lead_ops_service.generate_lead_proposal(
            lead_id=lead_id,
            offer_name=offer_name,
            offer_key=offer_key,
            custom_price=custom_price,
            custom_scope=custom_scope,
            next_follow_up_at=next_follow_up_at,
            mark_as_sent=mark_as_sent,
            auto_generated=auto_generated,
        )
        assets = self.lead_ops_service.ensure_assets()
        payload = {name: str(path) for name, path in assets.items()}
        payload["proposal_path"] = str(result["proposal_path"])
        payload["lead_id"] = str(result["lead_id"])
        return payload

    def mark_lead_outreach_sent(
        self,
        *,
        lead_id: str,
        channel: str = "",
        message: str = "",
        next_follow_up_at: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.mark_outreach_sent(
            lead_id=lead_id,
            channel=channel,
            message=message,
            next_follow_up_at=next_follow_up_at,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["outreach_sent_at"] = str(entry.get("outreach_sent_at", ""))
        result["recommended_next_action"] = str(entry.get("recommended_next_action", ""))
        return result

    def triage_lead_reply(
        self,
        *,
        lead_id: str,
        reply_text: str,
        next_follow_up_at: str = "",
    ) -> dict[str, str]:
        result = self.lead_ops_service.triage_reply(
            lead_id=lead_id,
            reply_text=reply_text,
            next_follow_up_at=next_follow_up_at,
        )
        assets = self.lead_ops_service.ensure_assets()
        payload = {name: str(path) for name, path in assets.items()}
        payload["lead_id"] = str(result.get("lead_id", ""))
        payload["reply_temperature"] = str(result.get("reply_temperature", ""))
        payload["recommended_status"] = str(result.get("recommended_status", ""))
        payload["suggested_reply"] = str(result.get("suggested_reply", ""))
        payload["recommended_next_action"] = str(result.get("recommended_next_action", ""))
        payload["next_follow_up_at"] = str(result.get("next_follow_up_at", ""))
        return payload

    def mark_lead_proposal_sent(
        self,
        *,
        lead_id: str,
        channel: str = "",
        message: str = "",
        next_follow_up_at: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.mark_proposal_sent(
            lead_id=lead_id,
            channel=channel,
            message=message,
            next_follow_up_at=next_follow_up_at,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["proposal_sent_at"] = str(entry.get("proposal_sent_at", ""))
        result["recommended_next_action"] = str(entry.get("recommended_next_action", ""))
        return result

    def mark_lead_call_booked(
        self,
        *,
        lead_id: str,
        call_scheduled_for: str,
        notes: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.mark_call_booked(
            lead_id=lead_id,
            call_scheduled_for=call_scheduled_for,
            notes=notes,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["call_scheduled_for"] = str(entry.get("call_scheduled_for", ""))
        result["recommended_next_action"] = str(entry.get("recommended_next_action", ""))
        return result

    def mark_lead_payment_received(
        self,
        *,
        lead_id: str,
        amount: float,
        received_at: str = "",
        notes: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.mark_payment_received(
            lead_id=lead_id,
            amount=amount,
            received_at=received_at,
            notes=notes,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["payment_received_at"] = str(entry.get("payment_received_at", ""))
        result["actual_value"] = str(entry.get("actual_value", ""))
        return result

    def mark_lead_delivery_started(
        self,
        *,
        lead_id: str,
        delivery_notes: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.mark_delivery_started(
            lead_id=lead_id,
            delivery_notes=delivery_notes,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["delivery_started_at"] = str(entry.get("delivery_started_at", ""))
        return result

    def mark_lead_delivery_completed(
        self,
        *,
        lead_id: str,
        delivery_notes: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.mark_delivery_completed(
            lead_id=lead_id,
            delivery_notes=delivery_notes,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["delivery_completed_at"] = str(entry.get("delivery_completed_at", ""))
        return result

    def request_lead_testimonial(
        self,
        *,
        lead_id: str,
        message: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.request_testimonial(
            lead_id=lead_id,
            message=message,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["testimonial_requested_at"] = str(entry.get("testimonial_requested_at", ""))
        return result

    def mark_lead_expansion_opportunity(
        self,
        *,
        lead_id: str,
        expansion_type: str,
        notes: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.mark_expansion_opportunity(
            lead_id=lead_id,
            expansion_type=expansion_type,
            notes=notes,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(entry.get("id", ""))
        result["expansion_identified_at"] = str(entry.get("expansion_identified_at", ""))
        result["expansion_opportunity_type"] = str(entry.get("expansion_opportunity_type", ""))
        return result

    def convert_expansion_to_lead(
        self,
        *,
        source_lead_id: str,
        referral_name: str,
        referral_contact: str,
        referral_intent: str = "咨询",
        notes: str = "",
    ) -> dict[str, str]:
        entry = self.lead_ops_service.convert_expansion_to_lead(
            source_lead_id=source_lead_id,
            referral_name=referral_name,
            referral_contact=referral_contact,
            referral_intent=referral_intent,
            notes=notes,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_entry_id"] = str(entry.get("id", ""))
        result["referral_source_lead_id"] = str(entry.get("referral_source_lead_id", ""))
        return result

    def generate_lead_expansion_proposal(
        self,
        *,
        lead_id: str,
        expansion_type: str,
        next_follow_up_at: str = "",
    ) -> dict[str, str]:
        payload = self.lead_ops_service.generate_expansion_proposal(
            lead_id=lead_id,
            expansion_type=expansion_type,
            next_follow_up_at=next_follow_up_at,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_id"] = str(payload.get("lead_id", ""))
        result["proposal_path"] = str(payload.get("proposal_path", ""))
        result["offer_name"] = str(payload.get("offer_name", ""))
        result["expansion_type"] = str(payload.get("expansion_type", ""))
        return result

    def list_offer_choices(self) -> list[dict[str, str]]:
        return self.lead_ops_service.list_offer_choices()

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
            score_payload = _safe_json_loads(score_path.read_text(encoding="utf-8"), default={}) if score_path.exists() else {}
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
        payload = _safe_json_loads(score_path.read_text(encoding="utf-8"), default={})
        raw_scores = payload.get("scores", {}) if isinstance(payload, dict) else {}
        snapshot = {
            "overall_score": int(raw_scores.get("overall_score", 0)),
            "ai_generated_risk": int(raw_scores.get("ai_generated_risk", 100)),
            "validation_score": int(raw_scores.get("validation_score", 0)),
            "publish_readiness_score": int(raw_scores.get("publish_readiness_score", 0)),
        }
        article_path = run_dir / "publish" / "article.md"
        if not article_path.exists():
            article_path = run_dir / "article.md"
        if article_path.exists():
            article_text = article_path.read_text(encoding="utf-8").replace("\n", "").replace(" ", "")
            snapshot["article_char_count"] = len(article_text)
        return snapshot

    def _qualifies_for_auto_approval(self, score_snapshot: dict[str, int]) -> bool:
        if not score_snapshot:
            return False
        article_chars = int(score_snapshot.get("article_char_count", 0))
        if 0 < article_chars < 800:
            self.logger.warning(
                "Article has only %d chars, below 800-char minimum for auto-approval. "
                "Marking as review_required.",
                article_chars,
            )
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
        skip_wait: bool = False,
    ) -> tuple[dict[str, str], dict[str, dict[str, object]], list[str]]:
        if dry_run:
            return {}, {}, []

        exported_files: dict[str, str] = {}
        platform_results: dict[str, dict[str, object]] = {}
        platform_errors: list[str] = []

        should_generate_images = (
            not self.settings.skip_image_generation
            and (
                self.settings.wechat.enabled
                or str(self.settings.publishing.cover_mode or "auto").strip().lower() != "none"
            )
        )
        if should_generate_images:
            try:
                image_exports = self.image_generation_service.generate_for_run(
                    run_dir,
                    include_infographics=bool(self.settings.wechat.enabled),
                )
                exported_files.update({name: str(path) for name, path in image_exports.items()})
            except Exception as exc:
                message = f"Image generation failed before publish: {exc}"
                platform_errors.append(message)
                self.logger.exception(message)
        else:
            self.logger.info(
                "Skipping image generation for run %s because current platform workflow does not consume generated images.",
                run_dir,
            )

        wechat_completed = False
        wechat_blocked_by_ip = False
        if self.settings.wechat.enabled:
            if self.circuit_breaker.is_open("wechat"):
                self.logger.warning("WeChat publish skipped: circuit breaker is open for wechat.")
                platform_results["wechat"] = {"status": "circuit_open", "error": "Circuit breaker is open for wechat"}
            else:
                try:
                    wechat_exports = self.publish_wechat_run(str(run_dir), submit=True)
                    exported_files.update(wechat_exports)
                    platform_results["wechat"] = self._load_wechat_publish_snapshot(run_dir)
                    snapshot_path = self._write_latest_wechat_automation_snapshot(run_dir)
                    exported_files["wechat_automation_result"] = str(snapshot_path)
                    wechat_completed = self._wechat_publish_succeeded(platform_results["wechat"])
                    if wechat_completed:
                        self.circuit_breaker.record_success("wechat")
                        self.notification_service.notify_success(
                            platform="WeChat",
                            article_title=self._load_selected_title(run_dir),
                            run_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
                        )
                    else:
                        self.circuit_breaker.record_failure("wechat")
                        message = (
                            "WeChat publish did not reach a submitted state. "
                            "Toutiao publish is blocked to preserve WeChat first-release priority."
                        )
                        platform_errors.append(message)
                        self.logger.warning(message)
                except WechatIpWhitelistBlockedException as exc:
                    self.circuit_breaker.record_failure("wechat")
                    message = f"WeChat publish blocked by IP whitelist: {exc}"
                    platform_errors.append(message)
                    wechat_blocked_by_ip = True
                    platform_results["wechat"] = {
                        "status": "blocked_by_ip",
                        "error": str(exc),
                        "invalid_ip": str(getattr(exc, "ip_address", "")).strip(),
                    }
                    self.logger.warning(message)
                except Exception as exc:
                    self.circuit_breaker.record_failure("wechat")
                    message = f"WeChat publish failed: {exc}"
                    platform_errors.append(message)
                    platform_results["wechat"] = {"status": "failed", "error": str(exc)}
                    self.logger.exception(message)
                    self.notification_service.notify_failure(
                        platform="WeChat",
                        stage="browser_publish",
                        error_msg=str(exc),
                        run_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    )
                    if self._is_session_expired_error(exc):
                        self._notify_session_expired("WeChat", exc)

        if self.settings.publishing.enabled and self.settings.publishing.auto_publish:
            if self.circuit_breaker.is_open("toutiao"):
                self.logger.warning("Toutiao publish skipped: circuit breaker is open for toutiao.")
                platform_results["toutiao"] = {"status": "circuit_open", "error": "Circuit breaker is open for toutiao"}
            else:
                wechat_blocks_toutiao = (
                    self.settings.wechat.enabled
                    and not wechat_completed
                    and not wechat_blocked_by_ip
                )
                if wechat_blocks_toutiao:
                    self.logger.warning(
                        "WeChat publish did not complete, but proceeding with Toutiao publish independently."
                    )
                delay_seconds = self._get_wechat_toutiao_sync_delay_seconds()
                if not wechat_completed:
                    delay_seconds = 0
                if skip_wait and delay_seconds > 0:
                    delay_seconds = 5
                    self.logger.info("--skip-wait enabled: shortening WeChat→Toutiao delay to %d seconds.", delay_seconds)
                if delay_seconds > 0 and wechat_completed and self.settings.wechat.enabled:
                    self.logger.info(
                        "Waiting %s seconds after successful WeChat publish before Toutiao distribution.",
                        delay_seconds,
                    )
                    time.sleep(delay_seconds)
                try:
                    toutiao_declare_first = not wechat_completed
                    if toutiao_declare_first:
                        self.logger.info("WeChat did not publish successfully, declaring Toutiao first-release.")
                    toutiao_exports = self.publish_run(str(run_dir), declare_first_release=toutiao_declare_first)
                    exported_files.update(toutiao_exports)
                    platform_results["toutiao"] = self._load_toutiao_publish_snapshot(run_dir)
                    self.circuit_breaker.record_success("toutiao")
                    self.notification_service.notify_success(
                        platform="Toutiao",
                        article_title=self._load_selected_title(run_dir),
                        run_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    )
                except Exception as exc:
                    self.circuit_breaker.record_failure("toutiao")
                    message = f"Toutiao publish failed: {exc}"
                    platform_errors.append(message)
                    platform_results["toutiao"] = {"status": "failed", "error": str(exc)}
                    self.logger.exception(message)
                    self.notification_service.notify_failure(
                        platform="Toutiao",
                        stage="browser_publish",
                        error_msg=str(exc),
                        run_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    )
                    if self._is_session_expired_error(exc):
                        self._notify_session_expired("Toutiao", exc)

        # ── 多平台发布（知乎 / 小红书） ──
        self._publish_to_extra_platforms(
            run_dir, platform_results, platform_errors, exported_files
        )

        return exported_files, platform_results, platform_errors

    def _publish_to_extra_platforms(
        self,
        run_dir: Path,
        platform_results: dict[str, dict[str, object]],
        platform_errors: list[str],
        exported_files: dict[str, str],
    ) -> None:
        """发布文章到知乎/小红书等额外平台。"""
        # 读取文章内容
        article_path = run_dir / "article.md"
        title_path = run_dir / self.settings.review.publish_dirname / "title.txt"
        if not article_path.exists():
            self.logger.warning("article.md 不存在，跳过多平台发布")
            return

        article_content = article_path.read_text(encoding="utf-8", errors="replace").strip()
        article_title = ""
        if title_path.exists():
            article_title = title_path.read_text(encoding="utf-8", errors="replace").strip()
        if not article_title:
            article_title = article_content[:30].replace("\n", " ")

        # 收集图片
        images: list[str] = []
        cover_dir = run_dir / "images"
        if cover_dir.exists():
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                images.extend(str(p) for p in sorted(cover_dir.glob(ext)))
        cover_path = run_dir / "cover.png"
        if cover_path.exists() and str(cover_path) not in images:
            images.insert(0, str(cover_path))

        # 收集标签
        tags: list[str] = []
        tags_path = run_dir / self.settings.review.publish_dirname / "tags.txt"
        if tags_path.exists():
            for line in tags_path.read_text(encoding="utf-8", errors="replace").splitlines():
                tag = line.strip().lstrip("#").strip()
                if tag:
                    tags.append(tag)

        # 知乎
        if self.settings.zhihu_enabled:
            try:
                from toutiao_content_factory.services.zhihu_publisher import ZhihuPublisher
                publisher = ZhihuPublisher(self.settings, self.logger)
                result = publisher.publish_article(
                    title=article_title,
                    content=article_content,
                    images=images,
                    tags=tags,
                )
                platform_results["zhihu"] = {
                    "status": "success" if result.success else "failed",
                    "permalink": result.permalink,
                    "error": result.error,
                }
                if result.success:
                    self.logger.info("知乎发布成功: %s", result.permalink)
                else:
                    platform_errors.append(f"知乎发布失败: {result.error}")
            except Exception as exc:
                platform_errors.append(f"知乎发布异常: {exc}")
                platform_results["zhihu"] = {"status": "failed", "error": str(exc)}
                self.logger.exception("知乎发布异常")

        # 小红书
        if self.settings.xiaohongshu_enabled:
            try:
                from toutiao_content_factory.services.xiaohongshu_publisher import XiaohongshuPublisher
                publisher = XiaohongshuPublisher(self.settings, self.logger)
                result = publisher.publish_article(
                    title=article_title,
                    content=article_content,
                    images=images,
                    tags=tags,
                )
                platform_results["xiaohongshu"] = {
                    "status": "success" if result.success else "failed",
                    "permalink": result.permalink,
                    "error": result.error,
                }
                if result.success:
                    self.logger.info("小红书发布成功: %s", result.permalink)
                else:
                    platform_errors.append(f"小红书发布失败: {result.error}")
            except Exception as exc:
                platform_errors.append(f"小红书发布异常: {exc}")
                platform_results["xiaohongshu"] = {"status": "failed", "error": str(exc)}
                self.logger.exception("小红书发布异常")

    def _wechat_publish_succeeded(self, snapshot: dict[str, object]) -> bool:
        status = str(snapshot.get("status", "")).strip().lower()
        submit_status = str(snapshot.get("submit_status", "")).strip().lower()
        return status in {"submitted", "published", "success"} or submit_status in {
            "submitted",
            "published",
            "success",
        }

    def _get_wechat_toutiao_sync_delay_seconds(self) -> int:
        delay = int(getattr(self.settings.wechat, "toutiao_sync_delay_seconds", 1800) or 0)
        return max(delay, 0)

    def _load_toutiao_publish_snapshot(self, run_dir: Path) -> dict[str, object]:
        result_path = run_dir / self.settings.review.publish_dirname / "publish_result.json"
        if not result_path.exists():
            return {"status": "missing"}
        payload = _safe_json_loads(result_path.read_text(encoding="utf-8"), default={})
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
        payload = _safe_json_loads(result_path.read_text(encoding="utf-8"), default={})
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

    def _normalize_platform_status(self, status: str) -> str:
        normalized = str(status or "").strip().lower()
        if normalized in {"running", "retrying"}:
            return "running"
        if normalized in {"success", "completed", "published", "submitted", "approved", "draft_created", "ready"}:
            return "success"
        if normalized in {"failed", "error", "blocked", "blocked_by_ip", "publish_login_required", "missing", "invalid"}:
            return "failed"
        return "idle"

    def _resolve_overall_platform_status(self, platforms: dict[str, dict[str, object]]) -> str:
        states = {self._normalize_platform_status(str(item.get("status", ""))) for item in platforms.values() if isinstance(item, dict)}
        states.discard("")
        if "running" in states:
            return "running"
        if "success" in states and "failed" in states:
            return "partial_success"
        if "failed" in states:
            return "failed"
        if "success" in states:
            return "success"
        return "idle"

    def _build_scheduled_platform_map(
        self,
        payload: dict[str, object],
        *,
        updates: dict[str, dict[str, object]] | None = None,
        now: str,
    ) -> dict[str, dict[str, object]]:
        existing = payload.get("platforms", {}) if isinstance(payload.get("platforms"), dict) else {}
        result: dict[str, dict[str, object]] = {}
        for key in ("toutiao", "wechat"):
            previous = existing.get(key, {}) if isinstance(existing.get(key), dict) else {}
            result[key] = {
                "status": str(previous.get("status", "idle") or "idle"),
                "last_run": str(previous.get("last_run", "") or ""),
                "message": str(previous.get("message", "") or ""),
            }
        for key, item in (updates or {}).items():
            if key not in result or not isinstance(item, dict):
                continue
            result[key] = {
                "status": self._normalize_platform_status(str(item.get("status", ""))),
                "last_run": str(item.get("last_run", now) or now),
                "message": str(item.get("message", "") or ""),
            }
        return result

    def _write_retry_publish_status(
        self,
        *,
        run_dir: Path,
        platform: str,
        platform_snapshot: dict[str, object],
        exported: dict[str, str],
    ) -> Path:
        status_path = self.settings.automation_dir / "scheduled_status.json"
        if status_path.exists():
            try:
                payload = _safe_json_loads(status_path.read_text(encoding="utf-8-sig"), default={})
            except json.JSONDecodeError:
                payload = {}
        else:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        now = datetime.now().isoformat(timespec="seconds")
        platform_status = str(platform_snapshot.get("status", "")).strip() or "submitted"
        platform_message = f"Retry publish finished for {platform}: {platform_status}."
        platforms = self._build_scheduled_platform_map(
            payload,
            updates={
                platform: {
                    "status": platform_status,
                    "last_run": now,
                    "message": platform_message,
                }
            },
            now=now,
        )
        overall_status = self._resolve_overall_platform_status(platforms)

        payload["updated_at"] = now
        payload["last_run_time"] = now
        payload["run_name"] = run_dir.name
        payload["run_dir"] = str(run_dir)
        payload["status"] = platform_status
        payload["overall_status"] = overall_status
        payload["overall_message"] = platform_message
        payload["workflow_status"] = f"retry_{platform}"
        payload["message"] = platform_message
        payload["retry_platform"] = platform
        payload["platforms"] = platforms

        if platform == "toutiao":
            payload["publish_status"] = platform_status
            payload["publish_url"] = str(platform_snapshot.get("external_url", "")).strip()
        elif platform == "wechat":
            payload["wechat_status"] = platform_status
            payload["wechat_submit_status"] = str(platform_snapshot.get("submit_status", "")).strip()
            payload["wechat_publish_url"] = str(platform_snapshot.get("external_url", "")).strip()
            payload["wechat_draft_media_id"] = str(platform_snapshot.get("draft_media_id", "")).strip()

        if "resolved_run_dir" in exported:
            payload["resolved_run_dir"] = exported["resolved_run_dir"]

        status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return status_path

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

    def _degrade_blocked_platforms_to_draft(self, blocking_platforms: list[str]) -> None:
        """将登录失败的平台降级为草稿模式（auto_publish=False, enabled=False），不中断整个流程。"""
        degraded = []
        if "toutiao" in blocking_platforms:
            if self.settings.publishing.auto_publish:
                object.__setattr__(self.settings.publishing, "auto_publish", False)
            if self.settings.publishing.enabled:
                object.__setattr__(self.settings.publishing, "enabled", False)
            degraded.append("toutiao")
        if "wechat" in blocking_platforms:
            if self.settings.wechat.auto_publish:
                object.__setattr__(self.settings.wechat, "auto_publish", False)
            if self.settings.wechat.enabled:
                object.__setattr__(self.settings.wechat, "enabled", False)
            degraded.append("wechat")
        if degraded:
            self.logger.warning(
                "已将以下平台降级（禁用发布）: %s",
                ", ".join(degraded),
            )

    def _notify_platform_auth_failure(
        self,
        *,
        blocking_platforms: list[str],
        platform_health_path: str,
        auth_health: dict[str, object],
    ) -> None:
        run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        platform_labels = {
            "toutiao": "Toutiao",
            "wechat": "WeChat OA",
        }
        readable_platforms = [platform_labels.get(item, item) for item in blocking_platforms]
        platform_messages: list[str] = []
        platforms = auth_health.get("platforms", {})
        if isinstance(platforms, dict):
            for key in blocking_platforms:
                payload = platforms.get(key, {})
                if not isinstance(payload, dict):
                    continue
                blockers = [str(item) for item in payload.get("blocking_flags", []) if str(item).strip()]
                if blockers:
                    platform_messages.append(f"{platform_labels.get(key, key)}: {' | '.join(blockers)}")
        message = (
            f"{run_time} publish auth precheck failed before model generation.\n"
            f"account: {self.settings.account_key}\n"
            f"blocked platforms: {', '.join(readable_platforms)}\n"
            f"reasons: {'; '.join(platform_messages) if platform_messages else 'login session invalid'}\n"
            f"platform health: {platform_health_path or 'N/A'}\n"
            "This run was aborted before any AI generation to avoid unnecessary cost."
        )
        try:
            self.notification_service.notify_urgent("[URGENT] Publish auth precheck failed", message)
        except Exception as exc:
            self.logger.warning("Failed to send urgent platform auth notification: %s", exc)

    @staticmethod
    def _is_session_expired_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        session_keywords = [
            "session expired",
            "not logged in",
            "login form detected",
            "session may have expired",
            "passport",
            "sso.toutiao.com",
            "auth/login",
            "cookie expired",
            "登录已过期",
            "登录态失效",
            "需要重新登录",
        ]
        return any(kw in msg for kw in session_keywords)

    def _notify_session_expired(self, platform: str, exc: Exception) -> None:
        run_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        message = (
            f"{run_time} {platform} session expired during publish.\n"
            f"account: {self.settings.account_key}\n"
            f"error: {exc}\n"
            "Action required: re-run the login initialization script to restore the session."
        )
        try:
            self.notification_service.notify_urgent(
                f"[URGENT] {platform} session expired", message
            )
        except Exception as notify_exc:
            self.logger.warning("Failed to send session expiry notification: %s", notify_exc)

    def _load_selected_title(self, run_dir: Path | None) -> str:
        if run_dir is None:
            return ""
        status_path = run_dir / "review_status.json"
        if not status_path.exists():
            return ""
        payload = _safe_json_loads(status_path.read_text(encoding="utf-8"), default={})
        return str(payload.get("selected_title", "")).strip()

    _STALE_RUN_MAX_AGE_DAYS = 30

    def _cleanup_stale_runs(self) -> None:
        runs_dir = self.settings.runs_dir
        if not runs_dir.exists():
            return
        now_ts = time.time()
        cutoff = self._STALE_RUN_MAX_AGE_DAYS * 86400
        removed = 0
        for item in runs_dir.iterdir():
            if not item.is_dir():
                continue
            try:
                age = now_ts - item.stat().st_mtime
                if age > cutoff:
                    shutil.rmtree(item, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
        if removed:
            self.logger.info("已清理 %d 个超过 %d 天的旧运行目录。", removed, self._STALE_RUN_MAX_AGE_DAYS)

    _DAILY_RUN_LOCK_TIMEOUT_SECONDS = 1800
    _DAILY_RUN_HEARTBEAT_TIMEOUT_SECONDS = 600

    def _acquire_daily_run_lock(self) -> bool:
        lock_path = self.settings.automation_dir / "daily_run.lock"
        if lock_path.exists():
            try:
                payload = _safe_json_loads(lock_path.read_text(encoding="utf-8"), default={})
                # 优先检查心跳超时
                last_heartbeat_ts = float(payload.get("last_heartbeat_ts", 0))
                if last_heartbeat_ts > 0 and time.time() - last_heartbeat_ts > self._DAILY_RUN_HEARTBEAT_TIMEOUT_SECONDS:
                    self.logger.warning(
                        "Dead daily_run lock detected (heartbeat age > %ds). Removing.",
                        self._DAILY_RUN_HEARTBEAT_TIMEOUT_SECONDS,
                    )
                else:
                    started_at = float(payload.get("started_at_ts", 0))
                    if time.time() - started_at < self._DAILY_RUN_LOCK_TIMEOUT_SECONDS:
                        self.logger.info(
                            "daily_run lock held by PID %s since %s. Skipping.",
                            payload.get("pid"), payload.get("started_at"),
                        )
                        return False
                    self.logger.warning("Stale daily_run lock detected (age > %ds). Removing.", self._DAILY_RUN_LOCK_TIMEOUT_SECONDS)
            except Exception:
                self.logger.warning("Corrupted daily_run lock file. Removing.")
            with suppress(Exception):
                lock_path.unlink()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        now_ts = time.time()
        lock_path.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "started_at_ts": now_ts,
            "last_heartbeat_ts": now_ts,
        }), encoding="utf-8")
        return True

    def _heartbeat_daily_run_lock(self) -> None:
        lock_path = self.settings.automation_dir / "daily_run.lock"
        if not lock_path.exists():
            return
        try:
            payload = _safe_json_loads(lock_path.read_text(encoding="utf-8"), default={})
            payload["last_heartbeat_ts"] = time.time()
            lock_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _release_daily_run_lock(self) -> None:
        lock_path = self.settings.automation_dir / "daily_run.lock"
        with suppress(Exception):
            if lock_path.exists():
                lock_path.unlink()

    def _path_or_none(self, raw_path: object) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(str(raw_path)).resolve()
        if candidate.exists():
            return candidate
        return None

    def _preflight_health_check(self) -> None:
        """执行轻量级平台健康检查，如果必须的平台认证失败则通知并抛出异常。"""
        try:
            health = self.platform_health_service.quick_health_check()
        except Exception as exc:
            self.logger.warning("Platform health check failed (non-fatal): %s", exc)
            return

        blocking_platforms = health.get("blocking_platforms", [])
        if not blocking_platforms:
            return

        blocking_flags = health.get("blocking_flags", [])
        platform_labels = {
            "toutiao": "Toutiao",
            "wechat": "WeChat OA",
        }
        readable_platforms = [platform_labels.get(p, p) for p in blocking_platforms]
        message = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} auto_run 前置健康检查失败。\n"
            f"账号: {self.settings.account_key}\n"
            f"阻塞平台: {', '.join(readable_platforms)}\n"
            f"原因: {'; '.join(str(f) for f in blocking_flags[:5]) if blocking_flags else '认证状态异常'}\n"
            "本次运行已终止，请先修复平台认证问题。"
        )
        try:
            self.notification_service.notify_urgent("[URGENT] auto_run 健康检查失败", message)
        except Exception as notify_exc:
            self.logger.warning("健康检查通知发送失败: %s", notify_exc)

        raise PlatformAuthError(
            f"Platform health check blocked: {', '.join(readable_platforms)}",
            blocking_platforms=blocking_platforms,
        )

    def check_ai_services_health(self) -> dict[str, bool]:
        """Check health of all one-person company AI services."""
        health = {}
        if self.settings.deerflow_enabled:
            from toutiao_content_factory.services.deerflow_client import DeerFlowClient
            client = DeerFlowClient(self.settings.deerflow_base_url, self.logger)
            health["deerflow"] = client.health_check()
        if self.settings.letta_enabled:
            from toutiao_content_factory.services.letta_client import LettaClient
            client = LettaClient(self.settings.letta_base_url, self.settings.letta_agent_id, self.logger)
            health["letta"] = bool(client.get_memory_context())
        if self.settings.prompt_optimizer_enabled:
            from toutiao_content_factory.services.prompt_optimizer_client import PromptOptimizerClient
            client = PromptOptimizerClient(self.settings.prompt_optimizer_base_url, self.logger)
            health["prompt_optimizer"] = client.health_check()
        if self.settings.ruflo_enabled:
            from toutiao_content_factory.services.ruflo_client import RufloClient
            client = RufloClient(self.settings.ruflo_base_url, self.logger)
            health["ruflo"] = client.health_check()
        return health
