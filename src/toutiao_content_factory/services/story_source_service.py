"""Original-report candidates and expendable retrieval hints, never approval."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import tempfile
import os
import time
from urllib.parse import urljoin, urlsplit, parse_qs

from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.material_search_service import fetch_text_smart
from toutiao_content_factory.services.reader_value_service import story_reader_fit
from toutiao_content_factory.services.topic_selection_evidence import selection_evidence

FEEDS = ("https://www.hncourt.gov.cn/", "https://hqqfy.hncourt.gov.cn/public/index.php?LocationID=0601000000")
SEED_REPORTS = (
    ("【以案释法】婚介相亲需警惕！订婚对象失联，钱就打水漂？", "https://hqqfy.hncourt.gov.cn/public/detail.php?id=1374"),
    ("许昌某华物业有限公司与赵某蕾物业服务合同纠纷案", "https://www.hncourt.gov.cn/public/detail.php?id=188830"),
    ("某友物业公司与王某轩物业服务合同纠纷案", "https://www.hncourt.gov.cn/public/detail.php?id=188834"),
    ("冯某与天某物业管理公司物业服务合同纠纷案", "https://www.hncourt.gov.cn/public/detail.php?id=188829"),
    ("刘某丽与某弘物业公司物业服务合同纠纷案", "https://www.hncourt.gov.cn/public/detail.php?id=188832"),
)
TTL = 6 * 3600


def _article_url(value, *, host=None):
    try:
        parsed = urlsplit(str(value))
        domain = parsed.hostname or ""
        valid_host = domain == "www.hncourt.gov.cn" or domain.endswith(".hncourt.gov.cn")
        if (parsed.scheme != "https" or not valid_host or (host and domain != host)
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path != "/public/detail.php" or parsed.fragment):
            return ""
        query = parse_qs(parsed.query)
        if set(query) != {"id"} or len(query["id"]) != 1 or not re.fullmatch(r"[1-9]\d{0,9}", query["id"][0]):
            return ""
        return f"https://{domain}/public/detail.php?id={query['id'][0]}"
    except (ValueError, TypeError):
        return ""


def _topic(title, url):
    return TopicCandidate(title=title, summary=title, url=url, source="法院公开案例", source_type="court_case",
                          topic_type="evergreen", matched_industries=["social_story"], rank=10000)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.current, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current, self.text = dict(attrs), []

    def handle_data(self, data):
        if self.current is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.rows.append((self.current.get("href", ""), self.current.get("title") or "".join(self.text)))
            self.current = None


def parse_source_links(page_html, page_url):
    host = urlsplit(page_url).hostname or ""
    if not (host == "www.hncourt.gov.cn" or host.endswith(".hncourt.gov.cn")):
        return []
    parser = _Links()
    parser.feed(page_html)
    result, seen = [], set()
    for href, title in parser.rows:
        url = _article_url(urljoin(page_url, href), host=host)
        title = re.sub(r"\s+", " ", title).strip()
        if not url or url in seen or not 6 <= len(title) <= 120 or ".." in title or "…" in title:
            continue
        item = _topic(title, url)
        if story_reader_fit(item)["eligible"]:
            seen.add(url)
            result.append(item)
    return result


class StorySourceService:
    def __init__(self, output_dir, logger, *, live_feeds=True):
        self.path = Path(output_dir) / "growth_ops/source_material/story_supply_hints.json"
        self.logger, self.live_feeds = logger, live_feeds

    def _load(self):
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if state.get("schema") == 1 and isinstance(state.get("failures"), dict):
                return state
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return {"schema": 1, "catalogue_at": 0, "catalogue": [], "failures": {}}

    def _save(self, state):
        name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as handle:
                name = handle.name
                json.dump(state, handle, ensure_ascii=False, allow_nan=False)
            os.replace(name, self.path)
        except (OSError, ValueError):
            self.logger.warning("Story retrieval hints could not be saved; normal evidence checks remain active.")
        finally:
            if name:
                Path(name).unlink(missing_ok=True)

    def candidates(self, *, deadline):
        state, now = self._load(), time.time()
        stamp = state.get("catalogue_at", 0)
        fresh = type(stamp) in (int, float) and math.isfinite(stamp) and 0 <= now - stamp < TTL
        rows = state.get("catalogue", []) if fresh else []
        if not isinstance(rows, list):
            rows = []
        if not fresh and self.live_feeds:
            collected = []
            for feed in FEEDS:
                if time.monotonic() >= deadline:
                    break
                try:
                    page, final, _ = fetch_text_smart(feed, timeout=8, deadline=min(deadline, time.monotonic() + 8))
                    if urlsplit(final).hostname == urlsplit(feed).hostname:
                        collected.extend(parse_source_links(page, feed))
                except Exception as exc:
                    self.logger.info("Court-source listing unavailable: %s", type(exc).__name__)
            rows = [{"title": item.title, "url": item.url} for item in collected[:24]]
            state.update(catalogue_at=now, catalogue=rows)
            self._save(state)
        result, seen = [], set()
        pairs = [(row.get("title", ""), row.get("url", "")) for row in rows if isinstance(row, dict)]
        for title, raw_url in [*pairs, *SEED_REPORTS]:
            url = _article_url(raw_url)
            if not url or url in seen or not isinstance(title, str) or not 6 <= len(title) <= 120:
                continue
            item = _topic(title, url)
            if story_reader_fit(item)["eligible"]:
                seen.add(url)
                result.append(item)
        return result

    @staticmethod
    def _key(topic):
        identity = _article_url(topic.url) or f"{topic.title}|{topic.url}"
        if supplied := str(topic.snippet or "").strip():
            identity += "\n" + supplied
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def can_retry(self, topic):
        if selection_evidence(topic)["priority"] == 0:
            return True
        until = self._load()["failures"].get(self._key(topic), 0)
        return not (type(until) in (int, float) and math.isfinite(until) and until > time.time())

    def record_result(self, topic, passed):
        state, now = self._load(), time.time()
        state["failures"] = {key: value for key, value in state["failures"].items()
                             if type(value) in (int, float) and math.isfinite(value) and value > now}
        if passed:
            state["failures"].pop(self._key(topic), None)
        else:
            state["failures"][self._key(topic)] = now + TTL
        self._save(state)
