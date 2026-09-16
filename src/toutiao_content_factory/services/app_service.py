from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any


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
from toutiao_content_factory.services.notification_service import NotificationService, notification_now
from toutiao_content_factory.services.circuit_breaker import PlatformCircuitBreaker
from toutiao_content_factory.services.company_os_service import CompanyOSService
from toutiao_content_factory.services.lead_ops_service import LeadOpsService
from toutiao_content_factory.services.monetization_service import MonetizationService
from toutiao_content_factory.services.revenue_engine_service import RevenueEngineService
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator


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
        # Preserve scheduled auth failures after settings are downgraded to draft mode.
        self._daily_blocked_platforms: set[str] = set()

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
                    f"时间: {notification_now().strftime('%Y-%m-%d %H:%M')}\n"
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
        # ── 当日已发布短路：每日 1 篇上限下，发布成功后的后续时段任务
        # 不再重复选题、写作、评分（这些环节会持续消耗 LLM token 且无法再发布），
        # 直接返回 already_published，由定时任务快速跳过。
        # 注意：不依赖 auto_publish 开关——即使脚本因缺少 -EnableAutoPublish 把
        # TOUTIAO_AUTO_PUBLISH 设为 0，只要当日已发布过，生成就是纯浪费。
        if not dry_run:
            try:
                from toutiao_content_factory.services.compliance_service import (
                    count_today_published_by_platform,
                )
                history_path = self.settings.output_dir / self.settings.publishing.history_filename
                # 只按「图文」平台计数判断今日图文是否已发布：
                # 独立微头条（platform=toutiao_weitoutiao）不算图文已发布，
                # 避免早间先发微头条导致当日图文被短路跳过。
                if count_today_published_by_platform(
                    history_path, platform=self.settings.publishing.platform
                ) >= 1:
                    self.logger.info(
                        "Daily publish limit already reached for today; skipping generation to save tokens. "
                        "account=%s platform=%s",
                        self.settings.account_key,
                        self.settings.publishing.platform,
                    )
                    # 微头条高频化：图文已发但微头条未达每日目标时，后续时段补发独立微头条，
                    # 仍保持生成短内容的轻量成本，不重新走图文选题/写作/评分。
                    topup_exports: dict[str, Any] = {}
                    if (
                        self.settings.publishing.weitoutiao_enabled
                        and self.settings.publishing.weitoutiao_standalone_enabled
                    ):
                        try:
                            topup_exports = self._top_up_standalone_weitoutiao(max_count=1)
                        except Exception as topup_exc:
                            topup_exports = {
                                "standalone_topup_status": "failed",
                                "standalone_topup_summary": str(topup_exc),
                                "standalone_topup_requested": "1",
                                "standalone_topup_selected": "0",
                                "standalone_topup_accepted": "0",
                                "standalone_topup_skipped": "1",
                                "standalone_topup_fallback_count": "0",
                            }
                            self.logger.warning(
                                "WeiToutiao standalone top-up in already-published branch failed (non-fatal): %s",
                                topup_exc,
                            )
                    return {
                        "status": "already_published",
                        "message": "Daily publish limit reached; skipped generation for this scheduled slot.",
                        **topup_exports,
                    }
            except Exception as exc:
                self.logger.warning("Daily publish limit pre-check failed (non-fatal): %s", exc)

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
            self._daily_blocked_platforms = set(blocking_platforms)
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
                    f"时间: {notification_now().strftime('%Y-%m-%d %H:%M')}\n"
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
            content_guard_report: dict[str, Any] | None = None
            content_guard_error = ""
            if run_dir is not None and not no_archive:
                content_guard_report = self._load_content_guard_report(run_dir)
                if not content_guard_report:
                    try:
                        # The archive step normally writes this report. Rebuild it
                        # here when an older or interrupted run did not, so the
                        # automatic approval gate never relies on a missing check.
                        content_guard_report = self.content_guard_service.evaluate_run(run_dir)
                    except Exception as exc:
                        content_guard_error = str(exc)
                        content_guard_report = {
                            "blocked": True,
                            "blocking_flags": [f"内容守卫检查失败，禁止自动发布：{exc}"],
                        }
            qualifies = self._qualifies_for_auto_approval(
                score_snapshot,
                content_guard_report=content_guard_report,
            )
            title_selection: dict[str, object] = {}
            if qualifies and run_dir is not None:
                try:
                    title_selection = self.review_service.select_auto_title(run_dir)
                except Exception as exc:
                    self.logger.warning("Automatic title quality gate failed: %s", exc)
                    title_selection = {
                        "selected_title": "",
                        "selected_title_index": 0,
                        "evaluations": [],
                        "reason": f"Automatic title quality gate failed: {exc}",
                    }
                if not str(title_selection.get("selected_title", "")).strip():
                    qualifies = False
            attempt_record = {
                "attempt_index": attempt_index,
                "candidate_rank": int(candidate["rank"]),
                "topic_title": str(candidate["title"]),
                "topic_url": str(candidate["url"]),
                "run_dir": str(run_dir) if run_dir else "",
                "score_snapshot": score_snapshot,
                "qualified_for_auto_approval": qualifies,
            }
            content_guard_blocked = bool(
                content_guard_report
                and (
                    content_guard_report.get("blocked")
                    or content_guard_report.get("bad_words_blocked")
                    or content_guard_report.get("blocking_flags")
                )
            )
            if content_guard_report is not None:
                attempt_record["content_guard_blocked"] = content_guard_blocked
                attempt_record["content_guard_warnings"] = [
                    str(item)
                    for item in (
                        list(content_guard_report.get("blocking_flags", []))
                        + list(content_guard_report.get("warnings", []))
                    )
                    if str(item).strip()
                ][:5]
            if content_guard_error:
                attempt_record["content_guard_error"] = content_guard_error
            if title_selection:
                attempt_record["title_quality_gate"] = title_selection

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
                    selected_title=str(title_selection.get("selected_title", "")),
                    selected_title_index=int(title_selection.get("selected_title_index", 0) or 0),
                    review_notes="Auto-approved by the daily automation workflow after the title quality gate.",
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

                # A stale or concurrent history check can still reject a title
                # after the title gate passed. Retry the next generated
                # candidate only when the rejection is explicitly a duplicate
                # title and no platform has an accepted or uncertain result.
                if self._is_safe_title_duplicate_fallback(
                    platform_results,
                    platform_errors,
                ):
                    duplicate_reason = self._first_title_duplicate_reason(
                        platform_results,
                        platform_errors,
                    )
                    attempt_record.update(
                        {
                            "status": "title_duplicate_publish_blocked",
                            "platform_results": platform_results,
                            "platform_errors": platform_errors,
                            "skip_reason": (
                                "Title duplicate blocked before publication; "
                                "trying the next daily candidate. "
                                f"{duplicate_reason}"
                            ),
                        }
                    )
                    losing_attempts.append(attempt_record)
                    if best_attempt is not None and best_attempt.get("run_dir") == str(run_dir):
                        best_attempt.update(
                            {
                                "status": attempt_record["status"],
                                "platform_results": platform_results,
                                "platform_errors": platform_errors,
                                "skip_reason": attempt_record["skip_reason"],
                                "exported_files": dict(attempt_exports),
                            }
                        )
                    self.logger.warning(
                        "Daily candidate %s/%s was blocked by a duplicate title; trying the next candidate.",
                        attempt_index,
                        len(candidate_specs),
                    )
                    continue

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

            if content_guard_blocked:
                attempt_record["status"] = "content_guard_rejected"
                guard_reasons = attempt_record.get("content_guard_warnings", [])
                attempt_record["skip_reason"] = (
                    "Content guard blocked automatic approval: "
                    + " | ".join(str(item) for item in guard_reasons[:3])
                    if guard_reasons
                    else "Content guard blocked automatic approval."
                )
            else:
                attempt_record["status"] = (
                    "title_quality_rejected" if title_selection else "candidate_rejected"
                )
            if title_selection and not content_guard_blocked:
                attempt_record["skip_reason"] = str(
                    title_selection.get("reason", "No candidate title passed the final title quality gate.")
                )
            losing_attempts.append(attempt_record)

        if best_attempt is None:
            raise ValueError("Daily workflow did not produce any runnable candidates.")

        skipped_attempts = [item for item in losing_attempts if item.get("run_dir") != best_attempt.get("run_dir")]

        # 兜底发布：本流程跑在 GitHub Actions 上，没有人可以介入，直接落到
        # review_required 等于当天颗粒无收。这里在「守得住安全底线」的前提下
        # 放行最优的一篇——被忽略的只是模型的软指控（「可能存在……」），
        # 确定性硬伤（污染、内部标签、抄录过高、字数不足）依然一票否决。
        fallback_result = self._try_fallback_publish(
            [best_attempt, *losing_attempts], dry_run=dry_run
        )
        if fallback_result is not None:
            if skipped_attempts:
                self._mark_attempts_skipped(skipped_attempts)
            published_dir = str(fallback_result.get("run_dir", ""))
            fallback_attempt = {
                key: value
                for key, value in best_attempt.items()
                if key != "exported_files"
            }
            if published_dir and published_dir != best_attempt.get("run_dir"):
                # 兜底选中的不是最优候选，用被选中的那份记录覆盖展示信息。
                for item in losing_attempts:
                    if item.get("run_dir") == published_dir:
                        fallback_attempt = {
                            key: value for key, value in item.items() if key != "exported_files"
                        }
                        break
            fallback_attempt.update(fallback_result["attempt_fields"])
            exported_files.update(dict(best_attempt.get("exported_files", {})))
            exported_files.update(fallback_result["exports"])
            if published_dir:
                # 兜底发的常常不是最优候选（最优那篇可能是抄录率超标被刷掉的）。
                # 下游一律以 `archive_root` 解析 run_dir —— main.py 的日报摘要
                # 就是从那里读 publish/<publish_result.json>。若 archive_root 仍
                # 指向未发布的最优候选，publish_status 会解析成空字符串，
                # 一次成功发布就被误报成 review_required（2026-09-16 实测：
                # 图文已 submitted_for_review 且拿到 pgc_id，摘要却写「头条: -」）。
                exported_files["archive_root"] = published_dir
            report_exports = self._write_daily_workflow_report(
                status="fallback_published",
                final_attempt=fallback_attempt,
                attempts=[*skipped_attempts, fallback_attempt],
                supporting_exports=benchmark_refresh_exports,
            )
            exported_files.update(report_exports)
            return exported_files

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
        self._notify_daily_review_required(final_attempt)
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

    def publish_weitoutiao_run(self, run_dir: str | None = None, draft_only: bool = False) -> dict[str, Any]:
        """把已审核通过的 run 以微头条（短内容）形式发布。"""
        resolved = self.review_service.resolve_run_dir(run_dir)
        previous_draft_only = self.settings.publishing.draft_only
        object.__setattr__(self.settings.publishing, "draft_only", bool(draft_only))
        try:
            result = self.review_service.publish_weitoutiao(resolved, draft_only=draft_only)
        finally:
            object.__setattr__(self.settings.publishing, "draft_only", previous_draft_only)
        return self._format_weitoutiao_publish_exports(resolved, result)

    def publish_weitoutiao_latest_approved(
        self,
        unpublished_only: bool = True,
        today_only: bool = True,
        draft_only: bool = False,
    ) -> dict[str, str]:
        """把最新审核通过、尚未发布的 run 以微头条形式发布。"""
        resolved = self.review_service.resolve_latest_approved_run(
            unpublished_only=unpublished_only,
            today_only=today_only,
        )
        previous_draft_only = self.settings.publishing.draft_only
        object.__setattr__(self.settings.publishing, "draft_only", bool(draft_only))
        try:
            result = self.review_service.publish_weitoutiao(resolved, draft_only=draft_only)
        finally:
            object.__setattr__(self.settings.publishing, "draft_only", previous_draft_only)
        exported = {name: str(path) for name, path in result.items()}
        exported["resolved_run_dir"] = str(resolved)
        return exported

    def publish_standalone_weitoutiao(self, count: int = 1, *, draft_only: bool = False) -> dict[str, Any]:
        """生成并发布 count 条独立微头条（微头条高频化）。

        独立选题（与图文/伴生微头条去重）+ 独立写作（多个人设轮换）+
        复用 publish_weitoutiao 浏览器发布，并写入 publish_history。
        """
        generator = WeiToutiaoStandaloneGenerator(self.settings, self.logger)
        previous_draft_only = self.settings.publishing.draft_only
        object.__setattr__(self.settings.publishing, "draft_only", bool(draft_only))
        try:
            result = generator.publish_standalone(count=count, draft_only=draft_only, purpose="standalone")
        finally:
            object.__setattr__(self.settings.publishing, "draft_only", previous_draft_only)
        exports = {name: str(path) for name, path in result.get("exports", {}).items()}
        if result.get("result_path"):
            exports["standalone_result"] = str(result["result_path"])
        if result.get("latest_result_path"):
            exports["standalone_latest_result"] = str(result["latest_result_path"])
        exports["standalone_status"] = str(result.get("status", ""))
        exports["standalone_summary"] = (
            f"status={result.get('status', '')} accepted={result.get('accepted', 0)} "
            f"skipped={result.get('skipped_count', len(result.get('skipped', [])))}"
        )
        for index, item in enumerate(result.get("published", []), start=1):
            exports[f"wtt_published_{index}"] = str(item.get("topic_title", ""))
        return exports

    def _top_up_standalone_weitoutiao(self, max_count: int = 2) -> dict[str, Any]:
        """微头条高频化：按每日目标补齐独立微头条。

        图文伴生 1 条 + 独立微头条补到 weitoutiao_daily_target（默认 3，即每日 2-3 条）。
        仅在 weitoutiao_enabled 且 weitoutiao_standalone_enabled 时生效；
        今日微头条已达目标或独立微头条关闭时直接返回空，不阻塞主流程。
        """
        publishing = self.settings.publishing
        if not publishing.weitoutiao_enabled or not publishing.weitoutiao_standalone_enabled:
            return {"standalone_topup_status": "disabled"}
        generator = WeiToutiaoStandaloneGenerator(self.settings, self.logger)
        remaining = generator.remaining_for_target()
        count = min(remaining, max(0, int(max_count)))
        self.logger.info(
            "WeiToutiao high-frequency top-up: target=%s today=%s remaining=%s publishing=%s",
            publishing.weitoutiao_daily_target,
            generator.today_weitoutiao_count(),
            remaining,
            count,
        )
        result = generator.publish_standalone(
            count=count,
            draft_only=publishing.draft_only,
            purpose="topup",
        )
        exports = {name: str(path) for name, path in result.get("exports", {}).items()}
        if result.get("result_path"):
            exports["standalone_topup_result"] = str(result["result_path"])
        if result.get("latest_result_path"):
            exports["standalone_topup_latest_result"] = str(result["latest_result_path"])
        exports["standalone_topup_status"] = str(result.get("status", ""))
        exports["standalone_topup_requested"] = str(result.get("requested", count))
        exports["standalone_topup_selected"] = str(result.get("selected", 0))
        exports["standalone_topup_accepted"] = str(result.get("accepted", 0))
        exports["standalone_topup_skipped"] = str(result.get("skipped_count", len(result.get("skipped", []))))
        exports["standalone_topup_fallback_count"] = str(result.get("fallback_count", 0))
        exports["standalone_topup_summary"] = (
            f"status={result.get('status', '')} accepted={result.get('accepted', 0)} "
            f"skipped={result.get('skipped_count', len(result.get('skipped', [])))}"
        )
        return exports

    @staticmethod
    def _is_accepted_weitoutiao_status(status: object) -> bool:
        return str(status or "").strip().lower() in {
            "published",
            "submitted",
            "submitted_for_review",
            "success",
        }

    @staticmethod
    def _is_failed_weitoutiao_status(status: object) -> bool:
        return str(status or "").strip().lower() in {
            "failed",
            "error",
            "blocked",
            "blocked_by_ip",
            "publish_login_required",
            "publish_uncertain",
            "circuit_open",
            "missing",
            "invalid",
            "no_topics",
            "draft_only",
        }

    def _load_weitoutiao_batch_result(self, exports: dict[str, Any]) -> dict[str, Any]:
        result_path = str(
            exports.get("standalone_topup_result")
            or exports.get("standalone_result")
            or ""
        ).strip()
        if result_path:
            payload = _safe_json_loads(
                Path(result_path).read_text(encoding="utf-8-sig"),
                default={},
            ) if Path(result_path).exists() else {}
            if isinstance(payload, dict):
                payload.setdefault("result_path", result_path)
                return payload
        status = str(
            exports.get("standalone_topup_status")
            or exports.get("standalone_status")
            or "not_attempted"
        ).strip()
        return {
            "status": status,
            "message": str(exports.get("standalone_topup_summary") or exports.get("standalone_summary") or ""),
            "requested": int(exports.get("standalone_topup_requested", 0) or 0),
            "selected": int(exports.get("standalone_topup_selected", 0) or 0),
            "accepted": int(exports.get("standalone_topup_accepted", 0) or 0),
            "skipped_count": int(exports.get("standalone_topup_skipped", 0) or 0),
            "fallback_count": int(exports.get("standalone_topup_fallback_count", 0) or 0),
            "result_path": result_path,
        }

    def _load_weitoutiao_companion_result(
        self,
        run_dir: Path,
        exports: dict[str, Any],
    ) -> dict[str, Any]:
        result_path = str(exports.get("weitoutiao_publish_result", "")).strip()
        candidates: list[Path] = []
        if result_path:
            candidate = Path(result_path)
            if not candidate.is_absolute():
                candidate = run_dir / candidate
            candidates.append(candidate)
        candidates.append(run_dir / self.settings.review.publish_dirname / "weitoutiao_publish_result.json")

        for candidate in candidates:
            try:
                payload = _safe_json_loads(candidate.read_text(encoding="utf-8-sig"), default={})
            except OSError:
                continue
            if isinstance(payload, dict):
                payload.setdefault("result_path", str(candidate))
                payload.setdefault("status", str(exports.get("weitoutiao_status", "")).strip())
                return payload

        status = str(exports.get("weitoutiao_status", "")).strip() or "not_attempted"
        return {
            "status": status,
            "message": str(exports.get("weitoutiao_message", "")).strip(),
            "external_url": str(exports.get("weitoutiao_external_url", "")).strip(),
            "result_path": result_path,
        }

    def _summarize_weitoutiao_status(
        self,
        companion: dict[str, Any],
        topup: dict[str, Any],
    ) -> str:
        neutral_statuses = {"", "not_attempted", "disabled", "target_reached"}
        companion_status = str(companion.get("status", "")).strip().lower()
        topup_status = str(topup.get("status", "")).strip().lower()
        attempted_statuses = [
            value
            for value in (companion_status, topup_status)
            if value not in neutral_statuses
        ]
        failure_count = sum(
            1
            for value in attempted_statuses
            if self._is_failed_weitoutiao_status(value)
            or (value not in neutral_statuses and not self._is_accepted_weitoutiao_status(value))
        )
        success_count = sum(
            1
            for value in attempted_statuses
            if self._is_accepted_weitoutiao_status(value) or value == "target_reached"
        )
        if failure_count:
            return "partial_success" if success_count else "failed"
        if success_count:
            return "success"
        return "not_attempted"

    def _write_weitoutiao_daily_result(
        self,
        run_dir: Path,
        *,
        companion: dict[str, Any],
        topup: dict[str, Any],
    ) -> tuple[Path, dict[str, Any]]:
        status = self._summarize_weitoutiao_status(companion, topup)
        details: list[str] = []
        companion_error = str(companion.get("error", "")).strip()
        if companion_error:
            details.append(f"companion: {companion_error}")
        topup_message = str(topup.get("message", "")).strip()
        if topup_message and str(topup.get("status", "")).strip().lower() not in {
            "success",
            "target_reached",
        }:
            details.append(f"top-up: {topup_message}")
        try:
            topup_shortfall = max(0, int(topup.get("selection_shortfall", 0) or 0))
        except (TypeError, ValueError):
            topup_shortfall = 0
        if topup_shortfall:
            details.append(
                "top-up: all eligible candidates were processed, "
                f"but the candidate pool was short by {topup_shortfall} item(s)."
            )
        skipped_items = topup.get("skipped", [])
        if isinstance(skipped_items, list):
            for item in skipped_items:
                if not isinstance(item, dict):
                    continue
                error = str(item.get("error") or item.get("reason") or "").strip()
                if error:
                    details.append(f"top-up: {error}")
                    break
        if status == "success":
            message = "WeiToutiao companion and top-up workflow completed."
        elif status == "partial_success":
            message = "WeiToutiao workflow partially succeeded; one or more items need follow-up."
        elif status == "failed":
            message = "WeiToutiao workflow did not complete successfully."
        else:
            message = "WeiToutiao workflow was not attempted."
        if details:
            message = f"{message} {'; '.join(details[:3])}"
        result_path = run_dir / self.settings.review.publish_dirname / "weitoutiao_daily_result.json"
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "account_key": self.settings.account_key,
            "account_slug": self.settings.account_slug,
            "run_dir": str(run_dir),
            "status": status,
            "message": message,
            "companion": companion,
            "topup": topup,
            "result_path": str(result_path),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return result_path, payload

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
                    "weitoutiao": {"status": "idle", "last_run": "", "message": ""},
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
        inline_images_enabled = bool(getattr(self.settings.publishing, "inline_images_enabled", True))
        if (
            not self.settings.wechat.enabled
            and str(self.settings.publishing.cover_mode or "auto").strip().lower() == "none"
            and not inline_images_enabled
        ):
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
        # Toutiao body images and three-image covers both consume infographics.
        cover_mode = str(self.settings.publishing.cover_mode or "auto").strip().lower()
        include_infographics = bool(self.settings.wechat.enabled) or cover_mode == "three" or inline_images_enabled
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
        source_content_id: str = "",
        cta_source_code: str = "",
        platform: str = "",
    ) -> dict[str, Any]:
        self._daily_blocked_platforms = set()
        entry = self.lead_ops_service.record_lead(
            name=name,
            contact=contact,
            intent=intent,
            source=source,
            notes=notes,
            status=status,
            estimated_value=estimated_value,
            next_follow_up_at=next_follow_up_at,
            source_content_id=source_content_id,
            cta_source_code=cta_source_code,
            platform=platform,
        )
        assets = self.lead_ops_service.ensure_assets()
        result = {name: str(path) for name, path in assets.items()}
        result["lead_entry_id"] = str(entry.get("id", ""))
        result["source_content_id"] = str(entry.get("source_content_id", ""))
        result["attribution_status"] = str(entry.get("attribution_status", "unattributed"))
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
        result["source_content_id"] = str(entry.get("source_content_id", ""))
        result["attribution_status"] = str(entry.get("attribution_status", "unattributed"))
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
        snapshot_label: str | None = None,
        snapshot_at: str | None = None,
        published_date: str | None = None,
        earnings_date: str | None = None,
        availability_status: str = "available",
        revenue_status: str = "unknown",
        platform_revenue_status: str = "unknown",
        revenue_amount_known: bool = False,
        platform_revenue: float | None = None,
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
            snapshot_label=snapshot_label,
            snapshot_at=snapshot_at,
            published_date=published_date,
            earnings_date=earnings_date,
            availability_status=availability_status,
            revenue_status=revenue_status,
            platform_revenue_status=platform_revenue_status,
            revenue_amount_known=revenue_amount_known,
            platform_revenue=platform_revenue,
            impression_count=impression_count,
            read_count=read_count,
            recommendation_read_count=recommendation_read_count,
            like_count=like_count,
            wow_count=wow_count,
            share_count=share_count,
            comment_count=comment_count,
            follower_gain=follower_gain,
        )
        return self._format_weitoutiao_publish_exports(resolved, result)

    def _format_weitoutiao_publish_exports(self, run_dir: Path, result: object) -> dict[str, Any]:
        """Persist a companion result without overwriting the graphic publish result."""
        raw_result = dict(result) if isinstance(result, dict) else {}
        exported = {name: str(value) for name, value in raw_result.items()}
        exported["resolved_run_dir"] = str(run_dir)
        publish_dir = run_dir / self.settings.review.publish_dirname
        result_path = publish_dir / "weitoutiao_publish_result.json"
        payload = {
            **raw_result,
            "platform": "toutiao_weitoutiao",
            "run_dir": str(run_dir),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        exported["weitoutiao_publish_result"] = str(result_path)
        exported["weitoutiao_status"] = str(raw_result.get("status", "")).strip()
        exported["weitoutiao_published_at"] = str(raw_result.get("published_at", "")).strip()
        exported["weitoutiao_external_url"] = str(raw_result.get("external_url", "")).strip()
        return exported

    def build_performance_report(self) -> dict[str, str]:
        result = self.growth_ops_service.build_performance_report()
        return {name: str(path) for name, path in result.items()}

    def build_revenue_attribution_report(self) -> dict[str, str]:
        """生成收益归因与经营分析报表。"""
        from toutiao_content_factory.services.revenue_attribution_service import (
            RevenueAttributionService,
        )

        service = RevenueAttributionService(self.settings.output_dir, self.logger)
        revenue_config = getattr(self.settings, "revenue_optimization", None)
        report_kwargs = {
            "lookback_days": int(getattr(revenue_config, "lookback_days", 30)),
            "min_samples": int(getattr(revenue_config, "min_samples", 10)),
            "weak_rpm_threshold": float(getattr(revenue_config, "weak_rpm_threshold", 0.0)),
            "weak_read_rate_threshold": float(getattr(revenue_config, "weak_read_rate_threshold", 3.0)),
            "cooldown_weak_ratio": float(getattr(revenue_config, "cooldown_weak_ratio", 0.5)),
            "stop_weak_ratio": float(getattr(revenue_config, "stop_weak_ratio", 0.8)),
            "cooldown_penalty": int(getattr(revenue_config, "cooldown_penalty", 12)),
        }
        business_report = service.build_business_report(**report_kwargs)
        return {
            "business_report": str(business_report),
            "content_mix_report": str(service.mix_report_path),
            "cooling_report": str(service.cooling_report_path),
            "experiment_report": str(service.experiment_report_path),
            "unit_economics": str(service.unit_economics_path),
            "snapshot_ledger": str(service.snapshot_ledger_path),
            "content_index": str(service.content_index_path),
        }

    def register_content_identity(
        self,
        *,
        platform: str,
        run_dir: str | Path,
        title: str = "",
        topic_title: str = "",
        published_at: str = "",
        commercial_intent: str = "medium",
        content_mix: str = "professional",
        cta_variant: str = "",
        cta_source_code: str = "",
        title_variant: str = "",
        cover_variant: str = "",
        platform_variant: str = "",
        topic_bucket: str = "",
    ) -> dict[str, str]:
        """注册内容级 ID 并返回归因元数据路径。"""
        from toutiao_content_factory.services.revenue_attribution_service import (
            RevenueAttributionService,
        )

        resolved_run_dir = Path(run_dir).resolve()
        service = RevenueAttributionService(self.settings.output_dir, self.logger)
        identity = service.build_content_identity(
            account_key=self.settings.account_key,
            platform=platform,
            run_dir=resolved_run_dir,
            topic_title=topic_title,
            title=title,
            published_at=published_at or datetime.now().isoformat(timespec="seconds"),
            commercial_intent=commercial_intent,
            content_mix=content_mix,
            cta_variant=cta_variant,
            cta_source_code=cta_source_code,
            title_variant=title_variant,
            cover_variant=cover_variant,
            platform_variant=platform_variant,
            topic_bucket=topic_bucket,
        )
        index_path = service.register_content(identity)
        return {
            "content_id": identity.content_id,
            "content_index": str(index_path),
        }

    def record_content_cost(
        self,
        content_id: str,
        *,
        api_cost: float = 0.0,
        image_cost: float = 0.0,
        labor_minutes: float = 0.0,
        labor_rate_per_hour: float = 0.0,
        currency: str = "CNY",
    ) -> dict[str, str]:
        from toutiao_content_factory.services.revenue_attribution_service import (
            CostRecord,
            RevenueAttributionService,
        )

        service = RevenueAttributionService(self.settings.output_dir, self.logger)
        cost = CostRecord(
            api_cost=api_cost,
            image_cost=image_cost,
            labor_minutes=labor_minutes,
            labor_rate_per_hour=labor_rate_per_hour,
            currency=currency,
        )
        ledger_path = service.record_cost(content_id, cost)
        return {"cost_ledger": str(ledger_path), "content_id": content_id}

    def record_content_revenue_event(
        self,
        content_id: str,
        *,
        event_type: str,
        amount: float,
        currency: str = "CNY",
        lead_id: str = "",
        product_key: str = "",
        channel: str = "",
        note: str = "",
        event_id: str = "",
        replace_existing: bool = False,
    ) -> dict[str, str]:
        from toutiao_content_factory.services.revenue_attribution_service import (
            RevenueAttributionService,
            RevenueEvent,
        )

        service = RevenueAttributionService(self.settings.output_dir, self.logger)
        event = RevenueEvent(
            event_type=event_type,
            amount=amount,
            currency=currency,
            source_article_id=content_id,
            lead_id=lead_id,
            product_key=product_key,
            channel=channel,
            note=note,
            event_id=event_id,
        )
        ledger_path = service.record_revenue_event(
            content_id,
            event,
            replace_existing=replace_existing,
        )
        return {"revenue_ledger": str(ledger_path), "content_id": content_id}

    def compute_unit_economics(
        self,
        content_id: str,
        *,
        platform_revenue: float = 0.0,
    ) -> dict[str, Any]:
        from toutiao_content_factory.services.revenue_attribution_service import (
            RevenueAttributionService,
        )

        service = RevenueAttributionService(self.settings.output_dir, self.logger)
        economics = service.compute_unit_economics(content_id, platform_revenue=platform_revenue)
        return {"unit_economics": str(service.unit_economics_path), "metrics": economics.to_dict()}

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
            # Re-evaluating publish readiness must preserve the independent
            # quality council gate stored with the workflow result.
            workflow_service._apply_quality_review_gate(
                workflow.content_score,
                workflow.quality_review_council,
            )
            workflow_service._apply_bad_words_gate(workflow.content, workflow.content_score)
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
        # Prefer candidates that can support a grounded article.  Hot-board
        # items are often title-only and should remain a fallback, otherwise
        # they can consume all retry slots before a usable source is tried.
        hot_source_types = {"hot_board", "baidu_hot"}

        def topic_sort_key(topic: TopicCandidate) -> tuple[int, int, int, int]:
            return (
                -int(getattr(topic, "evidence_strength", 0) or 0),
                -int(getattr(topic, "quality_score", 0) or 0),
                -int(getattr(topic, "total_score", 0) or 0),
                -int(getattr(topic, "title_potential_score", 0) or 0),
            )

        grounded_material_topics = sorted(
            [
                topic
                for topic in available_topics
                if topic.source_type not in hot_source_types
                and int(getattr(topic, "evidence_strength", 0) or 0) >= 55
                and "title_only" not in {
                    str(flag).strip().lower()
                    for flag in (getattr(topic, "quality_flags", []) or [])
                }
            ],
            key=topic_sort_key,
        )
        other_material_topics = sorted(
            [
                topic
                for topic in available_topics
                if topic.source_type not in hot_source_types
                and topic not in grounded_material_topics
                and int(getattr(topic, "evidence_strength", 0) or 0) >= 55
            ],
            key=topic_sort_key,
        )
        remaining_topics = sorted(
            [
                topic
                for topic in available_topics
                if topic.source_type not in hot_source_types
                and topic not in grounded_material_topics
                and topic not in other_material_topics
            ],
            key=topic_sort_key,
        )
        hot_topics = sorted(
            [topic for topic in available_topics if topic.source_type in hot_source_types],
            key=topic_sort_key,
        )
        ordered = [
            *grounded_material_topics,
            *other_material_topics,
            *remaining_topics,
            *hot_topics,
        ]
        return [
            {
                "rank": available_rank,
                "title": topic.title,
                "url": topic.url,
            }
            for available_rank, topic in enumerate(ordered[:max_attempts], start=1)
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
        # 读取热榜快评标记：标题型热点使用放宽的自动发布门槛
        workflow_path = run_dir / "workflow_result.json"
        if workflow_path.exists():
            wf = _safe_json_loads(workflow_path.read_text(encoding="utf-8"), default={})
            if isinstance(wf, dict):
                data_report = wf.get("data_report") or {}
                if isinstance(data_report, dict):
                    snapshot["is_hot_topic"] = 1 if bool(data_report.get("is_hot_topic", False)) else 0
        article_path = run_dir / "publish" / "article.md"
        if not article_path.exists():
            article_path = run_dir / "article.md"
        if article_path.exists():
            article_text = article_path.read_text(encoding="utf-8").replace("\n", "").replace(" ", "")
            snapshot["article_char_count"] = len(article_text)
        return snapshot

    def _qualifies_for_auto_approval(
        self,
        score_snapshot: dict[str, int],
        *,
        content_guard_report: dict[str, Any] | None = None,
    ) -> bool:
        if not score_snapshot:
            return False
        article_chars = int(score_snapshot.get("article_char_count", 0))
        is_hot_topic = bool(score_snapshot.get("is_hot_topic", 0))
        publishing = self.settings.publishing
        auto_publish_mode = bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )
        minimum_article_chars = (
            1750 if auto_publish_mode else 850 if is_hot_topic else 800
        )
        if article_chars < minimum_article_chars:
            self.logger.warning(
                "Article has only %d chars, below %d-char minimum for auto-approval. "
                "Marking as review_required.",
                article_chars,
                minimum_article_chars,
            )
            return False
        if content_guard_report is not None and (
            content_guard_report.get("blocked")
            or content_guard_report.get("bad_words_blocked")
            or content_guard_report.get("blocking_flags")
        ):
            self.logger.warning(
                "Content guard blocked automatic approval for the generated run: %s",
                " | ".join(
                    str(item)
                    for item in (
                        list(content_guard_report.get("blocking_flags", []))
                        + list(content_guard_report.get("warnings", []))
                    )
                    if str(item).strip()
                )[:500],
            )
            return False
        # 原创红线：与素材原文的连续抄录率。
        # ⚠️ 2026-09-16 发现并修的缺口：content_guard 只把 ≥25% 写成
        # `human_review_required`，而那个字段**全项目没有任何地方读**，于是
        # 分数够的稿子会直接从这条「严格」路径发出去——9-16 02:02 那篇
        # 30.7% 抄录的图文就是这么发的，和 9-15 被扣 5 分的是同一个风险。
        # 现在严格门与兜底门共用同一条线（`_originality_block_reason`），
        # 两条路径不可能再各说各话。
        if content_guard_report is not None:
            originality_reason = self._originality_block_reason(
                content_guard_report.get("source_copy_ratio"),
                is_hot_topic=is_hot_topic,
            )
            if originality_reason:
                self.logger.warning(
                    "Originality gate blocked automatic approval: %s",
                    originality_reason,
                )
                return False
        return self.settings.review.qualifies_for_auto_publish(
            overall_score=int(score_snapshot.get("overall_score", 0)),
            ai_risk=int(score_snapshot.get("ai_generated_risk", 100)),
            validation_score=int(score_snapshot.get("validation_score", 0)),
            publish_readiness_score=int(score_snapshot.get("publish_readiness_score", 0)),
            is_hot_topic=is_hot_topic,
        )

    def _minimum_article_chars(self, run_dir: Path | None) -> int:
        """自动发布模式下正文的最低字数（热点快评放宽）。"""
        publishing = self.settings.publishing
        auto_publish_mode = bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )
        snapshot = self._load_run_score_snapshot(Path(run_dir) if run_dir else None)
        is_hot_topic = bool(snapshot.get("is_hot_topic", 0))
        return 1750 if auto_publish_mode else 850 if is_hot_topic else 800

    @staticmethod
    def _originality_block_reason(
        ratio: float | None, *, is_hot_topic: bool
    ) -> str:
        """原创红线：与素材原文的连续抄录率。严格门与兜底门共用这一条判定。

        被头条判「不满足原创要求」要扣 5 分，还打断「连续 10 天不扣分 +10 分」
        的节奏，代价高于少发一天，所以 ≥25% 直接拦。

        关键：抄录率**量不出来**时不能当作「没问题」。2026-09-15 的 -5 分事故
        正是抄了一篇外部稿，而当时本地只比对自家历史、看不见外部素材。既然这
        条线存在的意义就是守住它，那么没有素材原文可比对时，宁可放弃当天发布。

        例外只有热点选题：热榜条目本来就没有正文素材，无从比对，放行但记录风险。
        返回空串表示放行；返回非空字符串即拦截原因。
        """
        from toutiao_content_factory.services.originality_service import (
            REVIEW_THRESHOLD,
        )

        if ratio is None:
            if is_hot_topic:
                return ""
            return "原创性无法核验：素材原文缺失或过短，拒绝自动发布"
        if float(ratio) >= REVIEW_THRESHOLD:
            return f"与素材连续抄录 {ratio}%，高于 {REVIEW_THRESHOLD}% 上限"
        return ""

    def _fallback_safety_check(self, run_dir: Path | None) -> tuple[bool, list[str], list[str]]:
        """兜底发布前的安全底线检查。

        返回 (是否安全, 阻断原因, 说明)。这里**只查确定性指标**——模型那些
        「可能存在……」的软指控不参与判断，因为兜底的意义正是绕过无证据的
        过度拦截；但任何能被代码证实的硬伤依然一票否决。
        """
        if run_dir is None:
            return False, ["缺少 run 目录"], []
        run_dir = Path(run_dir)
        reasons: list[str] = []
        notes: list[str] = []

        article_file = run_dir / "article.md"
        if not article_file.exists():
            return False, ["缺正文文件"], []
        article = article_file.read_text(encoding="utf-8")
        body_chars = len(re.sub(r"\s+", "", article))
        minimum = self._minimum_article_chars(run_dir)
        if body_chars < minimum:
            reasons.append(f"正文 {body_chars} 字，低于自动发布下限 {minimum} 字")

        # 正文硬伤：内部标签残留、序列化残留、提示词元叙述
        from toutiao_content_factory.services.auto_repair_service import (
            INTERNAL_LEAK_PATTERNS,
            SEQUENCE_RESIDUE_PATTERNS,
        )

        leaks = []
        for pattern in INTERNAL_LEAK_PATTERNS:
            hits = pattern.findall(article)
            if hits:
                leaks.extend(str(h) for h in hits[:3])
        if leaks:
            reasons.append(f"正文含内部标签/占位符：{'、'.join(leaks[:3])}")
        for pattern in SEQUENCE_RESIDUE_PATTERNS:
            if pattern.search(article):
                reasons.append("正文含 Python 序列化残留")
                break
        meta_narration = ("下面只按现有材料", "明确保留为推测", "只按现有材料展开")
        if any(token in article for token in meta_narration):
            reasons.append("正文含提示词元叙述")

        # AI 生成风险：平台会直接对「疑似AI生成」降权，因此这是运营方配置的
        # 硬性上限（非热点 40 / 热点 50），不属于「模型软指控」，兜底也必须
        # 遵守。快照缺失时该值默认为 100（从严），避免无评分数据时放行。
        snapshot = self._load_run_score_snapshot(run_dir)
        is_hot_topic = bool(snapshot.get("is_hot_topic", 0))
        review_settings = self.settings.review
        ai_risk = int(snapshot.get("ai_generated_risk", 100) or 0)
        ai_risk_limit = int(
            review_settings.hot_topic_max_auto_publish_ai_risk
            if is_hot_topic
            else review_settings.max_auto_publish_ai_risk
        )
        if ai_risk > ai_risk_limit:
            reasons.append(
                f"AI 生成风险 {ai_risk}，高于自动发布上限 {ai_risk_limit}"
            )

        # 原创：与素材原文的连续抄录率。判定逻辑与严格自动批准门共用
        # `_originality_block_reason`，避免两条路径各说各话。
        source_file = run_dir / "source_material_full.txt"
        from toutiao_content_factory.services.originality_service import (
            source_copy_ratio,
        )

        ratio = None
        if source_file.exists():
            ratio = source_copy_ratio(
                article, source_file.read_text(encoding="utf-8")
            )["ratio"]
        originality_reason = self._originality_block_reason(
            ratio, is_hot_topic=is_hot_topic
        )
        if originality_reason:
            reasons.append(originality_reason)
        elif ratio is None:
            notes.append("热点选题无原文素材，原创风险未量化")
        else:
            notes.append(f"与素材连续抄录 {ratio}%")

        # 可用标题
        if not self._fallback_title_for(run_dir):
            reasons.append("没有可用标题")

        return (not reasons), reasons, notes

    def _fallback_title_for(self, run_dir: Path) -> str:
        """找一个能用的标题：已选标题 → **质量达标的候选** → 候选首行 → 选题原标题。

        ⚠️ 2026-09-16：兜底发布是云端当前的主力路径（评审委员会几乎总给
        `needs_revision`，于是每次都落到这里），而它此前**完全跳过标题质量门**
        —— `select_auto_title` 那套 RULE_009 校验只在严格门路径上跑。结果被判
        「信息密度不足」的标题照样发出去。头条实测：同一事件完整标题 1.3 万阅读、
        简略标题仅 23 阅读，这个差距直接吃掉当天收益。

        这里用**择优**而不是加门槛：候选里谁先通过标题质量门就用谁，一个都没有
        就退回原有顺序。标题差一点也比当天不发强——这道门守的是流量，不是合规。
        """
        status_path = run_dir / "review_status.json"
        if status_path.exists():
            payload = _safe_json_loads(status_path.read_text(encoding="utf-8-sig"), default={})
            if isinstance(payload, dict):
                selected = str(payload.get("selected_title") or "").strip()
                if selected:
                    return selected

        candidates = self._fallback_title_candidates(run_dir)
        if not candidates:
            return ""
        return self._best_quality_title(candidates, run_dir) or candidates[0]

    @staticmethod
    def _fallback_title_candidates(run_dir: Path) -> list[str]:
        """按兜底的历史优先级收集候选标题（去重、保持顺序）。"""
        candidates: list[str] = []

        def _add(title: object) -> None:
            cleaned = str(title or "").strip()
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        titles_file = run_dir / "titles.txt"
        if titles_file.exists():
            for raw in titles_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip().lstrip("0123456789.、 ")
                if not line or line.endswith(("：", ":")) or line in ("全部标题", "推荐标题"):
                    continue
                _add(line)

        topic_file = run_dir / "selected_topic.json"
        if topic_file.exists():
            payload = _safe_json_loads(topic_file.read_text(encoding="utf-8-sig"), default={})
            if isinstance(payload, dict):
                topic = payload.get("selected_topic") or {}
                _add(topic.get("title"))
        return candidates

    def _best_quality_title(self, candidates: list[str], run_dir: Path) -> str:
        """返回候选里第一个通过标题质量门的标题；都不达标返回空串。"""
        try:
            from toutiao_content_factory.services.article_quality_checker import (
                ArticleQualityChecker,
            )

            article = ""
            publish_dir = run_dir / self.settings.review.publish_dirname
            for path in (publish_dir / "article.md", run_dir / "article.md"):
                if path.exists():
                    article = path.read_text(encoding="utf-8", errors="replace")
                    break
            checker = ArticleQualityChecker()
            for candidate in candidates:
                result = checker._check_title(candidate, article)
                if not [item for item in result.get("issues", []) if str(item).strip()]:
                    return candidate
        except Exception:
            self.logger.debug(
                "Title quality scan failed; keeping legacy fallback order.", exc_info=True
            )
        return ""

    def _fallback_rank_key(self, attempt: dict[str, Any]) -> tuple[int, int]:
        """兜底候选的排序依据：就绪度优先，其次总分。"""
        snapshot = attempt.get("score_snapshot") or {}
        return (
            int(snapshot.get("publish_readiness_score", 0) or 0),
            int(snapshot.get("overall_score", 0) or 0),
        )

    def _try_fallback_publish(
        self,
        candidates: list[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any] | None:
        """所有候选都没过自动门槛时，放行一篇安全的稿子。

        按「就绪度 → 总分」排序后**逐个尝试**，而不是只看最优那篇：最优的
        那篇可能恰好抄录率超标或字数不足，但排在后面的稿子完全可用。只在
        自动发布模式下启用；任何确定性硬伤都会跳过该候选，全部不过就宁可
        当天不发，也不发一篇会被判不原创或含内部标签的稿子。
        """
        publishing = self.settings.publishing
        auto_mode = bool(
            getattr(publishing, "enabled", False)
            and getattr(publishing, "auto_publish", False)
            and not getattr(publishing, "draft_only", False)
        )
        if not auto_mode or not candidates:
            return None

        ranked = sorted(candidates, key=self._fallback_rank_key, reverse=True)
        chosen: dict[str, Any] | None = None
        run_dir: Path | None = None
        notes: list[str] = []
        for attempt in ranked:
            candidate_dir = self._path_or_none(attempt.get("run_dir"))
            safe, reasons, candidate_notes = self._fallback_safety_check(candidate_dir)
            if not safe:
                self.logger.warning(
                    "Fallback publish skipped %s: %s",
                    candidate_dir,
                    " | ".join(reasons)[:300],
                )
                continue
            chosen = attempt
            run_dir = candidate_dir
            notes = candidate_notes
            break

        if chosen is None or run_dir is None:
            self.logger.warning(
                "Fallback publish declined: none of the %d candidates passed safety checks.",
                len(ranked),
            )
            return None

        title = self._fallback_title_for(run_dir)
        self.logger.info(
            "Fallback publish engaged for %s (title=%s; notes=%s)",
            run_dir,
            title,
            " | ".join(notes)[:300],
        )
        try:
            approve_exports = self.approve_run(
                str(run_dir),
                selected_title=title,
                selected_title_index=0,
                review_notes=(
                    "Fallback auto-approval: no candidate passed the strict gate, "
                    "publishing the best one after deterministic safety checks."
                ),
                auto_publish=False,
            )
        except Exception as exc:
            self.logger.error("Fallback approve failed for %s: %s", run_dir, exc)
            return None

        exports = {name: str(path) for name, path in approve_exports.items()}
        platform_exports, platform_results, platform_errors = self._run_daily_platform_automation(
            run_dir,
            dry_run=dry_run,
            skip_wait=False,
        )
        exports.update(platform_exports)
        return {
            "run_dir": str(run_dir),
            "attempt_fields": {
                "status": "fallback_published",
                "run_dir": str(run_dir),
                "fallback_reason": "no_candidate_passed_strict_gate",
                "fallback_notes": notes,
                "selected_title": title,
                "platform_results": platform_results,
                "platform_errors": platform_errors,
            },
            "exports": exports,
        }

    def _load_content_guard_report(self, run_dir: Path | None) -> dict[str, Any]:
        if run_dir is None:
            return {}
        report_path = run_dir / "content_guard_report.json"
        if not report_path.exists():
            return {}
        try:
            payload = _safe_json_loads(report_path.read_text(encoding="utf-8-sig"), default={})
        except OSError:
            return {}
        return payload if isinstance(payload, dict) else {}

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
        weitoutiao_enabled = bool(self.settings.publishing.weitoutiao_enabled)
        inline_images_enabled = bool(getattr(self.settings.publishing, "inline_images_enabled", True))
        companion: dict[str, Any] = {
            "status": "not_attempted" if weitoutiao_enabled else "disabled",
        }
        topup: dict[str, Any] = {
            "status": "not_attempted" if weitoutiao_enabled else "disabled",
        }

        daily_wechat_enabled = bool(self.settings.wechat.enabled and self.settings.wechat.auto_publish)
        should_generate_images = (
            not self.settings.skip_image_generation
            and (
                daily_wechat_enabled
                or inline_images_enabled
                or str(self.settings.publishing.cover_mode or "auto").strip().lower() != "none"
            )
        )
        if should_generate_images:
            try:
                image_exports = self.image_generation_service.generate_for_run(
                    run_dir,
                    include_infographics=daily_wechat_enabled or inline_images_enabled,
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

        blocked_platforms = set(getattr(self, "_daily_blocked_platforms", set()))
        wechat_completed = False
        wechat_blocked_by_ip = False
        if "wechat" in blocked_platforms:
            message = "WeChat publish blocked by auth preflight; login is required before retry."
            platform_results["wechat"] = {"status": "publish_login_required", "error": message}
            platform_errors.append(message)
        elif self.settings.wechat.enabled and self.settings.wechat.auto_publish:
            if self.circuit_breaker.is_open("wechat"):
                self.logger.warning("WeChat publish skipped: circuit breaker is open for wechat.")
                message = "WeChat publish skipped because the circuit breaker is open."
                platform_results["wechat"] = {"status": "circuit_open", "error": message}
                platform_errors.append(message)
            else:
                try:
                    wechat_result = self.wechat_service.publish_run(run_dir, submit=True)
                    wechat_exports = {name: str(path) for name, path in wechat_result.items()}
                    wechat_exports["resolved_run_dir"] = str(run_dir)
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
                            run_time=notification_now().strftime("%Y-%m-%d %H:%M"),
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
                        run_time=notification_now().strftime("%Y-%m-%d %H:%M"),
                    )
                    if self._is_session_expired_error(exc):
                        self._notify_session_expired("WeChat", exc)

        if "toutiao" in blocked_platforms:
            message = "Toutiao publish blocked by auth preflight; login is required before retry."
            platform_results["toutiao"] = {"status": "publish_login_required", "error": message}
            platform_errors.append(message)
        elif self.settings.publishing.enabled and self.settings.publishing.auto_publish:
            if self.circuit_breaker.is_open("toutiao"):
                self.logger.warning("Toutiao publish skipped: circuit breaker is open for toutiao.")
                message = "Toutiao publish skipped because the circuit breaker is open."
                platform_results["toutiao"] = {"status": "circuit_open", "error": message}
                platform_errors.append(message)
            else:
                wechat_blocks_toutiao = (
                    daily_wechat_enabled
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
                if delay_seconds > 0 and wechat_completed and daily_wechat_enabled:
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
                    toutiao_snapshot = self._load_toutiao_publish_snapshot(run_dir)
                    platform_results["toutiao"] = toutiao_snapshot
                    toutiao_status = str(toutiao_snapshot.get("status", "")).strip().lower()
                    if self._is_accepted_publish_status(toutiao_status):
                        self.circuit_breaker.record_success("toutiao")
                        self.notification_service.notify_success(
                            platform="Toutiao",
                            article_title=self._load_selected_title(run_dir),
                            run_time=notification_now().strftime("%Y-%m-%d %H:%M"),
                        )
                    elif toutiao_status == "draft_saved" and bool(self.settings.publishing.draft_only):
                        self.logger.info(
                            "Toutiao daily workflow intentionally stopped at draft_saved because draft_only is enabled."
                        )
                    else:
                        message = (
                            "Toutiao publish did not reach an accepted state: "
                            f"{toutiao_status or 'missing'}. Manual confirmation is required before retry."
                        )
                        platform_errors.append(message)
                        self.circuit_breaker.record_failure("toutiao")
                        self.logger.warning(message)
                        self.notification_service.notify_failure(
                            platform="Toutiao",
                            stage="publish_verification",
                            error_msg=message,
                            run_time=notification_now().strftime("%Y-%m-%d %H:%M"),
                        )
                    # 图文发布成功后，同步发一条微头条引流（涨粉 + 图文双通道）。
                    # 微头条内容取自同一 run，可复用登录会话与内容包。
                    # 微头条错峰（2026-09-04）：weitoutiao_companion_enabled=false 时不立即发，
                    # 交给后续时段的 standalone top-up 补发，避免图文+微头条同窗口连发。
                    if (
                        self._is_accepted_publish_status(toutiao_status)
                        and self.settings.publishing.weitoutiao_enabled
                        and self.settings.publishing.weitoutiao_companion_enabled
                    ):
                        try:
                            wtt_exports = self.publish_weitoutiao_run(str(run_dir))
                            exported_files.update(wtt_exports)
                            companion = self._load_weitoutiao_companion_result(run_dir, wtt_exports)
                            companion_status = str(companion.get("status", "")).strip().lower()
                            if self._is_accepted_weitoutiao_status(companion_status):
                                self.logger.info(
                                    "WeiToutiao companion post published for run: %s", run_dir
                                )
                                self.notification_service.notify_success(
                                    platform="WeiToutiao",
                                    article_title=self._load_selected_title(run_dir),
                                    run_time=notification_now().strftime("%Y-%m-%d %H:%M"),
                                )
                        except Exception as wtt_exc:
                            companion = {
                                "status": "failed",
                                "error": str(wtt_exc),
                            }
                            # 微头条失败不阻塞主流程，仅记录
                            self.logger.warning(
                                "WeiToutiao companion post failed (non-fatal): %s", wtt_exc
                            )

                    # 微头条高频化（2026-08-27）：伴生 1 条之外，按每日目标
                    # 补齐独立微头条（独立选题 + 独立写作），每日共 2-3 条。
                    # 独立微头条失败同样不阻塞主流程。
                    if (
                        self._is_accepted_publish_status(toutiao_status)
                        and self.settings.publishing.weitoutiao_enabled
                        and self.settings.publishing.weitoutiao_standalone_enabled
                    ):
                        try:
                            topup_exports = self._top_up_standalone_weitoutiao(max_count=2)
                            exported_files.update(topup_exports)
                            topup = self._load_weitoutiao_batch_result(topup_exports)
                        except Exception as topup_exc:
                            topup = {
                                "status": "failed",
                                "message": "WeiToutiao standalone top-up raised an exception.",
                                "error": str(topup_exc),
                            }
                            self.logger.warning(
                                "WeiToutiao standalone top-up failed (non-fatal): %s", topup_exc
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
                        run_time=notification_now().strftime("%Y-%m-%d %H:%M"),
                    )
                    if self._is_session_expired_error(exc):
                        self._notify_session_expired("Toutiao", exc)

        # ── 多平台发布（知乎 / 小红书） ──
        if weitoutiao_enabled:
            neutral_statuses = {"", "not_attempted", "disabled", "target_reached"}
            for label, payload in (("companion", companion), ("standalone top-up", topup)):
                status = str(payload.get("status", "")).strip().lower()
                if status in neutral_statuses or self._is_accepted_weitoutiao_status(status):
                    continue
                error = str(
                    payload.get("error")
                    or payload.get("message")
                    or f"status={status or 'missing'}"
                ).strip()
                payload["error"] = error
                platform_errors.append(f"WeiToutiao {label} failed: {error}")

            weitoutiao_result_path, weitoutiao_daily_result = self._write_weitoutiao_daily_result(
                run_dir,
                companion=companion,
                topup=topup,
            )
            exported_files["weitoutiao_daily_result"] = str(weitoutiao_result_path)
            platform_results["weitoutiao"] = weitoutiao_daily_result

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

    @staticmethod
    def _is_accepted_publish_status(status: str) -> bool:
        return str(status or "").strip().lower() in {
            "published",
            "submitted",
            "submitted_for_review",
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
        if normalized in {
            "success",
            "completed",
            "published",
            "submitted",
            "submitted_for_review",
            "approved",
            "draft_created",
            "draft_saved",
            "ready",
        }:
            return "success"
        if normalized in {
            "failed",
            "error",
            "blocked",
            "blocked_by_ip",
            "publish_login_required",
            "publish_uncertain",
            "missing",
            "invalid",
        }:
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

    @staticmethod
    def _title_duplicate_error_markers() -> tuple[str, ...]:
        return (
            "same or highly similar title was published",
            "same or similar title was already submitted",
            "title was already published in the last 7 days",
            "duplicate publish blocked per rule_009/rule_007",
            "duplicate publish detected on retry",
        )

    def _is_safe_title_duplicate_fallback(
        self,
        platform_results: dict[str, dict[str, object]],
        platform_errors: list[str],
    ) -> bool:
        """Return whether a failed candidate can be replaced without retry risk."""
        toutiao_result = platform_results.get("toutiao")
        if not isinstance(toutiao_result, dict):
            return False

        toutiao_status = str(toutiao_result.get("status", "")).strip().lower()
        if toutiao_status not in {"failed", "error", "blocked"}:
            return False

        # Never switch candidates after any platform may have accepted or
        # possibly accepted the current run.
        for payload in platform_results.values():
            if not isinstance(payload, dict):
                continue
            status = str(payload.get("status", "")).strip().lower()
            if self._is_accepted_publish_status(status) or status == "publish_uncertain":
                return False

        messages = [str(toutiao_result.get("error", ""))]
        messages.extend(str(item) for item in platform_errors)
        combined = " ".join(messages).lower()
        return any(marker in combined for marker in self._title_duplicate_error_markers())

    def _first_title_duplicate_reason(
        self,
        platform_results: dict[str, dict[str, object]],
        platform_errors: list[str],
    ) -> str:
        toutiao_result = platform_results.get("toutiao", {})
        candidates = []
        if isinstance(toutiao_result, dict):
            candidates.append(str(toutiao_result.get("error", "")).strip())
        candidates.extend(str(item).strip() for item in platform_errors)
        for message in candidates:
            if message:
                return message
        return "Duplicate title guard rejected the candidate."

    def _notify_daily_review_required(self, final_attempt: dict[str, object]) -> None:
        run_dir = str(final_attempt.get("run_dir", "")).strip()
        reason = str(final_attempt.get("skip_reason", "")).strip()
        if not reason:
            score_snapshot = dict(final_attempt.get("score_snapshot", {}))
            reason = self._build_skip_reason(score_snapshot)
        try:
            self.notification_service.notify_review_required(
                account=self.settings.account_label,
                selected_title=str(final_attempt.get("selected_title", "")).strip(),
                run_dir=run_dir,
                reason=reason,
                run_time=notification_now().strftime("%Y-%m-%d %H:%M"),
            )
        except Exception as exc:
            self.logger.warning("Failed to send daily review-required notification: %s", exc)

    def _count_successful_platform_results(self, platform_results: dict[str, dict[str, object]]) -> int:
        successful_statuses = {
            "published",
            "submitted",
            "submitted_for_review",
            "success",
            "draft_created",
            "draft_saved",
        }
        count = 0
        for payload in platform_results.values():
            status = str(payload.get("status", "")).strip()
            if status in successful_statuses:
                count += 1
        return count

    def _summarize_distribution_status(self, platform_results: dict[str, dict[str, object]]) -> str:
        if not platform_results:
            return "approved"
        statuses = {
            str(item.get("status", "")).strip().lower()
            for item in platform_results.values()
            if isinstance(item, dict)
        }
        for status in (
            "published",
            "submitted_for_review",
            "submitted",
            "success",
            "draft_saved",
            "draft_created",
        ):
            if status in statuses:
                return status
        for status in (
            "publish_uncertain",
            "publish_login_required",
            "failed",
            "blocked",
            "circuit_open",
            "missing",
            "invalid",
        ):
            if status in statuses:
                return status
        return "approved"

    def _mark_attempts_skipped(self, attempts: list[dict[str, object]]) -> None:
        for attempt in attempts:
            run_dir = self._path_or_none(attempt.get("run_dir"))
            if run_dir is None:
                continue
            reason = str(attempt.get("skip_reason", "")).strip() or self._build_skip_reason(
                dict(attempt.get("score_snapshot", {}))
            )
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
        run_time = notification_now().strftime("%Y-%m-%d %H:%M")
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
        reason = "; ".join(platform_messages) if platform_messages else "login session invalid"
        try:
            self.notification_service.notify_failure(
                platform=", ".join(readable_platforms),
                stage="auth_preflight",
                error_msg=(
                    "Publish auth precheck failed; publishing was disabled for this run and "
                    f"content generation continued in draft mode. reasons: {reason}"
                ),
                run_time=run_time,
                debug_url=platform_health_path or None,
            )
        except Exception as exc:
            self.logger.warning("Failed to queue platform auth notification: %s", exc)

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
        run_time = notification_now().strftime("%Y-%m-%d %H:%M")
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
        if status_path.exists():
            payload = _safe_json_loads(status_path.read_text(encoding="utf-8"), default={})
            if isinstance(payload, dict):
                selected = str(payload.get("selected_title", "")).strip()
                if selected:
                    return selected

        # Pending/skipped runs intentionally leave selected_title empty. The
        # review packet still has usable candidates, so notifications should
        # identify the actual draft instead of reporting "未选择标题".
        workflow_path = run_dir / "workflow_result.json"
        if workflow_path.exists():
            payload = _safe_json_loads(workflow_path.read_text(encoding="utf-8"), default={})
            if isinstance(payload, dict):
                content = payload.get("content", {})
                if isinstance(content, dict):
                    for field in ("recommended_titles", "titles"):
                        candidates = content.get(field, [])
                        if isinstance(candidates, list):
                            for candidate in candidates:
                                title = str(candidate).strip()
                                if title:
                                    return title

        titles_path = run_dir / "titles.txt"
        if titles_path.exists():
            recommended: list[str] = []
            all_titles: list[str] = []
            in_recommended_section = False
            for raw_line in titles_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if "推荐标题" in line:
                    in_recommended_section = True
                    continue
                if line.startswith(("全部标题", "候选标题")):
                    continue
                line = re.sub(r"^\d+[.)、：:]\s*", "", line).strip()
                if not line:
                    continue
                all_titles.append(line)
                if in_recommended_section:
                    recommended.append(line)
            if recommended:
                return recommended[0]
            if all_titles:
                return all_titles[0]
        return ""

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
            f"{notification_now().strftime('%Y-%m-%d %H:%M')} auto_run 前置健康检查失败。\n"
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
