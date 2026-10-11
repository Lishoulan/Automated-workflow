from __future__ import annotations

import hashlib
from html import unescape as html_unescape
from html.parser import HTMLParser
import json
import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote_plus, urlencode, urlsplit

from toutiao_content_factory.config import AppSettings, RPAResilienceSettings
from toutiao_content_factory.constants.selectors import TOUTIAO_EDITOR_PAGE, TOUTIAO_WEITOUTIAO_PAGE
from toutiao_content_factory.models import PublishRecord
from toutiao_content_factory.services.automation import TOPIC_DEDUP_WINDOW_DAYS
from toutiao_content_factory.services.article_sanitizer import (
    clean_article_text,
    looks_like_html_document,
    strip_external_source_metadata,
)
from toutiao_content_factory.services.browser_launch_guard import (
    ensure_browser_session_ready,
    prepare_browser_profile,
)
from toutiao_content_factory.services.compliance_service import get_proxy_server
from toutiao_content_factory.services.content_guard_service import ContentGuardService
from toutiao_content_factory.services.creative_scoring_service import CreativeScoringService
from toutiao_content_factory.services.growth_ops_service import GrowthOpsService
from toutiao_content_factory.services.image_generation_service import ImageGenerationService, article_image_fingerprint, generate_text_cover
from toutiao_content_factory.services.notification_service import NotificationService
from toutiao_content_factory.services.publish_precheck_service import PublishPrecheckService
# 健壮性增强模块：动态重试退避 / 弹窗守卫 / 定位器降级 / 发布状态双重校验
# 所有增强能力均以可选方式接入，不破坏原有配置隔离与业务逻辑
from toutiao_content_factory.services.rpa_resilience import (
    LocatorStrategy,
    PopupGuard,
    PublishVerificationResult,
    RetryPolicy,
    classify_exception,
    locate_with_fallbacks,
    retry_with_backoff,
    verify_publish_dually,
)
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe
from toutiao_content_factory.utils.browser_utils import apply_stealth, build_persistent_context_options
from toutiao_content_factory.utils.title_utils import fit_title_to_limit

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    PlaywrightTimeoutError = Exception
    PlaywrightError = Exception
    sync_playwright = None

# 模块 A 合并新增：tenacity 动态重试装饰器
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

try:
    import msvcrt
except ModuleNotFoundError:  # pragma: no cover - fallback for non-Windows environments
    msvcrt = None


def _persistent_image_reference(value: Any, *, is_web_uri: bool = False) -> str:
    """Return a reference that can survive an editor save, or an empty string."""
    if value is None:
        return ""
    normalized = html_unescape(str(value)).strip()
    if not normalized:
        return ""
    if normalized.lower().startswith(("blob:", "data:", "javascript:", "about:")):
        return ""
    if is_web_uri:
        return normalized
    if re.match(r"^(?:https?:)?//", normalized, flags=re.IGNORECASE):
        return normalized
    return ""


