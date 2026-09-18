"""社交媒体互动代理 — 自动执行点赞、评论、关注等互动操作"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.multi_platform_publisher import PlatformPublisher
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class EngagementStrategy:
    """互动策略配置"""

    target_keywords: list[str] = field(default_factory=lambda: ["AI", "机器人", "新能源"])
    target_accounts: list[str] = field(default_factory=list)
    max_daily_likes: int = 50
    max_daily_comments: int = 20
    comment_style: str = "professional"
    engagement_hours: list[int] = field(default_factory=lambda: list(range(8, 23)))


@dataclass
class EngagementRecord:
    """单次互动记录"""

    platform: str
    action: str  # like, favorite, follow, comment, reply
    target_id: str
    target_title: str
    content: str  # comment/reply content
    timestamp: datetime
    success: bool
    failure_reason: str = ""  # page_open_error / button_missing / invalid_url / exception / api_error


# ---------------------------------------------------------------------------
# DeepSeek API 辅助（仅使用 urllib，无额外依赖）
# ---------------------------------------------------------------------------

_base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/")
# ⚠️ 2026-09-18 修正：这里原来在 base 不以 /v1 结尾时**硬拼** "/v1/chat/completions"。
# 两个 provider 的规范并不一致：
#   - DeepSeek:  base=https://api.deepseek.com        → 需要 /v1/chat/completions
#   - 智谱 GLM:  base=https://open.bigmodel.cn/api/paas/v4 → 直接 /chat/completions
# 于是智谱配置下拼出了 /api/paas/v4/v1/chat/completions，云端日志里
# 满屏「DeepSeek API 调用失败: HTTP Error 404」，互动评论全线失败还白烧运行时间。
# 仓库其余部分（title_agent / 本文件实测探针）用的都是 `${base}/chat/completions`，
# 所以以它为主选，仅在 404 时回落到 /v1 变体，两种 provider 都能工作。
_DEEPSEEK_MODEL = os.getenv("OPENAI_MODEL", "deepseek-v4-flash")


def _candidate_endpoints(base_url: str | None = None) -> list[str]:
    """按「主选 → 回落」顺序给出候补端点，主选与仓库其余部分一致。

    `base_url` 显式传入时以它为准（便于测试，不受模块导入时的环境变量影响）。
    """
    base = (_base if base_url is None else base_url.rstrip("/"))
    primary = base + "/chat/completions"
    if base.endswith("/v1"):
        # base 已经带 /v1（DeepSeek 写法），只有主选一种。
        return [primary]
    return [primary, base + "/v1/chat/completions"]


_DEEPSEEK_BASE_URL = _candidate_endpoints()[0]  # 保留旧名，供外部与测试引用


def _call_deepseek(api_key: str, system_prompt: str, user_prompt: str, max_tokens: int = 200) -> str:
    """调用 DeepSeek Chat API，返回助手回复文本。

    限流韧性（2026-08-27）：上游 429/5xx 时按 5s×attempt 退避重试最多 3 次，
    与 OpenAIContentService 策略一致，避免限流时互动评论全部生成失败。
    """
    payload = json.dumps({
        "model": _DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }).encode("utf-8")

    last_exc: Exception | None = None
    endpoints = _candidate_endpoints()
    for endpoint_index, endpoint in enumerate(endpoints):
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 404 and endpoint_index + 1 < len(endpoints):
                    # 端点写法不匹配（provider 规范差异），直接换下一个候补，不必重试。
                    break
                retryable = exc.code in (429, 500, 502, 503, 504)
                if not retryable or attempt >= 3:
                    raise RuntimeError(f"DeepSeek API 调用失败: {exc}") from exc
            except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
                last_exc = exc
                retryable = True
                if attempt >= 3:
                    raise RuntimeError(f"DeepSeek API 调用失败: {exc}") from exc
            time.sleep(min(5.0 * attempt, 30.0))
    raise RuntimeError(f"DeepSeek API 调用失败: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# SQLite 持久化
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS engagement_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    target_id   TEXT    NOT NULL,
    target_title TEXT   NOT NULL DEFAULT '',
    content     TEXT    NOT NULL DEFAULT '',
    timestamp   TEXT    NOT NULL,
    success     INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT NOT NULL DEFAULT ''
);
"""


