from __future__ import annotations

import asyncio
import html
import logging
import re
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from toutiao_content_factory.config import AppSettings, TopicCollectionSettings
from toutiao_content_factory.models import DailyTopicsReport, TopicCandidate


@dataclass
class TopicSourceResult:
    source_name: str
    items: list[TopicCandidate]
    errors: list[str]


class TopicCollectionService:
    NUMBER_PATTERN = re.compile(
        r"\d+(?:\.\d+)?(?:%|倍|年|季度|个月|周|天|小时|分钟|亿元|亿美元|万元|万台|万辆|万套|万吨|万千瓦|万千瓦时|"
        r"GWh|MWh|kWh|Wh|Ah|GW|MW|kW|W|V|A)"
    )
    TITLE_NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    DISCUSSION_TOKENS = ("融资", "订单", "量产", "发布", "争议", "突破", "价格战", "出海", "裁员", "收购")
    SAVE_TOKENS = ("一图看懂", "产业链", "数据", "测算", "对比", "拆解", "复盘", "指标", "路线图")
    READ_TOKENS = ("为什么", "背后", "首次", "最新", "量产", "融资", "爆发", "拐点", "机会")
    GENERIC_MACRO_TOKENS = (
        "国民经济",
        "经济运行",
        "宏观数据",
        "社会消费品零售总额",
        "固定资产投资",
        "规上工业增加值",
        "统计局",
        "发布会",
        "中国经济数据",
        "宏观",
    )
    TOPIC_TYPE_LABELS = {
        "deep_analysis": "深度分析",
        "quick_commentary": "快讯点评",
        "project_breakdown": "项目拆解",
        "data_recap": "数据复盘",
        "industry_event": "行业事件",
        "project_progress": "项目进展",
        "financing_update": "融资动态",
        "earnings_signal": "业绩信号",
        "policy_market": "政策与市场",
        "product_launch": "产品发布",
    }
    PROJECT_TOKENS = ("项目", "中标", "签约", "并网", "开工", "投产", "示范", "验收", "交付", "装机")
    FINANCE_TOKENS = ("融资", "募资", "pre-a", "a轮", "b轮", "c轮", "天使轮", "种子轮", "投资")
    EARNINGS_TOKENS = ("财报", "业绩", "营收", "净利", "亏损", "毛利", "一季报", "半年报", "年报", "q1", "q2", "q3", "q4")
    POLICY_TOKENS = ("政策", "意见", "通知", "规划", "补贴", "新规", "办法", "指引")
    PRODUCT_TOKENS = ("发布", "推出", "首发", "问世", "新品", "新机", "方案", "平台")
    DATA_RECAP_TOKENS = ("统计数据表", "统计公报", "产量统计", "销量统计", "装机量", "出货量", "价格指数", "均价", "统计数据")
    EXECUTION_TOKENS = ("订单", "客户", "交付", "量产", "中标", "签约", "投产", "并网", "装机", "验收", "出货")
    COMPANY_ORG_RE = re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:科技|能源|汽车|股份|集团|电气|智能|机器人|半导体|电子|时代|精工|制造|材料|电池|重工|工业|光伏|储能|动力|机电|控股|装备|技术)"
    )
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    HTML_CHARSET_PATTERN = re.compile(br"charset\s*=\s*[\"']?([a-zA-Z0-9_\-]+)", re.I)
    MOJIBAKE_MARKERS = ("å", "æ", "ç", "é", "ï", "¤", "�")
    MOJIBAKE_CJK_MARKERS = ("鍥", "鏂", "鐢", "鑳", "缃", "鍙", "璁", "闂", "绔", "鍌", "姹", "鍑", "閿", "浠", "璧")
    LOW_SIGNAL_TOPIC_TEXT_RE = re.compile(
        r"^(?:"
        r"\u521a\u521a|"
        r"\u6700\u65b0(?:\u52a8\u6001)?|"
        r"\d+\s*(?:\u5206\u949f\u524d|\u5c0f\u65f6\u524d|\u5929\u524d)|"
        r"(?:20\d{2}[./-](?:0?[1-9]|1[0-2])[./-](?:0?[1-9]|[12]\d|3[01]))|"
        r"(?:(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01]))"
        r")(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$"
    )
    META_DESCRIPTION_PATTERNS = (
        re.compile(r'<meta[^>]+(?:name|property)=["\']description["\'][^>]+content=["\']([^"\']+)["\']', re.I),
        re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']description["\']', re.I),
        re.compile(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', re.I),
        re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', re.I),
    )
    ARTICLE_PARAGRAPH_PATTERN = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.topic_settings: TopicCollectionSettings = settings.topic_collection
        self._ssl_context = ssl._create_unverified_context()

    def collect_sync(self) -> DailyTopicsReport:
        return asyncio.run(self.collect())

    async def collect(self) -> DailyTopicsReport:
        collectors = self._build_collectors()
        results = await asyncio.gather(*(collector.collect() for collector in collectors))

        raw_items: list[TopicCandidate] = []
        source_stats: dict[str, dict[str, int | str]] = {}
        failed_sources: list[dict[str, str]] = []
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
            raw_items.extend(result.items)
            source_stats[result.source_name] = {
                "raw_count": len(result.items),
                "status": "ok" if not result.errors else "partial_failure",
            }
            for error in result.errors:
                failed_sources.append({"source": result.source_name, "error": error})

        scored = [self._enrich_topic(item) for item in raw_items]
        freshness_filtered = self._filter_by_freshness(scored, filtered_out)
        filtered = self._filter_to_focus_industries(freshness_filtered, filtered_out)
        filtered = [item for item in filtered if self._passes_topic_focus(item, filtered_out)]
        deduped = self._dedupe_topics(filtered, filtered_out)
        ranked = self._rank_topics(deduped)
        capped = self._apply_source_cap(ranked, filtered_out)[: self.topic_settings.max_export_topics]
        capped = self._supplement_topics_if_needed(capped, scored, filtered_out)
        capped = await self._hydrate_selected_topic_previews(capped)
        capped = self._rank_topics(capped)[: self.topic_settings.max_export_topics]

        for index, item in enumerate(capped, start=1):
            item.rank = index

        return DailyTopicsReport(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            lookback_days=self.topic_settings.lookback_days,
            max_topic_age_hours=self.topic_settings.max_topic_age_hours,
            focus_industries=self.topic_settings.focus_industries,
            total_raw_items=len(raw_items),
            total_selected_items=len(capped),
            topics=capped,
            source_stats=source_stats,
            failed_sources=failed_sources,
            filtered_out=filtered_out,
        )

    def _build_collectors(self) -> list["_BaseTopicCollector"]:
        collectors: list[_BaseTopicCollector] = []
        source_configs = self.topic_settings.source_configs
        for source_name in self.topic_settings.enabled_sources:
            source_config = dict(source_configs.get(source_name, {}))
            if not source_config:
                continue

            source_type = str(source_config.get("type", "html"))
            if source_type in {"rss", "google_rss"}:
                collectors.append(_RssTopicCollector(source_name, source_config, self, google_mode=source_type == "google_rss"))
            else:
                same_domain_only = bool(source_config.get("same_domain_only", source_name == "gg_robot"))
                if source_name == "zhidx":
                    collectors.append(_ZhidxTopicCollector(source_name, source_config, self))
                elif source_name == "ofweek":
                    collectors.append(_OfweekTopicCollector(source_name, source_config, self))
                else:
                    collectors.append(
                        _GenericHtmlTopicCollector(
                            source_name,
                            source_config,
                            self,
                            same_domain_only=same_domain_only,
                        )
                    )
        return collectors

    async def fetch_text(self, url: str, encoding: str | None = None) -> str:
        return await asyncio.to_thread(self._fetch_text_sync, url, encoding)

    def _fetch_text_sync(self, url: str, encoding: str | None = None) -> str:
        retry_delays = (0.0, 1.2, 3.0)
        last_error: Exception | None = None
        for attempt, delay in enumerate(retry_delays, start=1):
            if delay > 0:
                time.sleep(delay)
            request = Request(
                url,
                headers={
                    "User-Agent": self.USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            try:
                with urlopen(
                    request,
                    context=self._ssl_context,
                    timeout=self.topic_settings.request_timeout_seconds,
                ) as response:
                    raw = response.read()
                    charset = encoding or response.headers.get_content_charset()
                    return self._decode_html_payload(raw, charset)
            except HTTPError as exc:
                last_error = exc
                if exc.code in {400, 401, 403, 404, 410} or attempt == len(retry_delays):
                    raise
                self.logger.warning("Retrying topic fetch after HTTP %s from %s", exc.code, url)
            except Exception as exc:
                last_error = exc
                if attempt == len(retry_delays):
                    raise
                self.logger.warning("Retrying topic fetch after error from %s: %s", url, exc)

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to fetch topic source: {url}")

    def _decode_html_payload(self, raw: bytes, preferred_charset: str | None = None) -> str:
        candidates: list[str] = []
        meta_charset = self._extract_meta_charset(raw)
        for candidate in (preferred_charset, meta_charset, "utf-8", "utf-8-sig", "gb18030", "gbk", "big5"):
            normalized = str(candidate or "").strip().lower()
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        best_text = ""
        best_score = float("-inf")
        for candidate in candidates:
            try:
                decoded = raw.decode(candidate, "ignore")
            except LookupError:
                continue
            score = self._decoded_text_score(decoded)
            if score > best_score:
                best_score = score
                best_text = decoded
        return best_text or raw.decode("utf-8", "ignore")

    def _extract_meta_charset(self, raw: bytes) -> str:
        match = self.HTML_CHARSET_PATTERN.search(raw[:2048])
        if not match:
            return ""
        try:
            return match.group(1).decode("ascii", "ignore").strip()
        except Exception:
            return ""

    def _decoded_text_score(self, text: str) -> float:
        if not text:
            return float("-inf")
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        ascii_count = len(re.findall(r"[A-Za-z]", text))
        mojibake_count = sum(text.count(marker) for marker in self.MOJIBAKE_MARKERS)
        mojibake_cjk_count = sum(text.count(marker) for marker in self.MOJIBAKE_CJK_MARKERS)
        replacement_count = text.count("\ufffd")
        readable_hits = sum(text.count(keyword) for keyword in self._decoding_hint_keywords())
        return (
            cjk_count * 1.5
            + min(ascii_count, 300) * 0.05
            + readable_hits * 18
            - mojibake_count * 4
            - mojibake_cjk_count * 3
            - replacement_count * 6
        )

    def _decoding_hint_keywords(self) -> list[str]:
        keywords = {
            "储能",
            "新能源",
            "机器人",
            "行业",
            "产业",
            "项目",
            "公司",
            "技术",
            "市场",
            "电池",
            "光伏",
            "风电",
            "电力",
        }
        for items in self.topic_settings.keyword_map.values():
            keywords.update(keyword for keyword in items if keyword)
        return list(keywords)

    def _enrich_topic(self, topic: TopicCandidate) -> TopicCandidate:
        combined = f"{topic.title}\n{topic.snippet}\n{topic.summary}"
        matched_industries, matched_keywords = self._match_industries(combined)
        numbers = self._extract_numbers(combined)
        freshness_score = self._freshness_score(topic.published_at)
        topic.matched_industries = matched_industries
        topic.matched_keywords = matched_keywords
        topic.extracted_numbers = numbers
        topic.summary = self._summarize_topic(topic)
        topic.read_score = self._score_read(topic, freshness_score)
        topic.save_score = self._score_save(topic, freshness_score)
        topic.discussion_score = self._score_discussion(topic, freshness_score)
        topic.heat_score = self._score_heat(topic, freshness_score)
        topic.topic_type = self._classify_topic_type(topic)
        topic.topic_type_label = self.TOPIC_TYPE_LABELS.get(topic.topic_type, topic.topic_type or "行业事件")
        topic.evidence_strength = self._score_evidence_strength(topic, freshness_score)
        topic.quality_flags = self._topic_quality_flags(topic, freshness_score, topic.evidence_strength)
        topic.recommended_format, topic.recommended_format_label = self._recommended_format_for_topic(
            topic,
            topic.topic_type,
            topic.evidence_strength,
        )
        topic.quality_score = self._score_topic_quality(topic, freshness_score)
        base_total = round(
            topic.heat_score * 0.35
            + topic.read_score * 0.2
            + topic.save_score * 0.25
            + topic.discussion_score * 0.2
        )
        topic.total_score = round(base_total * 0.75 + topic.quality_score * 0.25)
        topic.is_high_read = topic.read_score >= 72
        topic.is_high_save = topic.save_score >= 72
        topic.is_high_discussion = topic.discussion_score >= 72
        return topic

    def _match_industries(self, text: str) -> tuple[list[str], list[str]]:
        lowered = text.lower()
        matched_industries: list[str] = []
        matched_keywords: list[str] = []
        for industry, keywords in self.topic_settings.keyword_map.items():
            current_keywords = [keyword for keyword in keywords if keyword.lower() in lowered]
            if current_keywords:
                matched_industries.append(industry)
                matched_keywords.extend(current_keywords)
        return matched_industries, self._unique(matched_keywords)

    def _extract_numbers(self, text: str) -> list[str]:
        return self._unique(self.NUMBER_PATTERN.findall(text))[:6]

    def _freshness_score(self, published_at: str) -> int:
        if not published_at:
            return 35
        published = self._parse_datetime(published_at)
        if published is None:
            return 35
        now = datetime.now(timezone.utc)
        delta = now - published.astimezone(timezone.utc)
        hours = max(delta.total_seconds() / 3600, 0)
        if hours <= 6:
            return 98
        if hours <= 12:
            return 95
        if hours <= 24:
            return 88
        if hours <= 48:
            return 70
        if hours <= 72:
            return 58
        return 42

    def _parse_datetime(self, value: str) -> datetime | None:
        value = value.strip()
        if not value:
            return None
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        now_local = datetime.now(local_tz)

        if value in {"刚刚", "最新", "最新动态"}:
            return now_local
        relative_match = re.search(r"(\d+)\s*(分钟|小时|天)前", value)
        if relative_match:
            amount = int(relative_match.group(1))
            unit = relative_match.group(2)
            delta = timedelta()
            if unit == "分钟":
                delta = timedelta(minutes=amount)
            elif unit == "小时":
                delta = timedelta(hours=amount)
            elif unit == "天":
                delta = timedelta(days=amount)
            return now_local - delta
        if value.startswith("今天"):
            return now_local
        if value.startswith("昨天"):
            return now_local - timedelta(days=1)
        if value.startswith("前天"):
            return now_local - timedelta(days=2)
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            pass

        date_only_patterns = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")
        datetime_patterns = ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y.%m.%d %H:%M:%S")

        for pattern in datetime_patterns:
            try:
                return datetime.strptime(value[:19], pattern).replace(tzinfo=local_tz)
            except ValueError:
                continue

        chinese_datetime_patterns = (
            "%Y年%m月%d日 %H:%M:%S",
            "%Y年%m月%d日 %H:%M",
            "%Y年%m月%d日",
        )
        for pattern in chinese_datetime_patterns:
            try:
                parsed = datetime.strptime(value, pattern)
                if pattern.endswith("日"):
                    parsed = parsed.replace(
                        hour=now_local.hour,
                        minute=now_local.minute,
                        second=0,
                        microsecond=0,
                    )
                return parsed.replace(tzinfo=local_tz)
            except ValueError:
                continue

        for pattern in date_only_patterns:
            try:
                parsed = datetime.strptime(value[:10], pattern)
                return parsed.replace(
                    hour=now_local.hour,
                    minute=now_local.minute,
                    second=0,
                    microsecond=0,
                    tzinfo=local_tz,
                )
            except ValueError:
                continue

        chinese_month_day_match = re.search(
            r"(?<!\d)(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
            value,
        )
        if chinese_month_day_match:
            month = int(chinese_month_day_match.group(1))
            day = int(chinese_month_day_match.group(2))
            now_local = datetime.now(local_tz)
            hour = int(chinese_month_day_match.group(3) or now_local.hour)
            minute = int(chinese_month_day_match.group(4) or 0)
            second = int(chinese_month_day_match.group(5) or 0)
            try:
                parsed = datetime(now_local.year, month, day, hour, minute, second, tzinfo=local_tz)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed > now_local + timedelta(days=1):
                    parsed = parsed.replace(year=parsed.year - 1)
                return parsed

        month_day_match = re.search(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})(?!\d)", value)
        if month_day_match:
            month, day = (int(part) for part in month_day_match.groups())
            now_local = datetime.now(local_tz)
            try:
                parsed = datetime(
                    now_local.year,
                    month,
                    day,
                    now_local.hour,
                    now_local.minute,
                    0,
                    tzinfo=local_tz,
                )
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed > now_local + timedelta(days=1):
                    parsed = parsed.replace(year=parsed.year - 1)
                return parsed

        match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                now_local = datetime.now(local_tz)
                return datetime(
                    year,
                    month,
                    day,
                    now_local.hour,
                    now_local.minute,
                    0,
                    tzinfo=local_tz,
                )
            except ValueError:
                return None
        return None

    def _filter_by_freshness(
        self,
        topics: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        age_limits = [
            value
            for value in (
                self.topic_settings.max_topic_age_hours,
                self.topic_settings.lookback_days * 24,
            )
            if value and value > 0
        ]
        max_age_hours = min(age_limits) if age_limits else 24
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        keep: list[TopicCandidate] = []

        for topic in topics:
            published = self._parse_datetime(topic.published_at)
            if published is None:
                if self.topic_settings.drop_topics_without_timestamp:
                    filtered_out["missing_timestamp"] += 1
                    continue
                keep.append(topic)
                continue

            published_utc = published.astimezone(timezone.utc)
            if published_utc < cutoff:
                filtered_out["stale"] += 1
                continue
            keep.append(topic)

        return keep

    def _filter_to_focus_industries(
        self,
        topics: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        matched_topics = [item for item in topics if item.matched_industries]
        filtered_out["industry_mismatch"] += max(len(topics) - len(matched_topics), 0)
        if not matched_topics:
            return []

        focus_set = set(self.topic_settings.focus_industries)
        if not focus_set:
            return matched_topics

        focused_topics = [
            item
            for item in matched_topics
            if any(industry in focus_set for industry in item.matched_industries)
        ]
        if focused_topics:
            filtered_out["industry_mismatch"] += max(len(matched_topics) - len(focused_topics), 0)
            return focused_topics

        self.logger.warning(
            "No topics matched configured focus industries %s; falling back to broader matched topics.",
            ", ".join(self.topic_settings.focus_industries),
        )
        return matched_topics

    def _score_heat(self, topic: TopicCandidate, freshness_score: int) -> int:
        score = 42 + len(topic.matched_industries) * 12 + len(topic.matched_keywords[:4]) * 4 + freshness_score // 5
        if topic.source == "google_news":
            score += 6
        if topic.source == "36kr":
            score += 5
        if topic.extracted_numbers:
            score += 7
        score += self._focus_bonus(topic)
        score -= self._macro_noise_penalty(topic)
        return min(98, score)

    def _score_read(self, topic: TopicCandidate, freshness_score: int) -> int:
        score = 45 + freshness_score // 4
        if 12 <= len(topic.title) <= 28:
            score += 10
        if any(token in topic.title for token in self.READ_TOKENS):
            score += 10
        if topic.matched_keywords:
            score += 8
        score += self._focus_bonus(topic)
        score -= self._macro_noise_penalty(topic)
        return min(96, score)

    def _score_save(self, topic: TopicCandidate, freshness_score: int) -> int:
        score = 38 + freshness_score // 6 + len(topic.extracted_numbers) * 8
        if len(topic.matched_keywords) >= 3:
            score += 12
        if any(token in f"{topic.title} {topic.summary}" for token in self.SAVE_TOKENS):
            score += 10
        score += self._focus_bonus(topic) // 2
        score -= self._macro_noise_penalty(topic) // 2
        return min(96, score)

    def _score_discussion(self, topic: TopicCandidate, freshness_score: int) -> int:
        text = f"{topic.title} {topic.summary}"
        score = 35 + freshness_score // 7
        if any(token in text for token in self.DISCUSSION_TOKENS):
            score += 16
        if len(topic.matched_industries) > 1:
            score += 10
        if "?" in topic.title or "？" in topic.title:
            score += 8
        score += self._focus_bonus(topic) // 2
        score -= self._macro_noise_penalty(topic)
        return min(96, score)

    def _classify_topic_type(self, topic: TopicCandidate) -> str:
        text = f"{topic.title} {topic.summary} {topic.snippet}".lower()
        if any(token.lower() in text for token in self.DATA_RECAP_TOKENS):
            return "data_recap"
        if any(token.lower() in text for token in self.POLICY_TOKENS):
            return "policy_market"
        if any(token.lower() in text for token in self.EARNINGS_TOKENS):
            return "earnings_signal"
        if any(token.lower() in text for token in self.FINANCE_TOKENS):
            return "financing_update"
        if any(token.lower() in text for token in self.PROJECT_TOKENS):
            return "project_progress"
        if any(token.lower() in text for token in self.PRODUCT_TOKENS):
            return "product_launch"
        return "industry_event"

    def _score_evidence_strength(self, topic: TopicCandidate, freshness_score: int) -> int:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        score = 22 + freshness_score // 8
        score += min(20, len(topic.extracted_numbers) * 6)
        score += min(14, len(topic.matched_keywords) * 3)
        if self._has_specific_subject(topic):
            score += 12
        if any(token in text for token in self.EXECUTION_TOKENS):
            score += 10
        if any(token in text for token in self.FINANCE_TOKENS):
            score += 6
        if any(token in text for token in self.EARNINGS_TOKENS):
            score += 8
        if topic.source in {"36kr", "zhidx", "ofweek"}:
            score += 4
        if self._is_title_only_topic(topic):
            score -= 12
        if self._is_old_statistics_topic(topic):
            score -= 18
        score -= self._macro_noise_penalty(topic) // 2
        return max(18, min(98, score))

    def _topic_quality_flags(
        self,
        topic: TopicCandidate,
        freshness_score: int,
        evidence_strength: int,
    ) -> list[str]:
        flags: list[str] = []
        if self._macro_noise_penalty(topic) >= 12:
            flags.append("macro_noise")
        if self._is_title_only_topic(topic):
            flags.append("title_only")
        if not self._has_specific_subject(topic):
            flags.append("weak_subject")
        if evidence_strength < 48:
            flags.append("weak_evidence")
        if freshness_score < 58:
            flags.append("low_freshness")
        if self._is_old_statistics_topic(topic):
            flags.append("old_statistics")
        if topic.topic_type == "data_recap" and len(topic.extracted_numbers) < 2:
            flags.append("weak_data_recap")
        return self._unique(flags)

    def _recommended_format_for_topic(
        self,
        topic: TopicCandidate,
        topic_type: str,
        evidence_strength: int,
    ) -> tuple[str, str]:
        flags = set(topic.quality_flags)
        text = f"{topic.title} {topic.summary} {topic.snippet}"

        if "title_only" in flags and topic_type in {"policy_market", "industry_event", "product_launch"}:
            return "quick_commentary", "快讯点评"
        if "title_only" in flags and topic_type == "project_progress" and not any(
            token in text for token in self.EXECUTION_TOKENS
        ):
            return "quick_commentary", "快讯点评"
        if topic_type == "data_recap":
            return "data_recap", "数据复盘"
        if topic_type == "policy_market":
            if evidence_strength >= 76 and "title_only" not in flags and self._has_specific_subject(topic):
                return "deep_analysis", "深度分析"
            return "quick_commentary", "快讯点评"
        if topic_type in {"project_progress", "earnings_signal"} and evidence_strength >= 58:
            return "project_breakdown", "项目拆解"
        if topic_type in {"financing_update", "policy_market", "product_launch"} and evidence_strength < 62:
            return "quick_commentary", "快讯点评"
        if evidence_strength >= 66:
            return "deep_analysis", "深度分析"
        return "quick_commentary", "快讯点评"

    def _score_topic_quality(self, topic: TopicCandidate, freshness_score: int) -> int:
        penalty = 0
        if "old_statistics" in topic.quality_flags:
            penalty += 18
        if "weak_evidence" in topic.quality_flags:
            penalty += 10
        if "weak_subject" in topic.quality_flags:
            penalty += 8
        if "title_only" in topic.quality_flags:
            penalty += 6
        if "macro_noise" in topic.quality_flags:
            penalty += 8
        if "weak_data_recap" in topic.quality_flags:
            penalty += 10

        score = round(
            topic.evidence_strength * 0.45
            + freshness_score * 0.15
            + topic.read_score * 0.15
            + topic.save_score * 0.15
            + topic.discussion_score * 0.10
        )
        return max(18, min(98, score - penalty))

    def _summarize_topic(self, topic: TopicCandidate) -> str:
        cleaned_snippet = self._clean_text(topic.snippet)
        if cleaned_snippet and not self._is_unusable_topic_snippet(cleaned_snippet):
            sentence = re.split(r"[。！？!?；;\n]", cleaned_snippet)[0].strip()
            if sentence and not self._is_unusable_topic_snippet(sentence):
                return sentence[:120]
        if topic.extracted_numbers and topic.matched_keywords:
            return (
                f"{topic.title}，当前可跟踪的信号包括"
                f"{'、'.join(topic.extracted_numbers[:2])}和{'、'.join(topic.matched_keywords[:2])}。"
            )
        if topic.matched_keywords:
            return f"{topic.title}，重点看{'、'.join(topic.matched_keywords[:2])}对应的项目落地和订单节奏。"
        if topic.extracted_numbers:
            return f"{topic.title}，关键数字包括{'、'.join(topic.extracted_numbers[:2])}。"
        return topic.title

    def _is_low_signal_topic_text(self, text: str) -> bool:
        cleaned = self._clean_text(text)
        if not cleaned:
            return True
        normalized = re.sub(r"\s+", "", cleaned)
        if self.LOW_SIGNAL_TOPIC_TEXT_RE.fullmatch(normalized):
            return True
        if len(normalized) <= 12 and re.fullmatch(r"[\d\-/:.年月日分小时前最新刚刚]+", normalized):
            return True
        return False

    def _looks_like_mojibake(self, text: str) -> bool:
        cleaned = str(text or "")
        if not cleaned:
            return False
        marker_count = sum(cleaned.count(marker) for marker in self.MOJIBAKE_CJK_MARKERS)
        if marker_count >= 6:
            return True
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
        readable_signal_count = sum(
            cleaned.count(token)
            for token in (
                "的",
                "了",
                "在",
                "和",
                "年",
                "月",
                "日",
                "项目",
                "公司",
                "市场",
                "储能",
                "新能源",
                "光伏",
                "风电",
                "电池",
                "并网",
                "订单",
            )
        )
        return cjk_count >= 10 and readable_signal_count == 0

    def _is_unusable_topic_snippet(self, text: str) -> bool:
        cleaned = self._clean_text(text)
        if not cleaned:
            return True
        if self._is_low_signal_topic_text(cleaned):
            return True
        if "<a " in cleaned.lower():
            return True
        lowered = cleaned.lower()
        if "copyright" in lowered or "all rights reserved" in lowered or "in-en.com" in lowered:
            return True
        if "»" in cleaned or "当前位置" in cleaned or cleaned.endswith("正文"):
            return True
        if self._looks_like_mojibake(cleaned):
            return True
        if len(cleaned) > 140 and len(re.findall(r"(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d+\s*(?:分钟前|小时前|天前))", cleaned)) >= 2:
            return True
        if len(cleaned) > 120 and cleaned.count(" ") >= 6:
            return True
        if len(cleaned) > 140 and sum(cleaned.count(token) for token in ("，", "、", " | ", " / ")) >= 6:
            return True
        return False

    def _dedupe_topics(self, topics: list[TopicCandidate], filtered_out: dict[str, int] | None = None) -> list[TopicCandidate]:
        result: list[TopicCandidate] = []
        seen_urls: set[str] = set()
        seen_titles: list[str] = []

        for item in sorted(topics, key=lambda current: current.total_score, reverse=True):
            normalized_url = self._normalize_url(item.url)
            normalized_title = self._normalize_title(item.title)
            if normalized_url and normalized_url in seen_urls:
                if filtered_out is not None:
                    filtered_out["duplicate"] += 1
                continue
            if any(self._title_similarity(normalized_title, existing) >= 0.9 for existing in seen_titles):
                if filtered_out is not None:
                    filtered_out["duplicate"] += 1
                continue

            if normalized_url:
                seen_urls.add(normalized_url)
            seen_titles.append(normalized_title)
            result.append(item)
        return result

    def _apply_source_cap(
        self,
        topics: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        cap = max(self.topic_settings.max_ranked_items_per_source, 1)
        per_source: dict[str, int] = {}
        result: list[TopicCandidate] = []

        for topic in topics:
            current = per_source.get(topic.source, 0)
            if current >= cap:
                filtered_out["source_overflow"] += 1
                continue
            per_source[topic.source] = current + 1
            result.append(topic)
        return result

    def _rank_topics(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        return sorted(
            topics,
            key=lambda item: (
                item.total_score,
                item.quality_score,
                item.evidence_strength,
                self._freshness_score(item.published_at),
                item.save_score,
                item.discussion_score,
                item.read_score,
            ),
            reverse=True,
        )

    def _supplement_topics_if_needed(
        self,
        selected: list[TopicCandidate],
        scored: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        target_count = min(self.topic_settings.max_export_topics, 6)
        if len(selected) >= target_count:
            return selected

        relaxed_candidates = self._build_relaxed_candidates(scored)
        if not relaxed_candidates:
            return selected

        existing_urls = {self._normalize_url(item.url) for item in selected if item.url}
        existing_titles = {self._normalize_title(item.title) for item in selected if item.title}
        supplemented = list(selected)

        for topic in relaxed_candidates:
            normalized_url = self._normalize_url(topic.url)
            normalized_title = self._normalize_title(topic.title)
            if normalized_url and normalized_url in existing_urls:
                continue
            if normalized_title and normalized_title in existing_titles:
                continue
            if any(self._title_similarity(normalized_title, existing) >= 0.9 for existing in existing_titles if existing):
                continue
            supplemented.append(topic)
            filtered_out["relaxed_added"] += 1
            if normalized_url:
                existing_urls.add(normalized_url)
            if normalized_title:
                existing_titles.add(normalized_title)
            if len(supplemented) >= target_count:
                break

        return supplemented[: self.topic_settings.max_export_topics]

    def _build_relaxed_candidates(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        focus_set = set(self.topic_settings.focus_industries)
        relaxed_cutoff = datetime.now(timezone.utc) - timedelta(hours=max(self.topic_settings.max_topic_age_hours, 72))
        pool: list[TopicCandidate] = []

        for topic in topics:
            if not topic.matched_industries:
                continue
            if focus_set and not any(industry in focus_set for industry in topic.matched_industries):
                continue
            if self._has_blocking_quality_issue(topic):
                continue
            title_hits = self._keyword_hits_in_title(topic)
            keyword_hits = len(topic.matched_keywords)
            if title_hits < 1 and keyword_hits < 2:
                continue
            published = self._parse_datetime(topic.published_at)
            if published is not None and published.astimezone(timezone.utc) < relaxed_cutoff:
                continue
            pool.append(topic)

        deduped = self._dedupe_topics(pool)
        return self._rank_topics(deduped)

    def _passes_topic_focus(
        self,
        topic: TopicCandidate,
        filtered_out: dict[str, int],
    ) -> bool:
        if self._has_blocking_quality_issue(topic):
            filtered_out["low_quality"] += 1
            return False
        title_hits = self._keyword_hits_in_title(topic)
        keyword_hits = len(topic.matched_keywords)
        macro_penalty = self._macro_noise_penalty(topic)
        if macro_penalty >= 18 and self._contains_macro_title(topic):
            filtered_out["weak_focus"] += 1
            return False
        if macro_penalty >= 12 and title_hits < 2:
            filtered_out["weak_focus"] += 1
            return False
        if title_hits >= 1:
            return True
        if keyword_hits >= 3 and macro_penalty == 0:
            return True
        filtered_out["weak_focus"] += 1
        return False

    def _focus_bonus(self, topic: TopicCandidate) -> int:
        title_hits = self._keyword_hits_in_title(topic)
        industry_bonus = self._focus_industry_bonus(topic)
        if title_hits >= 2:
            return 14 + industry_bonus
        if title_hits == 1:
            return 8 + industry_bonus
        if len(topic.matched_keywords) >= 3:
            return 4 + industry_bonus
        return industry_bonus

    def _focus_industry_bonus(self, topic: TopicCandidate) -> int:
        bonus = 0
        for index, industry in enumerate(self.topic_settings.focus_industries[:5]):
            if industry in topic.matched_industries:
                bonus += max(6 - index * 2, 1)
        return bonus

    def _keyword_hits_in_title(self, topic: TopicCandidate) -> int:
        title = topic.title.lower()
        hits = 0
        for keyword in topic.matched_keywords:
            token = keyword.lower().strip()
            if token and token in title:
                hits += 1
        return hits

    def _macro_noise_penalty(self, topic: TopicCandidate) -> int:
        text = f"{topic.title} {topic.summary} {topic.snippet}".lower()
        penalty = 0
        for token in self.GENERIC_MACRO_TOKENS:
            if token.lower() in text:
                penalty += 6
        if len(topic.matched_keywords) <= 1:
            penalty += 4
        return min(penalty, 24)

    def _contains_macro_title(self, topic: TopicCandidate) -> bool:
        title = topic.title.lower()
        return any(token.lower() in title for token in self.GENERIC_MACRO_TOKENS)

    def _has_blocking_quality_issue(self, topic: TopicCandidate) -> bool:
        flags = set(topic.quality_flags)
        if "old_statistics" in flags:
            return True
        if "title_only" in flags and topic.topic_type in {"policy_market", "industry_event"} and topic.evidence_strength < 78:
            return True
        if "title_only" in flags and not self._has_specific_subject(topic) and topic.quality_score < 72:
            return True
        if topic.topic_type == "data_recap" and topic.quality_score < 62:
            return True
        if topic.quality_score < 52:
            return True
        if "weak_evidence" in flags and "weak_subject" in flags:
            return True
        if "title_only" in flags and "weak_evidence" in flags:
            return True
        return False

    def _has_specific_subject(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary} {topic.snippet}"
        if self.COMPANY_ORG_RE.search(text):
            return True
        return any(token in text for token in (*self.PROJECT_TOKENS, *self.EARNINGS_TOKENS, *self.FINANCE_TOKENS))

    def _is_title_only_topic(self, topic: TopicCandidate) -> bool:
        snippet = self._clean_text(topic.snippet)
        return not snippet or len(snippet) < 18 or self._is_unusable_topic_snippet(snippet)

    def _is_old_statistics_topic(self, topic: TopicCandidate) -> bool:
        text = f"{topic.title} {topic.summary}"
        if not any(token in text for token in self.DATA_RECAP_TOKENS):
            return False
        year_matches = [int(item) for item in re.findall(r"(20\d{2})", text)]
        current_year = datetime.now().year
        if any(year <= current_year - 2 for year in year_matches):
            return True
        if "统计数据表" in text or "产量统计" in text:
            return True
        return False

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def _normalize_title(self, title: str) -> str:
        base = title.split(" - ")[0].strip()
        return self.TITLE_NORMALIZE_PATTERN.sub("", base.lower())

    def _title_similarity(self, first: str, second: str) -> float:
        if not first or not second:
            return 0.0
        if first == second:
            return 1.0
        min_length = min(len(first), len(second))
        common = sum(1 for left, right in zip(first, second) if left == right)
        return common / max(min_length, 1)

    def _clean_text(self, text: str) -> str:
        text = html.unescape(text or "")
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    async def _hydrate_selected_topic_previews(self, topics: list[TopicCandidate]) -> list[TopicCandidate]:
        if not topics:
            return topics

        tasks = [
            self._refresh_topic_preview(topic)
            for topic in topics
            if self._should_refresh_topic_preview(topic)
        ]
        if not tasks:
            return topics

        await asyncio.gather(*tasks)
        return topics

    def _should_refresh_topic_preview(self, topic: TopicCandidate) -> bool:
        if not topic.url or topic.source_type != "html":
            return False

        snippet = str(topic.snippet or "").strip()
        if not snippet:
            return True
        if self._is_unusable_topic_snippet(snippet):
            return True
        if topic.source in {"newenergy_in_en", "chuneng_in_en"} and len(snippet) > 100:
            return True
        return False

    async def _refresh_topic_preview(self, topic: TopicCandidate) -> None:
        try:
            html_text = await self.fetch_text(topic.url)
        except Exception as exc:
            self.logger.debug("Failed to refresh topic preview for %s: %s", topic.url, exc)
            return

        preview = self._extract_article_preview(html_text, topic.title)
        if not preview:
            return

        topic.snippet = preview[:260]
        self._enrich_topic(topic)

    def _extract_article_preview(self, html_text: str, title: str) -> str:
        for pattern in self.META_DESCRIPTION_PATTERNS:
            match = pattern.search(html_text)
            if not match:
                continue
            preview = self._normalize_article_preview(match.group(1), title)
            if preview:
                return preview

        for raw in self.ARTICLE_PARAGRAPH_PATTERN.findall(html_text):
            preview = self._normalize_article_preview(raw, title)
            if preview:
                return preview
        return ""

    def _normalize_article_preview(self, text: str, title: str) -> str:
        preview = self._clean_text(text)
        if not preview:
            return ""
        if title and preview.startswith(title):
            preview = preview[len(title) :].strip(" ：:-")
        preview = re.sub(
            r"^(?:刚刚|最新(?:动态)?|\d+\s*(?:分钟前|小时前|天前)|(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2})(?:\s+\d{1,2}:\d{2})?)[:：\- ]*",
            "",
            preview,
        ).strip()
        preview = preview.lstrip("，,；;| ")
        preview = re.sub(
            r"[，,。；;…\s]*(?:国际储能网|国际新能源网|新浪财经|车家号|新浪新闻_手机新浪网)\s*$",
            "",
            preview,
        ).strip()
        if len(preview) < 24 or len(preview) > 220:
            return ""
        if self._is_unusable_topic_snippet(preview):
            return ""
        return preview

    def _unique(self, items: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            value = item.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result


class _BaseTopicCollector:
    def __init__(
        self,
        source_name: str,
        source_config: dict[str, object],
        service: TopicCollectionService,
    ) -> None:
        self.source_name = source_name
        self.source_config = source_config
        self.service = service

    async def collect(self) -> TopicSourceResult:
        raise NotImplementedError


class _RssTopicCollector(_BaseTopicCollector):
    def __init__(
        self,
        source_name: str,
        source_config: dict[str, object],
        service: TopicCollectionService,
        google_mode: bool,
    ) -> None:
        super().__init__(source_name, source_config, service)
        self.google_mode = google_mode

    async def collect(self) -> TopicSourceResult:
        items: list[TopicCandidate] = []
        errors: list[str] = []
        urls = [str(item) for item in self.source_config.get("urls", [])]
        tasks = [self.service.fetch_text(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for url, payload in zip(urls, results):
            if isinstance(payload, Exception):
                errors.append(f"{url}: {payload}")
                continue
            try:
                items.extend(self._parse_rss(payload))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{url}: {exc}")

        return TopicSourceResult(
            source_name=self.source_name,
            items=items[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_rss(self, xml_text: str) -> list[TopicCandidate]:
        root = ElementTree.fromstring(xml_text)
        candidates: list[TopicCandidate] = []
        for item in root.findall(".//item"):
            title = self.service._clean_text(item.findtext("title", default=""))
            link = self.service._clean_text(item.findtext("link", default=""))
            description = item.findtext("description", default="")
            pub_date = self.service._clean_text(item.findtext("pubDate", default=""))

            if self.google_mode and " - " in title:
                title = title.rsplit(" - ", 1)[0].strip()

            snippet = self.service._clean_text(description)
            if not title or not link:
                continue
            candidates.append(
                TopicCandidate(
                    title=title,
                    url=link,
                    source=self.source_name,
                    source_type="rss",
                    published_at=pub_date,
                    snippet=snippet[:260],
                )
            )
        return candidates


class _ZhidxTopicCollector(_BaseTopicCollector):
    ARTICLE_PATTERN = re.compile(
        r'<a[^>]+href="(https://www\.zhidx\.com/p/\d+\.html)"[^>]+title="([^"]+)"[^>]*>(.*?)</a>',
        re.S,
    )
    DESC_PATTERN = re.compile(r'<div class="(?:info-left-desc|item-text|article-desc)">(.+?)</div>', re.S)
    DATE_PATTERN = re.compile(r'(\d{2}-\d{2}|\d{4}-\d{2}-\d{2})')

    async def collect(self) -> TopicSourceResult:
        items: list[TopicCandidate] = []
        errors: list[str] = []
        urls = [str(item) for item in self.source_config.get("urls", [])]
        results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

        for url, payload in zip(urls, results):
            if isinstance(payload, Exception):
                errors.append(f"{url}: {payload}")
                continue
            try:
                items.extend(self._parse_page(url, payload))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{url}: {exc}")

        return TopicSourceResult(
            source_name=self.source_name,
            items=items[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_page(self, page_url: str, html_text: str) -> list[TopicCandidate]:
        candidates: list[TopicCandidate] = []
        for match in self.ARTICLE_PATTERN.finditer(html_text):
            url = match.group(1)
            title = self.service._clean_text(match.group(2))
            if not title or "#comments" in url:
                continue

            window = html_text[match.end() : match.end() + 600]
            desc_match = self.DESC_PATTERN.search(window)
            date_match = self.DATE_PATTERN.search(window)
            snippet = self.service._clean_text(desc_match.group(1)) if desc_match else ""
            published_at = date_match.group(1) if date_match else ""

            candidates.append(
                TopicCandidate(
                    title=title,
                    url=urljoin(page_url, url),
                    source=self.source_name,
                    source_type="html",
                    published_at=published_at,
                    snippet=snippet[:260],
                )
            )
        return candidates


class _OfweekTopicCollector(_BaseTopicCollector):
    LINK_PATTERN = re.compile(r'href="([^"]+ART[^"]+\.html)"[^>]*>(.*?)</a>', re.S)
    PARAGRAPH_PATTERN = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
    DATE_PATTERN = re.compile(r"(20\d{2}-\d{2}-\d{2}|\d{2}-\d{2})")

    async def collect(self) -> TopicSourceResult:
        items: list[TopicCandidate] = []
        errors: list[str] = []
        urls = [str(item) for item in self.source_config.get("urls", [])]
        results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

        for url, payload in zip(urls, results):
            if isinstance(payload, Exception):
                errors.append(f"{url}: {payload}")
                continue
            try:
                items.extend(self._parse_page(url, payload))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{url}: {exc}")

        return TopicSourceResult(
            source_name=self.source_name,
            items=items[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_page(self, page_url: str, html_text: str) -> list[TopicCandidate]:
        candidates: list[TopicCandidate] = []
        seen_titles: set[str] = set()
        for match in self.LINK_PATTERN.finditer(html_text):
            url = self._absolutize(match.group(1))
            title = self.service._clean_text(match.group(2))
            if not title or len(title) < 4:
                continue
            if title in seen_titles:
                continue
            seen_titles.add(title)

            window = html_text[match.end() : match.end() + 700]
            paragraphs = [self.service._clean_text(item) for item in self.PARAGRAPH_PATTERN.findall(window)]
            snippet = next((item for item in paragraphs if item and item != title), "")
            published_match = self.DATE_PATTERN.search(window)
            published_at = published_match.group(1) if published_match else ""
            candidates.append(
                TopicCandidate(
                    title=title,
                    url=urljoin(page_url, url),
                    source=self.source_name,
                    source_type="html",
                    published_at=published_at,
                    snippet=snippet[:260],
                )
            )
        return candidates

    def _absolutize(self, url: str) -> str:
        return f"https:{url}" if url.startswith("//") else url


class _GenericHtmlTopicCollector(_BaseTopicCollector):
    LINK_PATTERN = re.compile(r'<a[^>]+href="([^"]+)"[^>]*(?:title="([^"]*)")?[^>]*>(.*?)</a>', re.S)
    RELATIVE_DATE_PATTERN = re.compile(r"(刚刚|最新|最新动态|\d+\s*(?:分钟|小时|天)前)")
    FULL_DATE_PATTERN = re.compile(
        r"((?:20\d{2}[./-](?:0?[1-9]|1[0-2])[./-](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
        r"|(?:20\d{2}年(?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?))"
    )
    CHINESE_MONTH_DAY_PATTERN = re.compile(
        r"((?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日(?:\s+\d{1,2}:\d{2})?)"
    )
    SHORT_DATE_PATTERN = re.compile(
        r"((?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])(?:\s+\d{1,2}:\d{2})?)"
    )
    BLOCK_PATTERN = re.compile(r"<(?:p|span|div|li)[^>]*>(.*?)</(?:p|span|div|li)>", re.S)
    TITLE_EXCLUSIONS = ("首页", "更多", "登录", "注册", "专题", "展会", "会议", "报告", "视频", "直播", "下载", "门户网站")

    def __init__(
        self,
        source_name: str,
        source_config: dict[str, object],
        service: TopicCollectionService,
        same_domain_only: bool,
    ) -> None:
        super().__init__(source_name, source_config, service)
        self.same_domain_only = same_domain_only

    async def collect(self) -> TopicSourceResult:
        items: list[TopicCandidate] = []
        errors: list[str] = []
        urls = [str(item) for item in self.source_config.get("urls", [])]
        results = await asyncio.gather(*(self.service.fetch_text(url) for url in urls), return_exceptions=True)

        for url, payload in zip(urls, results):
            if isinstance(payload, Exception):
                errors.append(f"{url}: {payload}")
                continue
            try:
                items.extend(self._parse_page(url, payload))
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{url}: {exc}")

        return TopicSourceResult(
            source_name=self.source_name,
            items=self._rank_items(items)[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_page(self, page_url: str, html_text: str) -> list[TopicCandidate]:
        base_domain = urlparse(page_url).netloc
        candidates: list[TopicCandidate] = []
        seen_titles: set[str] = set()
        for match in self.LINK_PATTERN.finditer(html_text):
            href, title_attr, inner_html = match.groups()
            url = self._absolutize(page_url, href)
            if not url.startswith("http"):
                continue
            if self.same_domain_only and urlparse(url).netloc != base_domain:
                continue

            title = self.service._clean_text(title_attr or inner_html)
            if len(title) < 8 or len(title) > 40 or "javascript:" in href.lower():
                continue
            if not re.search(r"[\u4e00-\u9fff]", title):
                continue
            if title in seen_titles:
                continue
            if any(token in title for token in self.TITLE_EXCLUSIONS):
                continue

            before_window = html_text[max(0, match.start() - 260) : match.start()]
            after_window = html_text[match.end() : match.end() + 1400]
            context = f"{before_window} {after_window}"
            published_at = self._extract_published_at(context)
            snippet = self._extract_snippet(after_window, context, title)
            seen_titles.add(title)

            candidates.append(
                TopicCandidate(
                    title=title,
                    url=url,
                    source=self.source_name,
                    source_type="html",
                    published_at=published_at,
                    snippet=snippet[:260],
                )
            )
        return candidates

    def _absolutize(self, page_url: str, href: str) -> str:
        if href.startswith("//"):
            return f"https:{href}"
        return urljoin(page_url, href)

    def _extract_snippet(self, html_window: str, context_window: str, title: str) -> str:
        fragments: list[str] = []
        seen: set[str] = set()

        for window in (html_window, context_window):
            for raw in self.BLOCK_PATTERN.findall(window):
                text = self.service._clean_text(raw.split("<a", 1)[0])
                if not text or text == title:
                    continue
                if len(text) < 12 or len(text) > 220:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", text):
                    continue
                text = self._normalize_snippet(text, title)
                if not text or text in seen:
                    continue
                seen.add(text)
                fragments.append(text)

        if fragments:
            return max(fragments, key=lambda item: self._score_snippet(item, title))

        plain_text = self.service._clean_text(re.sub(r"<[^>]+>", " ", html_window))
        plain_text = self._normalize_snippet(plain_text, title)
        return plain_text[:180]

    def _extract_published_at(self, context: str) -> str:
        for pattern in (
            self.RELATIVE_DATE_PATTERN,
            self.FULL_DATE_PATTERN,
            self.CHINESE_MONTH_DAY_PATTERN,
            self.SHORT_DATE_PATTERN,
        ):
            match = pattern.search(context)
            if match:
                return match.group(1)
        return ""

    def _rank_items(self, items: list[TopicCandidate]) -> list[TopicCandidate]:
        return sorted(items, key=self._score_item, reverse=True)

    def _score_item(self, item: TopicCandidate) -> tuple[int, int, int]:
        score = 0
        combined = f"{item.title} {item.snippet}"
        if item.published_at:
            score += 8
        if item.snippet and len(item.snippet) >= 20:
            score += 6
        if item.snippet and not self.service._is_low_signal_topic_text(item.snippet):
            score += 8
        if re.search(r"\d", combined):
            score += 3
        if any(token in combined for token in ("储能", "新能源", "电池", "项目", "并网", "中标", "订单", "装机", "电站")):
            score += 8
        if any(token in item.title for token in self.TITLE_EXCLUSIONS):
            score -= 12
        if len(item.title) < 10:
            score -= 5
        return score, len(item.snippet), len(item.title)

    def _score_snippet(self, text: str, title: str) -> int:
        if not text or self.service._is_low_signal_topic_text(text):
            return -100
        score = min(len(text), 120)
        if re.search(r"\d", text):
            score += 12
        if any(token in text for token in ("项目", "订单", "交付", "并网", "装机", "电站", "招标", "中标", "投产", "签约")):
            score += 14
        if any(token in text for token in ("储能", "电池", "光伏", "风电", "逆变器", "抽水蓄能", "超级电容", "压缩空气储能")):
            score += 16
        if "：" in text or "，" in text:
            score += 6
        if text.startswith(title):
            score -= 8
        return score

    def _normalize_snippet(self, text: str, title: str = "") -> str:
        snippet = str(text or "").strip()
        if not snippet:
            return ""
        snippet = re.sub(r"^(最新动态|更多)\s*", "", snippet).strip()
        snippet = snippet.split("了解更多", 1)[0].strip()
        snippet = re.sub(
            r"^(?:刚刚|最新(?:动态)?|\d+\s*(?:分钟前|小时前|天前)|(?:20\d{2}[./-]\d{1,2}[./-]\d{1,2})(?:\s+\d{1,2}:\d{2})?)[:：\- ]*",
            "",
            snippet,
        ).strip()
        if title and snippet.startswith(title):
            snippet = snippet[len(title) :].strip(" ：:-")
        if len(re.findall(r"\d+\s*(?:分钟前|小时前|天前)", snippet)) >= 2:
            return ""
        if self.service._is_low_signal_topic_text(snippet):
            return ""
        return re.sub(r"\s+", " ", snippet).strip()
