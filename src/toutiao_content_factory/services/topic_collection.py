from __future__ import annotations

import asyncio
import html
import logging
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse
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
    NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?(?:%|倍|亿元|万台|万吨|周|个月|季度|年)")
    TITLE_NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    DISCUSSION_TOKENS = ("融资", "订单", "量产", "发布", "争议", "突破", "价格战", "出海", "裁员", "收购")
    SAVE_TOKENS = ("一图看懂", "产业链", "数据", "测算", "对比", "拆解", "复盘", "指标", "路线图")
    READ_TOKENS = ("为什么", "背后", "首次", "最新", "量产", "融资", "爆发", "拐点", "机会")
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

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
            "duplicate": 0,
            "source_overflow": 0,
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
        filtered = [item for item in freshness_filtered if item.matched_industries]
        filtered_out["industry_mismatch"] += max(len(freshness_filtered) - len(filtered), 0)
        deduped = self._dedupe_topics(filtered, filtered_out)
        ranked = self._rank_topics(deduped)
        capped = self._apply_source_cap(ranked, filtered_out)[: self.topic_settings.max_export_topics]

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
                if source_name == "zhidx":
                    collectors.append(_ZhidxTopicCollector(source_name, source_config, self))
                elif source_name == "ofweek":
                    collectors.append(_OfweekTopicCollector(source_name, source_config, self))
                elif source_name == "gg_robot":
                    collectors.append(_GenericHtmlTopicCollector(source_name, source_config, self, same_domain_only=True))
                else:
                    collectors.append(_GenericHtmlTopicCollector(source_name, source_config, self, same_domain_only=False))
        return collectors

    async def fetch_text(self, url: str, encoding: str | None = None) -> str:
        return await asyncio.to_thread(self._fetch_text_sync, url, encoding)

    def _fetch_text_sync(self, url: str, encoding: str | None = None) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
            },
        )
        with urlopen(
            request,
            context=self._ssl_context,
            timeout=self.topic_settings.request_timeout_seconds,
        ) as response:
            raw = response.read()
            charset = encoding or response.headers.get_content_charset()
            if charset:
                try:
                    return raw.decode(charset, "ignore")
                except LookupError:
                    pass
            for candidate in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
                try:
                    return raw.decode(candidate)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "ignore")

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
        topic.total_score = round(
            topic.heat_score * 0.35
            + topic.read_score * 0.2
            + topic.save_score * 0.25
            + topic.discussion_score * 0.2
        )
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
            return 50
        published = self._parse_datetime(published_at)
        if published is None:
            return 50
        now = datetime.now(timezone.utc)
        delta = now - published.astimezone(timezone.utc)
        days = max(delta.total_seconds() / 86400, 0)
        if days <= 1:
            return 95
        if days <= 3:
            return 82
        if days <= 7:
            return 70
        return 55

    def _parse_datetime(self, value: str) -> datetime | None:
        value = value.strip()
        if not value:
            return None
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            pass

        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(value[:19], pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def _filter_by_freshness(
        self,
        topics: list[TopicCandidate],
        filtered_out: dict[str, int],
    ) -> list[TopicCandidate]:
        max_age_hours = max(self.topic_settings.max_topic_age_hours, self.topic_settings.lookback_days * 24)
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

    def _score_heat(self, topic: TopicCandidate, freshness_score: int) -> int:
        score = 42 + len(topic.matched_industries) * 12 + len(topic.matched_keywords[:4]) * 4 + freshness_score // 5
        if topic.source == "google_news":
            score += 6
        if topic.source == "36kr":
            score += 5
        if topic.extracted_numbers:
            score += 7
        return min(98, score)

    def _score_read(self, topic: TopicCandidate, freshness_score: int) -> int:
        score = 45 + freshness_score // 4
        if 12 <= len(topic.title) <= 28:
            score += 10
        if any(token in topic.title for token in self.READ_TOKENS):
            score += 10
        if topic.matched_keywords:
            score += 8
        return min(96, score)

    def _score_save(self, topic: TopicCandidate, freshness_score: int) -> int:
        score = 38 + freshness_score // 6 + len(topic.extracted_numbers) * 8
        if len(topic.matched_keywords) >= 3:
            score += 12
        if any(token in f"{topic.title} {topic.summary}" for token in self.SAVE_TOKENS):
            score += 10
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
        return min(96, score)

    def _summarize_topic(self, topic: TopicCandidate) -> str:
        cleaned_snippet = self._clean_text(topic.snippet)
        if cleaned_snippet:
            sentence = re.split(r"[。！？\n]", cleaned_snippet)[0].strip()
            if sentence:
                return sentence[:120]
        if topic.extracted_numbers:
            return f"{topic.title}，相关关键信号包括：{'、'.join(topic.extracted_numbers[:3])}。"
        return topic.title

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
            key=lambda item: (item.total_score, item.save_score, item.discussion_score, item.read_score),
            reverse=True,
        )

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
            items=items[: self.service.topic_settings.max_items_per_source],
            errors=errors,
        )

    def _parse_page(self, page_url: str, html_text: str) -> list[TopicCandidate]:
        base_domain = urlparse(page_url).netloc
        candidates: list[TopicCandidate] = []
        for href, title_attr, inner_html in self.LINK_PATTERN.findall(html_text):
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

            candidates.append(
                TopicCandidate(
                    title=title,
                    url=url,
                    source=self.source_name,
                    source_type="html",
                )
            )
        return candidates

    def _absolutize(self, page_url: str, href: str) -> str:
        if href.startswith("//"):
            return f"https:{href}"
        return urljoin(page_url, href)
