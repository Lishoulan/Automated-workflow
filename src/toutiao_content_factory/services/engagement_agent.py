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


# ---------------------------------------------------------------------------
# DeepSeek API 辅助（仅使用 urllib，无额外依赖）
# ---------------------------------------------------------------------------

_base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/")
# DeepSeek API: base_url + /chat/completions (OpenAI 兼容)
if _base.endswith("/v1"):
    _DEEPSEEK_BASE_URL = _base + "/chat/completions"
else:
    _DEEPSEEK_BASE_URL = _base + "/v1/chat/completions"
_DEEPSEEK_MODEL = os.getenv("OPENAI_MODEL", "deepseek-v4-flash")


def _call_deepseek(api_key: str, system_prompt: str, user_prompt: str, max_tokens: int = 200) -> str:
    """调用 DeepSeek Chat API，返回助手回复文本。"""
    payload = json.dumps({
        "model": _DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }).encode("utf-8")

    req = urllib.request.Request(
        _DEEPSEEK_BASE_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"DeepSeek API 调用失败: {exc}") from exc


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
    success     INTEGER NOT NULL DEFAULT 0
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

                for item in items_to_like:
                    record = self._perform_like_in_session(browser, platform, item)
                    records.append(record)
                    if not record.success:
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            self.logger.warning("连续 %d 次点赞失败，停止互动", consecutive_failures)
                            break
                    else:
                        consecutive_failures = 0
                    self._random_delay(min_s=3.0, max_s=10.0)

                # 4b. 点赞和评论之间增加间隔
                self._random_delay(min_s=5.0, max_s=15.0)

                # 4c. 生成并发布评论（评论间隔更长，降低风控风险）
                comment_count_today = self._count_today_actions("comment")
                remaining_comments = max(0, self._strategy.max_daily_comments - comment_count_today)
                items_to_comment = target_items[:remaining_comments]

                consecutive_failures = 0
                for item in items_to_comment:
                    comment_text = self._generate_comment(item.get("title", ""), item.get("summary", ""))
                    if not comment_text:
                        continue
                    record = self._perform_comment_in_session(browser, platform, item, comment_text)
                    records.append(record)
                    if not record.success:
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            self.logger.warning("连续 %d 次评论失败，停止互动", consecutive_failures)
                            break
                    else:
                        consecutive_failures = 0
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
        """在已有浏览器会话中执行点赞操作。"""
        record = EngagementRecord(
            platform=platform,
            action="like",
            target_id=item.get("id", ""),
            target_title=item.get("title", ""),
            content="",
            timestamp=datetime.now(),
            success=False,
        )
        try:
            page = browser.new_page()
            url = item.get("url", "")
            if url and url.startswith("http"):
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)

                # 头条文章页点赞按钮
                like_btn = page.query_selector(
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
                    self.logger.debug("未找到点赞按钮: %s", item.get("title", "")[:30])
            else:
                self.logger.debug("URL无效，跳过: %s", item.get("title", "")[:30])

            page.close()
        except Exception as exc:
            self.logger.error("点赞操作异常: %s", exc)

        self._save_record(record)
        return record

    def _perform_comment_in_session(
        self, browser, platform: str, item: dict, comment_text: str
    ) -> EngagementRecord:
        """在已有浏览器会话中执行评论操作。"""
        record = EngagementRecord(
            platform=platform,
            action="comment",
            target_id=item.get("id", ""),
            target_title=item.get("title", ""),
            content=comment_text,
            timestamp=datetime.now(),
            success=False,
        )
        try:
            page = browser.new_page()
            url = item.get("url", "")
            if not url or not url.startswith("http"):
                self.logger.debug("URL无效，跳过评论: %s", item.get("title", "")[:30])
                page.close()
                self._save_record(record)
                return record

            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)

            # 头条文章页评论输入框（多种选择器兼容）
            comment_input = page.query_selector(
                "textarea[class*='comment'], div[contenteditable='true'][class*='comment'], "
                "textarea[placeholder*='评论'], textarea[placeholder*='说'], "
                "div[contenteditable='true'][placeholder*='评论'], "
                "div.comment-input textarea, div.comment-box textarea, "
                "textarea#comment-textarea, textarea.input-area"
            )
            if comment_input:
                comment_input.fill(comment_text)
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
                    self.logger.debug("未找到评论提交按钮: %s", item.get("title", "")[:30])
            else:
                self.logger.debug("未找到评论输入框: %s", item.get("title", "")[:30])

            page.close()
        except Exception as exc:
            self.logger.error("评论操作异常: %s", exc)

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
            self.db.execute(
                "INSERT INTO engagement_records "
                "(platform, action, target_id, target_title, content, timestamp, success) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.platform,
                    record.action,
                    record.target_id,
                    record.target_title,
                    record.content,
                    record.timestamp.isoformat(),
                    1 if record.success else 0,
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