def _init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# EngagementAgent
# ---------------------------------------------------------------------------

class EngagementAgent:
    """社交媒体自动互动代理"""

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._strategy = EngagementStrategy()
        self._db_path = self.settings.cache_dir / "engagement.db"
        self._db: sqlite3.Connection | None = None
        self._deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")

    # ---- 属性 ----

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = _init_db(self._db_path)
        return self._db

    # ---- 公开方法 ----

    def run_daily_engagement(
        self,
        platform: str,
        account_id: str,
    ) -> list[EngagementRecord]:
        """执行每日互动流程，返回互动记录列表。

        1. 检查当前小时是否在 engagement_hours 内
        2. 从配置加载策略
        3. 使用关键词扫描目标内容
        4. 执行点赞（不超过 max_daily_likes）
        5. 生成并发布 AI 评论（不超过 max_daily_comments）
        6. 返回互动记录
        """
        now = datetime.now()

        # 1. 检查互动时间窗口
        if now.hour not in self._strategy.engagement_hours:
            self.logger.info("当前时间 %02d:00 不在互动时间窗口内，跳过", now.hour)
            return []

        # 2. 加载策略
        self._load_strategy()

        records: list[EngagementRecord] = []

        # 3. 扫描目标内容
        target_items = self._scan_target_content(platform, account_id)
        if not target_items:
            self.logger.info("未扫描到目标内容，跳过互动")
            return []

        # 4. 在单次浏览器会话中批量执行点赞和评论
        consecutive_failures = 0
        max_consecutive_failures = 3  # 连续失败3次则停止

        def _run():
            nonlocal consecutive_failures
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = self._launch_browser(pw, platform, account_id)
                if browser is None:
                    self.logger.warning("浏览器启动失败，跳过互动")
                    return [], [], 0, 0

                # 4a. 执行点赞
                like_count_today = self._count_today_actions("like")
                remaining_likes = max(0, self._strategy.max_daily_likes - like_count_today)
                items_to_like = target_items[:remaining_likes]
                # 连续"致命"失败（页面打不开/异常）达到阈值才停止；
                # 找不到点赞按钮/URL无效属于单篇跳过，不拖慢整轮（2026-08-27）。
                fatal_like_failures = 0
                max_fatal_like_failures = 3

                for item in items_to_like:
                    record = self._perform_like_in_session(browser, platform, item)
                    records.append(record)
                    if not record.success:
                        if record.failure_reason in ("page_open_error", "exception"):
                            fatal_like_failures += 1
                            if fatal_like_failures >= max_fatal_like_failures:
                                self.logger.warning(
                                    "连续 %d 次点赞致命失败（页面/异常），停止互动", fatal_like_failures
                                )
                                break
                        else:
                            self.logger.info(
                                "点赞单篇跳过（%s）: %s",
                                record.failure_reason,
                                item.get("title", "")[:30],
                            )
                    else:
                        fatal_like_failures = 0
                    self._random_delay(min_s=3.0, max_s=10.0)

                # 4b. 点赞和评论之间增加间隔
                self._random_delay(min_s=5.0, max_s=15.0)

                # 4c. 生成并发布评论（评论间隔更长，降低风控风险）
                comment_count_today = self._count_today_actions("comment")
                remaining_comments = max(0, self._strategy.max_daily_comments - comment_count_today)
                items_to_comment = target_items[:remaining_comments]

                fatal_comment_failures = 0
                max_fatal_comment_failures = 3
                for item in items_to_comment:
                    comment_text = self._generate_comment(item.get("title", ""), item.get("summary", ""))
                    if not comment_text:
                        # API 生成失败属于单篇跳过，不拖慢整轮（2026-08-27）
                        self.logger.info("评论生成失败（跳过该篇）: %s", item.get("title", "")[:30])
                        continue
                    record = self._perform_comment_in_session(browser, platform, item, comment_text)
                    records.append(record)
                    if not record.success:
                        if record.failure_reason in ("page_open_error", "exception"):
                            fatal_comment_failures += 1
                            if fatal_comment_failures >= max_fatal_comment_failures:
                                self.logger.warning(
                                    "连续 %d 次评论致命失败（页面/异常），停止互动", fatal_comment_failures
                                )
                                break
                        else:
                            self.logger.info(
                                "评论单篇跳过（%s）: %s",
                                record.failure_reason,
                                item.get("title", "")[:30],
                            )
                    else:
                        fatal_comment_failures = 0
                    # 评论间隔比点赞更长，模拟真实用户行为
                    self._random_delay(min_s=8.0, max_s=25.0)

                browser.close()
                return items_to_like, items_to_comment, remaining_likes, remaining_comments

        try:
            items_to_like, items_to_comment, remaining_likes, remaining_comments = run_sync_playwright_safe(_run, timeout=600)
        except ImportError:
            self.logger.warning("Playwright 未安装，跳过互动")
            items_to_like, items_to_comment, remaining_likes, remaining_comments = [], [], 0, 0
        except Exception as exc:
            self.logger.error("互动执行异常: %s", exc)
            items_to_like, items_to_comment, remaining_likes, remaining_comments = [], [], 0, 0

        self.logger.info(
            "每日互动完成: 点赞 %d / %d, 评论 %d / %d",
            min(len(items_to_like), remaining_likes),
            self._strategy.max_daily_likes,
            min(len(items_to_comment), remaining_comments),
            self._strategy.max_daily_comments,
        )
        return records

    def generate_reply(self, comment: str, article_title: str) -> str:
        """使用 DeepSeek API 生成专业、友好的回复（50 字以内）。"""
        if not self._deepseek_api_key:
            self.logger.warning("未配置 DEEPSEEK_API_KEY，无法生成回复")
            return ""

        system_prompt = (
            "你是一位专业、友好的行业评论员。请根据文章标题和读者评论，"
            "生成一条简洁的回复。回复必须控制在 50 个汉字以内，"
            "语气专业但不生硬，可以适度表达认同或补充观点。"
        )
        user_prompt = f"文章标题：{article_title}\n读者评论：{comment}\n请生成回复："

        try:
            reply = _call_deepseek(
                self._deepseek_api_key,
                system_prompt,
                user_prompt,
                max_tokens=100,
            )
            # 硬截断到 50 字
            if len(reply) > 50:
                reply = reply[:50]
            return reply
        except Exception as exc:
            self.logger.error("生成回复失败: %s", exc)
            return ""

    def mine_comments_for_topics(
        self,
        platform: str,
        account_id: str,
    ) -> list[dict]:
        """从自己文章的评论中挖掘选题灵感。

        1. 获取自己文章的近期评论
        2. 使用 DeepSeek 提取选题灵感
        3. 返回 [{topic, source_comment, relevance_score}] 列表
        """
        comments = self._fetch_own_comments(platform, account_id)
        if not comments:
            self.logger.info("未获取到自身文章评论，跳过选题挖掘")
            return []

        if not self._deepseek_api_key:
            self.logger.warning("未配置 DEEPSEEK_API_KEY，无法挖掘选题")
            return []

        topics: list[dict] = []
        for comment_item in comments:
            comment_text = comment_item.get("content", "")
            article_title = comment_item.get("article_title", "")
            if not comment_text.strip():
                continue

            system_prompt = (
                "你是一位行业内容策划师。请从读者评论中提取可能的选题灵感。"
                "返回 JSON 格式：{\"topic\": \"选题标题\", \"relevance_score\": 0-100}。"
                "只返回 JSON，不要其他内容。"
            )
            user_prompt = f"文章标题：{article_title}\n读者评论：{comment_text}\n请提取选题灵感："

            try:
                raw = _call_deepseek(
                    self._deepseek_api_key,
                    system_prompt,
                    user_prompt,
                    max_tokens=150,
                )
                parsed = json.loads(raw)
                topic = str(parsed.get("topic", "")).strip()
                score = int(parsed.get("relevance_score", 0))
                if topic:
                    topics.append({
                        "topic": topic,
                        "source_comment": comment_text,
                        "relevance_score": max(0, min(100, score)),
                    })
            except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
                self.logger.debug("解析选题失败: %s", exc)
                continue

            self._random_delay(min_s=0.5, max_s=1.5)

        topics.sort(key=lambda x: x["relevance_score"], reverse=True)
        return topics

    # ---- 内部方法 ----

    def _load_strategy(self) -> None:
        """从配置加载互动策略（支持 config.yaml 中的 engagement 节）。"""
        try:
            config_path = self.settings.config_path
            if not config_path.exists():
                return
            import yaml
            with open(config_path, encoding="utf-8") as f:
                payload = yaml.safe_load(f) or {}
            eng = payload.get("engagement", {})
            if not isinstance(eng, dict):
                return
            if "target_keywords" in eng:
                self._strategy.target_keywords = list(eng["target_keywords"])
            if "target_accounts" in eng:
                self._strategy.target_accounts = list(eng["target_accounts"])
            if "max_daily_likes" in eng:
                self._strategy.max_daily_likes = int(eng["max_daily_likes"])
            if "max_daily_comments" in eng:
                self._strategy.max_daily_comments = int(eng["max_daily_comments"])
            if "comment_style" in eng:
                self._strategy.comment_style = str(eng["comment_style"])
            if "engagement_hours" in eng:
                self._strategy.engagement_hours = [int(h) for h in eng["engagement_hours"]]
        except Exception as exc:
            self.logger.warning("加载互动策略配置失败，使用默认值: %s", exc)

    def _scan_target_content(self, platform: str, account_id: str) -> list[dict]:
        """使用关键词在平台上扫描目标内容，返回 [{id, title, summary, url}]。

        改进：直接访问头条搜索页面，从HTML中提取真实文章URL。
        """
        items: list[dict] = []

        def _run():
            from playwright.sync_api import sync_playwright
            import re

            with sync_playwright() as pw:
                browser = self._launch_browser(pw, platform, account_id)
                if browser is None:
                    return []
                page = browser.new_page()

                for keyword in self._strategy.target_keywords:
                    try:
                        # 头条搜索 URL（综合搜索，包含文章结果）
                        search_url = f"https://www.toutiao.com/search/?keyword={keyword}"
                        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                        page.wait_for_timeout(3000)

                        # 从页面HTML中提取文章URL（正则匹配）
                        html = page.content()
                        article_urls = re.findall(
                            r'https?://www\.toutiao\.com/(?:article|group)/\d+', html
                        )

                        # 同时提取搜索结果标题（从 jump 链接中提取）
                        jump_links = page.query_selector_all("a[href*='search/jump']")
                        titles_by_url: dict[str, str] = {}
                        for link_el in jump_links:
                            href = link_el.get_attribute("href") or ""
                            title = (link_el.inner_text() or "").strip()[:80]
                            if not title:
                                continue
                            # 从 jump URL 中解码真实 URL
                            url_match = re.search(r'url=([^&]+)', href)
                            if url_match:
                                from urllib.parse import unquote
                                real_url = unquote(url_match.group(1))
                                if "article" in real_url or "group" in real_url:
                                    titles_by_url[real_url] = title

                        # 合并结果
                        seen_ids: set[str] = set()
                        for url in article_urls:
                            article_id = url.split("/")[-1]
                            if article_id in seen_ids:
                                continue
                            seen_ids.add(article_id)
                            title = titles_by_url.get(url, f"文章 {article_id[:8]}")
                            items.append({
                                "id": article_id,
                                "title": title,
                                "summary": title[:100],
                                "url": url,
                            })

                    except Exception as exc:
                        self.logger.debug("搜索关键词 %s 失败: %s", keyword, exc)

                browser.close()
                return items

        try:
            items = run_sync_playwright_safe(_run, timeout=300)
        except ImportError:
            self.logger.warning("Playwright 未安装，无法扫描目标内容")
            items = []
        except Exception as exc:
            self.logger.error("扫描目标内容异常: %s", exc)
            items = []

        # 去重
        seen: set[str] = set()
        unique: list[dict] = []
        for item in items:
            if item["id"] not in seen:
                seen.add(item["id"])
                unique.append(item)
        return unique

    def _perform_like_in_session(self, browser, platform: str, item: dict) -> EngagementRecord:
        """在已有浏览器会话中执行点赞操作。

        失败原因分类（2026-08-27）：无法打开页面（网络/反爬）与找不到点赞按钮
        属于不同问题——前者应计入连续失败快速停止，后者通常是该文章本身无点赞
        入口（视频/图文详情页差异），应跳过该文章而不是拖慢整轮互动。
        """
        record = EngagementRecord(
            platform=platform,
            action="like",
            target_id=item.get("id", ""),
            target_title=item.get("title", ""),
            content="",
            timestamp=datetime.now(),
            success=False,
        )
        # 失败原因：page_open_error（页面打不开）/ button_missing（无点赞按钮）/
        # exception（异常），供外层跳过决策使用。
        failure_reason = ""
        try:
            page = browser.new_page()
            url = item.get("url", "")
            if url and url.startswith("http"):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1500)
                except Exception as exc:
                    failure_reason = "page_open_error"
                    self.logger.warning("点赞页面打开失败 %s: %s", item.get("title", "")[:30], exc)
                    page.close()
                    self._save_record(record)
                    record.failure_reason = failure_reason
                    return record

                # 头条文章页点赞按钮（2026-08-24 实测 DOM：div.detail-like > div.digg-icon）
                like_btn = page.query_selector(
                    "div.detail-like, div.digg-wrapper, "
                    "button[class*='like'], div[class*='like'], "
                    "span[class*='like'], [data-action='like'], "
                    "svg[class*='like'], [aria-label*='赞'], "
                    "div.digg, span.digg-word"
                )
                if like_btn:
                    like_btn.click()
                    record.success = True
                    self.logger.info("点赞成功: %s", item.get("title", "")[:30])
                else:
                    failure_reason = "button_missing"
                    self.logger.debug("未找到点赞按钮（跳过）: %s", item.get("title", "")[:30])
            else:
                failure_reason = "invalid_url"
                self.logger.debug("URL无效，跳过: %s", item.get("title", "")[:30])

            page.close()
        except Exception as exc:
            failure_reason = failure_reason or "exception"
            self.logger.error("点赞操作异常: %s", exc)

        record.failure_reason = failure_reason
        self._save_record(record)
        return record

    def _perform_comment_in_session(
        self, browser, platform: str, item: dict, comment_text: str
    ) -> EngagementRecord:
        """在已有浏览器会话中执行评论操作。

        失败原因分类同点赞：page_open_error / button_missing / invalid_url /
        exception，供外层决定跳过单篇还是停止整轮（2026-08-27）。
        """
        record = EngagementRecord(
            platform=platform,
            action="comment",
            target_id=item.get("id", ""),
            target_title=item.get("title", ""),
            content=comment_text,
            timestamp=datetime.now(),
            success=False,
        )
        failure_reason = ""
        try:
            page = browser.new_page()
            url = item.get("url", "")
            if not url or not url.startswith("http"):
                failure_reason = "invalid_url"
                self.logger.debug("URL无效，跳过评论: %s", item.get("title", "")[:30])
                page.close()
                self._save_record(record)
                record.failure_reason = failure_reason
                return record

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
            except Exception as exc:
                failure_reason = "page_open_error"
                self.logger.warning("评论页面打开失败 %s: %s", item.get("title", "")[:30], exc)
                page.close()
                self._save_record(record)
                record.failure_reason = failure_reason
                return record

            # 头条文章页评论输入框（2026-08-24 实测 DOM：div.comment-textarea[contenteditable]）
            comment_input = page.query_selector(
                "div.comment-textarea[contenteditable='true'], "
                "textarea[class*='comment'], div[contenteditable='true'][class*='comment'], "
                "textarea[placeholder*='评论'], textarea[placeholder*='说'], "
                "div[contenteditable='true'][placeholder*='评论'], "
                "div.comment-input textarea, div.comment-box textarea, "
                "textarea#comment-textarea, textarea.input-area"
            )
            if comment_input:
                try:
                    comment_input.click()
                    page.wait_for_timeout(300)
                    comment_input.fill(comment_text)
                except Exception:
                    # contenteditable div 不支持 fill，用键盘输入
                    try:
                        comment_input.click()
                        page.keyboard.type(comment_text, delay=30)
                    except Exception as exc:
                        failure_reason = "exception"
                        self.logger.debug("评论输入失败: %s", exc)
                        page.close()
                        self._save_record(record)
                        record.failure_reason = failure_reason
                        return record
                page.wait_for_timeout(500)

                submit_btn = page.query_selector(
                    "button[class*='submit'], button[class*='send'], "
                    "button:has-text('发表'), button:has-text('发送'), "
                    "button:has-text('评论'), button:has-text('提交'), "
                    "a:has-text('发表'), div.comment-btn button"
                )
                if submit_btn:
                    submit_btn.click()
                    page.wait_for_timeout(1000)
                    record.success = True
                    self.logger.info("评论成功: %s", item.get("title", "")[:30])
                else:
                    failure_reason = "button_missing"
                    self.logger.debug("未找到评论提交按钮（跳过）: %s", item.get("title", "")[:30])
            else:
                failure_reason = "button_missing"
                self.logger.debug("未找到评论输入框（跳过）: %s", item.get("title", "")[:30])

            page.close()
        except Exception as exc:
            failure_reason = failure_reason or "exception"
            self.logger.error("评论操作异常: %s", exc)

        record.failure_reason = failure_reason
        self._save_record(record)
        return record

    def _generate_comment(self, title: str, summary: str) -> str:
        """使用 DeepSeek 生成评论内容。"""
        if not self._deepseek_api_key:
            self.logger.warning("未配置 DEEPSEEK_API_KEY，无法生成评论")
            return ""

        style_instruction = {
            "professional": "专业、有见地，体现行业认知深度",
            "casual": "轻松、接地气，像普通读者一样",
            "enthusiastic": "热情、积极，表达强烈兴趣",
        }.get(self._strategy.comment_style, "专业、友好")

        system_prompt = (
            f"你是一位{style_instruction}的读者。请根据文章标题和摘要，"
            f"写一条简短评论（50 字以内）。评论要有价值，不要空洞赞美。"
        )
        user_prompt = f"文章标题：{title}\n摘要：{summary}\n请写评论："

        try:
            comment = _call_deepseek(
                self._deepseek_api_key,
                system_prompt,
                user_prompt,
                max_tokens=100,
            )
            if len(comment) > 50:
                comment = comment[:50]
            return comment
        except Exception as exc:
            self.logger.error("生成评论失败: %s", exc)
            return ""

    def _fetch_own_comments(self, platform: str, account_id: str) -> list[dict]:
        """获取自己文章的近期评论。"""
        comments: list[dict] = []

        def _run():
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = self._launch_browser(pw, platform, account_id)
                if browser is None:
                    return []
                page = browser.new_page()

                # 导航到评论管理页面
                comments_url = self._build_comments_url(platform)
                if comments_url:
                    page.goto(comments_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)

                    comment_els = page.query_selector_all(
                        "div[class*='comment'], div[class*='message'], div[class*='reply']"
                    )
                    for el in comment_els[:30]:
                        content_el = el.query_selector(
                            "span[class*='content'], p, div[class*='text']"
                        )
                        article_el = el.query_selector(
                            "a[class*='article'], span[class*='article'], [class*='source']"
                        )
                        content = (content_el.inner_text() if content_el else "").strip()
                        article_title = (article_el.inner_text() if article_el else "").strip()
                        if content:
                            comments.append({
                                "content": content,
                                "article_title": article_title,
                            })

                browser.close()
                return comments

        try:
            comments = run_sync_playwright_safe(_run, timeout=300)
        except ImportError:
            self.logger.warning("Playwright 未安装，无法获取评论")
            comments = []
        except Exception as exc:
            self.logger.error("获取自身评论异常: %s", exc)
            comments = []

        return comments

    def _check_daily_limit(self, action: str) -> bool:
        """检查今日该操作是否已达上限。"""
        count = self._count_today_actions(action)
        if action == "like":
            return count < self._strategy.max_daily_likes
        if action in ("comment", "reply"):
            return count < self._strategy.max_daily_comments
        return True

    def _count_today_actions(self, action: str) -> int:
        """统计今日某类操作次数。"""
        today = date.today().isoformat()
        try:
            cursor = self.db.execute(
                "SELECT COUNT(*) FROM engagement_records "
                "WHERE action = ? AND DATE(timestamp) = ? AND success = 1",
                (action, today),
            )
            return cursor.fetchone()[0]
        except sqlite3.Error:
            return 0

    def _save_record(self, record: EngagementRecord) -> None:
        """保存互动记录到 SQLite。"""
        try:
            # 兼容旧库：缺 failure_reason 列时先补列（2026-08-27）
            try:
                self.db.execute("ALTER TABLE engagement_records ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''")
                self.db.commit()
            except sqlite3.Error:
                pass
            self.db.execute(
                "INSERT INTO engagement_records "
                "(platform, action, target_id, target_title, content, timestamp, success, failure_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.platform,
                    record.action,
                    record.target_id,
                    record.target_title,
                    record.content,
                    record.timestamp.isoformat(),
                    1 if record.success else 0,
                    record.failure_reason,
                ),
            )
            self.db.commit()
        except sqlite3.Error as exc:
            self.logger.error("保存互动记录失败: %s", exc)

    # ---- 浏览器辅助 ----

    def _launch_browser(self, playwright, platform: str, account_id: str):
        """启动浏览器并加载已有 profile。"""
        try:
            profile_dir = self._resolve_browser_profile(platform, account_id)
            channel = self.settings.publishing.channel or "msedge"
            headless = self.settings.publishing.headless
            executable = self.settings.publishing.executable_path

            if profile_dir:
                # 使用 persistent context 加载已有 profile
                launch_kwargs: dict[str, Any] = {
                    "headless": headless,
                    "channel": channel,
                }
                if executable:
                    launch_kwargs["executable_path"] = executable
                return playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir), **launch_kwargs
                )
            else:
                # 无 profile，普通启动
                launch_kwargs = {
                    "headless": headless,
                    "channel": channel,
                }
                if executable:
                    launch_kwargs["executable_path"] = executable
                return playwright.chromium.launch(**launch_kwargs)
        except Exception as exc:
            self.logger.error("启动浏览器失败: %s", exc)
            return None

    def _resolve_browser_profile(self, platform: str, account_id: str) -> Path | None:
        """解析浏览器 profile 目录。"""
        raw = self.settings.publishing.user_data_dir

        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = self.settings.project_root / p
        return p

    def _build_search_url(self, platform: str) -> str:
        """构建平台搜索 URL。"""
        urls = {
            "toutiao": "https://www.toutiao.com/search/",
            "zhihu": "https://www.zhihu.com/search",
            "bilibili": "https://search.bilibili.com/",
        }
        return urls.get(platform, "")

    def _build_comments_url(self, platform: str) -> str:
        """构建评论管理页面 URL。"""
        urls = {
            "toutiao": "https://mp.toutiao.com/profile_v4/graphic/comments",
            "zhihu": "https://www.zhihu.com/notifications",
            "bilibili": "https://member.bilibili.com/platform/upload-manager/article",
        }
        return urls.get(platform, "")

    def _resolve_relative_url(self, platform: str, relative: str) -> str:
        """将相对 URL 解析为绝对 URL。"""
        bases = {
            "toutiao": "https://www.toutiao.com",
            "zhihu": "https://www.zhihu.com",
            "bilibili": "https://www.bilibili.com",
        }
        base = bases.get(platform, "")
        if relative.startswith("/"):
            return f"{base}{relative}"
        return f"{base}/{relative}"

    def _random_delay(self, min_s: float = 2.0, max_s: float = 8.0) -> None:
        """随机延迟，模拟人类操作节奏，防止反机器人检测。"""
        delay = random.uniform(min_s, max_s)
        self.logger.debug("随机延迟 %.1f 秒", delay)
        time.sleep(delay)
