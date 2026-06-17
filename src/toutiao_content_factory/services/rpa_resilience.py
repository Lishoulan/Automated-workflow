"""RPA 健壮性增强模块 — 动态重试退避 / 弹窗守卫 / 定位器降级 / 发布状态双重校验

本模块为 publish_service.py / wechat_service.py 等发布组件提供四类增强能力，
所有增强均以「无侵入式装饰器 / Mixin / 独立函数」形式提供，可在不破坏原有
配置隔离与业务逻辑的前提下平滑接入。

设计原则：
1. 配置隔离：所有阈值/选择器均通过参数传入或读取 settings，不引入硬编码全局状态
2. 失败可观测：每次重试、每次弹窗阻击、每次降级都记录到 notes，便于事后回溯
3. 异常分级：严格区分「可重试异常」与「阻断性异常」，避免盲目重试浪费时间
4. 向后兼容：原方法签名保持不变，新能力通过可选参数启用
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

try:
    from playwright.sync_api import Page, Locator, TimeoutError as PlaywrightTimeoutError
except ModuleNotFoundError:  # pragma: no cover - 可选依赖
    Page = None  # type: ignore
    Locator = None  # type: ignore
    PlaywrightTimeoutError = Exception


# ============================================================================
# 1) 异常分级 — 区分可重试异常与阻断性异常
# ============================================================================

class RpaBlockedException(RuntimeError):
    """阻断性异常基类 — 登录过期、账号封禁、平台 UI 大改等，不应重试"""


class LoginExpiredError(RpaBlockedException):
    """登录会话失效，重试无意义，需人工重新登录"""


class AccountBlockedError(RpaBlockedException):
    """账号被限流/封禁，重试无意义"""


class PlatformUIChangedError(RpaBlockedException):
    """平台 UI 大改，选择器全部失效，需人工介入更新配置"""


# 可重试异常的特征关键词（出现在异常消息中即视为可重试）
_RETRYABLE_MARKERS = (
    "timeout",
    "timed out",
    "element not visible",
    "element is not clickable",
    "waiting for selector",
    "waiting for element",
    "navigation timeout",
    "net::err_",
    "err_internet",
    "err_connection",
    "err_name_not_resolved",
    "err_socket",
    "temporarily unavailable",
    "暂时不可用",
    "网络",
)

# 阻断性异常的特征关键词
_BLOCKED_MARKERS = (
    "login",
    "passport",
    "sso.toutiao.com",
    "sign in",
    "登录",
    "账号已被封禁",
    "账号已被限流",
    "account has been blocked",
    "account has been restricted",
    "forbidden",
    "403",
)


def classify_exception(exc: Exception) -> str:
    """根据异常类型和消息分类，返回 'retryable' / 'blocked' / 'unknown'

    判定逻辑：
    - 显式的 RpaBlockedException 子类 → blocked
    - PlaywrightTimeoutError → retryable
    - 消息命中阻断性关键词 → blocked
    - 消息命中可重试关键词 → retryable
    - 其他 → unknown（默认不重试，由调用方决定）
    """
    if isinstance(exc, RpaBlockedException):
        return "blocked"
    if isinstance(exc, PlaywrightTimeoutError):
        return "retryable"
    msg = str(exc).lower()
    if any(marker in msg for marker in _BLOCKED_MARKERS):
        return "blocked"
    if any(marker in msg for marker in _RETRYABLE_MARKERS):
        return "retryable"
    return "unknown"


# ============================================================================
# 2) 动态重试与指数退避机制
# ============================================================================

@dataclass
class RetryPolicy:
    """重试策略配置 — 支持指数退避 + 抖动 + 异常分级

    Attributes:
        max_attempts: 最大尝试次数（含首次），默认 4
        base_delay: 初始退避秒数，默认 1.0
        max_delay: 最大退避秒数，默认 30.0
        multiplier: 退避倍数，默认 2.0（指数退避）
        jitter: 抖动比例 [0,1)，避免重试风暴，默认 0.3
        retryable_only: 为 True 时仅重试可重试异常，阻断性异常立即抛出
    """
    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.3
    retryable_only: bool = True

    def compute_delay(self, attempt: int) -> float:
        """计算第 attempt 次重试（从 1 开始）前的等待秒数，含指数退避 + 抖动"""
        # 指数退避：base * multiplier^(attempt-1)
        delay = self.base_delay * (self.multiplier ** (attempt - 1))
        delay = min(delay, self.max_delay)
        # 加入抖动，避免多个客户端同时重试造成「重试风暴」
        if self.jitter > 0:
            jitter_amount = delay * self.jitter * random.random()
            delay = delay + jitter_amount if random.random() < 0.5 else max(0, delay - jitter_amount)
        return delay


def retry_with_backoff(
    action_fn: Callable[[], Any],
    *,
    description: str = "rpa_action",
    policy: RetryPolicy | None = None,
    logger: logging.Logger | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    """带指数退避的智能重试执行器

    Args:
        action_fn: 要执行的无参可调用对象
        description: 动作描述（仅用于日志）
        policy: 重试策略，None 时使用默认 RetryPolicy()
        logger: 日志记录器，None 时不记录
        on_retry: 每次重试前的回调 (attempt, exc, delay)
        sleep_fn: 睡眠函数，便于测试注入

    Returns:
        action_fn 的返回值

    Raises:
        最后一次失败的异常（若是阻断性异常则立即抛出）
    """
    policy = policy or RetryPolicy()
    last_exc: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return action_fn()
        except Exception as exc:
            last_exc = exc
            category = classify_exception(exc)

            # 阻断性异常：立即抛出，不重试
            if category == "blocked":
                if logger:
                    logger.error(
                        "retry[%s] attempt %d/%d blocked (non-retryable): %s",
                        description, attempt, policy.max_attempts, exc,
                    )
                raise

            # 可重试性判定
            if policy.retryable_only and category != "retryable":
                # unknown 异常，按策略不重试
                if logger:
                    logger.warning(
                        "retry[%s] attempt %d/%d failed with unknown exception (not retryable): %s",
                        description, attempt, policy.max_attempts, exc,
                    )
                raise

            # 已达最大尝试次数
            if attempt >= policy.max_attempts:
                if logger:
                    logger.error(
                        "retry[%s] exhausted %d attempts, last error: %s",
                        description, policy.max_attempts, exc,
                    )
                break

            # 计算退避并等待
            delay = policy.compute_delay(attempt)
            if logger:
                logger.warning(
                    "retry[%s] attempt %d/%d failed (%s): %s. Retrying in %.2fs...",
                    description, attempt, policy.max_attempts, category, exc, delay,
                )
            if on_retry:
                try:
                    on_retry(attempt, exc, delay)
                except Exception:
                    pass
            sleep_fn(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"retry[{description}] exited without result or exception")


# ============================================================================
# 3) 防御性弹窗阻击（Anti-Popup Guard）
# ============================================================================

@dataclass
class PopupRule:
    """单条弹窗规则定义

    Attributes:
        name: 规则名称（用于日志）
        selectors: 关闭按钮的选择器列表（按优先级）
        close_texts: 关闭按钮的文本（用于文本匹配兜底）
        dismiss_with_escape: 是否尝试按 Escape 关闭
        force_remove_dom: 是否在点击失败后强制从 DOM 移除
    """
    name: str
    selectors: list[str] = field(default_factory=list)
    close_texts: list[str] = field(default_factory=list)
    dismiss_with_escape: bool = False
    force_remove_dom: bool = False


# 常见平台弹窗规则库（无侵入式，可被外部覆盖或扩展）
DEFAULT_POPUP_RULES: list[PopupRule] = [
    # 新手引导浮层
    PopupRule(
        name="新手引导",
        selectors=[
            ".guide-mask .guide-close",
            ".onboarding-close",
            "[class*='guide'] [class*='close']",
            ".byte-modal-wrapper button:has-text('跳过')",
            ".byte-modal-wrapper button:has-text('知道了')",
            ".byte-modal-wrapper button:has-text('我知道了')",
        ],
        close_texts=["跳过", "知道了", "我知道了", "关闭"],
        force_remove_dom=True,
    ),
    # AI 助手推荐抽屉
    PopupRule(
        name="AI助手推荐",
        selectors=[
            ".byte-drawer-wrapper.ai-assistant-drawer .byte-drawer-close",
            ".byte-drawer-wrapper [class*='close']",
            ".ai-assistant-drawer [class*='close']",
        ],
        dismiss_with_escape=True,
        force_remove_dom=True,
    ),
    # 平台规则更新公告
    PopupRule(
        name="平台规则公告",
        selectors=[
            ".byte-modal-wrapper button:has-text('我知道了')",
            ".byte-modal-wrapper button:has-text('确定')",
            ".byte-modal-wrapper .byte-modal-close-icon",
            ".notice-modal .notice-close",
            "[class*='announcement'] [class*='close']",
        ],
        close_texts=["我知道了", "确定", "关闭"],
        force_remove_dom=True,
    ),
    # 通用遮罩层（无明确关闭按钮时按 Escape 或移除）
    PopupRule(
        name="通用遮罩",
        selectors=[
            ".byte-drawer-mask",
            ".byte-modal-mask",
            ".overlay-mask",
        ],
        dismiss_with_escape=True,
        force_remove_dom=True,
    ),
]


class PopupGuard:
    """无侵入式弹窗扫描器 — 主动检测并关闭各类干扰弹窗

    使用方式：
        guard = PopupGuard(page, logger=logger)
        guard.sweep(notes)          # 主动扫描一次
        guard.sweep_before(notes, stage="editor_enter")  # 进入编辑页前
        guard.sweep_before(notes, stage="publish_click") # 点击发布前
    """

    def __init__(
        self,
        page: Page,
        *,
        rules: list[PopupRule] | None = None,
        logger: logging.Logger | None = None,
        platform: str = "unknown",
        sweep_timeout_ms: int = 600,
    ):
        self.page = page
        self.rules = rules if rules is not None else list(DEFAULT_POPUP_RULES)
        self.logger = logger or logging.getLogger(__name__)
        self.platform = platform
        self.sweep_timeout_ms = sweep_timeout_ms

    def sweep(self, notes: list[str] | None = None) -> list[str]:
        """执行一次弹窗扫描，返回被关闭的弹窗名称列表

        无侵入式：任何异常都被捕获，绝不影响主干流程
        """
        dismissed: list[str] = []
        for rule in self.rules:
            try:
                if self._try_close(rule):
                    dismissed.append(rule.name)
                    msg = f"PopupGuard: dismissed '{rule.name}' popup on {self.platform}."
                    if notes is not None:
                        notes.append(msg)
                    self.logger.info(msg)
            except Exception as exc:
                # 弹窗阻击失败不影响主流程，仅记录
                self.logger.debug("PopupGuard: rule '%s' failed: %s", rule.name, exc)
        return dismissed

    def sweep_before(self, notes: list[str] | None, *, stage: str) -> list[str]:
        """在关键节点前执行弹窗扫描（语义化入口）

        Args:
            notes: 备注列表，用于记录关闭动作
            stage: 阶段标识，如 'editor_enter' / 'publish_click' / 'cover_upload'
        """
        self.logger.debug("PopupGuard: sweeping before stage '%s' on %s", stage, self.platform)
        return self.sweep(notes)

    def _try_close(self, rule: PopupRule) -> bool:
        """尝试按规则关闭单个弹窗，成功返回 True"""
        # 策略 1: 按选择器点击关闭按钮
        for selector in rule.selectors:
            try:
                locator = self.page.locator(selector).first
                if locator.is_visible(timeout=self.sweep_timeout_ms):
                    try:
                        locator.click(force=True, timeout=800)
                    except Exception:
                        # 点击失败时尝试 JS click
                        try:
                            locator.evaluate("el => el.click()")
                        except Exception:
                            pass
                    self.page.wait_for_timeout(200)
                    return True
            except Exception:
                continue

        # 策略 2: 按文本匹配关闭按钮
        for text in rule.close_texts:
            try:
                candidate = self.page.get_by_role("button", name=text).first
                if candidate.is_visible(timeout=self.sweep_timeout_ms):
                    try:
                        candidate.click(force=True, timeout=800)
                    except Exception:
                        try:
                            candidate.evaluate("el => el.click()")
                        except Exception:
                            pass
                    self.page.wait_for_timeout(200)
                    return True
            except Exception:
                continue

        # 策略 3: 按 Escape 关闭
        if rule.dismiss_with_escape:
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(150)
            except Exception:
                pass

        # 策略 4: 强制从 DOM 移除（最后手段）
        if rule.force_remove_dom:
            try:
                removed = self.page.evaluate(
                    """(selectors) => {
                        let removed = false;
                        for (const sel of selectors) {
                            document.querySelectorAll(sel).forEach(node => {
                                node.remove();
                                removed = true;
                            });
                        }
                        return removed;
                    }""",
                    rule.selectors,
                )
                if removed:
                    self.page.wait_for_timeout(150)
                    return True
            except Exception:
                pass

        return False


# ============================================================================
# 4) 智能元素定位器降级（Selector Fallbacks）
# ============================================================================

@dataclass
class LocatorStrategy:
    """定位策略描述 — 用于多策略降级定位

    Attributes:
        css_selectors: CSS / Playwright 选择器列表（按优先级）
        xpath: XPath 表达式（CSS 全部失败时尝试）
        role: ARIA role（如 'button' / 'textbox'）
        role_name: role 对应的 accessible name（支持正则）
        text: 按可见文本匹配
        placeholder: 按 placeholder 匹配
        label: 按 label 文本匹配
    """
    css_selectors: list[str] = field(default_factory=list)
    xpath: str | None = None
    role: str | None = None
    role_name: str | None = None
    text: str | None = None
    placeholder: str | None = None
    label: str | None = None


def locate_with_fallbacks(
    page: Page,
    strategy: LocatorStrategy,
    *,
    description: str = "element",
    timeout_ms: int = 2000,
    logger: logging.Logger | None = None,
    notes: list[str] | None = None,
) -> Locator | None:
    """多策略降级定位元素

    降级顺序：CSS 选择器 → XPath → ARIA role → 文本 → placeholder → label
    任一策略命中即返回，全部失败返回 None（不抛异常，由调用方决定）

    Args:
        page: Playwright Page
        strategy: 定位策略
        description: 元素描述（用于日志/notes）
        timeout_ms: 单个策略的超时毫秒数
        logger: 日志记录器
        notes: 备注列表

    Returns:
        Locator 或 None
    """
    attempts: list[str] = []

    # 策略 1: CSS 选择器遍历
    for selector in strategy.css_selectors:
        try:
            candidate = page.locator(selector).first
            if candidate.is_visible(timeout=timeout_ms):
                if logger:
                    logger.debug("locate[%s]: hit via css: %s", description, selector[:80])
                return candidate
            attempts.append(f"css(not visible): {selector[:60]}")
        except Exception:
            attempts.append(f"css(error): {selector[:60]}")

    # 策略 2: XPath
    if strategy.xpath:
        try:
            candidate = page.locator(f"xpath={strategy.xpath}").first
            if candidate.is_visible(timeout=timeout_ms):
                if logger:
                    logger.debug("locate[%s]: hit via xpath: %s", description, strategy.xpath[:80])
                return candidate
            attempts.append(f"xpath(not visible): {strategy.xpath[:60]}")
        except Exception:
            attempts.append(f"xpath(error): {strategy.xpath[:60]}")

    # 策略 3: ARIA role
    if strategy.role:
        try:
            if strategy.role_name:
                # role_name 支持正则
                name_pattern = re.compile(strategy.role_name) if "(" in strategy.role_name else strategy.role_name
                candidate = page.get_by_role(strategy.role, name=name_pattern).first
            else:
                candidate = page.get_by_role(strategy.role).first
            if candidate.is_visible(timeout=timeout_ms):
                if logger:
                    logger.debug("locate[%s]: hit via role=%s", description, strategy.role)
                return candidate
            attempts.append(f"role(not visible): {strategy.role}")
        except Exception:
            attempts.append(f"role(error): {strategy.role}")

    # 策略 4: 文本
    if strategy.text:
        try:
            candidate = page.get_by_text(strategy.text, exact=False).first
            if candidate.is_visible(timeout=timeout_ms):
                if logger:
                    logger.debug("locate[%s]: hit via text: %s", description, strategy.text)
                return candidate
            attempts.append(f"text(not visible): {strategy.text[:40]}")
        except Exception:
            attempts.append(f"text(error): {strategy.text[:40]}")

    # 策略 5: placeholder
    if strategy.placeholder:
        try:
            candidate = page.get_by_placeholder(strategy.placeholder).first
            if candidate.is_visible(timeout=timeout_ms):
                if logger:
                    logger.debug("locate[%s]: hit via placeholder: %s", description, strategy.placeholder)
                return candidate
            attempts.append(f"placeholder(not visible): {strategy.placeholder[:40]}")
        except Exception:
            attempts.append(f"placeholder(error): {strategy.placeholder[:40]}")

    # 策略 6: label
    if strategy.label:
        try:
            candidate = page.get_by_label(strategy.label).first
            if candidate.is_visible(timeout=timeout_ms):
                if logger:
                    logger.debug("locate[%s]: hit via label: %s", description, strategy.label)
                return candidate
            attempts.append(f"label(not visible): {strategy.label[:40]}")
        except Exception:
            attempts.append(f"label(error): {strategy.label[:40]}")

    if notes is not None:
        notes.append(f"locate[{description}]: all strategies failed. Attempts: {'; '.join(attempts[:5])}")
    if logger:
        logger.warning("locate[%s]: all strategies failed. Attempts: %s", description, attempts)
    return None


# ============================================================================
# 5) 发布事件状态捕获双重校验
# ============================================================================

# 发布成功的 DOM 标志（URL 跳转 + 页面元素）
PUBLISH_SUCCESS_URL_PATTERNS = (
    "/profile_v4/graphic/contents",
    "/profile_v4/graphic/manage",
    "/cgi-bin/appmsg?success",
    "publish_success",
    "submitted_for_review",
)

PUBLISH_SUCCESS_DOM_MARKERS = (
    "发布成功",
    "提交成功",
    "已提交审核",
    "草稿已保存",
    "保存成功",
    "Publish succeeded",
    "Submitted for review",
)

PUBLISH_FAILURE_DOM_MARKERS = (
    "发布失败",
    "提交失败",
    "请勿重复发布",
    "内容违规",
    "审核未通过",
    "Publish failed",
    "频率过高",
    "稍后再试",
)


@dataclass
class PublishVerificationResult:
    """发布状态双重校验结果

    Attributes:
        api_confirmed: 网络 API 响应是否确认成功
        dom_confirmed: DOM 状态是否确认成功
        final_status: 最终判定 'success' / 'failure' / 'ambiguous'
        evidence: 证据列表（用于 notes）
    """
    api_confirmed: bool = False
    dom_confirmed: bool = False
    final_status: str = "ambiguous"
    evidence: list[str] = field(default_factory=list)


def verify_publish_dually(
    page: Page,
    *,
    api_success: bool,
    api_response_summary: dict[str, Any] | None = None,
    success_url_patterns: tuple[str, ...] = PUBLISH_SUCCESS_URL_PATTERNS,
    success_dom_markers: tuple[str, ...] = PUBLISH_SUCCESS_DOM_MARKERS,
    failure_dom_markers: tuple[str, ...] = PUBLISH_FAILURE_DOM_MARKERS,
    wait_timeout_ms: int = 8000,
    logger: logging.Logger | None = None,
) -> PublishVerificationResult:
    """发布状态双重校验 — API 响应 + DOM 状态

    校验逻辑：
    1. API 层：调用方传入的 api_success（基于网络响应判定）
    2. DOM 层：检测页面 URL 跳转 + 页面文本标志
    3. 综合判定：
       - API + DOM 都成功 → success
       - API 成功但 DOM 检测到失败标志 → failure（DOM 优先，平台可能事后拒绝）
       - API 成功且 DOM 无明确标志 → success（API 优先）
       - API 失败 → failure（无论 DOM）
       - 两者都失败 → failure

    Args:
        page: Playwright Page
        api_success: API 响应是否成功
        api_response_summary: API 响应摘要（用于证据记录）
        success_url_patterns: 发布成功后的 URL 模式
        success_dom_markers: 发布成功的 DOM 文本标志
        failure_dom_markers: 发布失败的 DOM 文本标志
        wait_timeout_ms: 等待 DOM 状态的最大毫秒数
        logger: 日志记录器

    Returns:
        PublishVerificationResult
    """
    result = PublishVerificationResult(api_confirmed=api_success)
    if api_response_summary:
        result.evidence.append(f"api_response: {api_response_summary}")

    # API 失败直接判定为 failure
    if not api_success:
        result.final_status = "failure"
        result.evidence.append("api returned failure, skipping DOM verification")
        if logger:
            logger.warning("verify_publish: API failed, final_status=failure")
        return result

    # 等待页面跳转或 DOM 更新
    deadline = datetime.now().timestamp() + (wait_timeout_ms / 1000)
    dom_failure_detected = False
    while datetime.now().timestamp() < deadline:
        try:
            current_url = str(page.url or "")
            # 检测 URL 跳转
            for pattern in success_url_patterns:
                if pattern in current_url:
                    result.dom_confirmed = True
                    result.evidence.append(f"url matched success pattern: {pattern}")
                    break

            # 检测 DOM 文本标志
            page_text = page.evaluate(
                """() => {
                    const body = document.body;
                    return body ? (body.innerText || '').substring(0, 5000) : '';
                }"""
            )
            for marker in success_dom_markers:
                if marker in page_text:
                    result.dom_confirmed = True
                    result.evidence.append(f"dom matched success marker: {marker}")
                    break
            for marker in failure_dom_markers:
                if marker in page_text:
                    dom_failure_detected = True
                    result.evidence.append(f"dom matched FAILURE marker: {marker}")
                    break

            if result.dom_confirmed or dom_failure_detected:
                break
        except Exception as exc:
            result.evidence.append(f"dom check error: {exc}")
            break
        page.wait_for_timeout(400)

    # 综合判定
    if dom_failure_detected:
        # DOM 检测到失败标志，即使 API 成功也判为失败（平台事后拒绝）
        result.final_status = "failure"
        result.evidence.append("DOM failure marker overrides API success")
    elif result.dom_confirmed:
        result.final_status = "success"
    else:
        # API 成功但 DOM 无明确标志，信任 API
        result.final_status = "success"
        result.evidence.append("API success trusted (no DOM marker detected)")

    if logger:
        logger.info(
            "verify_publish: api=%s dom=%s final=%s evidence=%s",
            result.api_confirmed, result.dom_confirmed, result.final_status, result.evidence,
        )
    return result


# ============================================================================
# 6) 便捷装饰器 — 给现有方法叠加重试能力
# ============================================================================

def with_rpa_retry(
    description: str = "rpa_action",
    policy: RetryPolicy | None = None,
    logger: logging.Logger | None = None,
):
    """方法装饰器 — 为 RPA 操作自动叠加指数退避重试

    用法：
        class PublishService:
            @with_rpa_retry(description="click_publish_button", logger=logger)
            def _click_publish(self, page):
                ...
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args, **kwargs):
            return retry_with_backoff(
                lambda: fn(*args, **kwargs),
                description=description,
                policy=policy,
                logger=logger,
            )
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorator
