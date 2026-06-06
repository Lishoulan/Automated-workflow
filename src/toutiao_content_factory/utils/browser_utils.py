"""
Browser utility helpers for Playwright automation.
Provides stealth mode, timeout protection, and human-like interaction.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from contextlib import contextmanager
from typing import Any

try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

logger = logging.getLogger(__name__)


@contextmanager
def BrowserTimeout(seconds: float = 30.0):
    """Wrap a browser launch or navigation with a timeout.
    
    If the block does not complete within `seconds`, raises TimeoutError.
    This prevents the pipeline from hanging indefinitely when the browser
    fails to start (e.g., crashed profile, missing dependencies).
    """
    event = threading.Event()
    timed_out: list[bool] = []

    def worker():
        if not event.wait(seconds):
            timed_out.append(True)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        yield
    finally:
        event.set()
    if timed_out:
        raise TimeoutError(f"Browser operation timed out after {seconds}s")


def apply_stealth(context_or_page: Any) -> None:
    """Apply playwright-stealth anti-detection measures.
    
    Accepts either a Playwright BrowserContext or Page object.
    Silently skips if playwright_stealth is not installed.
    """
    if stealth_sync is None:
        logger.debug("playwright-stealth not installed; skipping.")
        return
    try:
        stealth_sync(context_or_page)
        logger.debug("playwright-stealth applied successfully.")
    except Exception as exc:
        logger.warning("playwright-stealth apply failed (non-blocking): %s", exc)


def human_delay(min_ms: float = 50, max_ms: float = 200) -> None:
    """Sleep for a random interval to simulate human reaction time."""
    time.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


def random_scroll(page: Any, min_scrolls: int = 2, max_scrolls: int = 5) -> None:
    """Simulate human-like random scrolling on a page."""
    try:
        scrolls = random.randint(min_scrolls, max_scrolls)
        for _ in range(scrolls):
            delta = random.randint(100, 400)
            page.mouse.wheel(0, delta)
            time.sleep(random.uniform(0.3, 1.2))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Safe interaction methods with retry and fallback support
# ---------------------------------------------------------------------------

def safe_click(
    page: Any,
    selector: str,
    timeout: int = 5000,
    retries: int = 1,
) -> bool:
    """Safely click an element with retry logic.

    Args:
        page: Playwright Page object.
        selector: CSS/XPath selector string.
        timeout: Wait timeout in milliseconds.
        retries: Number of retry attempts after the first failure.

    Returns:
        True if click succeeded, False otherwise (logs WARNING on failure).
    """
    for attempt in range(1 + retries):
        try:
            locator = page.locator(selector)
            locator.wait_for(state="visible", timeout=timeout)
            locator.click()
            return True
        except Exception as exc:
            if attempt < retries:
                logger.debug(
                    "safe_click attempt %d/%d failed for '%s': %s; retrying...",
                    attempt + 1, 1 + retries, selector, exc,
                )
                time.sleep(0.5)
            else:
                logger.warning(
                    "safe_click failed for '%s' after %d attempts: %s",
                    selector, 1 + retries, exc,
                )
    return False


def safe_fill(
    page: Any,
    selector: str,
    value: str,
    timeout: int = 5000,
    retries: int = 1,
    force: bool = False,
) -> bool:
    """Safely fill an input element with retry logic.

    Args:
        page: Playwright Page object.
        selector: CSS/XPath selector string.
        value: The text value to fill.
        timeout: Wait timeout in milliseconds.
        retries: Number of retry attempts after the first failure.
        force: Whether to use Playwright's force fill option.

    Returns:
        True if fill succeeded, False otherwise (logs WARNING on failure).
    """
    for attempt in range(1 + retries):
        try:
            locator = page.locator(selector)
            locator.wait_for(state="visible", timeout=timeout)
            locator.fill(value, force=force)
            return True
        except Exception as exc:
            if attempt < retries:
                logger.debug(
                    "safe_fill attempt %d/%d failed for '%s': %s; retrying...",
                    attempt + 1, 1 + retries, selector, exc,
                )
                time.sleep(0.5)
            else:
                logger.warning(
                    "safe_fill failed for '%s' after %d attempts: %s",
                    selector, 1 + retries, exc,
                )
    return False


def safe_click_with_fallbacks(
    page: Any,
    selectors: list[str],
    timeout: int = 5000,
    retries: int = 1,
) -> bool:
    """Safely click an element using a list of fallback selectors.

    Tries each selector in order; returns True on the first successful click.

    Args:
        page: Playwright Page object.
        selectors: Ordered list of CSS/XPath selector strings to try.
        timeout: Wait timeout in milliseconds.
        retries: Number of retry attempts per selector.

    Returns:
        True if any selector succeeded, False if all failed.
    """
    for selector in selectors:
        if safe_click(page, selector, timeout=timeout, retries=retries):
            return True
    logger.warning(
        "safe_click_with_fallbacks: all %d selectors failed.",
        len(selectors),
    )
    return False


def safe_fill_with_fallbacks(
    page: Any,
    selectors: list[str],
    value: str,
    timeout: int = 5000,
    retries: int = 1,
    force: bool = False,
) -> bool:
    """Safely fill an input element using a list of fallback selectors.

    Tries each selector in order; returns True on the first successful fill.

    Args:
        page: Playwright Page object.
        selectors: Ordered list of CSS/XPath selector strings to try.
        value: The text value to fill.
        timeout: Wait timeout in milliseconds.
        retries: Number of retry attempts per selector.
        force: Whether to use Playwright's force fill option.

    Returns:
        True if any selector succeeded, False if all failed.
    """
    for selector in selectors:
        if safe_fill(page, selector, value, timeout=timeout, retries=retries, force=force):
            return True
    logger.warning(
        "safe_fill_with_fallbacks: all %d selectors failed for value '%s'.",
        len(selectors), value[:50],
    )
    return False
