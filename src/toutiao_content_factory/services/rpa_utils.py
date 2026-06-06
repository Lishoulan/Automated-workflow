"""RPA 反风控工具集 — Stealth 模式、拟人化交互、智能定位降级、Cookie 自愈、调试告警"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from playwright.sync_api import Page, Locator, BrowserContext, TimeoutError as PlaywrightTimeoutError
except ModuleNotFoundError:
    Page = None  # type: ignore
    Locator = None  # type: ignore
    BrowserContext = None  # type: ignore
    PlaywrightTimeoutError = Exception


logger = logging.getLogger(__name__)


# ============================================================================
# 1) Stealth 模式 — 抹除 navigator.webdriver 等自动化特征
# ============================================================================

class StealthPatcher:
    """为 Playwright 浏览器上下文注入反检测脚本"""

    STEALTH_SCRIPT = """
    // 1. 抹除 webdriver 标记
    Object.defineProperty(navigator, 'webdriver', { get: () => false });

    // 2. 伪造 Chrome 运行时对象
    window.chrome = { runtime: {} };

    // 3. 伪造权限查询
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );

    // 4. 伪造 plugins 数组（避免空数组暴露 headless）
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });

    // 5. 伪造 languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['zh-CN', 'zh', 'en'],
    });

    // 6. 伪造 hardwareConcurrency
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
    });

    // 7. 伪装 deviceMemory
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
    });

    // 8. 自动隐藏 video 元素（部分检测用 video 元素检测 headless）
    const _createElement = Document.prototype.createElement;
    Document.prototype.createElement = function(tag, ...args) {
        const el = _createElement.call(this, tag, ...args);
        if (tag.toLowerCase() === 'video') {
            const _play = el.play;
            el.play = function() { return Promise.resolve(); };
        }
        return el;
    };
    """

    @classmethod
    def apply(cls, context: BrowserContext) -> None:
        """为浏览器上下文的所有新页面注入 stealth 脚本"""
        context.add_init_script(cls.STEALTH_SCRIPT)
        logger.info("StealthPatcher: anti-detection scripts injected into browser context.")


# ============================================================================
# 2) 拟人化输入 — 随机延迟 + 逐字符输入
# ============================================================================

@dataclass
class HumanTyping:
    """模拟真人打字的输入辅助"""

    min_delay: int = 50
    max_delay: int = 150
    initial_pause_ms: int = 300
    post_typing_pause_ms: int = 500

    @staticmethod
    def _random_delay(min_ms: int, max_ms: int) -> int:
        return random.randint(min_ms, max_ms)

    def type_into(self, locator: Locator, text: str) -> None:
        """以随机延迟逐字符输入文本到指定元素"""
        if not text:
            return
        locator.click()
        time.sleep(self.initial_pause_ms / 1000.0)
        try:
            locator.fill("")
        except Exception:
            pass
        for char in text:
            locator.press_sequentially(char, delay=self._random_delay(self.min_delay, self.max_delay))
        time.sleep(self.post_typing_pause_ms / 1000.0)

    def random_scroll(self, page: Page) -> None:
        """页面上随机滚动几次，模拟真人浏览"""
        for _ in range(random.randint(2, 5)):
            scroll_y = random.randint(100, 600)
            page.evaluate(f"window.scrollBy(0, {scroll_y})")
            time.sleep(random.uniform(0.3, 1.2))

    def random_mouse_move(self, page: Page) -> None:
        """模拟随机鼠标移动"""
        try:
            viewport = page.viewport_size
            if not viewport:
                return
            steps = random.randint(5, 12)
            start_x, start_y = random.randint(50, 200), random.randint(50, 200)
            end_x = random.randint(100, viewport["width"] - 100)
            end_y = random.randint(100, viewport["height"] - 100)
            for i in range(steps + 1):
                t = i / steps
                cx, cy = random.randint(100, viewport["width"] - 100), random.randint(100, viewport["height"] - 100)
                x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * cx + t ** 2 * end_x
                y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * cy + t ** 2 * end_y
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.01, 0.05))
        except Exception:
            pass


# ============================================================================
# 3) 智能元素定位器 — 多策略降级 (Smart Locator)
# ============================================================================

@dataclass
class SmartLocatorResult:
    """智能定位结果"""
    locator: Locator | None = None
    found: bool = False
    strategy_used: str = ""
    attempts: list[str] = field(default_factory=list)
    debug_screenshot: str = ""
    debug_dom_path: str = ""


class SmartLocator:
    """多策略降级智能定位器 — 当主选择器失效时自动尝试备选方案"""

    def __init__(
        self,
        page: Page,
        *,
        debug_dir: Path | str | None = None,
        timeout_ms: int = 8000,
        step_timeout_ms: int = 3000,
        platform: str = "unknown",
        notify_fn: Callable[[str, str], None] | None = None,
    ):
        self.page = page
        self.debug_dir = Path(debug_dir) if debug_dir else Path("output/publish_debug")
        self.timeout_ms = timeout_ms
        self.step_timeout_ms = step_timeout_ms
        self.platform = platform
        self.notify_fn = notify_fn

    def locate(
        self,
        element_desc: str,
        *,
        primary_selectors: list[str] | None = None,
        fallback_selectors: list[str] | None = None,
        text_fallback: str | None = None,
        role_fallback: str | None = None,
        placeholder_fallback: str | None = None,
        label_fallback: str | None = None,
        xpath_fallback: str | None = None,
        required: bool = True,
    ) -> SmartLocatorResult:
        """多策略定位元素

        Args:
            element_desc: 元素描述（仅用于日志和告警）
            primary_selectors: 主 CSS/Playwright 选择器列表（按优先级排列）
            fallback_selectors: 备选 CSS 选择器列表
            text_fallback: 按文本内容回退定位
            role_fallback: 按 ARIA role 回退定位
            placeholder_fallback: 按 placeholder 属性回退定位
            label_fallback: 按 label 文本回退定位
            xpath_fallback: 按 XPath 回退定位
            required: 为 True 时全部失败则抛出 PlatformUIUpdateException

        Returns:
            SmartLocatorResult 包含定位结果和调试信息
        """
        attempts: list[str] = []
        locator: Locator | None = None
        strategy_used = ""

        # 策略 1: 主选择器遍历
        if primary_selectors:
            for selector in primary_selectors:
                try:
                    candidate = self.page.locator(selector).first
                    if candidate.is_visible(timeout=self.step_timeout_ms):
                        locator = candidate
                        strategy_used = f"primary_css: {selector[:80]}"
                        attempts.append(f"✓ primary: {selector[:80]}")
                        break
                    attempts.append(f"✗ primary (not visible): {selector[:80]}")
                except Exception:
                    attempts.append(f"✗ primary (error/timeout): {selector[:80]}")

        # 策略 2: 备选选择器列表
        if locator is None and fallback_selectors:
            for selector in fallback_selectors:
                try:
                    candidate = self.page.locator(selector).first
                    if candidate.is_visible(timeout=self.step_timeout_ms):
                        locator = candidate
                        strategy_used = f"fallback_css: {selector[:80]}"
                        attempts.append(f"✓ fallback: {selector[:80]}")
                        break
                    attempts.append(f"✗ fallback (not visible): {selector[:80]}")
                except Exception:
                    attempts.append(f"✗ fallback (error/timeout): {selector[:80]}")

        # 策略 3: 按文本内容查找
        if locator is None and text_fallback:
            try:
                candidate = self.page.get_by_text(text_fallback, exact=False).first
                if candidate.is_visible(timeout=self.step_timeout_ms):
                    locator = candidate
                    strategy_used = f"text: {text_fallback}"
                    attempts.append(f"✓ text: {text_fallback}")
            except Exception:
                attempts.append(f"✗ text: {text_fallback}")

        # 策略 4: 按 ARIA role 查找
        if locator is None and role_fallback:
            try:
                candidate = self.page.get_by_role("button", name=role_fallback).first
                if candidate.is_visible(timeout=self.step_timeout_ms):
                    locator = candidate
                    strategy_used = f"role: {role_fallback}"
                    attempts.append(f"✓ role: {role_fallback}")
            except Exception:
                attempts.append(f"✗ role: {role_fallback}")

        # 策略 5: 按 placeholder 查找
        if locator is None and placeholder_fallback:
            try:
                candidate = self.page.get_by_placeholder(placeholder_fallback).first
                if candidate.is_visible(timeout=self.step_timeout_ms):
                    locator = candidate
                    strategy_used = f"placeholder: {placeholder_fallback}"
                    attempts.append(f"✓ placeholder: {placeholder_fallback}")
            except Exception:
                attempts.append(f"✗ placeholder: {placeholder_fallback}")

        # 策略 6: 按 label 查找
        if locator is None and label_fallback:
            try:
                candidate = self.page.get_by_label(label_fallback).first
                if candidate.is_visible(timeout=self.step_timeout_ms):
                    locator = candidate
                    strategy_used = f"label: {label_fallback}"
                    attempts.append(f"✓ label: {label_fallback}")
            except Exception:
                attempts.append(f"✗ label: {label_fallback}")

        # 策略 7: 按 XPath 查找
        if locator is None and xpath_fallback:
            try:
                candidate = self.page.locator(f"xpath={xpath_fallback}").first
                if candidate.is_visible(timeout=self.step_timeout_ms):
                    locator = candidate
                    strategy_used = f"xpath: {xpath_fallback}"
                    attempts.append(f"✓ xpath: {xpath_fallback}")
            except Exception:
                attempts.append(f"✗ xpath: {xpath_fallback}")

        found = locator is not None

        if found:
            logger.info(
                "SmartLocator: found '%s' via %s (attempts: %d)",
                element_desc, strategy_used, len(attempts)
            )
            return SmartLocatorResult(
                locator=locator,
                found=True,
                strategy_used=strategy_used,
                attempts=attempts,
            )

        # 全部策略失败 → 保存调试快照并告警
        debug_screenshot, debug_dom_path = self._capture_debug_artifacts(element_desc, attempts)
        self._send_alert(element_desc, attempts, debug_screenshot, debug_dom_path)

        result = SmartLocatorResult(
            found=False,
            attempts=attempts,
            debug_screenshot=str(debug_screenshot) if debug_screenshot else "",
            debug_dom_path=str(debug_dom_path) if debug_dom_path else "",
        )

        if required:
            from toutiao_content_factory.exceptions import PlatformUIUpdateException
            raise PlatformUIUpdateException(
                f"SmartLocator: all strategies failed for '{element_desc}' on {self.platform}. " +
                f"Attempted {len(attempts)} strategies. Debug files saved.",
                platform=self.platform,
                element_desc=element_desc,
                attempted_selectors=attempts,
                debug_screenshot=str(debug_screenshot) if debug_screenshot else "",
                debug_dom_path=str(debug_dom_path) if debug_dom_path else "",
            )

        return result

    def _capture_debug_artifacts(
        self,
        element_desc: str,
        attempts: list[str],
    ) -> tuple[Path | None, Path | None]:
        """保存调试截图和 DOM 快照"""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in element_desc)[:40]
        run_debug_dir = self.debug_dir / f"{timestamp}_{safe_name}"
        try:
            run_debug_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None, None

        screenshot_path: Path | None = None
        dom_path: Path | None = None

        try:
            screenshot_path = run_debug_dir / "page_screenshot.png"
            self.page.screenshot(path=str(screenshot_path), full_page=True)
            logger.warning("SmartLocator: debug screenshot saved to %s", screenshot_path)
        except Exception as exc:
            logger.error("SmartLocator: failed to capture screenshot: %s", exc)

        try:
            dom_path = run_debug_dir / "page_dom.html"
            html_content = self.page.content()
            dom_path.write_text(html_content, encoding="utf-8")
            # Also save a concise DOM snapshot for easier diffing
            concise_path = run_debug_dir / "page_dom_concise.html"
            try:
                concise_html = self.page.evaluate("""
                    () => {
                        const el = document.querySelector('body');
                        return el ? el.outerHTML.substring(0, 50000) : '';
                    }
                """)
                concise_path.write_text(str(concise_html), encoding="utf-8")
            except Exception:
                pass
            logger.warning("SmartLocator: debug DOM saved to %s", dom_path)
        except Exception as exc:
            logger.error("SmartLocator: failed to capture DOM: %s", exc)

        # Save manifest
        try:
            manifest = {
                "timestamp": timestamp,
                "element_desc": element_desc,
                "platform": self.platform,
                "url": self.page.url,
                "attempts": attempts,
                "screenshot": str(screenshot_path) if screenshot_path else "",
                "dom": str(dom_path) if dom_path else "",
            }
            (run_debug_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        return screenshot_path, dom_path

    def _send_alert(
        self,
        element_desc: str,
        attempts: list[str],
        screenshot_path: Path | None,
        dom_path: Path | None,
    ) -> None:
        """通过通知渠道发送 UI 变更告警"""
        message = (
            f"平台：{self.platform}\\n"
            f"元素：{element_desc}\\n"
            f"尝试策略：{len(attempts)} 种均失败\\n"
            f"页面URL：{self.page.url}\\n"
        )
        if screenshot_path:
            message += f"截图：{screenshot_path}\\n"
        if dom_path:
            message += f"DOM：{dom_path}\\n"
        message += "\\n说明：平台前端可能已更新，请检查选择器。"

        logger.error(
            "SmartLocator: ALL STRATEGIES FAILED for '%s' on %s. URL: %s. Attempts: %s",
            element_desc, self.platform, self.page.url, "; ".join(attempts)
        )

        if self.notify_fn:
            try:
                self.notify_fn(f"UI变更告警 - {self.platform}", message)
            except Exception as exc:
                logger.warning("SmartLocator: failed to send alert: %s", exc)


# ============================================================================
# 4) Cookie 状态自愈机制
# ============================================================================

class CookieValidator:
    """Cookie 健康检查 + 自愈"""

    LOGIN_REDIRECT_PATTERNS = (
        "/login",
        "/signin",
        "passport.toutiao.com",
        "sso.toutiao.com",
        "mp.toutiao.com/profile_v4/login",
        "mp.weixin.qq.com/cgi-bin/login",
    )

    @classmethod
    def is_session_valid(cls, page: Page) -> bool:
        """检测当前页面是否还在有效登录态"""
        try:
            current_url = page.url.lower()
            if any(pattern.lower() in current_url for pattern in cls.LOGIN_REDIRECT_PATTERNS):
                logger.warning("CookieValidator: session redirected to login page: %s", page.url)
                return False
            try:
                title = page.title().lower()
                if any(word in title for word in ("登录", "login", "sign in")):
                    logger.warning("CookieValidator: page title suggests login required: %s", title)
                    return False
            except Exception:
                pass
            return True
        except Exception as exc:
            logger.error("CookieValidator: error checking session: %s", exc)
            return False

    @classmethod
    def validate_or_raise(cls, page: Page, notify_fn: Callable[[str, str], None] | None = None) -> None:
        """验证 Cookie 有效性，失效时触发通知并挂起"""
        if cls.is_session_valid(page):
            return

        message = f"Cookie 已失效，当前页面: {page.url}"
        logger.error(message)

        if notify_fn:
            try:
                notify_fn("Cookie失效告警", message + "\\n请手动扫码登录后继续。")
            except Exception:
                pass

        print("")
        print("=" * 60)
        print("  ⚠ Cookie 已失效，请在浏览器窗口中手动重新登录")
        print("  系统将在检测到有效登录态后自动继续...")
        print("=" * 60)

        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(3)
            if cls.is_session_valid(page):
                logger.info("CookieValidator: Session recovered!")
                return
            if page.is_closed():
                raise RuntimeError("Browser window was closed during cookie recovery wait.")

        raise RuntimeError("Cookie recovery timed out after 10 minutes. Please re-run with --init-publish-login.")


# ============================================================================
# 5) 浏览器进程健康检查 (Zombie Process Killer)
# ============================================================================

def check_and_cleanup_browser_processes(
    *,
    profile_dir: Path | str | None = None,
    process_names: tuple[str, ...] = ("msedge.exe", "msedgewebview2.exe", "chrome.exe", "chromium-browser.exe"),
    logger_override: logging.Logger | None = None,
) -> dict[str, Any]:
    """检测并清理残留浏览器进程，防止僵尸进程导致内存泄漏

    Returns 包含终止进程信息的字典。
    """
    _log = logger_override or logger

    try:
        import psutil
    except ModuleNotFoundError:
        _log.warning("psutil not available; skipping zombie process check.")
        return {"status": "skipped", "reason": "psutil not installed"}

    results: dict[str, Any] = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "found_processes": 0,
        "terminated": [],
        "killed": [],
        "orphaned": [],
        "errors": [],
    }

    profile_path_str = str(Path(profile_dir).resolve()).lower() if profile_dir else None

    for proc in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
        try:
            name = str(proc.info.get("name") or "").lower()
            if name not in process_names:
                continue

            pid = int(proc.info["pid"])
            ppid = int(proc.info.get("ppid") or 0)
            create_time = proc.info.get("create_time")

            results["found_processes"] += 1

            is_orphan = False
            try:
                parent = psutil.Process(ppid)
                if parent.status() == psutil.STATUS_ZOMBIE or not parent.is_running():
                    is_orphan = True
            except (psutil.NoSuchProcess, psutil.Error):
                is_orphan = True

            should_cleanup = is_orphan

            # If a profile_dir is specified, also check if this process is related
            if profile_path_str and not should_cleanup:
                try:
                    cmdline = [str(c).lower() for c in proc.cmdline()]
                    cwd = str(proc.cwd()).lower() if proc.cwd() else ""
                    if profile_path_str in cwd or any(profile_path_str in c for c in cmdline):
                        should_cleanup = True
                except (psutil.Error, OSError):
                    pass

            if should_cleanup:
                _log.info("Cleaning up browser process: pid=%d, name=%s, orphan=%s", pid, name, is_orphan)
                try:
                    proc.terminate()
                    results["terminated"].append(pid)
                except (psutil.NoSuchProcess, psutil.Error):
                    pass

                try:
                    gone, alive = psutil.wait_procs([proc], timeout=3)
                    if alive:
                        proc.kill()
                        results["killed"].append(pid)
                except (psutil.NoSuchProcess, psutil.Error):
                    pass

                if is_orphan:
                    results["orphaned"].append(pid)
        except (psutil.NoSuchProcess, psutil.Error, OSError, ValueError) as exc:
            results["errors"].append(str(exc))
            continue

    _log.info(
        "BrowserProcessHealth: found=%d, terminated=%d, killed=%d, orphaned=%d, errors=%d",
        results["found_processes"],
        len(results["terminated"]),
        len(results["killed"]),
        len(results["orphaned"]),
        len(results["errors"]),
    )
    return results


# ============================================================================
# 6) 带重试机制的安全交互函数 — safe_click / safe_fill
# ============================================================================

class SafeInteractionError(RuntimeError):
    """Raised when safe_click or safe_fill exhausts all retries."""


def safe_click(
    page: Page,
    selector: str,
    *,
    max_retries: int = 3,
    retry_interval_ms: int = 1000,
    timeout_ms: int = 5000,
    force: bool = False,
    description: str = "",
    platform: str = "unknown",
) -> bool:
    """点击元素，带自动重试和异常捕获。

    Args:
        page: Playwright Page 对象
        selector: CSS/Playwright 选择器
        max_retries: 最大重试次数（含首次尝试）
        retry_interval_ms: 重试间隔毫秒数
        timeout_ms: 单次点击超时毫秒数
        force: 是否强制点击（跳过可操作性检查）
        description: 元素描述（仅用于日志）
        platform: 平台标识（仅用于日志）

    Returns:
        True 表示点击成功，False 表示元素未找到（非异常场景）

    Raises:
        SafeInteractionError: 所有重试均失败时抛出
    """
    desc = description or selector
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=timeout_ms):
                if attempt < max_retries:
                    logger.debug(
                        "safe_click [%s/%s]: '%s' not visible on %s, retrying in %dms",
                        attempt, max_retries, desc, platform, retry_interval_ms,
                    )
                    page.wait_for_timeout(retry_interval_ms)
                    continue
                logger.warning(
                    "safe_click: '%s' not visible after %d attempts on %s",
                    desc, max_retries, platform,
                )
                return False

            locator.click(timeout=timeout_ms, force=force)
            logger.debug(
                "safe_click: clicked '%s' on %s (attempt %d/%d)",
                desc, platform, attempt, max_retries,
            )
            return True

        except PlaywrightTimeoutError as exc:
            last_exc = exc
            logger.debug(
                "safe_click [%s/%s]: timeout clicking '%s' on %s: %s",
                attempt, max_retries, desc, platform, exc,
            )
        except Exception as exc:
            last_exc = exc
            logger.debug(
                "safe_click [%s/%s]: error clicking '%s' on %s: %s",
                attempt, max_retries, desc, platform, exc,
            )

        if attempt < max_retries:
            page.wait_for_timeout(retry_interval_ms)

    raise SafeInteractionError(
        f"safe_click failed for '{desc}' on {platform} after {max_retries} attempts: {last_exc}"
    )


def safe_fill(
    page: Page,
    selector: str,
    value: str,
    *,
    max_retries: int = 3,
    retry_interval_ms: int = 1000,
    timeout_ms: int = 5000,
    force: bool = False,
    description: str = "",
    platform: str = "unknown",
) -> bool:
    """填充输入框，带自动重试和异常捕获。

    Args:
        page: Playwright Page 对象
        selector: CSS/Playwright 选择器
        value: 要填入的文本
        max_retries: 最大重试次数（含首次尝试）
        retry_interval_ms: 重试间隔毫秒数
        timeout_ms: 单次填充超时毫秒数
        force: 是否强制填充（跳过可操作性检查）
        description: 元素描述（仅用于日志）
        platform: 平台标识（仅用于日志）

    Returns:
        True 表示填充成功，False 表示元素未找到（非异常场景）

    Raises:
        SafeInteractionError: 所有重试均失败时抛出
    """
    desc = description or selector
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=timeout_ms):
                if attempt < max_retries:
                    logger.debug(
                        "safe_fill [%s/%s]: '%s' not visible on %s, retrying in %dms",
                        attempt, max_retries, desc, platform, retry_interval_ms,
                    )
                    page.wait_for_timeout(retry_interval_ms)
                    continue
                logger.warning(
                    "safe_fill: '%s' not visible after %d attempts on %s",
                    desc, max_retries, platform,
                )
                return False

            locator.fill(value, timeout=timeout_ms, force=force)
            logger.debug(
                "safe_fill: filled '%s' on %s (attempt %d/%d)",
                desc, platform, attempt, max_retries,
            )
            return True

        except PlaywrightTimeoutError as exc:
            last_exc = exc
            logger.debug(
                "safe_fill [%s/%s]: timeout filling '%s' on %s: %s",
                attempt, max_retries, desc, platform, exc,
            )
        except Exception as exc:
            last_exc = exc
            logger.debug(
                "safe_fill [%s/%s]: error filling '%s' on %s: %s",
                attempt, max_retries, desc, platform, exc,
            )

        if attempt < max_retries:
            page.wait_for_timeout(retry_interval_ms)

    raise SafeInteractionError(
        f"safe_fill failed for '{desc}' on {platform} after {max_retries} attempts: {last_exc}"
    )