class _PersistentImageReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []
        self._seen: set[str] = set()

    def _register_image(self, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {str(key).lower(): value for key, value in attrs}
        ref = ""
        for key in ("web_uri", "web-uri", "data-web-uri"):
            ref = _persistent_image_reference(attr_map.get(key), is_web_uri=True)
            if ref:
                break
        if not ref:
            ref = _persistent_image_reference(attr_map.get("src"))
        if ref and ref not in self._seen:
            self._seen.add(ref)
            self.refs.append(ref)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            self._register_image(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            self._register_image(attrs)


class PlatformUIUpdateException(RuntimeError):
    """Raised when a platform UI change is detected during browser automation."""
    def __init__(self, platform: str, element_desc: str = "", message: str = "",
                 debug_screenshot: str = "", debug_dom_path: str = ""):
        self.platform = platform
        self.element_desc = element_desc
        self.debug_screenshot = debug_screenshot
        self.debug_dom_path = debug_dom_path
        super().__init__(message or f"UI change detected on {platform}: {element_desc}")


class PublishService:
    AUTO_TITLE_SCORE_MIN = CreativeScoringService.AUTO_TITLE_SCORE_MIN
    AUTO_COVER_SCORE_MIN = CreativeScoringService.AUTO_COVER_SCORE_MIN
    AUTO_CREATIVE_COMBINED_MIN = CreativeScoringService.AUTO_COMBINED_SCORE_MIN

    DEBUG_PATH_PATTERN = re.compile(r"Debug files saved to:\s*(?P<path>.+)$", re.IGNORECASE)
    NORMALIZE_PATTERN = re.compile(r"[\s\W_]+", re.UNICODE)
    # 去重基线状态：凡可能已上线(含发布验证不确定但有成功后端确认或可能已发布)的记录，
    # 都纳入去重比对，避免同标题/同选题重复上线。
    # publish_uncertain 保守纳入：它代表"可能已发布"，漏判会直接导致完全同标题重复。
    SUCCESS_HISTORY_STATUSES = {"published", "submitted_for_review", "submitted", "success", "publish_uncertain"}

    # 生产层同模板标题去重：主题词/数字泛化为骨架占位符后比较，
    # 解决"不同 topic 生成同构模板标题"的重复漏洞（如'XX之后，XX开始重排利润'被跨次复用）。
    TITLE_SKELETON_FILLERS = (
        "人形机器人", "具身智能", "机器人", "AI芯片", "芯片", "半导体",
        "储能", "光伏", "新能源", "零部件", "整机", "执行器", "减速器",
        "传感器", "大脑", "核心件", "产业链", "环节", "AI应用", "大模型",
    )
    # 高频滥用模板的触发标记：仅当命中这些标记时才启用骨架去重，避免误判正常多样标题。
    KNOWN_TEMPLATE_MARKERS = (
        "重排利润", "兑现筛选期", "进入筛选期", "筛选期",
        "开始重排", "利润流向", "先流向", "先兑现", "开始进入兑现",
    )
    META_NOISE_PREFIXES = (
        "账号定位：",
        "账号：",
        "账号名：",
        "以下是今日自动选中的热点选题",
        "写作要求：",
        "固定写法：",
        "推荐主结论：",
        "跟踪变量：",
        "入选理由：",
        "请围绕这条产业线索",
        "请重点分析",
        "主赛道：",
        "主关键词：",
        "匹配赛道：",
        "匹配关键词：",
        "可确认数字：",
        "标题：",
        "来源：",
        "发布日期：",
        "原文链接：",
        "补充片段：",
        "摘要：",
    )

    DEFAULT_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
    DEFAULT_TITLE_SELECTORS = TOUTIAO_EDITOR_PAGE.get("title_input")
    DEFAULT_EDITOR_SELECTORS = TOUTIAO_EDITOR_PAGE.get("article_editor")
    DEFAULT_DRAFT_STATUS_SELECTORS = TOUTIAO_EDITOR_PAGE.get("draft_status")
    DEFAULT_ARTICLE_IMAGE_TRIGGER_SELECTORS = TOUTIAO_EDITOR_PAGE.get("image_insert_button")
    INLINE_IMAGE_DATA_TOKENS = (
        "数据",
        "装机",
        "订单",
        "出货",
        "并网",
        "项目",
        "GWh",
        "MWh",
        "MW",
        "亿元",
        "同比",
    )
    INLINE_IMAGE_ANALYSIS_TOKENS = (
        "产业链",
        "交付",
        "利润",
        "毛利",
        "验证",
        "节奏",
        "风险",
        "客户",
        "电站",
        "储能",
        "逆变器",
        "电池",
    )
    AI_DRAWER_MASK_SELECTORS = TOUTIAO_EDITOR_PAGE.get("ai_drawer_mask")
    AI_DRAWER_CLOSE_SELECTORS = TOUTIAO_EDITOR_PAGE.get("ai_drawer_close")
    PUBLISH_EVENT_PATTERNS = (
        "/mp/agw/article/publish",
        "/mp/agw/draft/save_ugc_draft",
        "/mp/agw/draft/",
    )
    DIAGNOSTIC_EVENT_PATTERNS = (
        "/mp/agw/article/new",
        "/mp/agw/creator_center/user_info",
        "/mp/agw/media/get_user_base_info",
        "/mp/agw/media/check_user_auth",
    )

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.history_path = settings.output_dir / settings.publishing.history_filename
        self.growth_ops_service = GrowthOpsService(settings, logger)
        self.content_guard_service = ContentGuardService(settings, logger)
        self.notification_service = NotificationService(
            dict(os.environ),
            fallback_log_dir=str(self.settings.output_dir / "automation"),
        )
        # 健壮性增强：默认重试策略（指数退避 1s→2s→4s→8s，含 30% 抖动）
        # 阻断性异常（登录过期/账号封禁）立即抛出，不浪费重试次数
        self._rpa_retry_policy = RetryPolicy(
            max_attempts=4,
            base_delay=1.0,
            max_delay=15.0,
            multiplier=2.0,
            jitter=0.3,
            retryable_only=True,
        )
        # 健壮性增强：弹窗守卫实例（延迟创建，需要 page 时才实例化）
        # 通过 _make_popup_guard(page) 在每次发布流程中创建绑定到当前 page 的守卫
        self._popup_guard_platform = "toutiao"
        # 模块 A 合并新增：读取 rpa_resilience 配置，若无则使用默认值（向下兼容）
        self.rpa_config = getattr(settings, "rpa_resilience", RPAResilienceSettings())
        # 发布前最终合规预检器（作为 sanitize 之后的最后防线）
        self.precheck_service = PublishPrecheckService(
            account=settings.account_key, logger=logger,
        )
        self._last_inline_image_assets: list[Path] = []
        # 2026-09-25：本次发布是否已经点过「发布」按钮。
        # 只要点过一次，重试就绝不能再点 —— 否则同一篇文章会被提交多次。
        # 详见 docs/重复发布根因_20260925.md
        self._publish_button_clicked_this_article = False
        self._save_override_state: dict[str, Any] | None = None

    def _launch_persistent_browser(self, playwright, *, profile_dir: Path):
        proxy_server = get_proxy_server()
        launch_options, fingerprint = build_persistent_context_options(
            account_key=self.settings.account_key,
            profile_dir=profile_dir,
            proxy_server=proxy_server,
            headless=self.settings.publishing.headless,
            channel=self.settings.publishing.channel or None,
            executable_path=self.settings.publishing.executable_path or None,
            timezone_id="Asia/Shanghai",
            locale="zh-CN",
        )
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            **launch_options,
        )
        apply_stealth(browser, fingerprint=fingerprint)
        return browser

    def _toutiao_login_page_detected(self, page) -> bool:
        current_url = str(getattr(page, "url", "") or "")
        login_indicators = ("/login", "/passport", "/auth/login", "sso.toutiao.com")
        if any(indicator in current_url for indicator in login_indicators):
            return True
        try:
            return bool(page.evaluate("""() => {
                const selectors = [
                    'input[name="account"]',
                    'input[type="password"]',
                    '.login-container',
                    '[class*="login-form"]'
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) return true;
                    }
                }
                return false;
            }"""))
        except Exception:
            return False

    def _toutiao_session_ready(self, page) -> bool:
        current_url = str(getattr(page, "url", "") or "").lower()
        return not self._toutiao_login_page_detected(page) and (
            "graphic/publish" in current_url or "profile_v4" in current_url or "mp.toutiao.com" in current_url
        )

    def _ensure_browser_session_ready(self, browser, page, probe_url: str) -> dict[str, Any]:
        summary = ensure_browser_session_ready(
            browser_context=browser,
            page=page,
            platform="Toutiao",
            profile_dir=self._resolve_profile_dir(),
            account_runtime_dir=self.settings.account_runtime_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            probe_url=probe_url,
            login_detected_fn=self._toutiao_login_page_detected,
            session_ready_fn=self._toutiao_session_ready,
            notification_service=self.notification_service,
            navigate_fn=lambda target_page, url: self._goto_publish_page(target_page, url),
            goto_timeout_ms=max(self.settings.publishing.wait_timeout_ms, 30000),
        )
        if summary.get("login_detected") or not summary.get("session_ready"):
            self._append_notification(
                level="warn",
                title="Toutiao session invalid",
                message=(
                    f"Toutiao browser session is invalid and requires manual QR login. "
                    f"Current URL: {summary.get('current_url', '')}"
                ),
                notification_type="toutiao_session_invalid",
                data=summary,
            )
            raise RuntimeError(
                "Toutiao browser session is invalid or expired. "
                f"Current URL: {summary.get('current_url', '')}. "
                "Operator has been notified to refresh the login session."
            )
        return summary

    def _get_retry_decorator(self):
        """模块 A 合并新增：动态生成基于配置的 tenacity 重试装饰器。"""
        return retry(
            stop=stop_after_attempt(self.rpa_config.max_retries),
            wait=wait_exponential(
                multiplier=self.rpa_config.retry_backoff_seconds,
                min=2,
                max=10,
            ),
            retry=retry_if_exception_type((PlaywrightError, TimeoutError, PlaywrightTimeoutError)),
            reraise=True,
            before_sleep=lambda retry_state: self.logger.warning(
                f"RPA 操作失败，正在进行第 {retry_state.attempt_number} 次重试..."
            ),
        )

    def _defend_popups(self, page) -> int:
        """模块 A 合并新增：主动拦截并关闭创作者后台的干扰弹窗。

        返回关闭的弹窗数量。
        """
        if not self.rpa_config.popup_defense_enabled:
            return 0

        closed_count = 0
        # 常见干扰弹窗的选择器（头条/微信通用常见干扰）
        popup_selectors = [
            "text='我知道了'",
            "text='确定'",
            ".modal-close",
            ".dialog-close-btn",
            "text='关闭'",
            "text='暂不安全'",
            ".editor-guide-close",
            "text='下次再说'",
        ]

        for selector in popup_selectors:
            try:
                element = page.locator(selector).first
                if element.is_visible(timeout=500):
                    element.click()
                    closed_count += 1
                    self.logger.info(f"弹窗防御机制：成功关闭弹窗 [{selector}]")
            except Exception:
                continue
        return closed_count

    @contextmanager
    def _publish_session_lock(self, purpose: str):
        lock_path = self.settings.output_dir / ".publish_session.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            try:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                    handle.seek(0)
            except Exception:
                pass

            if msvcrt is None:
                yield
                return

            self.logger.info("Waiting for publish session lock (%s)...", purpose)
            deadline = time.monotonic() + 60
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.logger.warning("Publish session lock timed out after 60s for %s, forcing release.", purpose)
                        try:
                            lock_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        break
                    time.sleep(1)

            try:
                yield
            finally:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass

    def publish_run(self, run_dir: Path, *, declare_first_release: bool = True) -> dict[str, Path]:
        with self._publish_session_lock("publish run"):
            return self._publish_run_locked(run_dir, declare_first_release=declare_first_release)

    def _publish_run_locked(self, run_dir: Path, *, declare_first_release: bool = True) -> dict[str, Path]:
        if not self.settings.publishing.enabled:
            raise RuntimeError("Publishing is disabled. Set TOUTIAO_PUBLISH_ENABLED=1 first.")

        requested_first_release = bool(declare_first_release)

        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")

        publish_meta = self._load_json(publish_dir / "publish_meta.json")
        review_status = self._load_json(run_dir / "review_status.json")
        if not publish_meta:
            raise FileNotFoundError("publish_meta.json is missing.")
        if str(review_status.get("status", "")) != "approved":
            raise RuntimeError("Run is not approved yet. Approve review before publishing.")
        if str(publish_meta.get("approval_mode", "")).strip().lower() == "automatic":
            self._validate_automatic_creative_gate(publish_dir)

        # 日发布频率硬限制：防止 max_daily_posts 配置被绕过
        # 历史数据：5月31日单日发布10篇，触发平台限流，账号权重被稀释
        if not bool(self.settings.publishing.draft_only):
            from toutiao_content_factory.services.compliance_service import (
                check_daily_post_limit,
                count_today_published,
                get_max_daily_posts,
            )
            if not check_daily_post_limit(self.history_path, platform=self.settings.publishing.platform):
                today_count = count_today_published(self.history_path, self.settings.publishing.platform)
                max_posts = get_max_daily_posts(self.settings.publishing.platform)
                raise RuntimeError(
                    f"Daily post limit reached: {today_count}/{max_posts} posts today. "
                    "Publish blocked to protect account weight."
                )

        topic = publish_meta.get("selected_topic") or {}
        topic_title = str(topic.get("title", ""))
        topic_url = str(topic.get("url", ""))
        if (
            not bool(self.settings.publishing.draft_only)
            and not self.settings.publishing.allow_duplicate_topics
            and self._was_topic_published(topic_title, topic_url)
        ):
            raise RuntimeError("This topic has already been published in the last 7 days. Duplicate publish blocked.")

        title = self._clip_title(str(publish_meta.get("selected_title", "")).strip())
        if (
            not bool(self.settings.publishing.draft_only)
            and not self.settings.publishing.allow_duplicate_topics
            and (
                self._was_article_title_published_recently(title)
                or self._blocked_by_template_dedup(title)
            )
        ):
            raise RuntimeError(
                "An article with the same or highly similar title was published in the last 7 days "
                "(edit distance <= 3 or template skeleton). Duplicate publish blocked per RULE_009/RULE_007."
            )

        article = self._sanitize_article_for_publish(self._read_text(publish_dir / "article.md").strip())
        article_quality_warnings: list[str] = []
        platform_preflight_summary = self._collect_preflight_summary_locked()
        content_guard_report = self.content_guard_service.build_publish_guard_report(run_dir)
        first_release_assessment = (
            self.content_guard_service.quality_checker.assess_first_release(article).as_dict()
        )
        content_guard_report["first_release_assessment"] = first_release_assessment
        if requested_first_release and first_release_assessment.get("eligible") is not True:
            declare_first_release = False
            reasons = [
                str(item).strip()
                for item in first_release_assessment.get("reasons", [])
                if str(item).strip()
            ]
            reason_text = "；".join(reasons) or "首发质量评估未通过"
            notes_warning = f"质量检查：{reason_text}，自动取消头条首发声明"
            article_quality_warnings.append(notes_warning)
            self.logger.warning(notes_warning)
        if article_quality_warnings:
            platform_preflight_summary = {
                **platform_preflight_summary,
                "warnings": self._unique_texts(
                    list(platform_preflight_summary.get("warnings", []))
                    + article_quality_warnings
                ),
            }
        preflight_summary = self._merge_content_guard_summary(platform_preflight_summary, content_guard_report)
        quality_check = content_guard_report.get("quality_check", {})
        if (
            not bool(self.settings.publishing.draft_only)
            and isinstance(quality_check, dict)
            and quality_check.get("is_passed") is False
        ):
            quality_issues = [str(item) for item in quality_check.get("issues", [])[:5]]
            issue_suffix = " | ".join(quality_issues)
            raise RuntimeError(
                "Publish quality gate blocked: final article quality check did not pass."
                + (f" {issue_suffix}" if issue_suffix else "")
            )
        platform_blocking_flags = list(platform_preflight_summary.get("blocking_flags", []))
        if platform_blocking_flags and not self._should_bypass_preflight_blockers(platform_preflight_summary):
            raise RuntimeError("Publish preflight blocked: " + " | ".join(str(item) for item in platform_blocking_flags))
        if platform_blocking_flags:
            self.logger.warning(
                "Publish preflight reported blocking flags, but the run is allowed to continue: %s",
                " | ".join(str(item) for item in platform_blocking_flags),
            )

        # 内容守卫属于文章本身的风险，不能因为平台页面可用而绕过；否则重复、敏感词或高 AI 风险内容仍可能被真实发布。
        content_guard_blocking_flags = list(content_guard_report.get("blocking_flags", []))
        if content_guard_blocking_flags:
            raise RuntimeError(
                "Publish content guard blocked: " + " | ".join(str(item) for item in content_guard_blocking_flags)
            )

        # 发布前最终合规预检：作为 sanitize 之后的最后防线，
        # 命中任一硬拦截规则即降级为草稿模式，不提交到头条公域。
        # effective_draft_only 仅影响本次发布，不修改 settings，不影响后续发布任务。
        effective_draft_only = bool(self.settings.publishing.draft_only)
        precheck_result = self.precheck_service.run_precheck(
            article,
            project_root=self.settings.project_root,
        )
        if precheck_result.get("status") == "blocked":
            effective_draft_only = True
            self.logger.info(
                "Precheck blocked,降级为草稿模式: %s",
                precheck_result.get("violations"),
            )

        max_publish_retries = 2
        last_publish_exc: Exception | None = None
        self._publish_button_clicked_this_article = False
        for attempt in range(1 + max_publish_retries):
            if attempt > 0:
                # ⚠️ 2026-09-25 新增，优先级高于下面的历史检查：
                # 只要「发布」按钮已经被点过一次，就绝不再点第二次。
                # 点完按钮之后若只是「没捕获到提交响应」，文章其实已经提交了，
                # 再重试就会产生重复发布。线上实测：同一标题被提交 3 次
                # （= 1 + max_publish_retries），平台侧据此判重降权。
                # 证据：docs/重复发布根因_20260925.md
                if self._publish_button_clicked_this_article:
                    self.logger.error(
                        "Retry aborted: the publish button was already clicked in a previous "
                        "attempt. A second click would submit the same article twice."
                    )
                    raise RuntimeError(
                        "Duplicate publish prevented on retry: the publish button was already "
                        "clicked in a previous attempt. Refusing to submit the same article twice."
                    )
                # 重试前重新检查发布历史，防止“第一次实际已提交但前端验证失败”
                # 导致重复点击发布按钮造成真正的重复发布。
                if not bool(self.settings.publishing.draft_only) and not self.settings.publishing.allow_duplicate_topics:
                    if self._was_article_title_published_recently(title) or self._blocked_by_template_dedup(title):
                        self.logger.warning(
                            "Retry aborted: article title was already published in the last 7 days. title=%s",
                            title,
                        )
                        raise RuntimeError(
                            "Duplicate publish detected on retry: an article with the same or similar title "
                            "was already submitted. Aborting retry to prevent duplicate publishes."
                        )
            try:
                result_payload = self._publish_to_toutiao(
                    title=title,
                    article=article,
                    draft_only=effective_draft_only,
                    run_dir=run_dir,
                    declare_first_release=declare_first_release,
                )
                break
            except Exception as exc:
                last_publish_exc = exc
                exc_msg = str(exc).lower()
                non_retryable = any(
                    kw in exc_msg
                    for kw in ("session expired", "not logged in", "login form detected", "session may have expired")
                )
                if non_retryable or attempt >= max_publish_retries:
                    self._write_failed_publish_result(
                        publish_dir=publish_dir,
                        run_dir=run_dir,
                        title=title,
                        topic_title=topic_title,
                        topic_url=topic_url,
                        error=str(exc),
                        preflight_summary=preflight_summary,
                    )
                    raise
                self.logger.warning(
                    "Toutiao publish attempt %d/%d failed: %s. Retrying in 5s...",
                    attempt + 1, 1 + max_publish_retries, exc,
                )
                time.sleep(5)
        else:
            if last_publish_exc is not None:
                raise last_publish_exc

        result_status = str(result_payload.get("status", "")).strip().lower() or "published"
        result_payload["status"] = result_status
        if result_status in {
            "failed",
            "error",
            "blocked",
            "blocked_by_ip",
            "publish_login_required",
        }:
            result_error = str(
                result_payload.get("error")
                or result_payload.get("message")
                or f"Toutiao publish returned status={result_status}"
            )
            self._write_failed_publish_result(
                publish_dir=publish_dir,
                run_dir=run_dir,
                title=title,
                topic_title=topic_title,
                topic_url=topic_url,
                error=result_error,
                preflight_summary=preflight_summary,
            )
            raise RuntimeError(f"Toutiao publish failed: {result_error}")

        record = PublishRecord(
            published_at=datetime.now().isoformat(timespec="seconds"),
            platform=self.settings.publishing.platform,
            title=title,
            run_dir=str(run_dir),
            topic_title=topic_title,
            topic_url=topic_url,
            status=str(result_payload.get("status", "published")),
            notes=[str(item) for item in result_payload.get("notes", [])],
            external_url=str(result_payload.get("external_url", "")),
        )
        if not result_payload.get("publish_verified", True):
            backend_summary = result_payload.get("backend_response_summary", {})
            backend_success = (
                isinstance(backend_summary, dict)
                and backend_summary.get("err_no") in {0, "0"}
                and bool(backend_summary.get("pgc_id"))
            )
            if backend_success and result_payload.get("status") == "submitted_for_review":
                self.logger.info(
                    "发布验证未通过但后端确认提交成功(pgc_id=%s)，保持 submitted_for_review。title=%s",
                    backend_summary.get("pgc_id"),
                    title[:30],
                )
            else:
                result_payload["status"] = "publish_uncertain"
                record.status = "publish_uncertain"
                self.logger.warning(
                    "发布验证未通过，状态标记为 publish_uncertain，请人工确认。title=%s",
                    title[:30],
                )
        publish_result_path = publish_dir / "publish_result.json"
        result_payload = self._normalize_publish_result_payload(result_payload)
        result_payload["requested_draft_only"] = bool(self.settings.publishing.draft_only)
        result_payload["effective_draft_only"] = bool(effective_draft_only)
        result_payload["first_release_requested"] = requested_first_release
        result_payload["first_release_option_enabled"] = bool(declare_first_release)
        result_payload["first_release_assessment"] = first_release_assessment
        publish_result_path.write_text(
            json.dumps(
                {
                    **result_payload,
                    "preflight_summary": preflight_summary,
                    "precheck_report": precheck_result.get("report_path"),
                    "record": asdict(record),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if result_status != "draft_saved":
            history = self._load_history()
            history.append(record)
            self._save_history(history)

        growth_exports: dict[str, Path] = {}
        if result_status != "draft_saved":
            growth_exports = self.growth_ops_service.register_publish_event(
                platform=self.settings.publishing.platform,
                run_dir=run_dir,
                result_path=publish_result_path,
            )
            try:
                from toutiao_content_factory.services.revenue_attribution_service import (
                    RevenueAttributionService,
                )

                attribution_service = RevenueAttributionService(self.settings.output_dir, self.logger)
                identity = attribution_service.register_published_run(
                    account_key=self.settings.account_key,
                    platform=self.settings.publishing.platform,
                    run_dir=run_dir,
                    publish_dirname=self.settings.review.publish_dirname,
                    title=title,
                    published_at=str(result_payload.get("published_at", "")),
                    topic=topic,
                    result_payload=result_payload,
                )
                growth_exports["content_id"] = identity.content_id
                growth_exports["content_index"] = str(attribution_service.content_index_path)
                growth_exports["cost_ledger"] = str(attribution_service.cost_ledger_path)
            except Exception as exc:  # pragma: no cover - attribution best-effort
                self.logger.warning("Revenue attribution registration failed (non-fatal): %s", exc)
        else:
            self.logger.info("Draft-only result saved without publish history or revenue attribution: %s", run_dir)
        self.logger.info("Publish finished for run: %s", run_dir)
        return {
            "publish_result": publish_result_path,
            "publish_history": self.history_path,
            **growth_exports,
        }

    def initialize_login_session(self) -> dict[str, str]:
        with self._publish_session_lock("login initialization"):
            return self._initialize_login_session_locked()

    def _initialize_login_session_locked(self) -> dict[str, str]:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        target_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="publish:init-session",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run() -> dict[str, str]:
            with sync_playwright() as playwright:
                browser = self._launch_persistent_browser(playwright, profile_dir=profile_dir)
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    diagnostic_events: list[dict[str, Any]] = []
                    self._install_publish_diagnostic_capture(page, diagnostic_events)
                    session_summary = ensure_browser_session_ready(
                        browser_context=browser,
                        page=page,
                        platform="Toutiao",
                        profile_dir=self._resolve_profile_dir(),
                        account_runtime_dir=self.settings.account_runtime_dir,
                        project_root=self.settings.project_root,
                        logger=self.logger,
                        probe_url=target_url,
                        login_detected_fn=self._toutiao_login_page_detected,
                        session_ready_fn=self._toutiao_session_ready,
                        notification_service=self.notification_service,
                        navigate_fn=lambda target_page, url: self._goto_publish_page(target_page, url),
                        goto_timeout_ms=max(self.settings.publishing.wait_timeout_ms, 30000),
                    )
                    if session_summary.get("session_ready"):
                        return {
                            "profile_dir": str(profile_dir),
                            "current_url": page.url,
                            "state": "ready",
                        }
                    print("")
                    print("Browser launched for Toutiao login session initialization.")
                    print(f"Profile dir: {profile_dir}")
                    print("Session is not ready. Notification has been sent to the operator.")
                    print("Log in to your Toutiao creator account in the opened browser window.")
                    print("Make sure the account can actually open the article publish page.")
                    print("This window will keep polling automatically for up to 10 minutes.")
                    print("If login succeeds, navigate to the Toutiao article publish page and wait for the preflight summary.")

                    deadline = datetime.now().timestamp() + 600
                    next_status_print_at = 0.0
                    preflight_summary: dict[str, Any] | None = None

                    while datetime.now().timestamp() < deadline:
                        if page.is_closed():
                            raise RuntimeError("The browser window was closed before login initialization finished.")

                        try:
                            diagnostics = self._build_publish_diagnostics(diagnostic_events)
                            preflight_summary = self._build_preflight_summary_from_diagnostics(page.url, diagnostics)
                        except Exception:
                            preflight_summary = None

                        if preflight_summary and not preflight_summary.get("blocking_flags"):
                            break

                        now_ts = datetime.now().timestamp()
                        if now_ts >= next_status_print_at:
                            current_url = page.url if not page.is_closed() else ""
                            blockers = []
                            if preflight_summary:
                                blockers = [str(item) for item in preflight_summary.get("blocking_flags", [])]
                            status_line = "Waiting for a publish-capable Toutiao session"
                            if current_url:
                                status_line += f". Current URL: {current_url}"
                            if blockers:
                                status_line += f" | blockers: {'; '.join(blockers[:3])}"
                            print(status_line)
                            next_status_print_at = now_ts + 15

                        page.wait_for_timeout(3000)

                    if not preflight_summary or preflight_summary.get("blocking_flags"):
                        export_paths = self._write_preflight_summary(preflight_summary or {})
                        self._append_notification(
                            level="warn",
                            title="Publish login required",
                            message="Toutiao login session initialization timed out before the account became publish-capable.",
                            notification_type="publish_login_required",
                            data={
                                "account_key": self.settings.account_key,
                                "platform": self.settings.publishing.platform,
                                "current_url": page.url,
                                **{name: str(path) for name, path in export_paths.items()},
                            },
                        )
                        raise RuntimeError(
                            "Login session initialization timed out before the account reached a publish-capable state. "
                            f"Current URL: {page.url}. Preflight summary saved to: {export_paths.get('publish_preflight')}"
                        )

                    export_paths = self._write_preflight_summary(preflight_summary)
                    print("")
                    print("Publish preflight summary:")
                    print(json.dumps(preflight_summary, ensure_ascii=False, indent=2))
                    return {
                        "profile_dir": str(profile_dir),
                        "current_url": page.url,
                        **{name: str(path) for name, path in export_paths.items()},
                    }
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=600)

    def collect_preflight_summary(self) -> dict[str, Any]:
        with self._publish_session_lock("preflight collection"):
            return self._collect_preflight_summary_locked()

    def _collect_preflight_summary_locked(self) -> dict[str, Any]:
        if sync_playwright is None:
            return {
                "blocking_flags": ["Playwright is not installed."],
                "warnings": [],
                "diagnostics": {},
            }

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        publish_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="publish:preflight",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run() -> dict[str, Any]:
            with sync_playwright() as playwright:
                browser = self._launch_persistent_browser(playwright, profile_dir=profile_dir)
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    diagnostic_events: list[dict[str, Any]] = []
                    self._install_publish_diagnostic_capture(page, diagnostic_events)
                    self._goto_publish_page(page, publish_url)
                    page.wait_for_timeout(2500)
                    notes: list[str] = []
                    self._dismiss_ai_assistant_drawer(page, notes)
                    self._wait_for_publish_editor_ready(page)
                    self._permanently_hide_ai_assistant(page, notes)
                    diagnostics = self._build_publish_diagnostics(diagnostic_events)
                    summary = self._build_preflight_summary_from_diagnostics(page.url, diagnostics)
                    if summary.get("blocking_flags") and self._page_looks_publish_capable(page):
                        summary["blocking_flags"] = [
                            item
                            for item in list(summary.get("blocking_flags", []))
                            if item != "Current browser session does not appear to have an accessible Toutiao publish page."
                        ]
                        warnings = list(summary.get("warnings", []))
                        warnings.append("Diagnostics API was incomplete, but the publish editor DOM was detected.")
                        summary["warnings"] = self._unique_texts(warnings)
                    return summary
                except Exception as exc:
                    return {
                        "blocking_flags": [f"Publish preflight failed: {self._clean_visible_text(exc)}"],
                        "warnings": [],
                        "diagnostics": {},
                    }
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=120)

    def export_preflight_summary(self) -> dict[str, Path]:
        summary = self.collect_preflight_summary()
        return self._write_preflight_summary(summary)

    def _merge_content_guard_summary(
        self,
        preflight_summary: dict[str, Any],
        content_guard_report: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(preflight_summary or {})
        payload["blocking_flags"] = self._unique_texts(
            list(payload.get("blocking_flags", []))
            + list(content_guard_report.get("blocking_flags", []))
        )
        payload["warnings"] = self._unique_texts(
            list(payload.get("warnings", []))
            + list(content_guard_report.get("warnings", []))
        )
        payload["content_guard"] = content_guard_report
        return payload

    def _append_notification(
        self,
        *,
        level: str,
        title: str,
        message: str,
        notification_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        notifications_path = self.settings.automation_dir / "notifications.json"
        notifications_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self._load_json(notifications_path)
        items: list[dict[str, Any]] = []
        if isinstance(existing, list):
            items = [item for item in existing if isinstance(item, dict)]
        elif isinstance(existing, dict):
            items = [existing]

        entry = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "type": notification_type,
            "title": title,
            "message": message,
            "data": data or {},
        }
        payload = [entry, *items[:49]]
        notifications_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _publish_to_toutiao(
        self,
        title: str,
        article: str,
        draft_only: bool = False,
        run_dir: Path | None = None,
        declare_first_release: bool = True,
    ) -> dict[str, Any]:
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        publish_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL
        selectors = self.settings.publishing.selectors
        notes: list[str] = []

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="publish:run",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run() -> dict[str, Any]:
            with sync_playwright() as playwright:
                browser = self._launch_persistent_browser(playwright, profile_dir=profile_dir)
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    publish_events: list[dict[str, Any]] = []
                    diagnostic_events: list[dict[str, Any]] = []
                    self._install_publish_event_capture(page, publish_events)
                    self._install_publish_diagnostic_capture(page, diagnostic_events)
                    # Arm the request guard before any session/probe navigation. The editor can
                    # emit its first save=0 request while the publish page is still settling.
                    self._install_save_mode_override(page, notes)
                    self._ensure_browser_session_ready(browser, page, publish_url)

                    self._goto_publish_page(page, publish_url)
                    page.wait_for_timeout(1500)

                    self._check_toutiao_login(page, notes)

                    if self.settings.publishing.base_url not in page.url:
                        notes.append(f"Current page redirected to: {page.url}")

                    publish_editor_url = self.settings.publishing.publish_url or self.DEFAULT_PUBLISH_URL
                    if "/profile_v4/graphic/publish" not in page.url:
                        if not self._maybe_click(page, selectors.get("new_post_button", [])):
                            notes.append("New post button not clicked. Falling back to direct publish URL.")
                            self._goto_publish_page(page, publish_editor_url)
                            page.wait_for_timeout(1500)

                    self._dismiss_ai_assistant_drawer(page, notes)
                    self._wait_for_publish_editor_ready(page)
                    self._permanently_hide_ai_assistant(page, notes)
                    # 健壮性增强：进入编辑页后进行一次弹窗阻击扫描
                    # 关闭"新手引导""平台规则更新"等可能遮挡标题/正文输入框的弹窗
                    try:
                        guard = self._make_popup_guard(page)
                        guard.sweep_before(notes, stage="editor_enter")
                    except Exception as exc:
                        self.logger.debug("PopupGuard editor-enter sweep failed (non-blocking): %s", exc)
                    title_selector = self._resolve_editor_selector(
                        page,
                        selectors.get("title_input", []),
                        self.DEFAULT_TITLE_SELECTORS,
                    )
                    editor_selector = self._resolve_editor_selector(
                        page,
                        selectors.get("article_editor", []),
                        self.DEFAULT_EDITOR_SELECTORS,
                    )

                    if (not title_selector or not editor_selector) and page.url != publish_editor_url:
                        notes.append("Editor selectors not found on current page. Retrying publish URL directly.")
                        self._goto_publish_page(page, publish_editor_url)
                        page.wait_for_timeout(1500)
                        self._wait_for_publish_editor_ready(page)
                        title_selector = self._resolve_editor_selector(
                            page,
                            selectors.get("title_input", []),
                            self.DEFAULT_TITLE_SELECTORS,
                        )
                        editor_selector = self._resolve_editor_selector(
                            page,
                            selectors.get("article_editor", []),
                            self.DEFAULT_EDITOR_SELECTORS,
                        )

                    if not title_selector or not editor_selector:
                        diagnostics = self._build_publish_diagnostics(diagnostic_events)
                        debug_dir = self._capture_debug_artifacts(
                            page,
                            extra_payload={
                                "publish_events": publish_events,
                                "publish_diagnostics": diagnostics,
                            },
                        )
                        raise RuntimeError(
                            "Could not locate title/editor selectors. "
                            f"Current URL: {page.url}. Debug files saved to: {debug_dir}"
                        )

                    draft_watch_start = len(publish_events)
                    title_locator = page.locator(title_selector).first
                    editor_locator = page.locator(editor_selector).first
                    # 关键修复：在第一次触发 autosave 之前就安装 save=1 + coverType=1 覆盖路由，
                    # 否则首个 autosave 会以 save=0(发布模式)+coverType=2+空封面发出，
                    # 被头条后端以 7050 保存失败 拒绝，导致草稿永远无法保存、后续发布点击无草稿可提交。
                    # Toutiao currently rejects the editor's initial save=0 request (7050).
                    # The route must be armed for both draft-only and publish flows so the
                    # first persisted payload uses the accepted save mode.
                    self._retry_playwright_action(
                        lambda: self._set_editor_content(page, editor_selector, article, notes),
                        description="set_editor_content",
                    )
                    self._retry_playwright_action(
                        lambda: self._set_title_content(page, title_locator, title, notes),
                        description="set_title_content",
                    )
                    inline_image_paths = self._resolve_toutiao_inline_image_assets(run_dir, notes)
                    inline_image_inserted = 0
                    if inline_image_paths:
                        inline_image_inserted = self._try_apply_article_images(
                            page,
                            editor_selector,
                            article,
                            inline_image_paths,
                            notes,
                        )
                    # Image insertion moves the ProseMirror caret. A click followed by
                    # End can land at the end of a wrapped line and discard the rest
                    # of that paragraph. Stop before save=1 can reach the server.
                    self._assert_editor_text_complete(page.locator(editor_selector).first, article)
                    self._update_save_mode_override_gate(
                        expected_inline_images=inline_image_inserted,
                        expected_refs=(),
                        ready=inline_image_inserted <= 0,
                    )
                    self._nudge_editor_to_trigger_autosave(page, editor_selector, notes)
                    self._apply_default_work_declarations(page, notes)
                    self._apply_safe_publish_options(page, notes, declare_first_release=declare_first_release)
                    self._finalize_editor_state(page, title_locator, editor_selector, title, notes)
                    cover_path = self._resolve_toutiao_cover_asset(run_dir, notes)
                    cover_mode = self._resolve_cover_mode(cover_path) if cover_path is not None else "none"
                    if cover_mode == "three" and cover_path is not None:
                        three_assets = self._resolve_three_cover_assets(run_dir)
                        if len(three_assets) >= 3:
                            self._apply_three_cover(page, three_assets, notes)
                        else:
                            notes.append(
                                "Three-image cover requested but <3 images available; "
                                "falling back to single-image cover."
                            )
                            if self._apply_cover_image(page, cover_path, notes):
                                cover_mode = "single"
                            else:
                                cover_mode = "none"
                    elif cover_mode == "single" and cover_path is not None:
                        if not self._apply_cover_image(page, cover_path, notes):
                            notes.append(
                                "Cover upload did not stick; publishing without a cover."
                            )
                            cover_mode = "none"
                    else:
                        self._ensure_no_cover_mode(page, notes)
                    media_state = self._verify_publish_media_state(
                        page,
                        editor_selector,
                        cover_mode=cover_mode,
                        expected_inline_images=inline_image_inserted,
                    )
                    self._assert_editor_text_complete(page.locator(editor_selector).first, article)
                    self._update_save_mode_override_gate(
                        expected_inline_images=inline_image_inserted,
                        expected_refs=list(media_state.get("refs", [])),
                        ready=bool(media_state.get("verified", False)),
                    )
                    inline_image_summary = {
                        "enabled": bool(getattr(self.settings.publishing, "inline_images_enabled", True)),
                        "expected_count": len(inline_image_paths),
                        "inserted_count": inline_image_inserted,
                        "available_assets": [path.name for path in inline_image_paths],
                        "inserted_assets": [path.name for path in self._last_inline_image_assets],
                        "editor_persistence": media_state,
                    }
                    self._stabilize_publish_form(page, notes)

                    diagnostics = self._build_publish_diagnostics(diagnostic_events)
                    preflight_blockers = self._format_publish_diagnostic_blockers(diagnostics)
                    if preflight_blockers:
                        notes.append(f"Publish preflight: {preflight_blockers.strip()}")

                    if not draft_only:
                        # 覆盖路由已在上方（首次 autosave 前）安装，这里只触发一次 autosave
                        self._nudge_editor_to_trigger_autosave(page, editor_selector, notes)

                    draft_save_confirmed = self._wait_for_draft_save(
                        page,
                        publish_events,
                        notes,
                        start_index=draft_watch_start,
                        expect_save_mode_1=not draft_only,
                    )
                    draft_save_event = self._latest_save_event(
                        publish_events,
                        start_index=draft_watch_start,
                        success_only=True,
                    )
                    draft_media_payload = self._summarize_publish_media_payload(
                        str((draft_save_event or {}).get("post_data", "")),
                        expected_inline_images=inline_image_inserted,
                        expected_refs=list(media_state.get("refs", [])),
                        editor_state_verified=bool(media_state.get("verified", False)),
                    )
                    draft_media_payload["event_captured"] = draft_save_event is not None
                    inline_image_summary["draft_save_payload"] = draft_media_payload
                    draft_response_json = (draft_save_event or {}).get("response_json") or {}
                    draft_response_data = (
                        draft_response_json.get("data")
                        if isinstance(draft_response_json, dict)
                        else {}
                    )
                    if not isinstance(draft_response_data, dict):
                        draft_response_data = {}
                    draft_pgc_id = str(draft_response_data.get("pgc_id", "0") or "0")

                    if not draft_save_confirmed and draft_pgc_id == "0":
                        raise RuntimeError(
                            "Draft autosave was not confirmed. Publish was blocked before submission."
                        )

                    if (
                        inline_image_inserted > 0
                        and draft_pgc_id == "0"
                        and (draft_save_event is None or not draft_media_payload.get("verified"))
                    ):
                        debug_dir = self._capture_debug_artifacts(
                            page,
                            extra_payload={
                                "publish_events": publish_events,
                                "editor_media_state": media_state,
                                "draft_media_payload": draft_media_payload,
                            },
                        )
                        raise RuntimeError(
                            "Publish blocked because the draft-save payload did not prove that all "
                            "inline images were persisted. "
                            f"{draft_media_payload.get('failure_reason') or 'save event missing'}. "
                            f"Debug files saved to: {debug_dir}"
                        )

                    if draft_only:
                        notes.append("Draft-only mode enabled. Stopped after draft autosave confirmation.")
                        return {
                            "status": "draft_saved",
                            "published_at": datetime.now().isoformat(timespec="seconds"),
                            "external_url": page.url,
                            "notes": self._finalize_notes(notes),
                            "cover_mode": cover_mode,
                            "inline_images": inline_image_summary,
                            "publish_diagnostics": self._compact_publish_diagnostics(diagnostics),
                            "backend_response_summary": {},
                            "publish_request_summary": {
                                "url": page.url,
                                "save_mode": "draft_only",
                                "media_payload": draft_media_payload,
                            },
                        }

                    submitted_event = self._latest_publish_event(
                        publish_events,
                        start_index=draft_watch_start,
                        save_mode=1,
                        success_only=False,
                    )
                    if submitted_event:
                        response_json = submitted_event.get("response_json") or {}
                        response_data = (
                            response_json.get("data")
                            if isinstance(response_json, dict)
                            else {}
                        )
                        if not isinstance(response_data, dict):
                            response_data = {}
                        pgc_id = str(response_data.get("pgc_id", "0") or "0")
                        if pgc_id != "0":
                            self._disable_save_mode_override(page, notes)
                            submission_evidence_complete = self._has_accepted_submission_evidence(
                                response_json,
                                pgc_id=pgc_id,
                                expected_inline_images=inline_image_inserted,
                                media_payload=draft_media_payload,
                            )
                            submission_uncertain = not submission_evidence_complete
                            if submission_uncertain:
                                notes.append(
                                    "Toutiao returned a pgc_id before media/save evidence was complete. "
                                    "No further publish attempt was made; result marked publish_uncertain."
                                )
                                publish_verified = False
                                return {
                                    "status": "publish_uncertain",
                                    "published_at": datetime.now().isoformat(timespec="seconds"),
                                    "external_url": page.url,
                                    "notes": self._finalize_notes(notes),
                                    "cover_mode": cover_mode,
                                    "inline_images": inline_image_summary,
                                    "publish_diagnostics": self._compact_publish_diagnostics(diagnostics),
                                    "backend_response_summary": self._compact_backend_response(response_json),
                                    "publish_request_summary": {
                                        "url": str(submitted_event.get("url", page.url)),
                                        "save_mode": "draft_auto_submitted",
                                        "pgc_id": pgc_id,
                                        "media_payload": draft_media_payload,
                                    },
                                    "publish_verified": False,
                                }

                            # ⚠️⚠️ 2026-09-21 关键修复：save=1 的 pgc_id **不等于已发布**。
                            #
                            # 这里原来直接 `return status=submitted_for_review`，
                            # 注释写着「Draft save auto-submitted article for review on
                            # Toutiao. Skipping manual publish click.」—— 那个判断是错的。
                            #
                            # 实测证据（scripts/list_recent_works.py / verify_works_visibility.py
                            # 读头条后台真实口径）：
                            #
                            #     全部 177 条 / 已发布 0 条 / 仅我可见 176 条 / 审核未通过 1 条
                            #
                            #   gid=7687678730147316251 老人去世，房产过户：兄弟姐妹为何反目  → 仅我可见
                            #   gid=7687304540994978358 独居老人摔倒两天，邻居报警破门…        → 仅我可见
                            #   gid=7687264638244831784 养老困境：独居老人摔倒…                → 仅我可见
                            #
                            # 三条「历史发布成功」全是仅我可见。也就是说：**一次都不曾真正发布**，
                            # 而这条捷径正是原因 —— 它把「草稿已持久化（头条对存草稿同样回 pgc_id）」
                            # 当成了「文章已提交」，真正的发布按钮从来没被点过。
                            #
                            # 同一份 notes 里其实早有反证，只是当时没被当成失败信号：
                            #     "Draft footer after no-cover selection: 草稿已保存"
                            #     "Publish verification: article not found in content list.
                            #      May need manual check."
                            #
                            # 现在的口径：save=1 只证明**草稿已落库、编辑器内容可安全提交**，
                            # 它是发布的前置条件，不是发布本身。拿到 pgc_id 后继续往下走，
                            # 去点真正的发布按钮。
                            if draft_save_confirmed:
                                notes.append(
                                    "Draft persisted via save=1 override (pgc_id=%s); "
                                    "proceeding to the real publish action." % pgc_id
                                )
                            else:
                                notes.append(
                                    "Toutiao accepted the save=1 submission with a pgc_id although the "
                                    "draft footer remained in a saving state. Proceeding to the real "
                                    "publish action; the draft payload is already persisted."
                                )

                    def result_from_platform_list(item: dict[str, Any] | None, stage: str) -> dict[str, Any]:
                        is_published = bool(item and item.get("status") == 2)
                        media_verified = inline_image_inserted <= 0 or bool(draft_media_payload.get("verified"))
                        notes.append(
                            f"Platform list reconciliation ({stage}): "
                            f"pgc_id={draft_pgc_id}, status={(item or {}).get('status', 'unknown')}."
                        )
                        return {
                            "status": "submitted_for_review" if is_published and media_verified else "publish_uncertain",
                            "published_at": datetime.now().isoformat(timespec="seconds"),
                            "external_url": page.url,
                            "notes": self._finalize_notes(notes),
                            "cover_mode": cover_mode,
                            "inline_images": inline_image_summary,
                            "publish_diagnostics": self._compact_publish_diagnostics(diagnostics),
                            "backend_response_summary": self._compact_backend_response(draft_response_json),
                            "publish_request_summary": {
                                "url": page.url,
                                "save_mode": stage,
                                "pgc_id": draft_pgc_id,
                                "media_payload": draft_media_payload,
                            },
                            "publish_verified": is_published and media_verified,
                        }

                    if draft_pgc_id != "0":
                        platform_item = self._lookup_toutiao_article_by_id(
                            page, pgc_id=draft_pgc_id, title=title
                        )
                        if platform_item is None or platform_item.get("status") != 4:
                            # A save=1 response alone is ambiguous: it can be a private
                            # draft or an already published item. Click only after the
                            # platform confirms the draft is still private.
                            return result_from_platform_list(platform_item, "before_publish_click")

                    self._dismiss_ai_assistant_drawer(page, notes)
                    page.wait_for_timeout(700)
                    try:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
                    notes.append("Waited briefly before clicking the primary publish action.")
                    # 健壮性增强：点击发布按钮前进行一次弹窗阻击扫描
                    # 防止在用户停留期间弹出的"平台规则更新""新手引导"等弹窗拦截点击
                    try:
                        guard = self._make_popup_guard(page)
                        guard.sweep_before(notes, stage="publish_click")
                    except Exception as exc:
                        self.logger.debug("PopupGuard pre-publish sweep failed (non-blocking): %s", exc)
                    # 健壮性增强：封面上传面板可能残留并遮挡发布按钮。
                    # 若面板仍打开：图片已上传则点"确定"完成封面；否则点"取消"关闭面板。
                    self._close_stray_cover_panel(page, notes)
                    debug_dir = self._capture_debug_artifacts(page, extra_payload={"notes": notes})
                    notes.append(f"Debug snapshot saved to {debug_dir}")
                    preview_start_index = len(publish_events)
                    if not self._click_immediate_publish_button(
                        page,
                        selectors.get("publish_button", []),
                        notes,
                    ):
                        debug_dir2 = self._capture_debug_artifacts(page, extra_payload={"notes": notes, "button_click_notes": notes[-20:]})
                        raise RuntimeError(f"Publish button not found. Update selectors in config.yaml. Debug: {debug_dir2}")

                    # ⚠️ 2026-09-25：发布按钮一旦点下去，文章就可能已经到达服务端。
                    # 从这一刻起禁止任何重试 —— 后面若因为「没捕获到提交响应」而抛异常，
                    # 外层重试会再点一次按钮，把同一篇文章提交多次。
                    # 线上实测：同一标题 3 条，正是 1 + max_publish_retries 次提交。
                    # 详见 docs/重复发布根因_20260925.md
                    self._publish_button_clicked_this_article = True

                    page.wait_for_timeout(600)
                    publish_start_index = self._complete_publish_confirmation_flow(
                        page,
                        publish_events,
                        notes,
                        fallback_start_index=preview_start_index,
                    )

                    try:
                        publish_event = self._wait_for_publish_submit(page, publish_events, publish_start_index)
                    except RuntimeError:
                        # The response listener can miss a successful submission.
                        # Resolve by exact id and title before reporting failure.
                        platform_item = self._lookup_toutiao_article_by_id(
                            page, pgc_id=draft_pgc_id, title=title
                        ) if draft_pgc_id != "0" else None
                        return result_from_platform_list(platform_item, "after_missing_response")
                    response_payload = publish_event.get("response_json")
                    if not self._publish_response_is_success(response_payload):
                        platform_item = self._lookup_toutiao_article_by_id(
                            page, pgc_id=draft_pgc_id, title=title
                        ) if draft_pgc_id != "0" else None
                        if platform_item and platform_item.get("status") == 2:
                            return result_from_platform_list(platform_item, "after_rejected_response")
                        diagnostics = self._build_publish_diagnostics(diagnostic_events)
                        debug_dir = self._capture_debug_artifacts(
                            page,
                            extra_payload={
                                "publish_events": publish_events,
                                "failed_publish_event": publish_event,
                                "publish_diagnostics": diagnostics,
                            },
                        )
                        code, message = self._extract_publish_error(response_payload)
                        blocker_text = self._format_publish_diagnostic_blockers(diagnostics)
                        raise RuntimeError(
                            f"Toutiao publish rejected the submission. code={code}, message={message}. "
                            f"{blocker_text}"
                            f"Debug files saved to: {debug_dir}"
                        )

                    final_media_payload = self._summarize_publish_media_payload(
                        str(publish_event.get("post_data", "")),
                        expected_inline_images=inline_image_inserted,
                        expected_refs=list(media_state.get("refs", [])),
                        editor_state_verified=bool(media_state.get("verified", False)),
                    )
                    final_media_payload["event_captured"] = True
                    inline_image_summary["final_publish_payload"] = final_media_payload
                    media_payload_verified = bool(final_media_payload.get("verified"))
                    if inline_image_inserted > 0 and not media_payload_verified:
                        notes.append(
                            "Toutiao accepted the final request, but its payload did not prove that "
                            "all inline images were persisted. Marking the result publish_uncertain."
                        )

                    success_selector = self._wait_for_any(page, selectors.get("success_indicator", []))
                    if success_selector:
                        notes.append(f"Detected success indicator: {success_selector}")
                    else:
                        notes.append("Backend accepted the publish request, but no explicit success toast was detected.")

                    publish_verified = self._verify_toutiao_publish_success(page, title, notes)

                    return {
                        "status": "submitted_for_review" if media_payload_verified else "publish_uncertain",
                        "published_at": datetime.now().isoformat(timespec="seconds"),
                        "external_url": page.url,
                        "notes": self._finalize_notes(notes),
                        "cover_mode": cover_mode,
                        "inline_images": inline_image_summary,
                        "publish_diagnostics": self._compact_publish_diagnostics(diagnostics),
                        "backend_response_summary": self._compact_backend_response(response_payload),
                        "publish_request_summary": {
                            "url": str(publish_event.get("url", "")),
                            "save_mode": publish_event.get("save_mode"),
                            "media_payload": final_media_payload,
                        },
                        "publish_verified": publish_verified and media_payload_verified,
                    }
                except PlatformUIUpdateException as pue:
                    self.logger.error(
                        "PlatformUIUpdateException: %s - %s",
                        pue.element_desc, pue.message
                    )
                    debug_url = pue.debug_screenshot or pue.debug_dom_path or ""
                    self._notify_ui_alert(
                        f"[URGENT] UI Change - {pue.platform}",
                        f"Element: {pue.element_desc}\n"
                        f"Attempts: {len(pue.attempted_selectors)} strategies failed"
                    )
                    raise
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=600)

    def _retry_playwright_action(self, action_fn, description: str, max_retries: int = 2) -> None:
        """执行 RPA 操作，带指数退避重试与异常分级

        健壮性增强说明（保留原签名，向后兼容）：
        - 原实现：固定 2s 间隔重试，不区分异常类型
        - 新实现：
          1) 使用 RetryPolicy 指数退避（1s→2s→4s，含 30% 抖动）
          2) 通过 classify_exception 区分可重试/阻断性异常
          3) 登录过期、账号封禁等阻断性异常立即抛出，不浪费重试
          4) max_retries 参数保留向后兼容，映射到 RetryPolicy.max_attempts
        """
        policy = RetryPolicy(
            max_attempts=1 + max_retries,
            base_delay=1.0,
            max_delay=15.0,
            multiplier=2.0,
            jitter=0.3,
            retryable_only=True,
        )
        retry_with_backoff(
            action_fn,
            description=description,
            policy=policy,
            logger=self.logger,
        )

    def _build_weitoutiao_content(self, article: str, publish_dir: Path) -> str:
        """构建微头条引流短内容：开头观点 + 核心结论 + 互动问题 + 关注引导 + 话题标签。

        微头条在头条信息流中以"短观点 + 话题标签"形态获得推荐与涨粉，
        不宜直接粘贴整篇长文。策略（2026-08-26 涨粉优化）：
        1. 取正文前 2 段（核心观点）
        2. 若正文有结尾互动问题则附加
        3. 追加"关注引导"（提升粉丝转化，粉丝阅读 CPM 是普通阅读 3-6 倍）
        4. 补 2-4 个 #话题标签（来自 tags.txt / 文章关键词）
        """
        try:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", article) if p.strip()]
            if not paragraphs:
                return ""
            parts: list[str] = []
            # 开头 1-2 段（观点主体）
            for para in paragraphs[:2]:
                clean = self._clean_visible_text(para)
                if clean:
                    parts.append(clean)
                if sum(len(p) for p in parts) >= 240:
                    break
            # 结尾互动问题（找含"？/?"的句子）
            tail = "\n".join(paragraphs[-2:])
            question = re.search(r"[^。！？\n]*[？?][^。！？\n]*", tail)
            if question and len(question.group(0)) <= 60:
                parts.append(question.group(0).strip())
            # 关注引导（涨粉核心：粉丝阅读 CPM 3-6 倍）
            # 2026-08-27：从"机器人专属"改为体现账号定位的通用话术，
            # 与 config.yaml industry.positioning 一致。
            positioning = str(getattr(self.settings, "brand_positioning", "") or "").strip()
            if positioning:
                parts.append(f"关注我。{positioning}")
            else:
                parts.append("关注我，每天拆解一个机器人产业链真相，带你读懂人形机器人怎么落地赚钱。")
            # 话题标签
            tags: list[str] = []
            tags_path = publish_dir / "tags.txt"
            if tags_path.exists():
                for line in self._read_text(tags_path).splitlines()[:4]:
                    tag = self._clean_visible_text(line).strip()
                    if tag and not tag.startswith("#"):
                        tags.append(f"#{tag}")
            if not tags:
                for kw in ("机器人", "具身智能", "AI", "科技"):
                    tags.append(f"#{kw}")
            body = "\n\n".join(parts).strip()
            if body and tags:
                body = body + "\n\n" + " ".join(tags[:4])
            return body[:1200]
        except Exception as exc:
            self.logger.debug("WeiToutiao content build failed (fallback to article): %s", exc)
            return ""

    def publish_weitoutiao(
        self,
        run_dir: Path,
        *,
        draft_only: bool = False,
        content_override: str | None = None,
    ) -> dict[str, Any]:
        """发布微头条（短内容）。

        微头条发布页与图文发布页不同：无标题栏，仅正文内容区（+可选图片）。
        复用持久化登录会话与弹窗守卫基础设施，仅替换页面/选择器。

        content_override: 独立微头条直接传入整条短内容（不再从 article.md 截取），
        用于「独立微头条选题+写作」高频化场景。
        """
        if sync_playwright is None:
            raise RuntimeError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )
        if not self.settings.publishing.weitoutiao_enabled:
            raise RuntimeError(
                "WeiToutiao publishing is disabled. Set TOUTIAO_WEITOUTIAO_ENABLED=1 or weitoutiao_enabled: true."
            )

        publish_dir = run_dir / self.settings.review.publish_dirname
        if not publish_dir.exists():
            raise FileNotFoundError(f"Publish package not found: {publish_dir}")
        publish_meta = self._load_json(publish_dir / "publish_meta.json") or {}
        article = self._sanitize_article_for_publish(self._read_text(publish_dir / "article.md").strip())
        if len(article.replace("\n", "").replace(" ", "")) < 200:
            raise RuntimeError("WeiToutiao content is too short after sanitization (minimum 200 chars).")

        # 微头条专用短内容：取文章开头 + 结尾互动问题 + 话题标签，
        # 避免把整篇 1500 字长文直接塞进微头条（信息流里微头条以短观点+标签引流效果最好）。
        # 独立微头条场景传入 content_override，直接使用已写好的短内容，不再截取。
        if content_override and len(content_override.replace("\n", "").replace(" ", "")) >= 200:
            wtt_content = self._sanitize_article_for_publish(content_override.strip())
        else:
            wtt_content = self._build_weitoutiao_content(article, publish_dir)
            if not wtt_content:
                wtt_content = article

        profile_dir = self._resolve_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        publish_url = (
            self.settings.publishing.weitoutiao_publish_url
            or "https://mp.toutiao.com/profile_v4/weitoutiao/publish"
        )
        wtt_selectors = self.settings.publishing.weitoutiao_selectors or {}
        editor_selectors = wtt_selectors.get("editor") or TOUTIAO_WEITOUTIAO_PAGE.get("editor")
        publish_button_selectors = wtt_selectors.get("publish_button") or TOUTIAO_WEITOUTIAO_PAGE.get("publish_button")
        success_selectors = wtt_selectors.get("success_indicator") or TOUTIAO_WEITOUTIAO_PAGE.get("success_indicator")
        notes: list[str] = []
        image_path = None
        if self.settings.publishing.inline_images_enabled:
            try:
                image_path = ImageGenerationService(self.settings, self.logger).generate_weitoutiao_image(run_dir, wtt_content)
            except Exception as exc:
                notes.append(f"WeiToutiao image rendering unavailable ({type(exc).__name__}); retaining text-only post.")

        prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label="publish:weitoutiao",
            extra_paths=[self.settings.account_runtime_dir],
        )

        def _run() -> dict[str, Any]:
            with sync_playwright() as playwright:
                browser = self._launch_persistent_browser(playwright, profile_dir=profile_dir)
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    self._ensure_browser_session_ready(browser, page, publish_url)
                    publish_events: list[dict[str, Any]] = []
                    self._install_publish_event_capture(
                        page,
                        publish_events,
                        patterns=("/mp/agw/",),
                        post_only=True,
                    )
                    self._goto_publish_page(page, publish_url)
                    page.wait_for_timeout(1500)
                    self._check_toutiao_login(page, notes)
                    self._dismiss_ai_assistant_drawer(page, notes)

                    editor_selector = self._resolve_editor_selector(page, editor_selectors, editor_selectors)
                    if not editor_selector:
                        debug_dir = self._capture_debug_artifacts(
                            page, extra_payload={"notes": notes, "step": "weitoutiao_editor_locate"}
                        )
                        raise RuntimeError(
                            "Could not locate WeiToutiao editor. "
                            f"Current URL: {page.url}. Debug files saved to: {debug_dir}"
                        )

                    self._retry_playwright_action(
                        lambda: self._set_editor_content(page, editor_selector, wtt_content, notes),
                        description="set_weitoutiao_editor_content",
                    )
                    page.wait_for_timeout(600)

                    image_summary = self._try_apply_weitoutiao_image(page, image_path, notes)
                    self._assert_editor_text_complete(page.locator(editor_selector).first, wtt_content)

                    if draft_only:
                        notes.append("Draft-only mode: WeiToutiao content filled, publish click skipped.")
                        return {
                            "status": "draft_saved",
                            "published_at": datetime.now().isoformat(timespec="seconds"),
                            "external_url": page.url,
                            "notes": self._finalize_notes(notes),
                            "platform": "toutiao_weitoutiao",
                            "images": image_summary,
                        }

                    try:
                        guard = self._make_popup_guard(page)
                        guard.sweep_before(notes, stage="weitoutiao_publish_click")
                    except Exception as exc:
                        self.logger.debug("PopupGuard weitoutiao sweep failed (non-blocking): %s", exc)

                    publish_start_index = len(publish_events)
                    if not self._click_immediate_publish_button(page, publish_button_selectors, notes):
                        debug_dir = self._capture_debug_artifacts(
                            page, extra_payload={"notes": notes, "step": "weitoutiao_publish_button"}
                        )
                        raise RuntimeError(
                            "WeiToutiao publish button not found. "
                            f"Current URL: {page.url}. Debug files saved to: {debug_dir}"
                        )

                    page.wait_for_timeout(1200)
                    success_selector = self._wait_for_any(page, success_selectors)
                    if success_selector:
                        notes.append(f"Detected WeiToutiao success indicator: {success_selector}")
                    else:
                        notes.append("WeiToutiao publish completed without an explicit success toast.")

                    publish_event = self._latest_weitoutiao_publish_event(
                        publish_events,
                        start_index=publish_start_index,
                    )
                    response_json = (publish_event or {}).get("response_json")
                    response_is_html = bool((publish_event or {}).get("response_is_html"))
                    image_summary["request_verified"] = self._weitoutiao_image_request_matches(publish_event, image_summary)
                    backend_confirmed = bool(
                        publish_event
                        and not response_is_html
                        and self._publish_response_is_success(response_json)
                        and image_summary["request_verified"]
                    )
                    if backend_confirmed:
                        notes.append("WeiToutiao backend returned an explicit success response.")
                    elif not image_summary["request_verified"]:
                        notes.append("WeiToutiao submit image payload did not match the uploaded image; result marked publish_uncertain.")
                    elif response_is_html:
                        notes.append(
                            "WeiToutiao publish response was an HTML page, not an API response; "
                            "result marked publish_uncertain."
                        )
                    elif publish_event and isinstance(response_json, dict):
                        code, message = self._extract_publish_error(response_json)
                        notes.append(
                            f"WeiToutiao backend did not confirm publish: code={code}, message={message}."
                        )
                    elif publish_event:
                        notes.append(
                            "WeiToutiao publish response was not valid JSON; result marked publish_uncertain."
                        )
                    else:
                        notes.append(
                            "WeiToutiao showed no verifiable publish API response; "
                            "result marked publish_uncertain."
                        )

                    if backend_confirmed:
                        result_status = "submitted_for_review"
                    elif publish_event and response_json is not None and not response_is_html and image_summary["request_verified"]:
                        result_status = "failed"
                    else:
                        result_status = "publish_uncertain"

                    result_payload = {
                        "status": result_status,
                        "published_at": datetime.now().isoformat(timespec="seconds"),
                        "external_url": page.url,
                        "notes": self._finalize_notes(notes),
                        "platform": "toutiao_weitoutiao",
                        "images": image_summary,
                        "backend_response_summary": self._compact_backend_response(response_json),
                        "publish_request_summary": {
                            "url": (publish_event or {}).get("url", ""),
                            "method": (publish_event or {}).get("method", ""),
                            "http_status": (publish_event or {}).get("status", ""),
                            "event_captured": publish_event is not None,
                            "response_is_html": response_is_html,
                        },
                    }
                    # 微头条发布记录写入 publish_history（供日报统计微头条引流效果）
                    if result_status in self.SUCCESS_HISTORY_STATUSES:
                        try:
                            from toutiao_content_factory.models import PublishRecord

                            topic = publish_meta.get("selected_topic") or {}
                            history_title = str(publish_meta.get("selected_title", "")).strip()
                            if looks_like_html_document(history_title):
                                history_title = ""
                                notes.append(
                                    "Ignored an HTML-like WeiToutiao title while saving publish history."
                                )
                            if not history_title:
                                history_title = str(topic.get("title", "")).strip() or self._load_topic_title(publish_dir)
                            if looks_like_html_document(history_title):
                                history_title = "WeiToutiao"
                            wtt_record = PublishRecord(
                                published_at=str(result_payload["published_at"]),
                                platform="toutiao_weitoutiao",
                                title=history_title,
                                run_dir=str(run_dir),
                                topic_title=str(topic.get("title", "")),
                                topic_url=str(topic.get("url", "")),
                                status=str(result_payload["status"]),
                                notes=[str(item) for item in result_payload.get("notes", [])],
                                external_url=str(result_payload.get("external_url", "")),
                            )
                            history = self._load_history()
                            history.append(wtt_record)
                            self._save_history(history)
                            self.logger.info(
                                "WeiToutiao record saved to publish_history: %s",
                                wtt_record.title[:30],
                            )
                        except Exception as exc:
                            self.logger.warning("WeiToutiao history save failed (non-fatal): %s", exc)

                    return result_payload
                finally:
                    browser.close()

        return run_sync_playwright_safe(_run, timeout=600)

    @staticmethod
    def _latest_weitoutiao_publish_event(
        publish_events: list[dict[str, Any]],
        *,
        start_index: int,
    ) -> dict[str, Any] | None:
        """Return the latest API mutation observed after the micro-post click."""
        for event in reversed(publish_events[start_index:]):
            path = urlsplit(str(event.get("url", ""))).path.lower()
            if (str(event.get("method", "")).upper() in {"POST", "PUT", "PATCH"}
                    and re.search(r"(?:publish|post|create)(?:/|$)", path)
                    and not re.search(r"upload|image|picture|photo|draft", path)):
                return event
        return None

    @staticmethod
    def _weitoutiao_image_references(value: Any) -> list[str]:
        """Read durable image references within an upload/image-list payload."""
        found: list[str] = []
        def visit(node, depth=0):
            if depth > 12:
                return
            if isinstance(node, dict):
                for key, item in node.items():
                    if key in {"uri", "web_uri", "image_uri", "image_url", "url", "origin_uri"} and isinstance(item, str):
                        reference = _persistent_image_reference(item, is_web_uri=key in {"uri", "web_uri", "image_uri", "origin_uri"})
                        if reference and reference not in found:
                            found.append(reference)
                    elif isinstance(item, (dict, list)):
                        visit(item, depth + 1)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, str):
                        reference = _persistent_image_reference(item)
                        if reference and reference not in found:
                            found.append(reference)
                    else:
                        visit(item, depth + 1)
        visit(value)
        return found

    @classmethod
    def _weitoutiao_request_image_references(cls, post_data: str) -> list[str]:
        try:
            fields = json.loads(post_data)
        except (ValueError, TypeError):
            fields = {key: values[-1] for key, values in parse_qs(post_data or "").items()}
        if not isinstance(fields, dict):
            return []
        references: list[str] = []
        for key in ("image_list", "images", "pic_list", "image_info", "image_infos"):
            images = fields.get(key)
            if isinstance(images, str):
                try:
                    images = json.loads(images)
                except ValueError:
                    continue
            references.extend(cls._weitoutiao_image_references(images))
        return list(dict.fromkeys(references))

    @staticmethod
    def _weitoutiao_reference_key(value: str) -> str:
        # A CDN preview URL and its stored URI differ only by host/query.
        path = urlsplit(value).path.strip("/") if "://" in value else value.strip("/")
        return path.split("~", 1)[0]

    @classmethod
    def _weitoutiao_image_request_matches(cls, event: dict[str, Any] | None, images: dict[str, Any]) -> bool:
        actual = {cls._weitoutiao_reference_key(ref) for ref in cls._weitoutiao_request_image_references((event or {}).get("post_data", ""))}
        if not images.get("inserted_count"):
            return not actual
        expected = {cls._weitoutiao_reference_key(ref) for ref in images.get("references", [])}
        return bool(len(actual) == 1 and expected and actual.intersection(expected))

    @staticmethod
    def _weitoutiao_image_state(page) -> dict[str, Any]:
        return page.evaluate("""() => {
          const root = document.querySelector('.upload-box');
          if (!root) return {count: 0, loading: false, error: false};
          return {count: root.querySelectorAll('img').length,
            loading: !!root.querySelector('[class*=loading], [class*=progress]'),
            error: !!root.querySelector('[class*=error], [class*=fail]')};
        }""")

    def _try_apply_weitoutiao_image(self, page, image_path: Path | None, notes: list[str]) -> dict[str, Any]:
        """Upload once via the normal UI; never publish with unresolved attachments."""
        initial = self._weitoutiao_image_state(page)
        if initial.get("count") or initial.get("loading") or initial.get("error"):
            raise RuntimeError("WeiToutiao editor has existing or unresolved image attachments; manual review required.")
        summary = {"enabled": bool(self.settings.publishing.inline_images_enabled),
                   "inserted_count": 0, "references": [], "status": "text_only", "request_verified": False}
        if image_path is None:
            return summary
        uploads: list[dict[str, Any]] = []
        def capture_upload(response):
            if response.request.method != "POST" or not re.search(r"upload|image|picture|photo", urlsplit(response.url).path, re.I):
                return
            try:
                payload = response.json()
                references = self._weitoutiao_image_references(payload.get("data", payload) if isinstance(payload, dict) else payload)
                if 200 <= response.status < 300 and self._publish_response_is_success(payload) and references:
                    uploads.append({"references": references})
            except Exception:
                return
        page.on("response", capture_upload)
        file_sent = False
        confirmed_selection = False
        def first_existing(selectors):
            for selector in selectors:
                locator = page.locator(selector)
                if locator.count() == 1:
                    return locator.first
            return None
        try:
            selectors = self.settings.publishing.weitoutiao_selectors or {}
            input_selector = selectors.get("image_file_input") or ["#upload-drag-input", ".btn-upload-handle input[type='file'][accept*='image']", "input[type='file'][accept*='image']"]
            target = first_existing(input_selector)
            if target is not None:
                file_sent = True
                target.set_input_files(str(image_path), timeout=10000)
            else:
                button_selector = selectors.get("image_button") or [".weitoutiao-image-plugin"]
                button = first_existing(button_selector)
                if button is None:
                    notes.append("WeiToutiao image upload control unavailable; retaining text-only post.")
                    return summary
                try:
                    with page.expect_file_chooser(timeout=5000) as chooser:
                        button.click(timeout=5000)
                    file_sent = True
                    chooser.value.set_files(str(image_path), timeout=10000)
                except PlaywrightTimeoutError:
                    target = first_existing(input_selector)
                    if target is None:
                        raise RuntimeError("WeiToutiao image picker did not expose a file input.")
                    file_sent = True
                    target.set_input_files(str(image_path), timeout=10000)
            for _ in range(40):
                page.wait_for_timeout(750)
                state = self._weitoutiao_image_state(page)
                if state.get("error"):
                    break
                if state.get("count") == 1 and not state.get("loading") and uploads:
                    summary.update(inserted_count=1, references=uploads[-1]["references"], status="uploaded")
                    notes.append("WeiToutiao original information card uploaded; server receipt and preview confirmed.")
                    return summary
                if uploads and not state.get("count") and not confirmed_selection:
                    confirm = first_existing(["[data-e2e='imageUploadConfirm-btn']",
                        ".upload-image-panel .footer .byte-btn-primary", ".upload-image-panel button:has-text('确定')"])
                    if confirm is not None and confirm.is_visible():
                        if not confirm.is_enabled():
                            thumbnail = first_existing([".upload-image-wrapper img"])
                            if thumbnail is not None:
                                thumbnail.click(timeout=1500)
                        if confirm.is_enabled():
                            confirm.click(timeout=5000)
                            confirmed_selection = True
            raise RuntimeError("WeiToutiao image upload has no complete server/preview evidence; publishing stopped.")
        except Exception:
            state = self._weitoutiao_image_state(page)
            if file_sent or state.get("count") or state.get("loading") or state.get("error"):
                raise
            notes.append("WeiToutiao image picker unavailable before file transfer; retaining text-only post.")
            return summary
        finally:
            page.remove_listener("response", capture_upload)

    def _load_topic_title(self, publish_dir: Path) -> str:
        """从 publish_meta.json 读取选题标题（微头条记录用）。"""
        try:
            meta = self._load_json(publish_dir / "publish_meta.json") or {}
            topic = meta.get("selected_topic") or {}
            return str(topic.get("title", "")).strip()
        except Exception:
            return ""

    def _resolve_profile_dir(self) -> Path:
        path = Path(self.settings.publishing.user_data_dir)
        if not path.is_absolute():
            path = self.settings.project_root / path
        return path

    def _resolve_toutiao_inline_image_assets(
        self,
        run_dir: Path | None,
        notes: list[str],
    ) -> list[Path]:
        """Resolve generated infographic files that can be inserted in the body."""
        self._last_inline_image_assets = []
        if not bool(getattr(self.settings.publishing, "inline_images_enabled", True)):
            notes.append("Toutiao inline article images disabled by configuration.")
            return []
        if run_dir is None:
            notes.append("Skipped inline article images because no run directory was provided.")
            return []

        publish_images_dir = run_dir / self.settings.review.publish_dirname / "images"
        run_images_dir = run_dir / "images"
        image_report = self._load_json(publish_images_dir.parent / "image_generation_result.json") or {}
        allowed_names = None
        if image_report.get("body_image_fingerprint"):
            meta = self._load_json(publish_images_dir.parent / "publish_meta.json") or {}
            title = str(meta.get("selected_title", "")).strip() or "科技行业观察"
            article = self._read_text(publish_images_dir.parent / "article.md")
            if image_report["body_image_fingerprint"] != article_image_fingerprint(title, article):
                notes.append("Skipped stale body images: the reviewed title or article changed after image generation.")
                return []
            allowed_names = {Path(str(path)).name for path in image_report.get("publish_artifacts", {}).get("infographics", [])}
        assets: list[Path] = []
        for filename in ("infographic_1.png", "infographic_2.png"):
            if allowed_names is not None and filename not in allowed_names:
                continue
            for root in (publish_images_dir, run_images_dir):
                candidate = root / filename
                if candidate.is_file() and candidate.stat().st_size > 0:
                    assets.append(candidate)
                    break

        if assets:
            notes.append(
                f"Resolved {len(assets)} Toutiao inline article image asset(s): "
                + ", ".join(path.name for path in assets)
            )
        else:
            notes.append("No generated infographic assets found; publishing without inline article images.")
        return assets

    def _resolve_toutiao_cover_asset(self, run_dir: Path | None, notes: list[str]) -> Path | None:
        if run_dir is None:
            notes.append("Skipped Toutiao cover upload because no run directory was provided.")
            return None

        image_result = self._load_json(
            run_dir / self.settings.review.publish_dirname / "image_generation_result.json"
        )
        backend = str(image_result.get("backend", "")).strip().lower() if image_result else ""
        used_fallback = bool(image_result.get("used_fallback", False)) if image_result else False

        # 优先复用已生成的 cover.png（不论 backend 来源）
        cover_path = self._resolve_existing_publish_asset(
            run_dir=run_dir,
            publish_dir_name="images",
            filename="cover.png",
        )

        # 兜底：image_generation_result.json 缺失或 cover.png 缺失时，调用本地 pillow 生成
        if cover_path is None or not cover_path.exists():
            try:
                publish_meta = self._load_json(
                    run_dir / self.settings.review.publish_dirname / "publish_meta.json"
                )
                cover_title = str(publish_meta.get("selected_title", "")).strip() or "科技行业观察"
                fallback_dir = run_dir / self.settings.review.publish_dirname / "images"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                fallback_path = fallback_dir / "cover.png"
                generate_text_cover(cover_title, str(fallback_path))
                if fallback_path.exists():
                    cover_path = fallback_path
                    notes.append(
                        "Cover generated via local pillow fallback (image_generation_result.json "
                        "or cover.png was missing). Fallback cover is upload-safe."
                    )
                else:
                    notes.append("Local pillow fallback failed to produce cover.png.")
                    return None
            except Exception as exc:
                notes.append(f"Local pillow fallback cover generation failed: {exc}")
                return None
        elif used_fallback:
            # pillow/renderer 生成的封面是合法可上传的，不再跳过
            notes.append(
                f"Using fallback-generated cover (backend={backend or 'pillow'}). "
                "Fallback covers are upload-safe per fix-2032."
            )
        elif not image_result:
            notes.append(
                "image_generation_result.json missing but cover.png exists; uploading existing cover directly."
            )

        if cover_path is None or not cover_path.exists():
            notes.append("Skipped Toutiao cover upload because no cover could be resolved or generated.")
            return None

        notes.append(
            f"Toutiao cover resolved (backend={backend or 'local'}, path={cover_path.name})."
        )
        return cover_path

    def _clip_title(self, title: str) -> str:
        title = " ".join(self._sanitize_publish_text(title).split()).strip()
        limit = max(1, int(self.settings.publishing.title_max_length))
        if len(title) <= limit:
            return title
        # 按标点语义边界回溯，避免截出 "……真正值得关" 这类半句标题。
        return fit_title_to_limit(title, limit)

    def _goto_publish_page(self, page, url: str) -> None:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 30000)
        attempts = 3
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
                except Exception:
                    pass
                return
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                page.wait_for_timeout(1200 * attempt)
        if last_error is not None:
            raise last_error

    def _check_toutiao_login(self, page, notes: list[str]) -> None:
        current_url = str(page.url or "")
        login_indicators = [
            "/login",
            "/passport",
            "/auth/login",
            "sso.toutiao.com",
        ]
        for indicator in login_indicators:
            if indicator in current_url:
                debug_dir = self._capture_debug_artifacts(page, {"step": "login_check"})
                raise RuntimeError(
                    f"Toutiao session expired or not logged in. Current URL: {current_url}. "
                    f"Please re-run the login initialization script. Debug files saved to: {debug_dir}"
                )
        is_login_page = page.evaluate("""() => {
            const selectors = [
                'input[name="account"]',
                'input[type="password"]',
                '.login-container',
                '[class*="login-form"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) return true;
                }
            }
            // 单独检查登录按钮文本（不能用 :has-text 伪选择器）
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                if (btn.textContent.trim() === '登录') {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) return true;
                }
            }
            return false;
        }""")
        if is_login_page:
            debug_dir = self._capture_debug_artifacts(page, {"step": "login_check"})
            raise RuntimeError(
                f"Toutiao login form detected on page. Session may have expired. "
                f"Please re-run the login initialization script. Debug files saved to: {debug_dir}"
            )

    def _validate_automatic_creative_gate(self, publish_dir: Path) -> None:
        """Require measured title and cover quality for unattended publishing."""
        score_path = publish_dir / "title_cover_score.json"
        payload = self._load_json(score_path)
        title = payload.get("title") if isinstance(payload.get("title"), dict) else {}
        title_score = int(title.get("score", 0) or 0)
        if title_score < self.AUTO_TITLE_SCORE_MIN:
            raise RuntimeError(
                "Automatic creative gate blocked: title score "
                f"{title_score} is below {self.AUTO_TITLE_SCORE_MIN}."
            )

        cover_mode = str(self.settings.publishing.cover_mode or "auto").strip().lower()
        if cover_mode == "none":
            return

        cover = payload.get("cover") if isinstance(payload.get("cover"), dict) else {}
        cover_score = int(cover.get("score", 0) or 0)
        combined_score = int(payload.get("combined_score", 0) or 0)
        if not cover or str(cover.get("status", "")).strip().lower() == "missing":
            raise RuntimeError("Automatic creative gate blocked: generated cover score is missing.")
        if cover_score < self.AUTO_COVER_SCORE_MIN:
            raise RuntimeError(
                "Automatic creative gate blocked: cover score "
                f"{cover_score} is below {self.AUTO_COVER_SCORE_MIN}."
            )
        if combined_score < self.AUTO_CREATIVE_COMBINED_MIN:
            raise RuntimeError(
                "Automatic creative gate blocked: combined title/cover score "
                f"{combined_score} is below {self.AUTO_CREATIVE_COMBINED_MIN}."
            )

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _resolve_existing_publish_asset(
        self,
        *,
        run_dir: Path | None,
        publish_dir_name: str,
        filename: str,
    ) -> Path | None:
        if run_dir is not None:
            candidate = run_dir / self.settings.review.publish_dirname / publish_dir_name / filename
            if candidate.exists():
                return candidate
        return None

    def _write_preflight_summary(self, summary: dict[str, Any]) -> dict[str, Path]:
        automation_dir = self.settings.automation_dir
        automation_dir.mkdir(parents=True, exist_ok=True)
        summary_path = automation_dir / "publish_preflight.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"publish_preflight": summary_path}

    def _read_text(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _sanitize_article_for_publish(self, article: str) -> str:
        if looks_like_html_document(article):
            raise RuntimeError(
                "Publish content was an HTML document instead of article text."
            )
        cleaned = clean_article_text(article)
        # Keep the public-platform boundary explicit: older review packages
        # may have been generated before the shared sanitizer learned these
        # source byline and account patterns.
        cleaned = strip_external_source_metadata(cleaned)
        # 头条为公域平台，剥离 MonetizationService 注入的私域引流 CTA 段落
        # （CTA 仅保留给微信等私域渠道）
        cleaned = self._strip_cta_for_toutiao(cleaned)
        sanitized_lines: list[str] = []
        for raw_line in cleaned.splitlines():
            line = self._sanitize_publish_text(raw_line.strip())
            if not line and sanitized_lines and sanitized_lines[-1] != "":
                sanitized_lines.append("")
                continue
            if line:
                sanitized_lines.append(line)
        return "\n".join(sanitized_lines).strip()

    def _strip_cta_for_toutiao(self, article: str) -> str:
        """剥离 CTA 段落（## 延伸服务 及其后全部内容）。

        注意:cta_markers 必须同时包含带 ## 前缀和不带前缀的版本,
        因为 clean_article_text 会剥离行首的 ## 前缀,导致带前缀的标记失效。
        另外加入内容型标记作为兜底,确保 CTA 段落被拦截。
        """
        cta_markers = (
            # 带 ## 前缀(clean_article_text 调用前的原始格式)
            "## 延伸服务",
            "## 延伸阅读",
            "## 进一步交流",
            # 不带 ## 前缀(clean_article_text 调用后被改写的格式)
            "延伸服务",
            "延伸阅读",
            "进一步交流",
            # 内容型标记(段落首句,作为兜底)
            "如果你想把信息差变成结果",
            "做内容的目的不是多发几篇",
        )
        for marker in cta_markers:
            idx = article.find(marker)
            if idx != -1:
                stripped = article[:idx].rstrip()
                self.logger.info("头条发布前剥离 CTA 段落（%s），保留正文 %d 字符", marker, len(stripped))
                return stripped
        return article

    def _sanitize_publish_text(self, text: str) -> str:
        cleaned = str(text or "")
        brand = str(self.settings.brand_name or "").strip()
        if brand:
            cleaned = cleaned.replace(brand, "")
        cleaned = re.sub(r"(我们账号|本账号|本号|本栏目|本文作者|笔者认为|作者认为)[:：]?", "", cleaned)
        cleaned = re.sub(r"对标账号高热内容[：:]?", "", cleaned)
        cleaned = re.sub(r"对标热点/[^\s，。！？；：]*", "", cleaned)
        cleaned = re.sub(r"36氪头条号[：:]?", "", cleaned)
        cleaned = re.sub(r"benchmark_hotspot", "", cleaned)
        cleaned = re.sub(r"evergreen_pool", "", cleaned)
        cleaned = re.sub(r"evergreen://[^\s，。]*", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"^[，。；：:、\-\s]+", "", cleaned)
        cleaned = re.sub(r"[，。；：:、\-\s]+$", "", cleaned)
        return cleaned.strip()

    def _resolve_editor_selector(
        self,
        page,
        configured_selectors: list[str],
        fallback_selectors: list[str],
    ) -> str | None:
        ordered: list[str] = []
        for selector in [*configured_selectors, *fallback_selectors]:
            if selector and selector not in ordered:
                ordered.append(selector)
        for _ in range(8):
            resolved = self._first_visible_selector(page, ordered)
            if resolved:
                return resolved
            page.wait_for_timeout(350)
        return None

    def _page_looks_publish_capable(self, page) -> bool:
        current_url = page.url if hasattr(page, "url") else ""
        if "/profile_v4/graphic/publish" not in current_url:
            return False

        title_selector = self._resolve_editor_selector(
            page,
            self.settings.publishing.selectors.get("title_input", []),
            self.DEFAULT_TITLE_SELECTORS,
        )
        editor_selector = self._resolve_editor_selector(
            page,
            self.settings.publishing.selectors.get("article_editor", []),
            self.DEFAULT_EDITOR_SELECTORS,
        )
        return bool(title_selector and editor_selector)

    def _wait_for_publish_editor_ready(self, page) -> None:
        editor_shell_selectors = [
            ".left-column .publish-editor-title-wrapper",
            ".publish-editor-title-wrapper",
            ".left-column .syl-editor-wrap",
            ".syl-editor-wrap",
            ".left-column div.ProseMirror",
            "div.ProseMirror",
        ]
        deadline = datetime.now().timestamp() + (max(self.settings.publishing.wait_timeout_ms, 12000) / 1000)
        while datetime.now().timestamp() < deadline:
            if self._first_visible_selector(page, editor_shell_selectors, strict=False):
                return
            page.wait_for_timeout(300)

    def _permanently_hide_ai_assistant(self, page, notes: list[str]) -> None:
        try:
            page.evaluate("""() => {
                const style = document.createElement('style');
                style.id = 'ai-assistant-blocker';
                style.textContent = `
                    .byte-drawer-wrapper.ai-assistant-drawer,
                    .byte-drawer-mask,
                    [class*="ai-assistant-drawer"],
                    [class*="ai-assistant-modal"],
                    [class*="ai-writer-sidebar"] {
                        display: none !important;
                        visibility: hidden !important;
                        pointer-events: none !important;
                    }
                `;
                if (!document.getElementById('ai-assistant-blocker')) {
                    document.head.appendChild(style);
                }
            }""")
            notes.append("AI assistant drawer permanently hidden via CSS injection.")
        except Exception as exc:
            self.logger.debug("Failed to inject AI assistant CSS: %s", exc)

    def _make_popup_guard(self, page) -> PopupGuard:
        """创建绑定到当前 page 的弹窗守卫实例

        健壮性增强：每次发布流程创建独立的 PopupGuard，避免跨 page 状态污染
        """
        return PopupGuard(
            page,
            logger=self.logger,
            platform=self._popup_guard_platform,
            sweep_timeout_ms=500,
        )

    def _dismiss_ai_assistant_drawer(self, page, notes: list[str]) -> None:
        """关闭 AI 助手抽屉 + 主动扫描其他干扰弹窗

        健壮性增强说明（保留原有 AI 抽屉关闭逻辑，叠加 PopupGuard 主动扫描）：
        - 原实现：仅关闭 AI 助手抽屉
        - 新实现：在关闭 AI 抽屉后，额外调用 PopupGuard.sweep() 主动扫描并关闭
                  新手引导、平台规则公告、通用遮罩等干扰弹窗
        - 无侵入式：PopupGuard 任何异常都被捕获，不影响主干流程
        """
        dismissed = False

        for selector in self.AI_DRAWER_CLOSE_SELECTORS:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=500)
                locator.click(force=True, timeout=1000)
                page.wait_for_timeout(500)
                dismissed = True
                break
            except Exception:
                continue

        if not dismissed:
            for selector in self.AI_DRAWER_MASK_SELECTORS:
                try:
                    locator = page.locator(selector).first
                    locator.wait_for(state="visible", timeout=500)
                    locator.click(force=True, timeout=1000)
                    page.wait_for_timeout(500)
                    dismissed = True
                    break
                except Exception:
                    continue

        if not dismissed:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass

        overlay_still_visible = False
        for selector in self.AI_DRAWER_MASK_SELECTORS:
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=500)
                overlay_still_visible = True
                break
            except Exception:
                continue

        if overlay_still_visible:
            try:
                page.evaluate(
                    """() => {
                        const selectors = [
                            '.byte-drawer-mask',
                            '.byte-drawer-wrapper.ai-assistant-drawer',
                        ];
                        for (const selector of selectors) {
                            document.querySelectorAll(selector).forEach((node) => node.remove());
                        }
                    }"""
                )
                page.wait_for_timeout(300)
                notes.append("Dismissed AI assistant overlay via DOM fallback.")
            except Exception:
                pass
        elif dismissed:
            notes.append("Dismissed AI assistant overlay before editor interaction.")

        # 健壮性增强：主动扫描其他干扰弹窗（新手引导/平台公告/通用遮罩）
        # 无侵入式：任何异常都被捕获，绝不影响主干流程
        try:
            guard = self._make_popup_guard(page)
            extra_dismissed = guard.sweep(notes)
            if extra_dismissed:
                notes.append(f"PopupGuard additionally dismissed: {', '.join(extra_dismissed)}")
        except Exception as exc:
            self.logger.debug("PopupGuard sweep failed (non-blocking): %s", exc)

    def _hide_ai_drawer_mask_css(self, page, notes: list[str]) -> None:
        """用 CSS 强制隐藏 AI 助手抽屉及其遮罩，防止拦截封面/按钮点击。

        实测（2026-08-24）：头条发布页的 AI 助手抽屉（byte-drawer-wrapper.ai-assistant-drawer）
        及其遮罩（byte-drawer-mask）会拦截封面触发区域的点击，
        导致 file-chooser 永远不出现、封面一直上传失败（文章以无封面发布）。
        """
        try:
            page.evaluate(
                """() => {
                    const style = document.createElement('style');
                    style.id = 'ai-drawer-hard-blocker';
                    style.textContent = `
                        .byte-drawer-wrapper.ai-assistant-drawer,
                        .byte-drawer-mask,
                        [class*="ai-assistant-drawer"],
                        [class*="ai-assistant-panel"],
                        [class*="ai-writer-sidebar"] {
                            display: none !important;
                            visibility: hidden !important;
                            pointer-events: none !important;
                            opacity: 0 !important;
                            z-index: -1 !important;
                        }
                    `;
                    if (!document.getElementById('ai-drawer-hard-blocker')) {
                        document.head.appendChild(style);
                    }
                }"""
            )
            notes.append("AI assistant drawer mask force-hidden via CSS (ai-drawer-hard-blocker).")
        except Exception as exc:
            self.logger.debug("AI drawer mask CSS injection failed (non-blocking): %s", exc)

    def _set_editor_content(self, page, selector: str, article: str, notes: list[str]) -> None:
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=self.settings.publishing.wait_timeout_ms)

        try:
            locator.click(force=True, timeout=1000)
        except Exception:
            notes.append("Editor click was intercepted once. Continuing with keyboard input.")

        paragraphs = self._split_article_paragraphs(article)
        if not paragraphs:
            raise RuntimeError("Article content is empty after sanitization.")

        attempt_notes: list[str] = []

        try:
            self._clear_editor_content(page, locator)
            self._paste_editor_content(page, locator, paragraphs)
            page.wait_for_timeout(500)
            if self._editor_content_matches(locator, paragraphs):
                return
            attempt_notes.append("clipboard paste verification failed")
        except Exception as exc:
            attempt_notes.append(f"clipboard paste failed: {exc}")

        try:
            self._clear_editor_content(page, locator)
            self._inject_editor_content_via_dom(page, locator, paragraphs)
            page.wait_for_timeout(500)
            if self._editor_content_matches(locator, paragraphs):
                notes.append("Editor content required DOM injection fallback.")
                return
            attempt_notes.append("dom injection verification failed")
        except Exception as exc:
            attempt_notes.append(f"dom injection failed: {exc}")

        debug_dir = self._capture_debug_artifacts(
            page,
            extra_payload={
                "editor_write_attempts": attempt_notes,
                "expected_paragraph_count": len(paragraphs),
                "expected_preview": paragraphs[0][:120],
            },
        )
        raise RuntimeError(
            "Failed to write article content into editor. "
            f"Attempts: {'; '.join(attempt_notes) or 'unknown'}. "
            f"Current URL: {page.url}. Debug files saved to: {debug_dir}"
        )

    def _clear_editor_content(self, page, locator) -> None:
        try:
            locator.click(force=True, timeout=1000)
        except Exception:
            pass

        try:
            locator.focus(timeout=1000)
        except Exception:
            pass

        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(150)
        except Exception:
            pass

        try:
            locator.evaluate(
                """(node) => {
                    node.innerHTML = "";
                    node.dispatchEvent(new InputEvent("input", {
                        bubbles: true,
                        cancelable: true,
                        inputType: "deleteContentBackward",
                        data: null,
                    }));
                    node.dispatchEvent(new Event("change", { bubbles: true }));
                }"""
            )
            page.wait_for_timeout(120)
        except Exception:
            pass

    def _paste_editor_content(self, page, locator, paragraphs: list[str]) -> None:
        content = "\n\n".join(paragraphs)
        try:
            page.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=self.settings.publishing.base_url,
            )
        except Exception:
            pass

        locator.focus(timeout=1000)
        page.evaluate("(text) => navigator.clipboard.writeText(text)", content)
        page.keyboard.press("Control+V")

    def _inject_editor_content_via_dom(self, page, locator, paragraphs: list[str]) -> None:
        locator.evaluate(
            """(node, payload) => {
                const paragraphs = payload.paragraphs || [];
                node.focus();
                node.innerHTML = "";
                for (const text of paragraphs) {
                    const p = document.createElement("p");
                    p.className = "pgc-p";
                    p.textContent = text;
                    node.appendChild(p);
                }
                const tail = document.createElement("p");
                tail.appendChild(document.createElement("br"));
                node.appendChild(tail);
                node.dispatchEvent(new InputEvent("input", {
                    bubbles: true,
                    cancelable: true,
                    inputType: "insertFromPaste",
                    data: paragraphs.join("\\n\\n"),
                }));
                node.dispatchEvent(new Event("change", { bubbles: true }));
            }""",
            {"paragraphs": paragraphs},
        )

    def _editor_content_matches(self, locator, paragraphs: list[str]) -> bool:
        try:
            editor_text = locator.inner_text(timeout=1200)
        except Exception:
            return False

        actual = re.sub(r"\s+", "", editor_text)
        if not actual or not paragraphs:
            return False
        position = 0
        for paragraph in paragraphs:
            expected = re.sub(r"\s+", "", paragraph)
            position = actual.find(expected, position)
            if position < 0:
                return False
            position += len(expected)
        return True

    def _assert_editor_text_complete(self, locator, article: str) -> None:
        paragraphs = self._split_article_paragraphs(article)
        if not self._editor_content_matches(locator, paragraphs):
            raise RuntimeError(
                "Editor article text was changed or truncated before submission; "
                "publishing was blocked."
            )

    def _try_apply_article_images(
        self,
        page,
        editor_selector: str,
        article: str,
        image_paths: list[Path],
        notes: list[str],
        *,
        first_image_as_cover: bool = False,
    ) -> int:
        self._last_inline_image_assets = []
        available_images = self._dedupe_article_image_paths(image_paths, notes)
        if not available_images:
            notes.append("Article images not found. Skipping inline image upload.")
            return 0

        target_paragraphs = self._resolve_inline_image_targets(
            article,
            len(available_images),
            first_image_as_cover=first_image_as_cover,
        )
        inserted = 0
        for image_path, target_paragraph in zip(available_images, target_paragraphs):
            if self._try_apply_article_image(page, editor_selector, image_path, notes, target_paragraph=target_paragraph):
                inserted += 1
                self._last_inline_image_assets.append(image_path)
        return inserted

    def _extract_persistent_image_refs_from_html(self, markup: str) -> list[str]:
        parser = _PersistentImageReferenceParser()
        try:
            parser.feed(markup or "")
            parser.close()
        except Exception:
            return []
        return list(parser.refs)

    def _is_article_content_payload_key(self, key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
        return normalized in {
            "content",
            "articlecontent",
            "contenthtml",
            "htmlcontent",
            "articlebody",
            "bodyhtml",
            "richtext",
        }

    def _collect_article_content_values(self, value: Any, *, key: str = "") -> list[str]:
        values: list[str] = []
        if isinstance(value, str):
            if not key or self._is_article_content_payload_key(key):
                values.append(value)
            return values
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if self._is_article_content_payload_key(str(child_key)):
                    values.extend(
                        self._collect_article_content_values(child_value, key=str(child_key))
                    )
                elif isinstance(child_value, (dict, list)):
                    values.extend(self._collect_article_content_values(child_value))
            return values
        if isinstance(value, list):
            for child_value in value:
                values.extend(self._collect_article_content_values(child_value, key=key))
        return values

    def _extract_article_content_payload_values(self, post_data: str) -> list[str]:
        if not post_data:
            return []

        values: list[str] = []
        stripped = post_data.strip()
        if stripped.startswith(("{", "[")):
            try:
                values.extend(self._collect_article_content_values(json.loads(stripped)))
            except Exception:
                pass

        try:
            form_payload = parse_qs(post_data, keep_blank_values=True)
        except Exception:
            form_payload = {}
        for key, raw_values in form_payload.items():
            for raw_value in raw_values:
                decoded_value = str(raw_value)
                if self._is_article_content_payload_key(str(key)):
                    values.append(decoded_value)
                elif "<img" in decoded_value.lower():
                    values.append(decoded_value)

        decoded_post_data = unquote_plus(post_data)
        if "<img" in decoded_post_data.lower():
            values.append(decoded_post_data)

        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = html_unescape(str(value))
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique_values.append(normalized)
        return unique_values

    def _extract_persistent_image_refs_from_post_data(self, post_data: str) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        for content in self._extract_article_content_payload_values(post_data):
            for ref in self._extract_persistent_image_refs_from_html(content):
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        return refs

    def _image_reference_matches(self, expected: str, actual: str) -> bool:
        expected_value = html_unescape(str(expected)).strip()
        actual_value = html_unescape(str(actual)).strip()
        if not expected_value or not actual_value:
            return False
        return (
            expected_value == actual_value
            or expected_value in actual_value
            or actual_value in expected_value
        )

    def _summarize_publish_media_payload(
        self,
        post_data: str,
        *,
        expected_inline_images: int,
        expected_refs: list[str] | tuple[str, ...] = (),
        editor_state_verified: bool = False,
    ) -> dict[str, Any]:
        payload_refs = self._extract_persistent_image_refs_from_post_data(post_data)
        content_values = self._extract_article_content_payload_values(post_data)
        expected_count = max(int(expected_inline_images), 0)
        normalized_expected_refs = []
        seen_expected: set[str] = set()
        for ref in expected_refs:
            normalized = html_unescape(str(ref)).strip()
            if normalized and normalized not in seen_expected:
                seen_expected.add(normalized)
                normalized_expected_refs.append(normalized)

        missing_refs = [
            expected
            for expected in normalized_expected_refs
            if not any(self._image_reference_matches(expected, actual) for actual in payload_refs)
            and not any(expected in content for content in content_values)
        ]
        unexpected_refs = [
            actual
            for actual in payload_refs
            if normalized_expected_refs
            and not any(self._image_reference_matches(expected, actual) for expected in normalized_expected_refs)
        ]
        request_refs_are_expected = bool(payload_refs) and bool(normalized_expected_refs) and not unexpected_refs
        complete_verified = expected_count <= 0 or (
            len(payload_refs) >= expected_count
            and not missing_refs
            and not unexpected_refs
        )
        partial_verified = (
            bool(editor_state_verified)
            and expected_count > 0
            and request_refs_are_expected
        )
        verified = complete_verified or partial_verified
        if complete_verified:
            verification_mode = "complete_request"
        elif partial_verified:
            verification_mode = "editor_state_plus_request_subset"
        else:
            verification_mode = "unverified"
        failure_reason = ""
        if not verified:
            if len(payload_refs) < expected_count:
                failure_reason = (
                    f"payload contains {len(payload_refs)} persistent image reference(s); "
                    f"expected {expected_count}"
                )
            elif missing_refs:
                failure_reason = "payload omitted one or more editor image references"
            elif unexpected_refs:
                failure_reason = "payload contains an unexpected editor image reference"
            else:
                failure_reason = "payload image persistence could not be verified"

        return {
            "expected_count": expected_count,
            "payload_count": len(payload_refs),
            "payload_refs": payload_refs[:20],
            "expected_refs": normalized_expected_refs[:20],
            "missing_refs": missing_refs[:20],
            "unexpected_refs": unexpected_refs[:20],
            "request_refs_are_expected": request_refs_are_expected,
            "editor_state_verified": bool(editor_state_verified),
            "verification_mode": verification_mode,
            "verified": verified,
            "failure_reason": failure_reason,
        }

    def _dedupe_article_image_paths(self, image_paths: list[Path], notes: list[str]) -> list[Path]:
        unique_paths: list[Path] = []
        seen_keys: set[str] = set()
        for path in image_paths:
            if not path.exists():
                continue
            key = self._build_image_dedupe_key(path)
            if key in seen_keys:
                notes.append(f"Skipped duplicate article image asset: {path.name}")
                continue
            seen_keys.add(key)
            unique_paths.append(path)
        return unique_paths

    def _build_image_dedupe_key(self, path: Path) -> str:
        try:
            return hashlib.sha1(path.read_bytes()).hexdigest()
        except Exception:
            return str(path.resolve())

    def _resolve_inline_image_targets(
        self,
        article: str,
        image_count: int,
        *,
        first_image_as_cover: bool = False,
    ) -> list[int]:
        paragraphs = [block.strip() for block in article.split("\n\n") if block.strip()]
        paragraph_count = len(paragraphs)
        if image_count <= 0:
            return []
        if first_image_as_cover:
            if image_count == 1:
                return [0]
            analysis_target = self._best_inline_image_target(
                paragraphs,
                self.INLINE_IMAGE_ANALYSIS_TOKENS,
                fallback=min(max(3, 2), max(paragraph_count, 1)),
            )
            return [0, analysis_target][:image_count]
        if paragraph_count <= 2:
            return [1] * image_count
        if image_count == 1:
            return [self._best_inline_image_target(paragraphs, self.INLINE_IMAGE_DATA_TOKENS, fallback=2)]

        first_target = self._best_inline_image_target(paragraphs, self.INLINE_IMAGE_DATA_TOKENS, fallback=2)
        second_target = self._best_inline_image_target(
            paragraphs,
            self.INLINE_IMAGE_ANALYSIS_TOKENS,
            fallback=min(max(4, first_target + 2), paragraph_count),
            exclude={first_target},
        )
        return [first_target, second_target][:image_count]

    def _best_inline_image_target(
        self,
        paragraphs: list[str],
        preferred_tokens: tuple[str, ...],
        *,
        fallback: int,
        exclude: set[int] | None = None,
    ) -> int:
        if not paragraphs:
            return 1

        excluded = exclude or set()
        best_index = min(max(fallback, 1), len(paragraphs))
        best_score = float("-inf")

        for index, paragraph in enumerate(paragraphs, start=1):
            if index in excluded:
                continue

            score = 0
            length = len(paragraph)
            if 40 <= length <= 150:
                score += 8
            elif length < 28:
                score -= 6
            elif length > 190:
                score -= 4

            if re.search(r"\d", paragraph):
                score += 10

            score += sum(4 for token in preferred_tokens if token and token in paragraph)

            if index == 1:
                score -= 5
            if paragraph.endswith(("？", "?")):
                score -= 4

            distance_penalty = abs(index - fallback)
            score -= distance_penalty

            if score > best_score:
                best_score = score
                best_index = index

        return best_index

    def _try_apply_article_image(
        self,
        page,
        editor_selector: str,
        image_path: Path,
        notes: list[str],
        *,
        target_paragraph: int,
    ) -> bool:
        if not image_path.exists():
            notes.append("Article image not found. Skipping inline image upload.")
            return False

        try:
            self._apply_article_image(page, editor_selector, image_path, notes, target_paragraph=target_paragraph)
            return True
        except Exception as exc:
            debug_dir = self._capture_debug_artifacts(
                page,
                extra_payload={
                    "inline_image_path": str(image_path),
                    "target_paragraph": target_paragraph,
                },
            )
            notes.append(
                "Inline article image upload failed and was skipped: "
                f"{self._clean_visible_text(exc)}. Debug files saved to: {debug_dir}"
            )
            return False

    def _apply_article_image(
        self,
        page,
        editor_selector: str,
        image_path: Path,
        notes: list[str],
        *,
        target_paragraph: int,
    ) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        editor_locator = page.locator(editor_selector).first
        editor_locator.wait_for(state="visible", timeout=self.settings.publishing.wait_timeout_ms)
        previous_image_count = self._count_editor_images(
            page,
            editor_selector=editor_selector,
            editor_locator=editor_locator,
        )

        try:
            editor_locator.click(force=True, timeout=1200)
        except Exception:
            pass

        try:
            editor_locator.focus(timeout=1000)
        except Exception:
            pass

        insertion_paragraph = self._position_editor_caret_for_inline_image(
            page,
            editor_locator,
            target_paragraph=target_paragraph,
        )

        self._prime_article_image_toolbar(page, editor_locator)

        trigger_locator = self._resolve_article_image_trigger(page)
        if trigger_locator is None:
            raise RuntimeError("Could not locate the article image upload trigger.")

        existing_input_count = page.locator("input[type='file']").count()
        file_chooser = self._open_article_image_file_chooser(page, trigger_locator)
        if file_chooser is not None:
            file_chooser.set_files(str(image_path))
        else:
            upload_input = self._wait_for_article_upload_input(page, existing_input_count)
            if upload_input is None:
                self._click_article_image_trigger(trigger_locator)
                upload_input = self._wait_for_article_upload_input(page, existing_input_count)
            if upload_input is None:
                raise RuntimeError("Article image upload input did not appear.")
            upload_input.set_input_files(str(image_path), timeout=max(self.settings.publishing.wait_timeout_ms, 60000))

        self._confirm_article_image_selection(
            page,
            editor_selector,
            editor_locator,
            previous_image_count,
        )
        self._wait_for_article_image_inserted(
            page,
            editor_selector,
            editor_locator,
            previous_image_count,
        )

        try:
            editor_locator.click(force=True, timeout=1200)
            page.keyboard.press("ArrowRight")
            page.keyboard.press("Enter")
        except Exception:
            pass

        notes.append(
            f"Inserted inline article image after paragraph {insertion_paragraph}: {image_path.name}"
        )

    def _prime_article_image_toolbar(self, page, editor_locator) -> None:
        self._dismiss_ai_assistant_drawer(page, [])
        try:
            editor_locator.evaluate(
                """(node) => {
                    node.focus();
                    const selection = window.getSelection();
                    if (!selection || selection.rangeCount === 0) {
                        return;
                    }
                    const anchor = selection.anchorNode;
                    const element = anchor && anchor.nodeType === Node.TEXT_NODE ? anchor.parentElement : anchor;
                    if (element && element.scrollIntoView) {
                        element.scrollIntoView({ block: 'center', inline: 'nearest' });
                    } else if (node.scrollIntoView) {
                        node.scrollIntoView({ block: 'center', inline: 'nearest' });
                    }
                }"""
            )
        except Exception:
            pass

        for _ in range(2):
            try:
                page.locator(".syl-editor-toolbar.visible.inline").first.wait_for(
                    state="visible",
                    timeout=800,
                )
                return
            except Exception:
                try:
                    editor_locator.focus(timeout=800)
                except Exception:
                    pass
                page.wait_for_timeout(180)

    def _position_editor_caret_for_inline_image(self, page, editor_locator, *, target_paragraph: int) -> int:
        try:
            paragraph_count = int(
                editor_locator.evaluate(
                    """(node) => {
                        const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'));
                        return blocks.filter((item) => item.textContent && item.textContent.trim()).length;
                    }"""
                )
            )
        except Exception:
            paragraph_count = 0

        if target_paragraph <= 0:
            if self._place_editor_caret_via_click(
                page,
                editor_locator,
                target_index=1,
                insert_before=True,
            ):
                return 1

            try:
                actual_index = int(
                    editor_locator.evaluate(
                        """(node) => {
                            const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'))
                                .filter((item) => item.textContent && item.textContent.trim());
                            const target = blocks[0];
                            if (!target) {
                                node.focus();
                                return 0;
                            }

                            if (target.scrollIntoView) {
                                target.scrollIntoView({ block: 'center', inline: 'nearest' });
                            }

                            const range = document.createRange();
                            range.selectNodeContents(target);
                            range.collapse(true);

                            const selection = window.getSelection();
                            if (selection) {
                                selection.removeAllRanges();
                                selection.addRange(range);
                            }

                            node.focus();
                            return 1;
                        }"""
                    )
                )
            except Exception:
                actual_index = 0

            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(120)
            except Exception:
                pass
            return actual_index or 1

        target_index = min(max(target_paragraph, 1), max(paragraph_count, 1))
        try:
            actual_index = int(
                editor_locator.evaluate(
                    """(node, payload) => {
                        const desired = payload.targetIndex || 1;
                        const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'))
                            .filter((item) => item.textContent && item.textContent.trim());
                        const target = blocks[Math.max(0, Math.min(blocks.length, desired) - 1)];
                        if (!target) {
                            node.focus();
                            return 0;
                        }

                        if (target.scrollIntoView) {
                            target.scrollIntoView({ block: 'center', inline: 'nearest' });
                        }

                        node.focus();
                        const range = document.createRange();
                        range.selectNodeContents(target);
                        range.collapse(false);

                        const selection = window.getSelection();
                        if (selection) {
                            selection.removeAllRanges();
                            selection.addRange(range);
                        }

                        return blocks.indexOf(target) + 1;
                    }""",
                    {"targetIndex": target_index},
                )
            )
        except Exception:
            actual_index = 0

        if actual_index <= 0:
            raise RuntimeError("Could not place inline image after a complete paragraph.")

        try:
            page.keyboard.press("Enter")
            page.wait_for_timeout(120)
        except Exception:
            pass
        return actual_index

    def _place_editor_caret_via_click(
        self,
        page,
        editor_locator,
        *,
        target_index: int,
        insert_before: bool,
    ) -> bool:
        try:
            rect = editor_locator.evaluate(
                """(node, payload) => {
                    const desired = payload.targetIndex || 1;
                    const blocks = Array.from(node.querySelectorAll('p, div[data-block-type], div.pgc-p'))
                        .filter((item) => item.textContent && item.textContent.trim());
                    const target = blocks[Math.max(0, Math.min(blocks.length - 1, desired - 1))];
                    if (!target) {
                        return null;
                    }

                    if (target.scrollIntoView) {
                        target.scrollIntoView({ block: 'center', inline: 'nearest' });
                    }

                    const box = target.getBoundingClientRect();
                    return {
                        left: box.left,
                        top: box.top,
                        width: box.width,
                        height: box.height,
                    };
                }""",
                {"targetIndex": target_index},
            )
        except Exception:
            return False

        if not isinstance(rect, dict):
            return False

        try:
            width = max(float(rect.get("width", 0.0)), 1.0)
            height = max(float(rect.get("height", 0.0)), 1.0)
            left = float(rect.get("left", 0.0))
            top = float(rect.get("top", 0.0))
        except Exception:
            return False

        click_x = left + min(28.0, width * 0.2) if insert_before else left + max(36.0, min(width - 20.0, width * 0.82))
        click_y = top + min(max(height / 2.0, 8.0), max(height - 4.0, 8.0))

        try:
            page.mouse.click(click_x, click_y)
            page.wait_for_timeout(120)
            page.keyboard.press("Home" if insert_before else "End")
            page.wait_for_timeout(80)
            page.keyboard.press("Enter")
            page.wait_for_timeout(140)
            return True
        except Exception:
            return False

    def _resolve_article_image_trigger(self, page):
        for toolbar_selector in [
            ".publish-editor .syl-editor-toolbar.visible.inline",
            ".syl-editor-toolbar.visible.inline",
            ".publish-editor .syl-editor-toolbar.visible",
            ".syl-editor-toolbar.visible",
        ]:
            try:
                toolbar = page.locator(toolbar_selector).first
                toolbar.wait_for(state="visible", timeout=700)
            except Exception:
                continue

            for button_selector in [
                ".syl-toolbar-tool.image button.syl-toolbar-button",
                ".syl-toolbar-tool.image button",
                ".syl-toolbar-tool.image",
            ]:
                try:
                    locator = toolbar.locator(button_selector).first
                    locator.wait_for(state="visible", timeout=500)
                    return locator
                except Exception:
                    continue

        for selector in self.DEFAULT_ARTICLE_IMAGE_TRIGGER_SELECTORS:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=600)
                return locator
            except Exception:
                continue
        return None

    def _open_article_image_file_chooser(self, page, trigger_locator):
        try:
            with page.expect_file_chooser(timeout=1800) as chooser_info:
                self._click_article_image_trigger(trigger_locator)
            return chooser_info.value
        except PlaywrightTimeoutError:
            return None
        except Exception:
            return None

    def _click_article_image_trigger(self, trigger_locator) -> None:
        click_errors: list[str] = []
        for strategy in ("click", "dispatch", "dom"):
            try:
                if strategy == "click":
                    trigger_locator.click(force=True, timeout=1500)
                elif strategy == "dispatch":
                    trigger_locator.dispatch_event("click")
                else:
                    trigger_locator.evaluate("(node) => node.click()")
                return
            except Exception as exc:
                click_errors.append(str(exc))
                continue
        raise RuntimeError(
            "Failed to activate the article image trigger. "
            + " | ".join(item for item in click_errors if item)
        )

    def _wait_for_article_upload_input(self, page, existing_count: int):
        scoped_selectors = [
            ".upload-image-panel .btn-upload-handle input[type='file']",
            ".upload-image-panel .upload-handler input[type='file']",
            ".upload-image-panel .image-item-upload input[type='file']",
            ".upload-image-panel input#upload-drag-input",
            ".upload-image-panel input[type='file']",
            ".byte-modal-wrapper .upload-image-panel input[type='file']",
        ]
        fallback_selectors = [
            ".btn-upload-handle input[type='file']",
            "input#upload-drag-input",
            "input[type='file']",
        ]
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 15000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        while datetime.now().timestamp() < deadline:
            panel_visible = False
            try:
                panel_visible = page.locator(".upload-image-panel").first.is_visible(timeout=300)
            except Exception:
                panel_visible = False

            selectors = scoped_selectors if panel_visible else fallback_selectors
            for selector in selectors:
                try:
                    file_inputs = page.locator(selector)
                    count = file_inputs.count()
                except Exception:
                    continue

                for index in range(count - 1, -1, -1):
                    if not panel_visible and selector == "input[type='file']" and count <= existing_count and index < existing_count:
                        continue
                    locator = file_inputs.nth(index)
                    try:
                        accept_value = (locator.get_attribute("accept") or "").lower()
                    except Exception:
                        accept_value = ""
                    if accept_value and "image" not in accept_value and ".png" not in accept_value:
                        continue
                    try:
                        locator.wait_for(state="attached", timeout=400)
                        return locator
                    except Exception:
                        continue

            page.wait_for_timeout(200)
        return None

    def _wait_for_article_image_inserted(
        self,
        page,
        editor_selector: str,
        editor_locator,
        previous_count: int,
    ) -> None:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 30000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        last_count = previous_count
        while datetime.now().timestamp() < deadline:
            current_count = self._count_editor_images(
                page,
                editor_selector=editor_selector,
                editor_locator=editor_locator,
            )
            if current_count > previous_count:
                page.wait_for_timeout(500)
                return
            last_count = current_count
            page.wait_for_timeout(300)

        raise RuntimeError(
            "Article image upload did not result in an inline image appearing in the editor. "
            f"Previous image count: {previous_count}, current image count: {last_count}."
        )

    def _confirm_article_image_selection(
        self,
        page,
        editor_selector: str,
        editor_locator,
        previous_count: int,
    ) -> None:
        panel_visible = False
        try:
            page.locator(".upload-image-panel").first.wait_for(
                state="visible",
                timeout=max(4000, min(self.settings.publishing.wait_timeout_ms, 12000)),
            )
            panel_visible = True
        except Exception:
            panel_visible = False

        if not panel_visible:
            return

        selection_visible = False
        for selector in [
            ".pic-select-image-item",
            ".pic-select-image-item-wrap",
            ".upload-image-wrapper img",
            ".upload-image-wrapper li",
            ".upload-image-panel li.image-item",
            ".upload-image-panel .image-list li",
        ]:
            try:
                page.locator(selector).first.wait_for(
                    state="visible",
                    timeout=max(3000, min(self.settings.publishing.wait_timeout_ms, 15000)),
                )
                selection_visible = True
                break
            except Exception:
                continue

        if selection_visible:
            selected = False
            for selector in [
                ".pic-select-image-item",
                ".pic-select-image-item-wrap",
                ".upload-image-wrapper img",
                ".upload-image-wrapper li",
                ".upload-image-panel li.image-item",
                ".upload-image-panel .image-list li",
            ]:
                try:
                    candidates = page.locator(selector)
                    count = candidates.count()
                    for index in range(count - 1, -1, -1):
                        candidate = candidates.nth(index)
                        try:
                            if not candidate.is_visible(timeout=300):
                                continue
                        except Exception:
                            continue

                        try:
                            candidate_text = self._clean_visible_text(candidate.text_content(timeout=300) or "")
                        except Exception:
                            candidate_text = ""
                        if "更多" in candidate_text:
                            continue

                        candidate.click(force=True, timeout=1500)
                        selected = True
                        page.wait_for_timeout(500)
                        break
                    if selected:
                        break
                except Exception:
                    continue
            if not selected:
                raise RuntimeError("Article image upload panel opened, but no uploaded image could be selected.")

        confirm_clicked = self._maybe_click(
            page,
            [
                "[data-e2e='imageUploadConfirm-btn']",
                ".upload-image-panel .footer .byte-btn-primary",
                ".upload-image-panel button:has-text('确定')",
            ],
            required=False,
        )

        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 15000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        panel_closed = False
        current_count = previous_count

        while datetime.now().timestamp() < deadline:
            current_count = self._count_editor_images(
                page,
                editor_selector=editor_selector,
                editor_locator=editor_locator,
            )
            if current_count > previous_count:
                return

            try:
                panel_closed = not page.locator(".upload-image-panel").first.is_visible(timeout=300)
            except Exception:
                panel_closed = True

            if panel_closed and confirm_clicked:
                break
            page.wait_for_timeout(250)

        if current_count > previous_count:
            return

        if panel_closed and confirm_clicked:
            return

    def _read_editor_image_refs(
        self,
        page,
        *,
        editor_selector: str | None = None,
        editor_locator=None,
    ) -> list[str]:
        script = """(root) => {
            const refs = [];
            const seen = new Set();
            const normalize = (value) => String(value || "").trim();
            const register = (value) => {
                const normalized = normalize(value);
                if (!normalized || /^(?:blob:|data:|javascript:|about:)/i.test(normalized)) {
                    return;
                }
                if (!seen.has(normalized)) {
                    seen.add(normalized);
                    refs.push(normalized);
                }
            };
            const isRemoteSrc = (value) => /^(?:https?:)?\\/\\//i.test(normalize(value));
            const images = root.matches && root.matches("img")
                ? [root]
                : Array.from(root.querySelectorAll("img"));
            for (const img of images) {
                const webUri = img.getAttribute("web_uri") ||
                    img.getAttribute("web-uri") ||
                    img.getAttribute("data-web-uri");
                if (normalize(webUri)) {
                    register(webUri);
                    continue;
                }
                const src = img.getAttribute("src");
                if (isRemoteSrc(src)) {
                    register(src);
                }
            }
            return refs;
        }"""

        if editor_locator is not None:
            try:
                result = editor_locator.evaluate(script)
                if isinstance(result, list):
                    refs = [str(item).strip() for item in result if str(item).strip()]
                    if refs:
                        return refs
            except Exception:
                pass

        try:
            result = page.evaluate(
                """(payload) => {
                    const selector = payload.selector;
                    const selectedRoots = selector
                        ? Array.from(document.querySelectorAll(selector))
                        : Array.from(document.querySelectorAll(".ProseMirror"));
                    const roots = selectedRoots.length
                        ? selectedRoots
                        : Array.from(document.querySelectorAll(".ProseMirror"));
                    const refs = [];
                    const seen = new Set();
                    const normalize = (value) => String(value || "").trim();
                    const register = (value) => {
                        const normalized = normalize(value);
                        if (!normalized || /^(?:blob:|data:|javascript:|about:)/i.test(normalized)) {
                            return;
                        }
                        if (!seen.has(normalized)) {
                            seen.add(normalized);
                            refs.push(normalized);
                        }
                    };
                    const isRemoteSrc = (value) => /^(?:https?:)?\\/\\//i.test(normalize(value));
                    for (const root of roots) {
                        for (const img of root.querySelectorAll("img")) {
                            const webUri = img.getAttribute("web_uri") ||
                                img.getAttribute("web-uri") ||
                                img.getAttribute("data-web-uri");
                            if (normalize(webUri)) {
                                register(webUri);
                                continue;
                            }
                            const src = img.getAttribute("src");
                            if (isRemoteSrc(src)) {
                                register(src);
                            }
                        }
                    }
                    return refs;
                }""",
                {"selector": editor_selector},
            )
            if isinstance(result, list):
                return [str(item).strip() for item in result if str(item).strip()]
        except Exception:
            pass
        return []

    def _count_editor_images(self, page, *, editor_selector: str | None = None, editor_locator=None) -> int:
        return len(
            self._read_editor_image_refs(
                page,
                editor_selector=editor_selector,
                editor_locator=editor_locator,
            )
        )

    def _verify_publish_media_state(
        self,
        page,
        editor_selector: str,
        *,
        cover_mode: str,
        expected_inline_images: int,
    ) -> dict[str, Any]:
        expected_count = max(int(expected_inline_images), 0)
        state: dict[str, Any] = {
            "cover_mode": cover_mode,
            "expected_count": expected_count,
            "persistent_count": 0,
            "refs": [],
            "verified": True,
            "failure_reason": "",
        }
        if cover_mode == "single":
            try:
                page.locator(".article-cover img").first.wait_for(
                    state="visible",
                    timeout=max(self.settings.publishing.wait_timeout_ms, 20000),
                )
            except Exception as exc:
                raise RuntimeError("Cover verification failed before publish.") from exc

        if expected_count <= 0:
            return state

        editor_locator = page.locator(editor_selector).first
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 20000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        last_signature: tuple[str, ...] = ()
        stable_rounds = 0
        while datetime.now().timestamp() < deadline:
            refs = self._read_editor_image_refs(
                page,
                editor_selector=editor_selector,
                editor_locator=editor_locator,
            )
            signature = tuple(refs)
            if len(refs) >= expected_count:
                stable_rounds = stable_rounds + 1 if signature == last_signature else 1
                if stable_rounds >= 2:
                    state.update(
                        {
                            "persistent_count": len(refs),
                            "refs": refs[:20],
                        }
                    )
                    return state
            else:
                stable_rounds = 0
            last_signature = signature
            page.wait_for_timeout(250)

        state.update(
            {
                "persistent_count": len(last_signature),
                "refs": list(last_signature[:20]),
                "verified": False,
                "failure_reason": (
                    f"editor exposed {len(last_signature)} persistent image reference(s); "
                    f"expected {expected_count}"
                ),
            }
        )
        self.logger.warning(
            "Inline article image persistence verification failed before publish: %s",
            state["failure_reason"],
        )
        raise RuntimeError(
            "Inline article image persistence could not be verified before publish. "
            f"{state['failure_reason']}"
        )

    def _nudge_editor_to_trigger_autosave(self, page, editor_selector: str, notes: list[str]) -> None:
        try:
            editor = page.locator(editor_selector).first
            editor.click(timeout=2000)
            page.keyboard.press("End")
            page.keyboard.type(" ", delay=30)
            page.wait_for_timeout(200)
            page.keyboard.press("Backspace")
            page.wait_for_timeout(500)
            notes.append("Nudged editor to trigger autosave after title was set.")
        except Exception as exc:
            notes.append(f"Editor nudge for autosave failed (non-fatal): {exc}")

    def _set_title_content(self, page, locator, title: str, notes: list[str]) -> None:
        locator.wait_for(state="visible", timeout=self.settings.publishing.wait_timeout_ms)
        observed = ""

        for strategy in ("type", "fill", "dom"):
            self._clear_title_input(page, locator)
            try:
                if strategy == "type":
                    locator.type(title, delay=35)
                elif strategy == "fill":
                    locator.fill(title)
                else:
                    self._set_title_input_via_dom(locator, title)
            except Exception:
                continue

            page.wait_for_timeout(300)
            observed = self._read_title_input(locator)
            if self._title_matches(observed, title):
                if strategy != "fill":
                    notes.append(f"Title content required {strategy} fallback.")
                return

        try:
            self._clear_title_input(page, locator)
            page.keyboard.insert_text(title)
            page.wait_for_timeout(300)
            observed = self._read_title_input(locator)
            if self._title_matches(observed, title):
                notes.append("Title content required keyboard fallback.")
                return
        except Exception:
            pass

        raise RuntimeError(
            "Failed to write title content into the Toutiao title field. "
            f"Expected: {title!r}, observed: {observed!r}"
        )

    def _split_article_paragraphs(self, article: str) -> list[str]:
        return [line.strip() for line in article.splitlines() if line.strip()]

    def _clear_title_input(self, page, locator) -> None:
        try:
            locator.click(force=True, timeout=1000)
        except Exception:
            pass

        try:
            locator.focus(timeout=1000)
        except Exception:
            pass

        try:
            locator.fill("")
        except Exception:
            pass

        try:
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(100)
        except Exception:
            pass

    def _set_title_input_via_dom(self, locator, title: str) -> None:
        locator.evaluate(
            """(node, value) => {
                const prototype = node instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype
                    : HTMLInputElement.prototype;
                const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                if (descriptor && descriptor.set) {
                    descriptor.set.call(node, value);
                } else {
                    node.value = value;
                }
                node.dispatchEvent(new Event('input', { bubbles: true }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            title,
        )

    def _read_title_input(self, locator) -> str:
        try:
            return locator.input_value(timeout=1000)
        except Exception:
            return ""

    def _title_matches(self, actual: str, expected: str) -> bool:
        return re.sub(r"\s+", "", actual or "") == re.sub(r"\s+", "", expected or "")

    def _apply_default_work_declarations(self, page, notes: list[str]) -> None:
        selected_labels: list[str] = []
        cleared_labels: list[str] = []

        desired_checked_groups = [
            [
                "label.byte-checkbox:has-text('取材网络')",
                "label.byte-checkbox:has(input[value='4'])",
            ],
            [
                "label.byte-checkbox:has-text('个人观点')",
                "label.byte-checkbox:has-text('仅供参考')",
                "label.byte-checkbox:has(input[value='2'])",
            ],
        ]
        desired_unchecked_groups = [
            [
                "label.byte-checkbox:has-text('引用AI')",
                "label.byte-checkbox:has(input[value='3'])",
            ],
        ]

        for selectors in desired_checked_groups:
            label = self._set_named_checkbox_state(page, selectors, checked=True)
            if label:
                selected_labels.append(label)

        for selectors in desired_unchecked_groups:
            label = self._set_named_checkbox_state(page, selectors, checked=False)
            if label:
                cleared_labels.append(label)

        if selected_labels:
            notes.append(f"Applied work declarations: {', '.join(selected_labels)}")
        if cleared_labels:
            notes.append(f"Cleared work declarations: {', '.join(cleared_labels)}")

    def _apply_safe_publish_options(self, page, notes: list[str], *, declare_first_release: bool = True) -> None:
        applied_labels: list[str] = []
        label = self._set_named_checkbox_state(
            page,
            [
                "label.byte-checkbox:has-text('头条首发')",
                "label.byte-checkbox:has-text('声明首发')",
                "label.byte-checkbox:has(input[value='1'])",
            ],
            checked=declare_first_release,
        )
        if label:
            applied_labels.append(label)

        # 说明：广告投放（投放广告赚收益）是 byte-radio 单选组，平台默认选中，
        # 已于 2026-09-15 用真实发布页 DOM 验证（.byte-radio-inner.checked）。
        # 不需要也不应该用代码干预，此处保持无操作。

        if applied_labels:
            action = "Applied" if declare_first_release else "Cleared"
            notes.append(f"{action} publish options: {', '.join(applied_labels)}")
        else:
            notes.append("Publish option selectors for first-release were not found.")

    def _set_named_checkbox_state(self, page, selectors: list[str], checked: bool) -> str:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=800)
                current_state = self._is_custom_control_selected(locator)
                label = self._clean_visible_text(locator.inner_text(timeout=500).strip()) or selector
                if current_state != checked:
                    self._click_custom_control(locator)
                    page.wait_for_timeout(250)
                    current_state = self._is_custom_control_selected(locator)
                if current_state == checked:
                    return label
            except Exception:
                continue
        return ""

    def _ensure_checkbox_state(self, page, selectors: list[str], checked: bool) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=800)
                current_state = self._is_custom_control_selected(locator)
                if current_state == checked:
                    return False
                self._click_custom_control(locator)
                page.wait_for_timeout(200)
                return self._is_custom_control_selected(locator) == checked
            except Exception:
                continue
        return False

    def _ensure_radio_state(self, page, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=800)
                if self._is_custom_control_selected(locator):
                    return False
                self._click_custom_control(locator)
                page.wait_for_timeout(200)
                return self._is_custom_control_selected(locator)
            except Exception:
                continue
        return False

    def _is_custom_control_selected(self, locator) -> bool:
        try:
            return bool(
                locator.evaluate(
                    """(node) => {
                        const input = node.querySelector('input');
                        if (input && typeof input.checked === 'boolean' && input.checked) {
                            return true;
                        }
                        if (node.classList.contains('byte-checkbox-checked')) {
                            return true;
                        }
                        if (node.getAttribute('aria-checked') === 'true') {
                            return true;
                        }
                        return !!node.querySelector('.byte-radio-inner.checked');
                    }"""
                )
            )
        except Exception:
            try:
                input = locator.locator("input").first
                return bool(input.count() and input.is_checked())
            except Exception:
                return False

    def _click_custom_control(self, locator) -> bool:
        try:
            locator.click(timeout=2000)
            return True
        except Exception:
            pass

        try:
            locator.click(force=True, timeout=1200)
            return True
        except Exception:
            pass

        try:
            input_el = locator.locator("input").first
            input_el.click(timeout=1200)
            return True
        except Exception:
            pass

        try:
            locator.evaluate(
                """(node) => {
                    const input = node.querySelector('input');
                    if (input) {
                        input.click();
                        return;
                    }
                    const target =
                        node.querySelector('.byte-checkbox-wrapper') ||
                        node.querySelector('.byte-radio-inner') ||
                        node;
                    target.dispatchEvent(new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }));
                }"""
            )
            return True
        except Exception:
            return False

    def _apply_cover_image(self, page, cover_path: Path, notes: list[str]) -> bool:
        """上传单图封面，返回**页面实际**是否有了封面。

        `_upload_cover_via_*` 返回的只是「这条策略自己认为成功了」，它曾在页面上什么
        都没有时也返回 True（2026-09-17 云端：编辑器里「已上传 0 张图片」、封面区还是
        空的 `+`，却没有任何一条策略报失败，日志里连 "All cover upload strategies
        failed" 都没出现）。所以这里不信策略的自述 —— **每条策略跑完都用
        `_is_cover_applied()` 复核页面实际状态**，把 claimed 与 actual 的对照写进日志与
        notes，并只在页面确实有封面时才判定成功。
        """
        self._dismiss_ai_assistant_drawer(page, notes)
        self._hide_ai_drawer_mask_css(page, notes)
        self._ensure_single_cover_mode(page)
        self._clear_existing_cover(page, notes)

        strategies = (
            ("file_chooser", self._upload_cover_via_file_chooser),
            ("direct_input", self._upload_cover_via_direct_input),
        )
        for name, strategy in strategies:
            claimed = False
            try:
                claimed = bool(strategy(page, cover_path, notes))
            except Exception as exc:
                self.logger.warning("Cover upload strategy %s raised: %s", name, exc)
                notes.append(f"Cover upload strategy {name} raised: {exc}")

            actual = self._is_cover_applied(page)
            self.logger.info(
                "Cover upload strategy %s: claimed=%s page_has_cover=%s",
                name,
                claimed,
                actual,
            )
            notes.append(
                f"Cover upload strategy {name}: claimed={claimed} page_has_cover={actual}"
            )
            if actual:
                if not claimed:
                    notes.append(
                        f"Strategy {name} reported failure but the page does have a cover."
                    )
                notes.append(f"Applied cover image: {cover_path.name}")
                return True

        self.logger.warning("All cover upload strategies failed. Falling back to no-cover mode.")
        notes.append("All cover upload strategies failed; falling back to no-cover mode.")
        self._ensure_no_cover_mode(page, notes)
        return False

    def _apply_three_cover(self, page, cover_paths: list[Path], notes: list[str]) -> None:
        """以三图封面模式向头条发布页上传三张图片。

        先切换到三图（three-image）封面 radio，再依次上传 cover/infographic 三张图。
        任一环节失败不中断发布：回退由调用方处理（当前调用方在 assets<3 时已回退单图，
        此处仅尽力而为，失败日志不影响主流程）。
        """
        self._dismiss_ai_assistant_drawer(page, notes)
        try:
            self._ensure_three_cover_mode(page)
        except Exception as exc:
            self.logger.warning("Failed to switch to three-image cover mode: %s", exc)
            notes.append(f"Three-image cover mode switch failed: {exc}")
        for idx, path in enumerate(cover_paths[:3], start=1):
            try:
                applied = self._upload_cover_via_file_chooser(page, path, notes)
                if not applied:
                    applied = self._upload_cover_via_direct_input(page, path, notes)
                notes.append(
                    f"Three-image cover slot {idx}: {'applied' if applied else 'failed'} {path.name}"
                )
            except Exception as exc:
                self.logger.warning("Three-image cover slot %d upload failed: %s", idx, exc)
                notes.append(f"Three-image cover slot {idx} upload failed: {exc}")

    def _ensure_three_cover_mode(self, page) -> None:
        self._ensure_radio_state(
            page,
            [
                ".article-cover-radio-group label.byte-radio:has(input[value='3'])",
                "label.byte-radio:has-text('三图')",
                "label.byte-radio:has-text('三封面')",
                "label.byte-radio:has-text('三张图')",
                "label:has-text('三图封面')",
                "label:has-text('多图封面')",
                "label.byte-radio:has(input[value='4'])",
            ],
        )

    def _upload_cover_via_file_chooser(self, page, cover_path: Path, notes: list[str]) -> bool:
        try:
            self._hide_ai_drawer_mask_css(page, notes)
            trigger_locator = self._resolve_cover_trigger(page)
            if trigger_locator is None:
                notes.append("Cover upload trigger not found for file-chooser strategy.")
                return False

            try:
                with page.expect_file_chooser(timeout=10000) as fc_info:
                    trigger_locator.click(force=True, timeout=5000)
                file_chooser = fc_info.value
                file_chooser.set_files(str(cover_path))
                self.logger.info("Cover uploaded via file-chooser triggered by cover area click.")
            except Exception:
                upload_btn = page.evaluate("""() => {
                    const btns = document.querySelectorAll('button, a, span, div');
                    for (const btn of btns) {
                        const text = (btn.textContent || '').trim();
                        if (text === '上传图片' || text === '上传' || text === '本地上传' || text === '选择图片') {
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) return true;
                        }
                    }
                    return false;
                }""")
                if upload_btn:
                    try:
                        with page.expect_file_chooser(timeout=10000) as fc_info:
                            page.evaluate("""() => {
                                const btns = document.querySelectorAll('button, a, span, div');
                                for (const btn of btns) {
                                    const text = (btn.textContent || '').trim();
                                    if (text === '上传图片' || text === '上传' || text === '本地上传' || text === '选择图片') {
                                        const rect = btn.getBoundingClientRect();
                                        if (rect.width > 0 && rect.height > 0) { btn.click(); return; }
                                    }
                                }
                            }""")
                        file_chooser = fc_info.value
                        file_chooser.set_files(str(cover_path))
                        self.logger.info("Cover uploaded via file-chooser triggered by text button.")
                    except Exception as exc:
                        self.logger.warning("File-chooser via text button failed: %s", exc)
                        return False
                else:
                    return False

            page.wait_for_timeout(3000)

            if self._is_cover_upload_panel_hidden(page):
                notes.append("Cover image auto-applied (upload panel closed after file upload).")
                return self._verify_cover_applied(page)

            self._select_uploaded_image_in_panel(page, notes)

            confirm_clicked = self._click_confirm_in_upload_panel(page, notes)

            if not confirm_clicked:
                try:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(800)
                except Exception:
                    pass

            timeout_ms = max(self.settings.publishing.wait_timeout_ms, 20000)
            deadline = datetime.now().timestamp() + (timeout_ms / 1000)
            while datetime.now().timestamp() < deadline:
                if self._is_cover_applied(page):
                    break
                if self._is_cover_upload_panel_hidden(page):
                    page.wait_for_timeout(400)
                else:
                    self._try_close_cover_upload_panel(page, notes)
                    page.wait_for_timeout(800)

            return self._verify_cover_applied(page)
        except Exception as exc:
            self.logger.warning("File-chooser cover upload strategy failed: %s", exc)
            return False

    def _upload_cover_via_js_data_transfer(self, page, cover_path: Path, notes: list[str]) -> bool:
        """JS DataTransfer 注入方式上传封面（2026-08-24 实测有效）。

        步骤：点击封面触发 → 打开上传面板 → JS 构造 File 对象注入 input →
        触发 change → 点"确定" → 等待封面区出现图片。
        绕过 Playwright file-chooser 事件与 is_visible 时序判断（此前两处都不可靠）。
        """
        try:
            import base64

            trigger_locator = self._resolve_cover_trigger(page)
            if trigger_locator is None:
                return False
            self._click_cover_trigger(trigger_locator)
            page.wait_for_timeout(1800)

            file_bytes = cover_path.read_bytes()
            b64 = base64.b64encode(file_bytes).decode("ascii")
            result = page.evaluate(
                """({b64, filename}) => {
                    const inputs = document.querySelectorAll("input[type='file']");
                    if (!inputs.length) return 'NO_INPUT';
                    let target = null;
                    for (const inp of inputs) {
                        const accept = (inp.getAttribute('accept') || '').toLowerCase();
                        if (accept.includes('image')) { target = inp; break; }
                    }
                    if (!target) target = inputs[inputs.length - 1];
                    const binary = atob(b64);
                    const bytes = new Uint8Array(binary.length);
                    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                    const file = new File([bytes], filename, { type: 'image/png' });
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    target.files = dt.files;
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    target.dispatchEvent(new Event('input', { bubbles: true }));
                    return 'OK';
                }""",
                {"b64": b64, "filename": cover_path.name},
            )
            if result != "OK":
                notes.append(f"JS DataTransfer upload failed: {result}")
                return False
            notes.append("Cover uploaded via JS DataTransfer injection.")

            page.wait_for_timeout(4000)

            # 点"确定"确认封面
            confirm_result = page.evaluate("""() => {
                const btn = document.querySelector("[data-e2e='imageUploadConfirm-btn']");
                if (btn) { btn.click(); return 'CLICKED'; }
                const buttons = document.querySelectorAll('.upload-image-panel button, .byte-modal__footer button');
                for (const b of buttons) {
                    if ((b.textContent || '').includes('确定')) { b.click(); return 'CLICKED2'; }
                }
                return 'NO_BTN';
            }""")
            if confirm_result.startswith("CLICKED"):
                notes.append("Cover upload confirm clicked via JS.")
            page.wait_for_timeout(3000)

            return self._verify_cover_applied(page)
        except Exception as exc:
            self.logger.warning("JS DataTransfer cover upload failed: %s", exc)
            return False

    def _upload_cover_via_direct_input(self, page, cover_path: Path, notes: list[str]) -> bool:
        try:
            self._hide_ai_drawer_mask_css(page, notes)
            trigger_locator = self._resolve_cover_trigger(page)
            if trigger_locator is None:
                notes.append("Cover upload trigger not found for direct-input strategy.")
                return False

            # 首选：JS DataTransfer 注入文件（2026-08-24 实测最可靠，
            # 不依赖 file-chooser 事件与 is_visible 时序判断）
            if self._upload_cover_via_js_data_transfer(page, cover_path, notes):
                return True

            # 点击封面触发点打开上传面板（面板内含 file input，直接 set_input_files 最可靠）
            self._click_cover_trigger(trigger_locator)
            page.wait_for_timeout(1500)
            upload_input = self._wait_for_cover_upload_input(page, existing_count=-1)
            if upload_input is None:
                page.wait_for_timeout(800)
                self._click_cover_trigger(trigger_locator)
                upload_input = self._wait_for_cover_upload_input(page, existing_count=-1)
            if upload_input is None:
                notes.append("Direct-input strategy: no file input found.")
                return False
            upload_input.set_input_files(str(cover_path), timeout=max(self.settings.publishing.wait_timeout_ms, 60000))

            page.wait_for_timeout(3000)

            if self._is_cover_upload_panel_hidden(page):
                notes.append("Cover image auto-applied via direct input (panel closed).")
                # 面板刚关闭时图片可能还在渲染到封面区，先等待图片真正出现再验证，
                # 避免误判"未应用"导致回退到无封面模式（2026-08-24 实测修复）。
                try:
                    page.locator(".article-cover img, .article-cover-images img").first.wait_for(
                        state="visible", timeout=8000
                    )
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                return self._verify_cover_applied(page)

            self._select_uploaded_image_in_panel(page, notes)

            confirm_clicked = self._click_confirm_in_upload_panel(page, notes)
            if not confirm_clicked:
                try:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(800)
                except Exception:
                    pass

            timeout_ms = max(self.settings.publishing.wait_timeout_ms, 20000)
            deadline = datetime.now().timestamp() + (timeout_ms / 1000)
            while datetime.now().timestamp() < deadline:
                if self._is_cover_applied(page):
                    break
                if self._is_cover_upload_panel_hidden(page):
                    page.wait_for_timeout(400)
                else:
                    self._try_close_cover_upload_panel(page, notes)
                    page.wait_for_timeout(800)

            return self._verify_cover_applied(page)
        except Exception as exc:
            self.logger.warning("Direct-input cover upload strategy failed: %s", exc)
            return False

    def _select_uploaded_image_in_panel(self, page, notes: list[str]) -> None:
        if self._is_cover_upload_panel_hidden(page):
            return
        for selector in [
            ".pic-select-image-item",
            ".pic-select-image-item-wrap",
            ".upload-image-wrapper img",
            ".upload-image-wrapper li",
            ".upload-image-panel li.image-item",
            ".upload-image-panel .image-list li",
            ".upload-image-panel img",
        ]:
            try:
                candidates = page.locator(selector)
                count = candidates.count()
                for index in range(count - 1, -1, -1):
                    candidate = candidates.nth(index)
                    try:
                        if not candidate.is_visible(timeout=300):
                            continue
                    except Exception:
                        continue
                    try:
                        candidate_text = self._clean_visible_text(candidate.text_content(timeout=300) or "")
                    except Exception:
                        candidate_text = ""
                    if "更多" in candidate_text:
                        continue
                    candidate.click(force=True, timeout=1500)
                    page.wait_for_timeout(600)
                    return
            except Exception:
                continue
        notes.append("Could not manually select cover image in picker; proceeding to confirm step.")

    def _click_confirm_in_upload_panel(self, page, notes: list[str]) -> bool:
        self._hide_ai_drawer_mask_css(page, notes)
        confirm_selectors = [
            "[data-e2e='imageUploadConfirm-btn']",
            ".upload-image-panel .footer .byte-btn-primary",
            ".upload-image-panel button:has-text('确定')",
            ".byte-modal__footer button.byte-btn-primary",
            "button.byte-btn-primary:has-text('确定')",
            ".upload-image-panel .footer button",
            ".byte-modal__footer button",
            "button:has-text('确定')",
            "button:has-text('完成')",
            "button:has-text('确认')",
            "[class*='upload'] button[class*='primary']",
            "[class*='image'] button[class*='primary']",
        ]
        confirm_clicked = self._maybe_click(page, confirm_selectors, required=False)
        if not confirm_clicked:
            page.wait_for_timeout(1500)
            confirm_clicked = self._maybe_click(page, confirm_selectors, required=False)
        if not confirm_clicked:
            # JS 兜底：直接触发确认按钮的 click 事件（2026-08-24 实测）
            try:
                clicked = page.evaluate("""() => {
                    const btn = document.querySelector("[data-e2e='imageUploadConfirm-btn']");
                    if (btn) { btn.click(); return true; }
                    const buttons = document.querySelectorAll('.upload-image-panel button');
                    for (const b of buttons) {
                        if ((b.textContent || '').includes('确定')) { b.click(); return true; }
                    }
                    return false;
                }""")
                if clicked:
                    notes.append("Cover upload confirm via JS click fallback.")
                    confirm_clicked = True
            except Exception as exc:
                self.logger.debug("Cover upload confirm JS fallback failed: %s", exc)
        if not confirm_clicked:
            notes.append("Cover upload confirm button not available; tried keyboard Enter as fallback.")
        return confirm_clicked

    def _verify_cover_applied(self, page) -> bool:
        if self._is_cover_applied(page):
            self._save_cover_verification_screenshot(page)
            return True
        try:
            has_img = page.evaluate("""() => {
                const coverArea = document.querySelector('.article-cover');
                if (!coverArea) return false;
                const coverImg = coverArea.querySelector('.article-cover-img-wrap img');
                if (coverImg) {
                    const r = coverImg.getBoundingClientRect();
                    if (r.width > 10 && r.height > 10) return true;
                }
                const imgs = coverArea.querySelectorAll('img');
                for (const img of imgs) {
                    const rect = img.getBoundingClientRect();
                    if (rect.width > 10 && rect.height > 10) return true;
                }
                return false;
            }""")
            result = bool(has_img)
            if result:
                self._save_cover_verification_screenshot(page)
            return result
        except Exception:
            return False

    def _save_cover_verification_screenshot(self, page) -> None:
        try:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            screenshot_dir = self.settings.output_dir / "cover_verification"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshot_dir / f"cover_applied_{timestamp}.png"
            page.screenshot(path=str(screenshot_path), full_page=False)
            self.logger.info("Cover verification screenshot saved: %s", screenshot_path)
        except Exception as exc:
            self.logger.debug("Failed to save cover verification screenshot: %s", exc)

    def _lookup_toutiao_article_by_id(
        self, page, *, pgc_id: str, title: str
    ) -> dict[str, Any] | None:
        """Read the creator-center status for this exact item after an ambiguous save."""
        expected_id = str(pgc_id or "").strip()
        if expected_id in {"", "0"}:
            return None
        last_match: dict[str, Any] | None = None
        for attempt in range(3):
            cursor = None
            try:
                for _ in range(3):
                    params: dict[str, Any] = {
                        "type": 0, "status": 0, "page_num": 1, "page_size": 20,
                    }
                    if cursor:
                        params["cursor"] = cursor
                    response = page.context.request.get(
                        "https://mp.toutiao.com/mp/agw/creator_center/list/v2",
                        params=params,
                        timeout=10000,
                    )
                    if not response.ok:
                        break
                    payload = response.json()
                    if not isinstance(payload, dict):
                        break
                    for content in payload.get("contents") or []:
                        item = content.get("article_attr") or {}
                        if str(item.get("gid", "")) != expected_id:
                            continue
                        if str(item.get("title", "")).strip() != title.strip():
                            self.logger.warning(
                                "Platform item id matched but title differed; refusing to reconcile pgc_id=%s",
                                expected_id,
                            )
                            return None
                        last_match = item
                        if item.get("status") == 2:
                            return item
                        break
                    if last_match or not payload.get("has_more"):
                        break
                    cursor = payload.get("end_cursor")
                    if not cursor:
                        break
            except Exception as exc:
                self.logger.warning("Platform list reconciliation attempt failed: %s", exc)
            if attempt < 2:
                page.wait_for_timeout(1000)
        return last_match

    def _verify_toutiao_publish_success(self, page, title: str, notes: list[str]) -> bool:
        """发布状态双重校验 — API 响应 + DOM 状态

        健壮性增强说明（保留原有内容管理列表检测，叠加 DOM 标志双重校验）：
        - 原实现：仅跳转到内容管理页检测标题是否出现
        - 新实现：
          1) 保留原有内容管理列表检测逻辑（向后兼容）
          2) 在检测前调用 verify_publish_dually 进行 API+DOM 双重校验
          3) DOM 层检测发布成功/失败的文本标志与 URL 跳转
          4) 若 DOM 检测到失败标志（如"内容违规""频率过高"），即使 API 成功也判为失败
        """
        try:
            content_mgmt_url = "https://mp.toutiao.com/profile_v4/graphic/publish"
            page.goto(content_mgmt_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

            # 健壮性增强：DOM 状态双重校验
            # 检测页面 URL 跳转 + 发布成功/失败的文本标志
            # 若检测到失败标志，即使 API 返回成功也判为失败（平台事后拒绝）
            try:
                dom_result = verify_publish_dually(
                    page,
                    api_success=True,  # 此处由调用方上下文保证 API 已成功
                    wait_timeout_ms=5000,
                    logger=self.logger,
                )
                for evidence in dom_result.evidence:
                    notes.append(f"DOM verification: {evidence}")
                if dom_result.final_status == "failure":
                    notes.append("DOM verification detected failure markers. Marking publish as failed.")
                    self.logger.warning("Toutiao publish DOM verification failed: %s", dom_result.evidence)
                    return False
            except Exception as exc:
                self.logger.debug("DOM dual verification skipped: %s", exc)

            title_snippet = title[:20] if len(title) > 20 else title
            found = page.evaluate("""(titleSnippet) => {
                const containers = document.querySelectorAll(
                    '[class*="article-item"], [class*="content-item"], ' +
                    '[class*="article-list"] a, [class*="pgc-item"], ' +
                    '.article-card, .content-card'
                );
                for (const el of containers) {
                    const text = (el.textContent || '').trim();
                    if (text.includes(titleSnippet)) return true;
                }
                return false;
            }""", title_snippet)

            if found:
                notes.append("Publish verified: article found in content management list.")
                self.logger.info("Toutiao publish verified: article '%s' found in content management.", title_snippet)
                return True
            else:
                notes.append("Publish verification: article not found in content list. May need manual check.")
                self.logger.warning("Toutiao publish verification: article '%s' not found in content management list.", title_snippet)
                try:
                    verify_dir = self.settings.output_dir / "publish_verification"
                    verify_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                    page.screenshot(path=str(verify_dir / f"verify_fail_{ts}.png"), full_page=False)
                except Exception:
                    pass
                return False
        except Exception as exc:
            notes.append(f"Publish verification skipped (navigation failed): {exc}")
            self.logger.warning("Toutiao publish verification failed: %s", exc)
            return False

    def _clear_existing_cover(self, page, notes: list[str]) -> None:
        if not self._is_cover_applied(page):
            return

        hovered = False
        for selector in [
            ".article-cover .article-cover-img-wrap",
            ".article-cover .article-cover-images",
            ".article-cover .article-cover-add",
        ]:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.hover(timeout=1000)
                    page.wait_for_timeout(250)
                    hovered = True
                    break
            except Exception:
                continue

        deleted = False
        for selector in [
            ".article-cover .article-cover-delete",
            ".article-cover-delete",
        ]:
            try:
                locator = page.locator(selector).first
                if self._click_button_candidate(locator):
                    deleted = True
                    notes.append(f"Removed existing cover via `{selector}` before uploading a new one.")
                    break
            except Exception:
                continue

        if not deleted:
            try:
                deleted = bool(
                    page.evaluate(
                        """() => {
                            const root = document.querySelector('.article-cover');
                            if (!root) {
                                return false;
                            }
                            const button = root.querySelector('.article-cover-delete');
                            if (!button) {
                                return false;
                            }
                            button.dispatchEvent(new MouseEvent('click', {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                            }));
                            return true;
                        }"""
                    )
                )
                if deleted:
                    notes.append("Removed existing cover via DOM fallback before uploading a new one.")
            except Exception:
                deleted = False

        if not deleted:
            if hovered:
                notes.append("Existing cover was detected, but the remove control was not clickable.")
            return

        deadline = datetime.now().timestamp() + 8
        while datetime.now().timestamp() < deadline:
            if not self._is_cover_applied(page):
                return
            page.wait_for_timeout(250)

    def _is_cover_upload_panel_hidden(self, page) -> bool:
        try:
            panel = page.locator(".upload-image-panel").first
            return not panel.is_visible(timeout=500)
        except Exception:
            return True

    def _is_cover_applied(self, page) -> bool:
        """封面区是否真的已经有一张可见的图。

        只认硬证据：`.article-cover` 区域里存在**可见**的 `img`。判定标准必须与
        `_verify_publish_media_state()` 一致，否则会出现「上传环节以为成功、发布校验
        却等不到封面」的死局 —— 2026-09-17 云端就是这样连续 3 次卡满重试的。

        这里原先还有三条**间接证据**分支，已全部移除：
        - `+` 按钮（`.article-cover-add`）不可见 → 页面没渲染完/被遮挡时同样成立；
        - 预览按钮（`.article-cover-preview .show-dialog-btn`）可见 → 没有封面时该按钮
          也在，只是 disabled，`is_visible()` 依旧返回 True；
        - `.byte-spin img`（加载动画）可见 → 转圈不等于有封面。
        """
        for selector in [
            ".article-cover img",
            ".article-cover-images img",
            ".article-cover .article-cover-img-wrap img",
        ]:
            try:
                if page.locator(selector).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue

        return False

    def _try_close_cover_upload_panel(self, page, notes: list[str]) -> None:
        for selector in [
            ".upload-image-panel .footer .byte-btn-primary",
            "[data-e2e='imageUploadConfirm-btn']",
            ".upload-image-panel button:has-text('确定')",
            ".upload-image-panel button:has-text('取消')",
            ".upload-image-panel .byte-modal-close-x",
            ".upload-image-panel .byte-modal-close-icon",
        ]:
            try:
                locator = page.locator(selector).first
                if not locator.is_visible(timeout=400):
                    continue
                if self._click_button_candidate(locator):
                    notes.append(f"Attempted to close cover upload panel via `{selector}`.")
                    return
            except Exception:
                continue

    def _close_stray_cover_panel(self, page, notes: list[str]) -> None:
        """关闭可能残留的封面上传面板/抽屉，避免其遮挡发布按钮。

        面板仍打开时优先点"确定"（图片已上传则完成封面选择并关闭面板），
        否则点"取消"或关闭图标；仍无法关闭时按 Escape 兜底，
        最后通过 CSS 强制隐藏 byte-drawer-wrapper 抽屉，保证发布按钮可点。
        """
        try:
            panel = page.locator(".upload-image-panel").first
            panel_visible = panel.is_visible(timeout=500)
        except Exception:
            panel_visible = False
        try:
            drawer = page.locator(".byte-drawer-wrapper").first
            drawer_visible = drawer.is_visible(timeout=500)
        except Exception:
            drawer_visible = False

        if not panel_visible and not drawer_visible:
            return
        notes.append("Detected stray cover upload panel/drawer before publish click; closing it.")

        if panel_visible:
            for selector in [
                "[data-e2e='imageUploadConfirm-btn']",
                ".upload-image-panel .footer .byte-btn-primary",
                ".upload-image-panel button:has-text('确定')",
            ]:
                try:
                    locator = page.locator(selector).first
                    if not locator.is_visible(timeout=400):
                        continue
                    if self._click_button_candidate(locator):
                        page.wait_for_timeout(1200)
                        try:
                            if not panel.is_visible(timeout=1000):
                                notes.append(f"Closed stray cover panel via confirm `{selector}`.")
                                panel_visible = False
                                break
                        except Exception:
                            panel_visible = False
                            break
                except Exception:
                    continue
            if panel_visible:
                for selector in [
                    ".upload-image-panel button:has-text('取消')",
                    ".upload-image-panel .byte-modal-close-x",
                    ".upload-image-panel .byte-modal-close-icon",
                    ".upload-image-panel .footer button.byte-btn-default",
                ]:
                    try:
                        locator = page.locator(selector).first
                        if not locator.is_visible(timeout=400):
                            continue
                        if self._click_button_candidate(locator):
                            page.wait_for_timeout(800)
                            try:
                                if not panel.is_visible(timeout=800):
                                    notes.append(f"Closed stray cover panel via cancel `{selector}`.")
                                    panel_visible = False
                                    break
                            except Exception:
                                panel_visible = False
                                break
                    except Exception:
                        continue

        # 关闭 byte-drawer-wrapper 抽屉（如 mp-ic-img-drawer 封面选择抽屉）
        if drawer_visible:
            for selector in [
                ".byte-drawer-close-icon",
                ".byte-drawer-wrapper .byte-drawer-close-icon",
                ".mp-ic-img-drawer .byte-drawer-close-icon",
                ".byte-drawer-header .byte-drawer-close",
                ".byte-drawer-wrapper .byte-drawer-header button",
            ]:
                try:
                    locator = page.locator(selector).first
                    if not locator.is_visible(timeout=400):
                        continue
                    if self._click_button_candidate(locator):
                        page.wait_for_timeout(800)
                        try:
                            if not drawer.is_visible(timeout=800):
                                notes.append(f"Closed stray drawer via `{selector}`.")
                                drawer_visible = False
                                break
                        except Exception:
                            drawer_visible = False
                            break
                except Exception:
                    continue

        # Escape 兜底
        if panel_visible or drawer_visible:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(800)
                panel_visible = bool(
                    panel.is_visible(timeout=600) if panel_visible else False
                )
                drawer_visible = bool(
                    drawer.is_visible(timeout=600) if drawer_visible else False
                )
                if not panel_visible and not drawer_visible:
                    notes.append("Closed stray cover panel/drawer via Escape key.")
                    return
            except Exception:
                pass

        # 最终兜底：CSS 强制隐藏 byte-drawer-wrapper（AI 抽屉 + 封面抽屉）
        if panel_visible or drawer_visible:
            try:
                page.evaluate("""() => {
                    const style = document.createElement('style');
                    style.id = 'stray-drawer-blocker';
                    style.textContent = `
                        .byte-drawer-wrapper,
                        .byte-drawer-mask,
                        .mp-ic-img-drawer,
                        [class*="mp-ic-img-drawer"] {
                            display: none !important;
                            visibility: hidden !important;
                            pointer-events: none !important;
                        }
                    `;
                    if (!document.getElementById('stray-drawer-blocker')) {
                        document.head.appendChild(style);
                    }
                }""")
                notes.append("Stray drawer hidden via CSS injection (stray-drawer-blocker).")
            except Exception as exc:
                notes.append(f"Stray drawer CSS injection failed: {exc}")

    def _resolve_cover_mode(self, cover_path: Path) -> str:
        configured = (self.settings.publishing.cover_mode or "auto").strip().lower()
        if configured in {"none", "single"}:
            return configured
        if configured == "three":
            return "three"
        return "single"

    def _resolve_three_cover_assets(self, run_dir: Path | None) -> list[Path]:
        """解析三图封面的三张可用图片。

        优先复用已生成的 cover.png 与 infographic_1/2.png；若不足三张，
        用 cover.png 复制补齐到三张，保证三图封面始终有三张合规可上传图。
        """
        if run_dir is None:
            return []
        assets: list[Path] = []
        cover: Path | None = None
        seen: set[Path] = set()
        for filename in ("cover.png", "infographic_1.png", "infographic_2.png"):
            cand = self._resolve_existing_publish_asset(
                run_dir=run_dir,
                publish_dir_name="images",
                filename=filename,
            )
            if cand is not None and cand.exists() and cand not in seen:
                seen.add(cand)
                assets.append(cand)
                if filename == "cover.png":
                    cover = cand
        if len(assets) < 3 and cover is not None:
            # 素材不足：用 cover.png 补齐到三张，保证三图封面三张齐备（同一主题近似图）
            while len(assets) < 3:
                assets.append(cover)
        return assets[:3]

    def _ensure_no_cover_mode(self, page, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)

        no_cover_selectors = [
            ".article-cover-radio-group label.byte-radio:has(input[value='1'])",
            "label.byte-radio:has-text('无封面')",
            "label.byte-radio:has-text('无图')",
            ".article-cover-radio-group label:last-child",
        ]
        for selector in no_cover_selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=2000):
                    locator.click(timeout=2000)
                    page.wait_for_timeout(500)
                    cover_type = page.evaluate("""() => {
                        const radios = document.querySelectorAll('.article-cover-radio-group input[type="radio"]');
                        for (const input of radios) {
                            if (input.checked) return input.value;
                        }
                        return null;
                    }""")
                    if cover_type == "1":
                        notes.append(f"Switched to no-cover mode via click on: {selector}")
                        return
            except Exception:
                continue

        try:
            js_result = page.evaluate("""() => {
                const radios = document.querySelectorAll('.article-cover-radio-group input[type="radio"]');
                let targetInput = null;
                for (const input of radios) {
                    if (input.value === '1') {
                        targetInput = input;
                        break;
                    }
                }
                if (!targetInput) {
                    for (const input of radios) {
                        const label = input.closest('label');
                        if (label && (label.textContent.includes('无封面') || label.textContent.includes('无图'))) {
                            targetInput = input;
                            break;
                        }
                    }
                }
                if (!targetInput && radios.length > 0) {
                    targetInput = radios[radios.length - 1];
                }
                if (!targetInput) return 'no radio found';

                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'checked'
                ).set;
                nativeInputValueSetter.call(targetInput, true);

                for (const input of radios) {
                    if (input !== targetInput) {
                        nativeInputValueSetter.call(input, false);
                    }
                }

                targetInput.dispatchEvent(new Event('change', { bubbles: true }));
                targetInput.dispatchEvent(new Event('input', { bubbles: true }));
                targetInput.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

                const label = targetInput.closest('label');
                if (label) {
                    label.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                }

                return 'set checked + events on value=' + targetInput.value;
            }""")
            notes.append(f"JS set no-cover radio: {js_result}")
        except Exception as exc:
            notes.append(f"JS set no-cover radio error: {exc}")

        page.wait_for_timeout(800)

        try:
            cover_type = page.evaluate("""() => {
                const radios = document.querySelectorAll('.article-cover-radio-group input[type="radio"]');
                for (const input of radios) {
                    if (input.checked) return input.value;
                }
                return null;
            }""")
            notes.append(f"Cover radio state after set: value={cover_type}")
        except Exception:
            notes.append("Could not verify cover radio state after set.")

    def _install_save_mode_override(self, page, notes: list[str]) -> None:
        try:
            state: dict[str, Any] = {
                "consumed": False,
                "ready": False,
                "expected_inline_images": 0,
                "expected_refs": [],
                "blocked_request_count": 0,
                "blocked_note_written": False,
                "observed_request_count": 0,
                "rewritten_request_count": 0,
                "last_request": {},
                "request_log": [],
                "last_error": "",
            }
            self._save_override_state = state
            self._save_override_applied_at = [None]

            def handle_route(route):
                post_data = ""
                save_mode: int | None = None
                request_record: dict[str, Any] = {}
                try:
                    post_data = route.request.post_data or ""
                    save_mode = self._extract_save_mode(post_data)
                    state["observed_request_count"] += 1
                    request_record = {
                        "captured_at": datetime.now().isoformat(timespec="milliseconds"),
                        "save_mode": save_mode,
                        "post_data_length": len(post_data),
                        "gate_ready": bool(state["ready"]),
                        "expected_inline_images": int(state["expected_inline_images"]),
                        "expected_refs_count": len(state["expected_refs"]),
                        "decision": "observed",
                    }
                    state["last_request"] = request_record

                    if not post_data:
                        route.continue_()
                        return

                    has_pgc_id = self._post_data_has_persisted_pgc_id(post_data)
                    save_zero_request = save_mode == 0
                    modified = False

                    if save_zero_request and not state["consumed"] and not has_pgc_id:
                        media_payload = self._summarize_publish_media_payload(
                            post_data,
                            expected_inline_images=int(state["expected_inline_images"]),
                            expected_refs=list(state["expected_refs"]),
                            editor_state_verified=bool(state["ready"]),
                        )
                        ready_for_override = bool(state["ready"]) and bool(media_payload["verified"])
                        request_record.update(
                            {
                                "has_persisted_pgc_id": has_pgc_id,
                                "media_payload": media_payload,
                                "ready_for_override": ready_for_override,
                            }
                        )
                        if not ready_for_override:
                            state["blocked_request_count"] += 1
                            request_record["decision"] = "blocked_not_ready"
                            if not state["blocked_note_written"]:
                                notes.append(
                                    "Blocked a pre-media save=0 request; save=1 override remains armed "
                                    "until the editor payload is ready."
                                )
                                state["blocked_note_written"] = True
                            route.abort(error_code="blockedbyclient")
                            return

                        rewritten_post_data, modified = self._rewrite_publish_request(
                            post_data,
                            save_mode=1,
                            cover_type=1,
                        )
                        if not modified or self._extract_save_mode(rewritten_post_data) != 1:
                            request_record.update(
                                {
                                    "decision": "blocked_rewrite_failed",
                                    "rewrite_error": "save=0 could not be converted to save=1",
                                }
                            )
                            state["last_error"] = "save=0 could not be converted to save=1"
                            route.abort(error_code="blockedbyclient")
                            return

                        post_data = rewritten_post_data
                        state["consumed"] = True
                        state["rewritten_request_count"] += 1
                        self._save_override_applied_at[0] = datetime.now().isoformat(timespec="milliseconds")
                        request_record.update(
                            {
                                "decision": "rewritten_save_0_to_save_1",
                                "rewritten_save_mode": self._extract_save_mode(post_data),
                            }
                        )
                        modified = True
                    elif save_zero_request:
                        request_record.update(
                            {
                                "has_persisted_pgc_id": has_pgc_id,
                                "decision": "passed_save_0_after_consumed",
                            }
                        )

                    if not save_zero_request or state["consumed"] or modified:
                        rewritten_post_data, cover_modified = self._rewrite_publish_request(
                            post_data,
                            cover_type=1,
                        )
                        if cover_modified:
                            post_data = rewritten_post_data
                            modified = True

                    if modified:
                        route.continue_(post_data=post_data)
                    else:
                        route.continue_()
                except Exception as exc:
                    state["last_error"] = self._clean_visible_text(exc)
                    request_record.update(
                        {
                            "decision": "blocked_handler_error" if save_mode == 0 else "passed_handler_error",
                            "error": state["last_error"],
                        }
                    )
                    state["last_request"] = request_record
                    # Never allow an unmodified save=0 request through after the guard failed.
                    # That request is the known 7050 failure mode.
                    if save_mode == 0 and not state["consumed"]:
                        state["blocked_request_count"] += 1
                        try:
                            route.abort(error_code="blockedbyclient")
                        except Exception:
                            pass
                        return
                    route.continue_()
                finally:
                    if request_record:
                        state["request_log"].append(dict(request_record))
                        del state["request_log"][:-50]

            page.route("**/mp/agw/article/publish**", handle_route)
            notes.append("Installed save=1 + coverType=1 override route handler.")
        except Exception as exc:
            notes.append(f"Failed to install save mode override: {exc}")

    def _post_data_has_persisted_pgc_id(self, post_data: str) -> bool:
        if not post_data:
            return False

        stripped = post_data.strip()
        if stripped.startswith(("{", "[")):
            try:
                payload = json.loads(stripped)
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                value = payload.get("pgc_id")
                if value is not None and str(value).strip() not in {"", "0"}:
                    return True

        try:
            parsed = parse_qs(post_data, keep_blank_values=True)
        except Exception:
            parsed = {}
        values = parsed.get("pgc_id", [])
        return any(str(value).strip() not in {"", "0"} for value in values)

    def _rewrite_publish_request(
        self,
        post_data: str,
        *,
        save_mode: int | None = None,
        cover_type: int | None = None,
    ) -> tuple[str, bool]:
        """Rewrite only publish form fields while preserving the article payload."""
        if not post_data or (save_mode is None and cover_type is None):
            return post_data, False

        stripped = post_data.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                changed = False
                if save_mode is not None and str(payload.get("save", "")) != str(save_mode):
                    payload["save"] = save_mode
                    changed = True
                if cover_type is not None:
                    changed = self._set_publish_cover_type(payload, cover_type) or changed
                if changed:
                    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), True

        try:
            pairs = parse_qsl(post_data, keep_blank_values=True)
        except Exception:
            pairs = []
        if pairs:
            changed = False
            save_found = False
            cover_found = False
            rewritten_pairs: list[tuple[str, str]] = []
            for key, value in pairs:
                normalized_key = str(key)
                rewritten_value = value
                if save_mode is not None and normalized_key == "save":
                    save_found = True
                    if value != str(save_mode):
                        rewritten_value = str(save_mode)
                        changed = True
                if cover_type is not None and normalized_key == "draft_form_data":
                    cover_found = True
                    rewritten_value, cover_changed = self._rewrite_draft_form_data(
                        value,
                        cover_type,
                    )
                    changed = cover_changed or changed
                rewritten_pairs.append((normalized_key, rewritten_value))

            if save_mode is not None and not save_found:
                rewritten_pairs.append(("save", str(save_mode)))
                changed = True
            if cover_type is not None and not cover_found:
                rewritten_pairs.append(
                    ("draft_form_data", json.dumps({"coverType": cover_type}, separators=(",", ":")))
                )
                changed = True
            if changed:
                return urlencode(rewritten_pairs), True

        # Keep a narrow fallback for legacy request bodies that are not parseable as a form.
        rewritten = post_data
        changed = False
        if save_mode is not None:
            pattern = re.compile(r"(^|&)save=\d+(?=&|$)")
            rewritten, count = pattern.subn(
                lambda match: f"{match.group(1)}save={save_mode}",
                rewritten,
                count=1,
            )
            changed = count > 0
        if cover_type is not None:
            rewritten, count = re.subn(
                r"(coverType%22%3A)\d+",
                lambda match: f"{match.group(1)}{cover_type}",
                rewritten,
                count=1,
            )
            changed = count > 0 or changed
        return rewritten, changed

    def _set_publish_cover_type(self, payload: dict[str, Any], cover_type: int) -> bool:
        changed = False
        for key in ("draft_form_data", "draftFormData"):
            if key not in payload:
                continue
            value = payload.get(key)
            if isinstance(value, str):
                rewritten, value_changed = self._rewrite_draft_form_data(value, cover_type)
                if value_changed:
                    payload[key] = rewritten
                    changed = True
            elif isinstance(value, dict):
                if value.get("coverType") != cover_type:
                    value["coverType"] = cover_type
                    changed = True
        if "draft_form_data" not in payload and "draftFormData" not in payload:
            payload["draft_form_data"] = {"coverType": cover_type}
            changed = True
        return changed

    def _rewrite_draft_form_data(self, value: str, cover_type: int) -> tuple[str, bool]:
        try:
            payload = json.loads(value)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            if payload.get("coverType") == cover_type:
                return value, False
            payload["coverType"] = cover_type
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), True
        return value, False

    def _update_save_mode_override_gate(
        self,
        *,
        expected_inline_images: int,
        expected_refs: list[str] | tuple[str, ...],
        ready: bool,
    ) -> None:
        state = self._save_override_state
        if not isinstance(state, dict):
            return
        state["expected_inline_images"] = max(int(expected_inline_images), 0)
        state["expected_refs"] = [
            str(ref).strip()
            for ref in expected_refs
            if str(ref).strip()
        ]
        state["ready"] = bool(ready)

    def _disable_save_mode_override(self, page, notes: list[str]) -> None:
        try:
            page.unroute("**/mp/agw/article/publish**")
            self._save_override_state = None
            notes.append("Removed save=1 override route handler to prevent duplicate publishes.")
        except Exception as exc:
            notes.append(f"Failed to remove save mode override route (non-fatal): {exc}")

    def _ensure_single_cover_mode(self, page) -> None:
        self._ensure_radio_state(
            page,
            [
                ".article-cover-radio-group label.byte-radio:has(input[value='2'])",
                "label.byte-radio:has-text('单图')",
                "label.byte-radio:has-text('单封面')",
                "label.byte-radio:has-text('一张图')",
                "label:has-text('单图封面')",
            ],
        )

    def _resolve_cover_trigger(self, page):
        selectors = [
            ".article-cover-add",
            ".article-cover-add .article-cover-add-icon",
            ".article-cover .article-cover-add",
            ".article-cover .article-cover-img-wrap",
            ".article-cover-img-replace",
            ".article-cover-img-modify",
            ".article-cover .article-cover-img-replace",
            ".article-cover .article-cover-img-modify",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=1500)
                return locator
            except Exception:
                continue
        return None

    def _open_cover_file_chooser(self, page, trigger_locator):
        try:
            with page.expect_file_chooser(timeout=1800) as chooser_info:
                self._click_cover_trigger(trigger_locator)
            return chooser_info.value
        except PlaywrightTimeoutError:
            return None
        except Exception:
            return None

    def _click_cover_trigger(self, trigger_locator) -> None:
        try:
            trigger_locator.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass

        if self._click_button_candidate(trigger_locator):
            return

        try:
            trigger_locator.hover(timeout=800)
        except Exception:
            pass

        if self._click_button_candidate(trigger_locator):
            return

        try:
            trigger_locator.evaluate(
                """(node) => {
                    const target =
                        node.querySelector('button') ||
                        node.querySelector('.article-cover-add-icon') ||
                        node.querySelector('.article-cover-img-replace') ||
                        node.querySelector('.article-cover-img-modify') ||
                        node;
                    target.dispatchEvent(new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }));
                }"""
            )
            return
        except Exception as exc:
            raise RuntimeError(f"Failed to activate the cover upload trigger: {exc}") from exc

    def _wait_for_cover_upload_input(self, page, existing_count: int = 0):
        selectors = [
            ".upload-image-panel .btn-upload-handle input[type='file']",
            ".upload-image-panel .upload-handler input[type='file']",
            ".upload-image-panel .image-item-upload input[type='file']",
            ".upload-image-panel input#upload-drag-input",
            ".upload-image-panel input[type='file']",
            ".byte-modal-wrapper .upload-image-panel input[type='file']",
            ".upload-handler input[type='file']",
            ".image-item-upload input[type='file']",
            ".btn-upload-handle input[type='file']",
            "input#upload-drag-input",
            "input[type='file']",
        ]
        deadline = datetime.now().timestamp() + (max(self.settings.publishing.wait_timeout_ms, 20000) / 1000)
        while datetime.now().timestamp() < deadline:
            for selector in selectors:
                try:
                    locator_group = page.locator(selector)
                    count = locator_group.count()
                except Exception:
                    continue

                for index in range(count - 1, -1, -1):
                    if existing_count >= 0 and selector == "input[type='file']" and count <= existing_count and index < existing_count:
                        continue
                    locator = locator_group.nth(index)
                    try:
                        accept_value = (locator.get_attribute("accept") or "").lower()
                    except Exception:
                        accept_value = ""
                    if accept_value and "image" not in accept_value and ".png" not in accept_value and ".jpg" not in accept_value and ".jpeg" not in accept_value:
                        continue
                    try:
                        locator.wait_for(state="attached", timeout=500)
                        return locator
                    except Exception:
                        continue
            page.wait_for_timeout(250)
        return None

    def _confirm_publish_modal(self, page, notes: list[str]) -> None:
        clicked_texts: list[str] = []

        for _ in range(6):
            timed_publish_selector = self._first_visible_selector(
                page,
                [
                    "div.byte-modal-wrapper button:has-text('预览并定时发布')",
                ],
                strict=False,
            )
            if timed_publish_selector:
                if self._maybe_click(
                    page,
                    [
                        "div.byte-modal-wrapper button:has-text('取消')",
                        "div.byte-modal-wrapper .byte-modal-close-icon",
                    ],
                    required=False,
                ):
                    notes.append("Closed timed publish modal and kept immediate publish flow.")
                    page.wait_for_timeout(900)
                    continue

            selector = self._first_visible_selector(
                page,
                [
                    "div.byte-modal-wrapper button:has-text('继续发布')",
                    "div.byte-modal-wrapper button:has-text('确认')",
                    "div.byte-modal-wrapper button:has-text('发布')",
                    "div.byte-modal-wrapper button:has-text('确定')",
                    "div.byte-modal-wrapper button.byte-btn-primary",
                ],
                strict=False,
            )
            if not selector:
                break

            try:
                locator = page.locator(selector).first
                button_text = locator.inner_text(timeout=500).strip()
                if "定时发布" in button_text:
                    break
                locator.click(force=True, timeout=1500)
                clicked_texts.append(button_text or selector)
                page.wait_for_timeout(900)
            except Exception:
                break

        if clicked_texts:
            notes.append(f"Confirmed publish modal via: {' -> '.join(clicked_texts)}")

    def _blur_active_element(self, page) -> None:
        try:
            page.evaluate(
                """() => {
                    const active = document.activeElement;
                    if (active && typeof active.blur === 'function') {
                        active.blur();
                    }
                }"""
            )
        except Exception:
            pass

    def _click_publish_surface(self, page) -> bool:
        for selector in [
            ".publish-editor-title-wrapper",
            ".publish-editor",
            ".article-cover",
            ".left-column",
            "body",
        ]:
            try:
                page.locator(selector).first.click(position={"x": 12, "y": 12}, force=True, timeout=800)
                return True
            except Exception:
                continue
        return False

    def _finalize_editor_state(self, page, title_locator, editor_selector: str, expected_title: str, notes: list[str]) -> None:
        try:
            title_locator.press("Tab", timeout=1000)
        except Exception:
            pass

        page.wait_for_timeout(300)

        try:
            page.locator(editor_selector).first.press("Control+End", timeout=1000)
        except Exception:
            pass

        self._blur_active_element(page)

        clicked = self._click_publish_surface(page)

        if not clicked:
            try:
                page.mouse.click(20, 20)
            except Exception:
                pass

        page.wait_for_timeout(1200)
        observed_title = self._read_title_input(title_locator)
        if not self._title_matches(observed_title, expected_title):
            notes.append(f"Title changed after blur. Reapplying expected title. Observed: {observed_title!r}")
            self._set_title_content(page, title_locator, expected_title, notes)
            page.wait_for_timeout(500)
            observed_title = self._read_title_input(title_locator)
            if not self._title_matches(observed_title, expected_title):
                raise RuntimeError(
                    "Title field did not remain stable after blur. "
                    f"Expected: {expected_title!r}, observed: {observed_title!r}"
                )
        notes.append("Triggered blur on title and editor to encourage draft autosave.")

    def _stabilize_publish_form(self, page, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        self._blur_active_element(page)

        clicked = self._click_publish_surface(page)
        if not clicked:
            try:
                page.mouse.click(20, 20)
            except Exception:
                pass

        page.wait_for_timeout(3500)
        footer_text = self._read_draft_status_text(page)
        if footer_text:
            notes.append(f"Draft footer after no-cover selection: {self._clean_visible_text(footer_text)}")
        else:
            notes.append("Waited for Toutiao autosave after no-cover selection.")

    def _install_publish_event_capture(
        self,
        page,
        bucket: list[dict[str, Any]],
        *,
        patterns: tuple[str, ...] | None = None,
        post_only: bool = False,
    ) -> None:
        event_patterns = patterns or self.PUBLISH_EVENT_PATTERNS

        def handle_response(response) -> None:
            request = response.request
            if post_only and str(request.method).upper() not in {"POST", "PUT", "PATCH"}:
                return
            if not any(pattern in response.url for pattern in event_patterns):
                return

            try:
                post_data = request.post_data or ""
            except Exception:
                # Image transfers can carry binary multipart data; only JSON/form
                # publication bodies belong in the submission evidence.
                post_data = ""
            save_mode = self._extract_save_mode(post_data)
            override_ts = getattr(self, "_save_override_applied_at", [None])[0]
            if save_mode == 0 and override_ts is not None and response.status == 200:
                try:
                    text = response.text()
                    rj = json.loads(text) if text else {}
                    if rj.get("err_no") == 0 and "pgc_id" in str(rj.get("data", {})):
                        save_mode = 1
                except Exception:
                    pass

            payload: dict[str, Any] = {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "url": response.url,
                "method": request.method,
                "status": response.status,
                "post_data": post_data,
                "save_mode": save_mode,
                "media_payload": self._summarize_publish_media_payload(
                    post_data,
                    expected_inline_images=0,
                ),
            }
            try:
                text = response.text()
            except Exception as exc:
                text = f"<<unavailable: {exc}>>"
            payload["response_text"] = text
            content_type = ""
            try:
                content_type = str((response.headers or {}).get("content-type", ""))
            except Exception:
                pass
            response_is_html = "text/html" in content_type.lower() or looks_like_html_document(text)
            payload["response_is_html"] = response_is_html
            try:
                payload["response_json"] = None if response_is_html else json.loads(text)
            except Exception:
                payload["response_json"] = None
            payload["save_override_state"] = self._save_override_debug_snapshot()
            bucket.append(payload)

        def handle_request_failed(request) -> None:
            if post_only and str(request.method).upper() not in {"POST", "PUT", "PATCH"}:
                return
            if not any(pattern in request.url for pattern in event_patterns):
                return
            failure = request.failure or {}
            bucket.append(
                {
                    "captured_at": datetime.now().isoformat(timespec="seconds"),
                    "url": request.url,
                    "method": request.method,
                    "status": "requestfailed",
                    "post_data": request.post_data or "",
                    "save_mode": self._extract_save_mode(request.post_data or ""),
                    "media_payload": self._summarize_publish_media_payload(
                        request.post_data or "",
                        expected_inline_images=0,
                    ),
                    "response_text": str(failure),
                    "response_json": None,
                    "response_is_html": False,
                    "save_override_state": self._save_override_debug_snapshot(),
                }
            )

        page.on("response", handle_response)
        page.on("requestfailed", handle_request_failed)

    def _install_publish_diagnostic_capture(self, page, bucket: list[dict[str, Any]]) -> None:
        def handle_response(response) -> None:
            if not any(pattern in response.url for pattern in self.DIAGNOSTIC_EVENT_PATTERNS):
                return

            payload: dict[str, Any] = {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "url": response.url,
                "status": response.status,
            }
            try:
                text = response.text()
            except Exception as exc:
                text = f"<<unavailable: {exc}>>"
            payload["response_text"] = text
            try:
                payload["response_json"] = json.loads(text)
            except Exception:
                payload["response_json"] = None
            bucket.append(payload)

        page.on("response", handle_response)

    def _save_override_debug_snapshot(self, *, include_log: bool = False) -> dict[str, Any]:
        state = self._save_override_state
        if not isinstance(state, dict):
            return {}
        snapshot: dict[str, Any] = {
            "consumed": bool(state.get("consumed")),
            "ready": bool(state.get("ready")),
            "expected_inline_images": int(state.get("expected_inline_images", 0)),
            "expected_refs": list(state.get("expected_refs", [])),
            "blocked_request_count": int(state.get("blocked_request_count", 0)),
            "observed_request_count": int(state.get("observed_request_count", 0)),
            "rewritten_request_count": int(state.get("rewritten_request_count", 0)),
            "last_error": str(state.get("last_error", "")),
            "last_request": dict(state.get("last_request") or {}),
        }
        if include_log:
            snapshot["request_log"] = [
                dict(item)
                for item in list(state.get("request_log") or [])[-50:]
                if isinstance(item, dict)
            ]
        return snapshot

    def _latest_publish_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
        save_mode: int,
        success_only: bool,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            if event.get("save_mode") != save_mode:
                continue
            if success_only and not self._publish_response_is_success(event.get("response_json")):
                continue
            return event
        return None

    def _read_draft_status_text(self, page) -> str:
        selector = self._first_visible_selector(page, self.DEFAULT_DRAFT_STATUS_SELECTORS, strict=False)
        if not selector:
            return ""
        try:
            return page.locator(selector).first.inner_text(timeout=500).strip()
        except Exception:
            return ""

    def _draft_status_indicates_saving(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(marker in normalized for marker in ["保存中", "草稿保存中", "draft saving"])

    def _draft_status_indicates_saved(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(marker in normalized for marker in ["已保存", "草稿已保存", "draft saved"])

    def _wait_for_publish_submit(
        self,
        page,
        publish_events: list[dict[str, Any]],
        start_index: int,
    ) -> dict[str, Any]:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 30000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)

        while datetime.now().timestamp() < deadline:
            event = self._latest_submit_event(
                publish_events,
                start_index=start_index,
            )
            if event is not None:
                return event
            page.wait_for_timeout(300)

        debug_dir = self._capture_debug_artifacts(
            page,
            extra_payload={"publish_events": publish_events},
        )
        raise RuntimeError(
            "No publish submission response was captured from Toutiao. "
            f"Debug files saved to: {debug_dir}"
        )

    def _latest_submit_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            if not self._is_final_submit_event(event):
                continue
            return event
        return None

    def _latest_preview_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            url = str(event.get("url", ""))
            if "/mp/agw/article/publish" not in url:
                continue
            post_data = str(event.get("post_data", ""))
            if "is_app_preview=1" in post_data:
                return event
        return None

    def _is_final_submit_event(self, event: dict[str, Any]) -> bool:
        url = str(event.get("url", ""))
        if "/mp/agw/article/publish" not in url:
            return False

        post_data = str(event.get("post_data", ""))
        if "is_app_preview=1" in post_data:
            return False

        return True

    def _extract_save_mode(self, post_data: str) -> int | None:
        if not post_data:
            return None

        stripped = post_data.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except Exception:
                payload = {}
            value = payload.get("save")
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

        try:
            parsed = parse_qs(post_data, keep_blank_values=True)
        except Exception:
            parsed = {}
        if "save" in parsed and parsed["save"]:
            try:
                return int(parsed["save"][0])
            except (TypeError, ValueError):
                return None

        match = re.search(r"(?:^|[&,{\"'])save(?:[\"']?\s*:\s*|=)(\d+)", post_data)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                return None
        return None

    def _publish_response_is_success(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        if self._contains_html_document(payload):
            return False

        code = payload.get("code")
        err_no = payload.get("err_no")
        if code is None and err_no is None:
            return False

        try:
            if code is not None and int(code) != 0:
                return False
        except (TypeError, ValueError):
            return False

        try:
            if err_no is not None and int(err_no) != 0:
                return False
        except (TypeError, ValueError):
            return False

        return True

    @staticmethod
    def _contains_html_document(value: Any) -> bool:
        if isinstance(value, str):
            return looks_like_html_document(value)
        if isinstance(value, dict):
            return any(PublishService._contains_html_document(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(PublishService._contains_html_document(item) for item in value)
        return False

    def _has_accepted_submission_evidence(
        self,
        payload: Any,
        *,
        pgc_id: str,
        expected_inline_images: int,
        media_payload: dict[str, Any],
    ) -> bool:
        """判断一次 save=1 响应是否足以证明文章已被头条接收。

        页面底部的草稿状态可能滞后于网络响应，因此不能把 UI 文案作为
        已成功提交的唯一依据。后端成功码、非零 pgc_id 和正文图片持久化
        载荷共同构成这里的最小证据集。
        """
        normalized_pgc_id = str(pgc_id or "").strip()
        if not self._publish_response_is_success(payload) or normalized_pgc_id in {"", "0"}:
            return False
        if int(expected_inline_images or 0) > 0 and not bool(media_payload.get("verified")):
            return False
        return True

    def _extract_publish_error(self, payload: Any) -> tuple[str, str]:
        if not isinstance(payload, dict):
            return ("unknown", "response body was not valid JSON")
        code = str(payload.get("code", payload.get("err_no", "unknown")))
        message = self._clean_visible_text(str(payload.get("message", payload.get("reason", "unknown error"))))
        return code, message

    def _build_publish_diagnostics(self, diagnostic_events: list[dict[str, Any]]) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        for event in diagnostic_events:
            url = str(event.get("url", ""))
            payload = event.get("response_json")
            if "/mp/agw/media/get_user_base_info" in url:
                raw["user_base_info"] = payload
            elif "/mp/agw/creator_center/user_info" in url:
                raw["creator_user_info"] = payload
            elif "/mp/agw/article/new" in url:
                raw["article_new"] = payload
            elif "/mp/agw/media/check_user_auth" in url:
                raw["check_user_auth"] = payload

        creator_user_info = raw.get("creator_user_info") if isinstance(raw.get("creator_user_info"), dict) else {}
        article_new = raw.get("article_new") if isinstance(raw.get("article_new"), dict) else {}
        article_data = article_new.get("data") if isinstance(article_new.get("data"), dict) else {}
        article_media = article_data.get("media") if isinstance(article_data.get("media"), dict) else {}
        content_cache = (
            article_media.get("content_cache")
            if isinstance(article_media.get("content_cache"), dict)
            else {}
        )
        check_user_auth = raw.get("check_user_auth") if isinstance(raw.get("check_user_auth"), dict) else {}
        user_base_info = raw.get("user_base_info") if isinstance(raw.get("user_base_info"), dict) else {}

        summary = {
            "creator_name": self._clean_visible_text(creator_user_info.get("name")),
            "is_creator": creator_user_info.get("is_creator"),
            "auth_type": creator_user_info.get("auth_type"),
            "media_status": user_base_info.get("media_status"),
            "is_auth": user_base_info.get("is_auth"),
            "has_user_auth": check_user_auth.get("has_auth"),
            "default_article_ad_type": article_data.get("article_ad_type"),
            "article_can_change_ad": article_data.get("article_can_change_ad"),
            "claim_origin_permission": article_data.get("claim_origin_permission"),
            "ads_permission": article_media.get("ads_permission"),
            "manual_publish_ad": content_cache.get("manual_publish_ad"),
            "content_cache_source": content_cache.get("source"),
            "modify_source": content_cache.get("modify_source"),
            "not_claim_origin_cnt": content_cache.get("not_claim_origin_cnt"),
        }
        return {
            "summary": summary,
            "suspicious_flags": self._summarize_publish_blockers(summary),
            "raw": raw,
        }

    def _build_preflight_summary_from_diagnostics(self, current_url: str, diagnostics: dict[str, Any]) -> dict[str, Any]:
        summary = diagnostics.get("summary", {}) if isinstance(diagnostics, dict) else {}
        suspicious_flags = list(diagnostics.get("suspicious_flags", [])) if isinstance(diagnostics, dict) else []
        blocking_flags: list[str] = []
        warnings: list[str] = []

        if "/profile_v4/graphic/publish" not in current_url:
            warnings.append(f"Current page is not the direct publish page: {current_url}")
        if not summary:
            blocking_flags.append("No publish diagnostics were captured from the current session.")
        elif not self._diagnostics_indicate_publish_capable_session(summary, current_url):
            blocking_flags.append("Current browser session does not appear to have an accessible Toutiao publish page.")
        # media_status=4 是本账号（手蓝数据笔记，个人号）在 2026-09-15/16 多次发布的
        # 稳定取值，属正常；此前一直报 "Unexpected media status: 4" 是纯噪音。
        if summary.get("media_status") not in {None, 1, "1", 4, "4"}:
            warnings.append(f"Unexpected media status: {summary.get('media_status')}")
        if suspicious_flags:
            warnings.extend(f"Diagnostic flag: {item}" for item in suspicious_flags[:6])

        return {
            "current_url": current_url,
            "blocking_flags": self._unique_texts(blocking_flags),
            "warnings": self._unique_texts(warnings),
            "diagnostics": self._compact_publish_diagnostics(diagnostics),
        }

    def _should_bypass_preflight_blockers(self, preflight_summary: dict[str, Any]) -> bool:
        diagnostics = preflight_summary.get("diagnostics", {}) if isinstance(preflight_summary, dict) else {}
        summary = diagnostics.get("summary", {}) if isinstance(diagnostics, dict) else {}
        if not isinstance(summary, dict) or not summary:
            return False
        return self._diagnostics_indicate_publish_capable_session(
            summary,
            str(preflight_summary.get("current_url", "")),
        )

    def _diagnostics_indicate_publish_capable_session(self, summary: dict[str, Any], current_url: str) -> bool:
        creator_name = self._clean_visible_text(summary.get("creator_name"))
        has_any_identity = bool(creator_name) or summary.get("is_auth") is True or summary.get("media_status") is not None
        page_is_publish_like = "/profile_v4/graphic/publish" in current_url if current_url else True
        return has_any_identity and page_is_publish_like

    def _summarize_publish_blockers(self, summary: dict[str, Any]) -> list[str]:
        """列出值得注意的账号/草稿标记。

        ⚠️ 语义修正（2026-09-16，实测依据在 `creator_account_service`）：
        原先把 `is_creator=false` / `has_auth=false` / `ads_permission=0` / `manual_publish_ad=3`
        当作「可疑、可能没有分成资格」，这是**误读**，已从清单移除：
        - `creator_center/user_info.is_creator=false` 与 `auth_type=-1` 同时出现
          → 指的是「未做兴趣认证」，不是「不是创作者」。
        - `media/check_user_auth.has_auth=false` 与 `media/get_user_base_info.is_auth=true`
          矛盾 → 前者是媒体资质认证，个人号本就不适用；以 `is_auth=true`（已实名）为准。
        - `article/new.media.ads_permission=0` 与 `default_article_ad_type=3`
          同时出现 → 它表示「不允许手动改广告位」，而文章默认**是**投放广告的。
        - `manual_publish_ad=3` 与 `default_article_ad_type=3` 同值 → 3 是正常的广告模式。
        权威结论应看创作权益接口：本账号 7 项权益已开通（含「文章创作收益」）。
        保留的两项是真问题：内容来源被标成第三方同步（影响原创判定）、存在未声明原创篇数。
        """
        blockers: list[str] = []
        if summary.get("content_cache_source"):
            blockers.append(f"article/new.media.content_cache.source={summary.get('content_cache_source')}")
        if summary.get("not_claim_origin_cnt"):
            blockers.append(
                f"article/new.media.content_cache.not_claim_origin_cnt={summary.get('not_claim_origin_cnt')}"
            )
        if summary.get("claim_origin_permission") is False:
            blockers.append("article/new.claim_origin_permission=false")
        return blockers

    def _format_publish_diagnostic_blockers(self, diagnostics: dict[str, Any]) -> str:
        blockers = diagnostics.get("suspicious_flags")
        if not isinstance(blockers, list) or not blockers:
            return ""
        return f"Suspicious account flags: {', '.join(str(item) for item in blockers)}. "

    def _compact_publish_diagnostics(self, diagnostics: dict[str, Any]) -> dict[str, Any]:
        summary = dict(diagnostics.get("summary", {})) if isinstance(diagnostics.get("summary"), dict) else {}
        if "creator_name" in summary:
            summary["creator_name"] = self._clean_visible_text(summary.get("creator_name"))
        return {
            "summary": summary,
            "suspicious_flags": list(diagnostics.get("suspicious_flags", [])),
        }

    def _compact_backend_response(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}

        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return {
            "code": payload.get("code"),
            "err_no": payload.get("err_no"),
            "message": self._clean_visible_text(str(payload.get("message", ""))),
            "reason": self._clean_visible_text(str(payload.get("reason", ""))),
            "pgc_id": str(data.get("pgc_id", "")),
        }

    def _normalize_publish_result_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized["notes"] = self._finalize_notes(payload.get("notes", []))
        if "backend_response_summary" in normalized:
            summary = dict(normalized.get("backend_response_summary", {}))
            for key in ("message", "reason", "pgc_id"):
                if key in summary:
                    summary[key] = self._clean_visible_text(summary.get(key))
            normalized["backend_response_summary"] = summary
        if "publish_diagnostics" in normalized:
            normalized["publish_diagnostics"] = self._compact_publish_diagnostics(
                normalized.get("publish_diagnostics", {})
            )
        return normalized

    def _finalize_notes(self, notes: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw_item in notes:
            text = self._clean_visible_text(str(raw_item)).strip()
            if not text:
                continue
            if "Debug files saved to:" in text and "Draft autosave did not complete before publish." in text:
                text = re.sub(r"\s*Debug files saved to: .+$", "", text).strip()
            if text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _clean_visible_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.replace("\u00a0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _unique_texts(self, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = self._clean_visible_text(item)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _write_failed_publish_result(
        self,
        publish_dir: Path,
        run_dir: Path,
        title: str,
        topic_title: str,
        topic_url: str,
        error: str,
        preflight_summary: dict[str, Any] | None = None,
    ) -> None:
        debug_bundle = self._build_debug_bundle_from_error(error)
        failure_payload = {
            "status": "failed",
            "failed_at": datetime.now().isoformat(timespec="seconds"),
            "run_dir": str(run_dir),
            "title": title,
            "topic_title": topic_title,
            "topic_url": topic_url,
            "error": self._clean_visible_text(error),
            "preflight_summary": preflight_summary or {},
        }
        if debug_bundle:
            failure_payload.update(debug_bundle)
        (publish_dir / "publish_result.json").write_text(
            json.dumps(failure_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_debug_bundle_from_error(self, error: str) -> dict[str, Any]:
        debug_dir = self._extract_debug_dir_from_error(error)
        if debug_dir is None:
            return {}
        return self._serialize_debug_bundle(debug_dir)

    def _extract_debug_dir_from_error(self, error: str) -> Path | None:
        match = self.DEBUG_PATH_PATTERN.search(error or "")
        if not match:
            return None
        raw_path = match.group("path").strip().strip("\"'")
        if not raw_path:
            return None
        debug_dir = Path(raw_path)
        if not debug_dir.is_absolute():
            debug_dir = Path.cwd() / debug_dir
        return debug_dir

    def _serialize_debug_bundle(self, debug_dir: Path) -> dict[str, Any]:
        resolved_dir = debug_dir.expanduser().resolve(strict=False)
        artifacts = self._collect_debug_artifacts(resolved_dir)
        payload: dict[str, Any] = {
            "debug_bundle_dir": str(resolved_dir),
            "debug_artifacts": artifacts,
        }
        if artifacts.get("screenshot"):
            payload["screenshot_path"] = artifacts["screenshot"]
        if artifacts.get("page_html"):
            payload["dom_path"] = artifacts["page_html"]
        if artifacts.get("current_url"):
            payload["current_url_path"] = artifacts["current_url"]
        if artifacts.get("context"):
            payload["context_path"] = artifacts["context"]
        if not resolved_dir.exists():
            payload["debug_bundle_missing"] = True
            return payload
        payload["debug_bundle_manifest"] = {
            "platform": self.settings.publishing.platform,
            "captured_at": self._extract_debug_bundle_timestamp(resolved_dir),
            "debug_dir": str(resolved_dir),
            "artifacts": artifacts,
        }
        return payload

    def _collect_debug_artifacts(self, debug_dir: Path) -> dict[str, str]:
        artifact_names = {
            "screenshot": "publish_page.png",
            "page_html": "publish_page.html",
            "current_url": "current_url.txt",
            "context": "publish_context.json",
        }
        artifacts: dict[str, str] = {}
        for key, filename in artifact_names.items():
            candidate = debug_dir / filename
            if candidate.exists():
                artifacts[key] = str(candidate.resolve(strict=False))
        return artifacts

    def _extract_debug_bundle_timestamp(self, debug_dir: Path) -> str:
        try:
            captured_at = datetime.strptime(debug_dir.name, "%Y%m%d-%H%M%S")
        except ValueError:
            return ""
        return captured_at.isoformat(timespec="seconds")

    def _load_history(self) -> list[PublishRecord]:
        if not self.history_path.exists():
            return []
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.logger.warning("publish_history.json 损坏或无法读取，将使用空历史记录。")
            return []
        if not isinstance(payload, list):
            return []
        return [PublishRecord.from_dict(item) for item in payload if isinstance(item, dict)]

    def _save_history(self, records: list[PublishRecord]) -> None:
        content = json.dumps([asdict(item) for item in records], ensure_ascii=False, indent=2)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.history_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(str(tmp_path), str(self.history_path))
        except OSError:
            # 原子替换失败时回退到直接写入
            self.logger.warning("原子写入 publish_history 失败，回退到直接写入。")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.history_path.write_text(content, encoding="utf-8")

    def _was_topic_published(self, topic_title: str, topic_url: str) -> bool:
        """检查近期是否已发布过相同选题。

        去重窗口从“仅当天”扩展到“近7天”，与 RULE_009 对齐。
        历史数据表明：'日本机场成中国机器人试验场' 在6天内被发布3次，
        单篇展现从11.7万降至548，跨天重复发布是核心泄漏点。

        ⚠️ 2026-09-21：窗口天数改为引用 `TOPIC_DEDUP_WINDOW_DAYS`，
        与选题层 `TopicAutomationService._load_published_keys_for_today` 共用同一常量。
        这两层曾经各写各的（这里 7 天、那里只有当天），后果是选题层放行的题
        会在写完稿之后才被这里拦下，当天一篇都发不出去（云端 run 35523301070）。
        """
        normalized_title = self._normalize(topic_title)
        cutoff = (
            datetime.now().date() - timedelta(days=TOPIC_DEDUP_WINDOW_DAYS - 1)
        ).isoformat()
        for record in self._load_history():
            if record.status not in self.SUCCESS_HISTORY_STATUSES:
                continue
            if not record.published_at or record.published_at[:10] < cutoff:
                continue
            if topic_url and record.topic_url and topic_url == record.topic_url:
                return True
            if normalized_title and self._normalize(record.topic_title) == normalized_title:
                return True
        return False

    def _was_article_title_published_recently(
        self, article_title: str, *, days: int = TOPIC_DEDUP_WINDOW_DAYS
    ) -> bool:
        """检查近7天是否已发布过相同或高度相似的最终文章标题。

        基于 RULE_009 的编辑距离≤3 阈值进行模糊匹配。
        解决“不同 topic 生成相似 article 标题”的去重漏洞：
        原始 topic_title 与最终 article 标题可能完全不同，
        仅靠 topic 维度去重无法拦截此类重复。
        """
        normalized_target = self._normalize(article_title)
        if not normalized_target:
            return False
        cutoff = (
            datetime.now().date() - timedelta(days=TOPIC_DEDUP_WINDOW_DAYS - 1)
        ).isoformat()
        for record in self._load_history():
            if record.status not in self.SUCCESS_HISTORY_STATUSES:
                continue
            if not record.published_at or record.published_at < cutoff:
                continue
            normalized_record = self._normalize(record.title)
            if not normalized_record:
                continue
            if normalized_record == normalized_target:
                return True
            if self._edit_distance_le3(normalized_record, normalized_target):
                return True
        return False

    @staticmethod
    def _edit_distance_le3(a: str, b: str) -> bool:
        """编辑距离≤3判断，长度差>3时直接剪枝。"""
        if abs(len(a) - len(b)) > 3:
            return False
        if len(a) < len(b):
            a, b = b, a
        previous = list(range(len(b) + 1))
        for i, ca in enumerate(a, start=1):
            current = [i]
            for j, cb in enumerate(b, start=1):
                cost = 0 if ca == cb else 1
                current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + cost))
            previous = current
        return previous[-1] <= 3

    def _title_structural_skeleton(self, title: str) -> str:
        """提取标题结构骨架：将主题词与数字统一替换为占位符再比较。

        例如 '这组数据之后，机器人开始重排利润' 与 '超1000%之后，具身智能开始重排利润'
        都会归一化为 '{N}之后，{S}开始重排利润'，从而在生产层去重中识别为同一模板。
        """
        skel = title
        for filler in self.TITLE_SKELETON_FILLERS:
            if filler in skel:
                skel = skel.replace(filler, "{S}")
        # 数字（含百分比、数量词）与通用数据词统一替换
        skel = re.sub(r"\d+(?:\.\d+)?%?", "{N}", skel)
        skel = skel.replace("这组数据", "{N}").replace("一组数据", "{N}").replace("市场数据", "{N}")
        while "{S}{S}" in skel:
            skel = skel.replace("{S}{S}", "{S}")
        return skel

    def _blocked_by_template_dedup(self, article_title: str) -> bool:
        """生产层同模板标题去重（RULE_007 落地）。

        仅当标题命中已知高频滥用模板标记（如'重排利润'）时，
        才将其结构骨架与近7天已发布标题对比；相同骨架即视为同模板复用并拦截。
        通过触发标记 + 骨架相等双重条件，避免对正常多样标题误判。
        """
        normalized_target = self._normalize(article_title)
        if not normalized_target:
            return False
        if not any(marker in normalized_target for marker in self.KNOWN_TEMPLATE_MARKERS):
            return False
        target_skeleton = self._title_structural_skeleton(normalized_target)
        if len(target_skeleton) < 6:
            return False
        cutoff = (
            datetime.now().date() - timedelta(days=TOPIC_DEDUP_WINDOW_DAYS - 1)
        ).isoformat()
        for record in self._load_history():
            if record.status not in self.SUCCESS_HISTORY_STATUSES:
                continue
            if not record.published_at or record.published_at < cutoff:
                continue
            normalized_record = self._normalize(record.title)
            if not normalized_record:
                continue
            if not any(marker in normalized_record for marker in self.KNOWN_TEMPLATE_MARKERS):
                continue
            if self._title_structural_skeleton(normalized_record) == target_skeleton:
                return True
        return False

    def _normalize(self, text: str) -> str:
        return self.NORMALIZE_PATTERN.sub("", text.lower())

    def _first_visible_selector(self, page, selectors: list[str], strict: bool = True) -> str | None:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 8)
            except Exception:
                continue
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    candidate.wait_for(state="visible", timeout=500)
                    if index == 0:
                        return selector
                    return f"{selector} >> nth={index}"
                except Exception:
                    continue
        if strict:
            return None
        return None

    def _maybe_click(self, page, selectors: list[str], required: bool = True) -> bool:
        return self.safe_click(page, selectors, required=required)

    def safe_click(self, page, selectors: list[str], timeout: int = 10000, required: bool = True) -> bool:
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout, 8000))
        except Exception:
            pass
        selector = self._first_visible_selector(page, selectors, strict=False)
        if not selector:
            return False if not required else False

        locator = page.locator(selector).first
        try:
            locator.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass
        try:
            locator.click(timeout=min(timeout, 1500))
            return True
        except Exception:
            try:
                locator.click(force=True, timeout=min(timeout, 1500))
                return True
            except Exception:
                return False if not required else False

    def _click_immediate_publish_button(self, page, configured_selectors: list[str], notes: list[str]) -> bool:
        try:
            semantic_button = page.get_by_role("button", name=re.compile(r"(发布|立即发布|预览并发布)")).first
            vis = self._locator_is_visible(semantic_button)
            notes.append(f"Semantic button visible={vis}")
            if vis and self._click_button_candidate(semantic_button):
                notes.append("Clicked primary publish button via semantic role selector.")
                return True
            if not vis:
                notes.append("Semantic button not visible, trying scroll + mouse.click anyway.")
                try:
                    semantic_button.scroll_into_view_if_needed(timeout=2000)
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                try:
                    box = semantic_button.bounding_box(timeout=2000)
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        notes.append("Clicked primary publish button via semantic role + mouse.click.")
                        return True
                except Exception as exc2:
                    notes.append(f"Semantic button mouse.click error: {exc2}")
        except Exception as exc:
            notes.append(f"Semantic button error: {exc}")

        try:
            text_button = page.locator("button:has-text('预览并发布')").first
            vis = self._locator_is_visible(text_button)
            notes.append(f"Text button '预览并发布' visible={vis}")
            if vis and self._click_button_candidate(text_button):
                notes.append("Clicked primary publish button via text selector '预览并发布'.")
                return True
            if not vis:
                notes.append("Text button not visible, trying scroll + mouse.click anyway.")
                try:
                    text_button.scroll_into_view_if_needed(timeout=2000)
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                try:
                    box = text_button.bounding_box(timeout=2000)
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        notes.append("Clicked primary publish button via text selector + mouse.click.")
                        return True
                except Exception as exc2:
                    notes.append(f"Text button mouse.click error: {exc2}")
        except Exception as exc:
            notes.append(f"Text button error: {exc}")

        preferred_selectors: list[str] = [
            ".publish-footer-content button.byte-btn-primary.publish-btn-last",
            ".publish-footer-content button.publish-btn-last.byte-btn-primary",
            ".publish-footer-content button.publish-btn-last",
            ".publish-footer button.byte-btn-primary.publish-btn-last",
            ".publish-footer button.publish-btn-last",
            "button.byte-btn-primary.publish-btn-last",
            "button.publish-btn-last",
        ]
        selector_candidates: list[str] = []
        seen: set[str] = set()
        for selector in [*preferred_selectors, *configured_selectors]:
            if selector and selector not in seen:
                selector_candidates.append(selector)
                seen.add(selector)

        for selector in selector_candidates:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 6)
            except Exception:
                count = 0
            if count > 0:
                notes.append(f"Selector `{selector}` found {count} element(s)")

            for index in range(count):
                candidate = locator.nth(index)
                button_text = self._read_button_text(candidate)
                normalized_text = self._normalize_button_text(button_text)
                if self._is_timed_publish_button_text(normalized_text):
                    continue
                if not self._is_immediate_publish_button_text(normalized_text) and "publish-btn-last" not in selector:
                    continue
                if self._click_button_candidate(candidate):
                    notes.append(
                        f"Clicked primary publish button via selector `{selector}`"
                        + (f" with text `{button_text}`." if button_text else ".")
                    )
                    return True

        footer_locator = page.locator(".publish-footer-content button, .publish-footer button")
        best_candidate = None
        best_text = ""
        best_score = -10_000
        try:
            footer_count = min(footer_locator.count(), 12)
        except Exception:
            footer_count = 0
        notes.append(f"Footer scan found {footer_count} button(s)")
        for index in range(footer_count):
            candidate = footer_locator.nth(index)
            is_vis = self._locator_is_visible(candidate)
            button_text = self._read_button_text(candidate)
            score = self._score_publish_button_candidate(candidate, button_text)
            notes.append(f"Footer button[{index}] visible={is_vis} text=`{button_text}` score={score}")
            if not is_vis and score <= 0:
                continue
            if score > best_score:
                best_candidate = candidate
                best_text = button_text
                best_score = score

        if best_candidate is not None and best_score > 0:
            try:
                best_candidate.scroll_into_view_if_needed(timeout=2000)
                page.wait_for_timeout(300)
            except Exception:
                pass
            try:
                best_candidate.click(timeout=2000)
                notes.append(
                    "Clicked primary publish button via footer scan"
                    + (f" with text `{best_text}`." if best_text else ".")
                )
                return True
            except Exception:
                pass
            try:
                best_candidate.evaluate("el => el.click()", timeout=2000)
                notes.append(
                    "Clicked primary publish button via footer scan (JS)"
                    + (f" with text `{best_text}`." if best_text else ".")
                )
                return True
            except Exception as exc:
                notes.append(f"Footer scan JS click failed: {exc}")

        try:
            js_result = page.evaluate("""() => {
                const buttons = document.querySelectorAll('.publish-footer-content button, .publish-footer button');
                for (const btn of buttons) {
                    if (btn.textContent.includes('预览并发布') || btn.textContent.includes('发布')) {
                        btn.click();
                        return btn.textContent.trim();
                    }
                }
                return null;
            }""")
            if js_result:
                notes.append(f"Clicked publish button via JS DOM click (fallback): text=`{js_result}`")
                return True
        except Exception as exc:
            notes.append(f"JS DOM click error: {exc}")

        # 健壮性增强：智能元素定位器降级（Selector Fallbacks）
        # 当所有 CSS 选择器、语义按钮、文本匹配、footer 扫描、JS DOM click 均失败时，
        # 使用 locate_with_fallbacks 尝试 XPath / ARIA role / 文本 / placeholder / label 等多策略降级
        # 这是最后的兜底手段，提高平台 UI 微调时的定位成功率
        try:
            fallback_strategy = LocatorStrategy(
                css_selectors=[],  # CSS 已全部尝试过，此处留空
                xpath="//div[contains(@class,'publish-footer')]//button[contains(.,'发布') or contains(.,'预览并发布')]",
                role="button",
                role_name="(发布|立即发布|预览并发布)",
                text="预览并发布",
            )
            fallback_locator = locate_with_fallbacks(
                page,
                fallback_strategy,
                description="publish_button_final_fallback",
                timeout_ms=1500,
                logger=self.logger,
                notes=notes,
            )
            if fallback_locator is not None and self._click_button_candidate(fallback_locator):
                notes.append("Clicked publish button via LocatorStrategy final fallback (xpath/role/text).")
                return True
        except Exception as exc:
            notes.append(f"LocatorStrategy final fallback error: {exc}")

        return False

    def _locator_is_visible(self, locator) -> bool:
        try:
            box = locator.bounding_box(timeout=500)
            return box is not None and box["width"] > 0 and box["height"] > 0
        except Exception:
            return False

    def _read_button_text(self, locator) -> str:
        try:
            return locator.inner_text(timeout=500).strip()
        except Exception:
            return ""

    def _normalize_button_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _is_timed_publish_button_text(self, normalized_text: str) -> bool:
        return "\u5b9a\u65f6\u53d1\u5e03" in normalized_text

    def _is_immediate_publish_button_text(self, normalized_text: str) -> bool:
        if not normalized_text:
            return False
        if self._is_timed_publish_button_text(normalized_text):
            return False
        if normalized_text in {"\u9884\u89c8", "\u53d6\u6d88"}:
            return False
        return "\u53d1\u5e03" in normalized_text

    def _score_publish_button_candidate(self, locator, button_text: str) -> int:
        normalized_text = self._normalize_button_text(button_text)
        if not self._is_immediate_publish_button_text(normalized_text):
            return -10_000

        score = 0
        if normalized_text == "\u9884\u89c8\u5e76\u53d1\u5e03":
            score += 300
        elif normalized_text.endswith("\u53d1\u5e03"):
            score += 180
        else:
            score += 120

        try:
            class_name = str(locator.get_attribute("class", timeout=500) or "")
        except Exception:
            class_name = ""

        if "publish-btn-last" in class_name:
            score += 80
        if "byte-btn-primary" in class_name:
            score += 60
        return score

    def _click_button_candidate(self, locator) -> bool:
        try:
            locator.scroll_into_view_if_needed(timeout=1000)
        except Exception:
            pass

        try:
            locator.click(timeout=1500)
            return True
        except Exception:
            pass

        try:
            box = locator.bounding_box(timeout=1000)
            if box:
                page = locator.page
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                page.mouse.click(cx, cy)
                return True
        except Exception:
            pass

        try:
            locator.evaluate(
                """(node) => {
                    node.dispatchEvent(new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }));
                }"""
            )
            return True
        except Exception:
            pass

        try:
            locator.click(force=True, timeout=1500)
            return True
        except Exception:
            pass

    def _complete_publish_confirmation_flow(
        self,
        page,
        publish_events: list[dict[str, Any]],
        notes: list[str],
        fallback_start_index: int,
    ) -> int:
        preview_detected = False
        preview_note_added = False
        deadline = datetime.now().timestamp() + 20

        while datetime.now().timestamp() < deadline:
            direct_submit = self._latest_submit_event(
                publish_events,
                start_index=fallback_start_index,
            )
            if direct_submit is not None and self._publish_response_is_success(
                direct_submit.get("response_json")
            ):
                notes.append(
                    "Detected a successful publish submission directly (save=1 mode). "
                    "Skipping confirmation modal flow."
                )
                return fallback_start_index

            self._confirm_publish_modal(page, notes)

            preview_event = self._latest_preview_event(
                publish_events,
                start_index=fallback_start_index,
            )
            if preview_event is not None:
                preview_detected = True
                if not preview_note_added:
                    notes.append("Detected the preview publish step. Waiting for the final confirm button.")
                    preview_note_added = True

            confirm_locator = self._find_confirm_publish_button(page, preview_seen=preview_detected)
            if confirm_locator is not None:
                page.wait_for_timeout(1500)
                submit_start_index = len(publish_events)
                button_text = self._read_button_text(confirm_locator)
                if not self._click_button_candidate(confirm_locator):
                    raise RuntimeError("Found the confirm publish button, but clicking it failed.")
                notes.append(
                    "Clicked the final confirm publish button"
                    + (f" with text `{button_text}`." if button_text else ".")
                )
                page.wait_for_timeout(1500)
                return submit_start_index

            page.wait_for_timeout(800)

        if preview_detected:
            notes.append("Preview publish request was detected, but no final confirm button appeared.")
        else:
            notes.append("No in-page confirm publish button appeared; proceeding with the current submission flow.")
        return fallback_start_index

    def _find_confirm_publish_button(self, page, preview_seen: bool = False):
        candidates = [
            "div.byte-modal-wrapper button:has-text('确认发布')",
            "div.byte-modal-wrapper button:has-text('继续发布')",
            "div.byte-modal-wrapper button:has-text('发布')",
            "div.byte-modal-wrapper .byte-btn-primary",
            ".publish-footer-content button.byte-btn-primary.publish-btn-last",
            ".publish-footer button.byte-btn-primary.publish-btn-last",
            "button.byte-btn-primary.publish-btn-last",
            ".publish-footer-content button",
            ".publish-footer button",
        ]

        for selector in candidates:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 8)
            except Exception:
                count = 0

            for index in range(count):
                candidate = locator.nth(index)
                if not self._locator_is_visible(candidate):
                    continue
                button_text = self._read_button_text(candidate)
                normalized_text = self._normalize_button_text(button_text)
                if normalized_text == "\u786e\u8ba4\u53d1\u5e03":
                    return candidate
                if preview_seen and normalized_text in {"\u7ee7\u7eed\u53d1\u5e03", "\u786e\u8ba4", "\u53d1\u5e03"}:
                    return candidate
        return None

    def _wait_for_any(self, page, selectors: list[str]) -> str | None:
        for selector in selectors:
            try:
                page.locator(selector).first.wait_for(
                    state="visible",
                    timeout=self.settings.publishing.wait_timeout_ms,
                )
                return selector
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return None

    def _capture_debug_artifacts(self, page, extra_payload: dict[str, Any] | None = None) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        debug_dir = self.settings.output_dir / "publish_debug" / timestamp
        debug_dir.mkdir(parents=True, exist_ok=True)

        screenshot_path = debug_dir / "publish_page.png"
        html_path = debug_dir / "publish_page.html"
        url_path = debug_dir / "current_url.txt"

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as exc:
            self.logger.warning("Failed to save publish debug screenshot: %s", exc)

        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception as exc:
            self.logger.warning("Failed to save publish debug html: %s", exc)

        try:
            url_path.write_text(page.url, encoding="utf-8")
        except Exception as exc:
            self.logger.warning("Failed to save publish debug url: %s", exc)

        debug_payload = dict(extra_payload or {})
        override_snapshot = self._save_override_debug_snapshot(include_log=True)
        if override_snapshot:
            debug_payload["save_override_state"] = override_snapshot

        if debug_payload:
            try:
                (debug_dir / "publish_context.json").write_text(
                    json.dumps(debug_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                self.logger.warning("Failed to save publish debug context: %s", exc)

        self._refresh_latest_debug_bundle(debug_dir)
        return debug_dir

    def _refresh_latest_debug_bundle(self, debug_dir: Path) -> None:
        latest_dir = self.settings.output_dir / "debug"
        latest_dir.mkdir(parents=True, exist_ok=True)

        mapping = {
            "publish_page.png": "latest_error.png",
            "publish_page.html": "latest_error_dom.html",
            "current_url.txt": "latest_error_url.txt",
            "publish_context.json": "latest_error_context.json",
        }
        copied: dict[str, str] = {}
        for source_name, target_name in mapping.items():
            source = debug_dir / source_name
            if not source.exists():
                continue
            target = latest_dir / target_name
            try:
                shutil.copyfile(source, target)
                copied[source_name] = str(target.resolve(strict=False))
            except Exception as exc:
                self.logger.warning("Failed to refresh latest Toutiao debug artifact %s: %s", target_name, exc)

        manifest = {
            "platform": self.settings.publishing.platform,
            "captured_at": self._extract_debug_bundle_timestamp(debug_dir),
            "debug_dir": str(debug_dir.resolve(strict=False)),
            "artifacts": copied,
        }
        try:
            (latest_dir / "latest_error_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            self.logger.warning("Failed to write latest Toutiao debug manifest: %s", exc)

    def _confirm_publish_modal(self, page, notes: list[str]) -> None:
        clicked_texts: list[str] = []

        for _ in range(6):
            timed_publish_selector = self._first_visible_selector(
                page,
                [
                    ".common-timing-picker .byte-modal-footer .byte-btn-default",
                    ".common-timing-picker .byte-modal-close-icon",
                ],
                strict=False,
            )
            if timed_publish_selector:
                if self._maybe_click(page, [timed_publish_selector], required=False):
                    notes.append("Closed timed publish modal and kept immediate publish flow.")
                    page.wait_for_timeout(900)
                    continue

            selector = self._first_visible_selector(
                page,
                [
                    "div.byte-modal-wrapper[aria-hidden='false'] .byte-modal-footer button.byte-btn-primary",
                    "div.byte-modal-wrapper:not(.byte-modal-wrapper-hide) .byte-modal-footer button.byte-btn-primary",
                    "div.byte-modal-wrapper button:has-text('继续发布')",
                    "div.byte-modal-wrapper button:has-text('确认')",
                    "div.byte-modal-wrapper button:has-text('发布')",
                    "div.byte-modal-wrapper button:has-text('确定')",
                ],
                strict=False,
            )
            if not selector:
                break

            try:
                locator = page.locator(selector).first
                button_text = locator.inner_text(timeout=500).strip()
                if "定时发布" in button_text:
                    break
                locator.click(force=True, timeout=1500)
                clicked_texts.append(button_text or selector)
                page.wait_for_timeout(900)
            except Exception:
                break

        if clicked_texts:
            notes.append(f"Confirmed publish modal via: {' -> '.join(clicked_texts)}")

    def _wait_for_draft_save(
        self,
        page,
        publish_events: list[dict[str, Any]],
        notes: list[str],
        start_index: int = 0,
        expect_save_mode_1: bool = False,
    ) -> bool:
        timeout_ms = max(self.settings.publishing.wait_timeout_ms, 18000)
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        last_footer_text = ""
        idle_streak = 0
        saw_footer_text = False
        saw_saving_state = False
        last_nudge_at = 0.0

        while datetime.now().timestamp() < deadline:
            latest_autosave = self._latest_save_event(
                publish_events,
                start_index=start_index,
                success_only=None,
            )
            footer_text = self._clean_visible_text(self._read_draft_status_text(page))
            footer_saving = self._draft_status_indicates_saving(footer_text)
            footer_saved = self._draft_status_indicates_saved(footer_text)
            footer_failed = self._draft_status_indicates_failed(footer_text)

            if footer_text:
                saw_footer_text = True
            if footer_saving:
                saw_saving_state = True
                idle_streak = 0
            elif footer_text == last_footer_text:
                idle_streak += 1
            else:
                idle_streak = 1 if (footer_text or saw_footer_text) else 0

            if latest_autosave is not None and self._publish_response_is_success(latest_autosave.get("response_json")):
                if footer_saving:
                    last_footer_text = footer_text
                    page.wait_for_timeout(600)
                    continue
                self._disable_save_mode_override(page, notes)
                if footer_text:
                    notes.append(f"Draft autosave completed: {footer_text}")
                    return True
                if idle_streak >= 3:
                    notes.append("Draft autosave completed and the publish form stayed idle.")
                    return True

            if footer_saved and not expect_save_mode_1:
                notes.append(f"Draft footer reported saved state: {footer_text}")
                return True

            if latest_autosave is not None and not self._publish_response_is_success(latest_autosave.get("response_json")):
                code, message = self._extract_publish_error(latest_autosave.get("response_json"))
                if code in (4029, 4017):
                    page.wait_for_timeout(600)
                    last_footer_text = footer_text
                    continue
                debug_dir = self._capture_debug_artifacts(
                    page,
                    extra_payload={
                        "publish_events": publish_events,
                        "failed_draft_event": latest_autosave,
                        "last_footer_text": footer_text,
                    },
                )
                notes.append(
                    "Draft save request was rejected by Toutiao. "
                    f"code={code}, message={message}. "
                    f"Last footer state: {footer_text or 'unavailable'}. "
                    f"Debug files saved to: {debug_dir}"
                )
                return False

            if footer_failed:
                debug_dir = self._capture_debug_artifacts(
                    page,
                    extra_payload={
                        "publish_events": publish_events,
                        "last_footer_text": footer_text,
                    },
                )
                notes.append(
                    "Draft footer reported a save failure. "
                    f"Last footer state: {footer_text}. Debug files saved to: {debug_dir}"
                )
                return False

            now_ts = datetime.now().timestamp()
            if now_ts - last_nudge_at >= 4 and (footer_saving or (not saw_footer_text and latest_autosave is None)):
                self._nudge_draft_autosave(page, notes)
                last_nudge_at = now_ts

            if (
                not expect_save_mode_1
                and not saw_saving_state
                and not saw_footer_text
                and latest_autosave is None
                and idle_streak >= 6
            ):
                notes.append("Draft footer never exposed a saving indicator, but the publish form stayed idle long enough.")
                return True

            last_footer_text = footer_text
            page.wait_for_timeout(700)

        latest_autosave = self._latest_save_event(
            publish_events,
            start_index=start_index,
            success_only=True,
        )
        if latest_autosave is not None:
            if self._draft_status_indicates_saving(last_footer_text):
                notes.append("Draft autosave request succeeded, but the footer still showed a saving state.")
                return False
            notes.append("Draft autosave completed before publish.")
            return True

        debug_dir = self._capture_debug_artifacts(
            page,
            extra_payload={
                "publish_events": publish_events,
                "last_footer_text": last_footer_text,
                "saw_footer_text": saw_footer_text,
                "saw_saving_state": saw_saving_state,
                "idle_streak": idle_streak,
            },
        )
        notes.append(
            "Draft autosave did not complete; publish must be blocked. "
            f"Last footer state: {last_footer_text or 'unavailable'}. "
            f"Debug files saved to: {debug_dir}"
        )
        return False

    def _nudge_draft_autosave(self, page, notes: list[str]) -> None:
        self._dismiss_ai_assistant_drawer(page, notes)
        self._blur_active_element(page)
        clicked = self._click_publish_surface(page)
        if not clicked:
            try:
                page.mouse.click(20, 20)
            except Exception:
                pass
        page.wait_for_timeout(900)

    def _latest_save_event(
        self,
        publish_events: list[dict[str, Any]],
        start_index: int,
        success_only: bool | None,
    ) -> dict[str, Any] | None:
        for event in reversed(publish_events[start_index:]):
            if not self._is_draft_save_event(event):
                continue
            if success_only is True and not self._publish_response_is_success(event.get("response_json")):
                continue
            if success_only is False and self._publish_response_is_success(event.get("response_json")):
                continue
            return event
        return None

    def _is_draft_save_event(self, event: dict[str, Any]) -> bool:
        save_mode = event.get("save_mode")
        if save_mode == 1:
            return True
        url = str(event.get("url", ""))
        return "/mp/agw/draft/" in url and "/mp/agw/article/publish" not in url

    def _draft_status_indicates_saving(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in [
                "保存中",
                "草稿保存中",
                "draft saving",
            ]
        )

    def _draft_status_indicates_saved(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in [
                "已保存",
                "草稿已保存",
                "draft saved",
            ]
        )

    def _draft_status_indicates_failed(self, footer_text: str) -> bool:
        normalized = footer_text.strip().lower()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in [
                "保存失败",
                "草稿保存失败",
                "draft save failed",
            ]
        )
