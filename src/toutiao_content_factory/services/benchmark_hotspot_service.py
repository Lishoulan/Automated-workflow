from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings, TopicCollectionSettings
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.services.topic_collection import TopicCollectionService


class BenchmarkHotspotService:
    TRACK_INDUSTRY_MAP = {
        "AI机器人": ["humanoid_robot", "ai_hardware", "industrial_automation"],
        "新能源储能": ["energy_storage", "new_energy"],
        "国际热点": ["international"],
        "体育赛事": ["sports"],
        "科技商业": [
            "humanoid_robot",
            "new_energy",
            "energy_storage",
            "ai_hardware",
            "industrial_automation",
        ],
    }
    # Track keywords must be scoped to the industry they describe.  Adding an
    # account name or a broad editorial pillar to every target industry makes
    # an unrelated article such as a railway earnings report look like a
    # robotics/energy topic merely because it contains "AI" or "公司".
    TRACK_INDUSTRY_KEYWORDS = {
        "AI机器人": {
            "humanoid_robot": ("机器人", "人形机器人", "具身智能", "灵巧手", "减速器", "执行器", "无框力矩电机", "丝杠"),
            "ai_hardware": ("AI硬件", "算力", "服务器", "GPU", "芯片", "边缘计算", "CPO"),
            "industrial_automation": ("智能装备", "工业自动化", "工业机器人", "机器视觉", "PLC", "伺服", "传感器", "工业母机"),
        },
        "新能源储能": {
            "new_energy": ("新能源", "光伏", "风电", "锂电", "电池", "充电", "新能源汽车", "动力电池", "固态电池", "钠电", "充换电", "光储"),
            "energy_storage": ("储能", "工商业储能", "PCS", "EMS", "逆变器", "储能电站", "储能电池", "大储", "户储", "液流电池", "源网荷储", "构网型储能"),
        },
        "国际热点": {
            "international": ("国际", "美国", "欧洲", "俄罗斯", "乌克兰", "中东", "关税", "外交", "局势", "制裁", "地缘"),
        },
        "体育赛事": {
            "sports": ("体育", "足球", "篮球", "网球", "国足", "NBA", "英超", "欧冠", "比赛", "联赛", "世界杯"),
        },
        # The broad technology track intentionally contributes no keywords.
        # Its configured industry map remains the source of topic matching.
        "科技商业": {},
    }

    GENERIC_KEYWORD_BLACKLIST = frozenset(
        {
            "AI",
            "公司",
            "企业",
            "科技",
            "技术",
            "模型",
            "行业",
            "产业",
            "供应链",
            "发展",
            "市场",
            "项目",
            "投资",
            "合作",
            "建设",
            "生产",
            "制造",
        }
    )

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.growth_ops_service = GrowthOpsService(settings, logger)
        self.root_dir = settings.output_dir / "growth_ops" / "benchmark_hotspots"

    def collect(self, *, platform: str | None = None, track: str | None = None, limit: int = 20) -> dict[str, Path]:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        platform_key = self.growth_ops_service.resolve_benchmark_platform(platform)
        requested_track = str(track or "").strip()
        normalized_track = (
            self.growth_ops_service.infer_title_playbook_track(requested_track)
            if requested_track
            else ""
        )

        accounts = [
            self.growth_ops_service._normalize_benchmark_account(item)
            for item in self.growth_ops_service.load_benchmark_accounts(platform_key)
        ]
        accounts = [
            item
            for item in accounts
            if item.get("monitor_enabled", True) and item.get("source_feeds")
        ]
        accounts.sort(
            key=lambda item: (
                int(item.get("monitor_priority", 0)),
                str(item.get("name", "")),
            ),
            reverse=True,
        )
        if normalized_track:
            accounts = [
                item for item in accounts if str(item.get("track", "")).strip() == normalized_track
            ]
        if not accounts:
            raise ValueError(
                f"No monitorable benchmark accounts found for platform={platform_key}"
                + (f" track={normalized_track}" if normalized_track else "")
            )

        source_configs: dict[str, dict[str, Any]] = {}
        source_meta: dict[str, dict[str, str]] = {}
        for account in accounts:
            account_key = str(account.get("key", "")).strip() or "benchmark"
            account_name = str(account.get("name", "")).strip() or account_key
            account_track = str(account.get("track", "")).strip()
            for feed_index, source_feed in enumerate(account.get("source_feeds", []), start=1):
                urls = [str(item).strip() for item in source_feed.get("urls", []) if str(item).strip()]
                if not urls:
                    continue
                source_name = f"benchmark_{account_key}_{feed_index}"
                source_configs[source_name] = {
                    "type": str(source_feed.get("type", "html")).strip() or "html",
                    "collector_name": str(source_feed.get("collector_name", "")).strip(),
                    "same_domain_only": bool(source_feed.get("same_domain_only", False)),
                    "urls": urls,
                }
                source_meta[source_name] = {
                    "account_key": account_key,
                    "account_name": account_name,
                    "track": account_track,
                    "platform": platform_key,
                    "feed_label": str(source_feed.get("label", "")).strip() or source_name,
                }

        if not source_configs:
            raise ValueError(
                f"Benchmark account library for platform={platform_key} has no usable source feeds."
            )

        focus_industries = self._resolve_focus_industries(accounts, normalized_track)
        keyword_map = self._build_keyword_map(accounts)
        base_settings = self.settings.topic_collection
        benchmark_topic_settings = TopicCollectionSettings(
            lookback_days=max(7, int(base_settings.lookback_days)),
            max_topic_age_hours=max(240, int(base_settings.max_topic_age_hours)),
            drop_topics_without_timestamp=False,
            max_ranked_items_per_source=max(4, min(8, int(base_settings.max_ranked_items_per_source))),
            max_items_per_source=max(6, min(10, int(base_settings.max_items_per_source))),
            max_export_topics=max(limit * 2, int(base_settings.max_export_topics)),
            request_timeout_seconds=int(base_settings.request_timeout_seconds),
            enabled_sources=list(source_configs.keys()),
            focus_industries=focus_industries,
            keyword_map=keyword_map,
            source_configs=source_configs,
        )
        benchmark_settings = replace(self.settings, topic_collection=benchmark_topic_settings)
        collector = TopicCollectionService(benchmark_settings, self.logger)
        report = collector.collect_sync()
        fallback_used = False
        if not report.topics:
            fallback_topics = self._collect_relaxed_topics(collector)
            if fallback_topics:
                report.topics = fallback_topics[: max(1, limit * 2)]
                report.total_selected_items = len(report.topics)
                fallback_used = True

        topic_rows: list[dict[str, Any]] = []
        for rank, topic in enumerate(report.topics[: max(1, limit)], start=1):
            metadata = source_meta.get(topic.source, {})
            row = {
                "rank": rank,
                "title": topic.title,
                "url": topic.url,
                "published_at": topic.published_at,
                "summary": topic.summary,
                "snippet": topic.snippet,
                "benchmark_account_key": metadata.get("account_key", ""),
                "benchmark_account_name": metadata.get("account_name", topic.source),
                "benchmark_platform": metadata.get("platform", platform_key),
                "benchmark_track": metadata.get("track", topic.title_playbook_track),
                "benchmark_feed_label": metadata.get("feed_label", ""),
                "matched_industries": list(topic.matched_industries),
                "matched_keywords": list(topic.matched_keywords),
                "extracted_numbers": list(topic.extracted_numbers),
                "topic_type": topic.topic_type,
                "topic_type_label": topic.topic_type_label,
                "heat_score": int(topic.heat_score),
                "read_score": int(topic.read_score),
                "save_score": int(topic.save_score),
                "discussion_score": int(topic.discussion_score),
                "quality_score": int(topic.quality_score),
                "title_potential_score": int(topic.title_potential_score),
                "total_score": int(topic.total_score),
                "title_potential_reasons": list(topic.title_potential_reasons),
                "quality_flags": list(topic.quality_flags),
                "recommended_format": topic.recommended_format,
                "recommended_format_label": topic.recommended_format_label,
            }
            topic_rows.append(row)

        source_stats: dict[str, dict[str, Any]] = {}
        for source_name, stats in report.source_stats.items():
            metadata = source_meta.get(source_name, {})
            source_stats[source_name] = {
                "account_name": metadata.get("account_name", source_name),
                "account_key": metadata.get("account_key", ""),
                "track": metadata.get("track", ""),
                "feed_label": metadata.get("feed_label", ""),
                **dict(stats),
            }

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "platform": platform_key,
            "requested_track": requested_track,
            "resolved_track": normalized_track,
            "account_count": len(accounts),
            "source_count": len(source_configs),
            "fallback_used": fallback_used,
            "focus_industries": focus_industries,
            "accounts": [
                {
                    "key": item.get("key", ""),
                    "name": item.get("name", ""),
                    "track": item.get("track", ""),
                    "positioning": item.get("positioning", ""),
                    "monitor_priority": item.get("monitor_priority", 0),
                    "source_feed_count": len(item.get("source_feeds", [])),
                }
                for item in accounts
            ],
            "topics": topic_rows,
            "source_stats": source_stats,
            "failed_sources": list(report.failed_sources),
            "filtered_out": dict(report.filtered_out),
        }

        suffix = f"_{platform_key}" + (f"_{self._slugify(normalized_track)}" if normalized_track else "")
        json_path = self.root_dir / f"benchmark_hot_topics{suffix}.json"
        md_path = self.root_dir / f"benchmark_hot_topics{suffix}.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(self._build_markdown(payload), encoding="utf-8")

        exports: dict[str, Path] = {
            "benchmark_hot_topics_json": json_path,
            "benchmark_hot_topics_markdown": md_path,
        }
        if not normalized_track:
            alias_json_path = self.root_dir / f"benchmark_hot_topics_latest_{platform_key}.json"
            alias_md_path = self.root_dir / f"benchmark_hot_topics_latest_{platform_key}.md"
            alias_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            alias_md_path.write_text(self._build_markdown(payload), encoding="utf-8")
            exports["benchmark_hot_topics_latest_json"] = alias_json_path
            exports["benchmark_hot_topics_latest_markdown"] = alias_md_path
        return exports

    def _collect_relaxed_topics(self, collector: TopicCollectionService) -> list[Any]:
        async def _run_collectors() -> list[Any]:
            return await asyncio.gather(*(item.collect() for item in collector._build_collectors()))

        try:
            results = asyncio.run(_run_collectors())
        except Exception as exc:
            self.logger.warning("Relaxed benchmark hotspot fallback failed: %s", exc)
            return []

        topics = []
        seen: set[str] = set()
        filtered_out = {
            "missing_timestamp": 0,
            "stale": 0,
            "industry_mismatch": 0,
            "weak_focus": 0,
            "low_quality": 0,
            "duplicate": 0,
            "source_overflow": 0,
            "relaxed_added": 0,
        }
        for result in results:
            for item in result.items:
                enriched = collector._enrich_topic(item)
                if not collector._filter_by_freshness([enriched], filtered_out):
                    continue
                if not collector._filter_to_focus_industries([enriched], filtered_out):
                    continue
                if not collector._passes_topic_focus(enriched, filtered_out):
                    continue
                identity = f"{enriched.url}|{enriched.title}".strip()
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                topics.append(enriched)
        topics.sort(
            key=lambda item: (
                int(getattr(item, "total_score", 0)),
                int(getattr(item, "title_potential_score", 0)),
                int(getattr(item, "quality_score", 0)),
            ),
            reverse=True,
        )
        return topics

    def _resolve_focus_industries(self, accounts: list[dict[str, Any]], normalized_track: str) -> list[str]:
        if normalized_track and normalized_track in self.TRACK_INDUSTRY_MAP:
            return list(self.TRACK_INDUSTRY_MAP[normalized_track])
        return list(self.settings.topic_collection.keyword_map.keys()) or list(self.settings.topic_collection.focus_industries)

    def _build_keyword_map(self, accounts: list[dict[str, Any]]) -> dict[str, list[str]]:
        keyword_map = {
            key: [str(item).strip() for item in values if str(item).strip()]
            for key, values in self.settings.topic_collection.keyword_map.items()
        }
        additions: dict[str, list[str]] = {key: [] for key in keyword_map}
        for account in accounts:
            track = str(account.get("track", "")).strip()
            target_industries = self.TRACK_INDUSTRY_MAP.get(track, [])
            track_keywords = self.TRACK_INDUSTRY_KEYWORDS.get(track, {})
            content_pillars = [
                str(item).strip()
                for item in account.get("content_pillars", [])
                if str(item).strip()
                and str(item).strip() not in self.GENERIC_KEYWORD_BLACKLIST
            ]
            for industry in target_industries:
                additions.setdefault(industry, [])
                additions[industry].extend(track_keywords.get(industry, ()))
                configured_keywords = keyword_map.get(industry, [])
                # Preserve a custom pillar only when it is already an
                # industry keyword (or a longer label containing one).  This
                # keeps useful custom configuration while preventing broad
                # pillars such as "公司战略" from matching every industry.
                additions[industry].extend(
                    token
                    for token in content_pillars
                    if any(
                        token == configured or configured in token
                        for configured in configured_keywords
                        if configured and configured not in self.GENERIC_KEYWORD_BLACKLIST
                    )
                )
        for industry, tokens in additions.items():
            merged = keyword_map.setdefault(industry, [])
            for token in tokens:
                if token and token not in merged:
                    merged.append(token)
        return keyword_map

    def _slugify(self, value: str) -> str:
        lowered = str(value or "").strip().lower()
        if not lowered:
            return ""
        parts = [ch if ch.isalnum() else "_" for ch in lowered]
        return "".join(parts).strip("_") or "track"

    def _build_markdown(self, payload: dict[str, Any]) -> str:
        lines = [
            "# 对标热点监控",
            "",
            f"- 生成时间：{payload.get('generated_at', '')}",
            f"- 平台：{payload.get('platform', '')}",
            f"- 赛道过滤：{payload.get('resolved_track', '') or '全部'}",
            f"- 监控账号数：{payload.get('account_count', 0)}",
            f"- 监控源数：{payload.get('source_count', 0)}",
            "",
            "## 监控账号",
            "",
        ]
        for account in payload.get("accounts", []):
            lines.append(
                f"- {account.get('name', '')} | {account.get('track', '')} | 源 {account.get('source_feed_count', 0)} 个"
            )
        lines.append("")
        lines.extend(["## 最新最热热点", ""])
        for item in payload.get("topics", []):
            lines.extend(
                [
                    f"### {item.get('rank', 0)}. {item.get('title', '')}",
                    f"- 对标账号：{item.get('benchmark_account_name', '')}",
                    f"- 赛道：{item.get('benchmark_track', '')}",
                    f"- 发布时间：{item.get('published_at', '') or '未知'}",
                    f"- 分数：总分 {item.get('total_score', 0)} / 热度 {item.get('heat_score', 0)} / 标题潜力 {item.get('title_potential_score', 0)}",
                    f"- 关键词：{', '.join(item.get('matched_keywords', [])[:8]) or '暂无'}",
                    f"- 关键数字：{', '.join(item.get('extracted_numbers', [])[:6]) or '暂无'}",
                    f"- 链接：{item.get('url', '')}",
                    f"- 摘要：{item.get('summary', '') or item.get('snippet', '') or '暂无'}",
                    "",
                ]
            )
        failed_sources = payload.get("failed_sources", [])
        if failed_sources:
            lines.extend(["## 失败源", ""])
            for item in failed_sources[:20]:
                lines.append(f"- {item.get('source', '')}: {item.get('error', '')}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"
