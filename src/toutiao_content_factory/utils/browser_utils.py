"""
Browser utility helpers for Playwright automation.
Provides stealth mode, stable per-account fingerprints, and human-like interaction.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

logger = logging.getLogger(__name__)


REALISTIC_UA_POOL = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
)
VIEWPORT_POOL = (
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1728, "height": 1117},
    {"width": 1920, "height": 1080},
)
WEBGL_POOL = (
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0)"),
)
LANGUAGE_POOL = (
    ("zh-CN", "zh", "en-US", "en"),
    ("zh-CN", "zh", "en"),
    ("zh-CN", "en-US", "en"),
)
TIMEZONE_POOL = ("Asia/Shanghai", "Asia/Hong_Kong", "Asia/Singapore")
LOCALE_POOL = ("zh-CN", "zh-HK", "en-US")
COLOR_SCHEMES = ("light", "no-preference")


@dataclass(frozen=True)
class BrowserFingerprint:
    seed: str
    user_agent: str
    viewport: dict[str, int]
    screen: dict[str, int]
    webgl_vendor: str
    webgl_renderer: str
    languages: tuple[str, ...]
    locale: str
    timezone_id: str
    platform: str
    hardware_concurrency: int
    device_memory: int
    color_scheme: str
    max_touch_points: int
    canvas_noise: tuple[int, int, int]
    webrtc_policy: str


@contextmanager
def BrowserTimeout(seconds: float = 30.0):
    """Wrap a browser launch or navigation with a timeout."""
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


def _stable_random(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def build_account_fingerprint(
    *,
    account_key: str,
    profile_dir: str | Path,
    timezone_id: str | None = None,
    locale: str | None = None,
) -> BrowserFingerprint:
    seed = f"{account_key}::{Path(profile_dir)}"
    rng = _stable_random(seed)
    viewport = dict(rng.choice(VIEWPORT_POOL))
    screen = {"width": viewport["width"], "height": viewport["height"]}
    webgl_vendor, webgl_renderer = rng.choice(WEBGL_POOL)
    chosen_locale = locale or rng.choice(LOCALE_POOL)
    chosen_timezone = timezone_id or rng.choice(TIMEZONE_POOL)
    return BrowserFingerprint(
        seed=seed,
        user_agent=rng.choice(REALISTIC_UA_POOL),
        viewport=viewport,
        screen=screen,
        webgl_vendor=webgl_vendor,
        webgl_renderer=webgl_renderer,
        languages=tuple(rng.choice(LANGUAGE_POOL)),
        locale=chosen_locale,
        timezone_id=chosen_timezone,
        platform="Win32",
        hardware_concurrency=rng.choice((4, 8, 12, 16)),
        device_memory=rng.choice((4, 8, 16)),
        color_scheme=rng.choice(COLOR_SCHEMES),
        max_touch_points=0,
        canvas_noise=(rng.randint(0, 2), rng.randint(0, 2), rng.randint(0, 2)),
        webrtc_policy="disable_non_proxied_udp",
    )


def build_persistent_context_options(
    *,
    account_key: str,
    profile_dir: str | Path,
    proxy_server: str = "",
    headless: bool = False,
    channel: str | None = None,
    executable_path: str | None = None,
    timezone_id: str | None = None,
    locale: str | None = None,
) -> tuple[dict[str, Any], BrowserFingerprint]:
    fingerprint = build_account_fingerprint(
        account_key=account_key,
        profile_dir=profile_dir,
        timezone_id=timezone_id,
        locale=locale,
    )
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        f"--lang={fingerprint.locale}",
        f"--force-webrtc-ip-handling-policy={fingerprint.webrtc_policy}",
        f"--webrtc-ip-handling-policy={fingerprint.webrtc_policy}",
        "--enforce-webrtc-ip-permission-check",
        "--disable-background-networking",
        "--disable-renderer-backgrounding",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    options: dict[str, Any] = {
        "headless": headless,
        "channel": channel or None,
        "executable_path": executable_path or None,
        "viewport": fingerprint.viewport,
        "screen": fingerprint.screen,
        "user_agent": fingerprint.user_agent,
        "locale": fingerprint.locale,
        "timezone_id": fingerprint.timezone_id,
        "color_scheme": fingerprint.color_scheme,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
    }
    if proxy_server:
        options["proxy"] = {"server": proxy_server}
    return options, fingerprint


def _build_stealth_script(fingerprint: BrowserFingerprint) -> str:
    payload = {
        "userAgent": fingerprint.user_agent,
        "platform": fingerprint.platform,
        "languages": list(fingerprint.languages),
        "hardwareConcurrency": fingerprint.hardware_concurrency,
        "deviceMemory": fingerprint.device_memory,
        "screenWidth": fingerprint.screen["width"],
        "screenHeight": fingerprint.screen["height"],
        "webglVendor": fingerprint.webgl_vendor,
        "webglRenderer": fingerprint.webgl_renderer,
        "maxTouchPoints": fingerprint.max_touch_points,
        "canvasNoise": list(fingerprint.canvas_noise),
    }
    return f"""
(() => {{
  const cfg = {json.dumps(payload, ensure_ascii=True)};
  const define = (obj, key, getter) => {{
    try {{
      Object.defineProperty(obj, key, {{
        get: getter,
        configurable: true,
      }});
    }} catch (_err) {{}}
  }};

  define(Navigator.prototype, 'webdriver', () => false);
  define(Navigator.prototype, 'userAgent', () => cfg.userAgent);
  define(Navigator.prototype, 'platform', () => cfg.platform);
  define(Navigator.prototype, 'languages', () => cfg.languages.slice());
  define(Navigator.prototype, 'language', () => cfg.languages[0]);
  define(Navigator.prototype, 'hardwareConcurrency', () => cfg.hardwareConcurrency);
  define(Navigator.prototype, 'deviceMemory', () => cfg.deviceMemory);
  define(Navigator.prototype, 'maxTouchPoints', () => cfg.maxTouchPoints);

  if (!window.chrome) {{
    Object.defineProperty(window, 'chrome', {{
      value: {{ runtime: {{}}, app: {{}}, csi: () => ({{}}) }},
      configurable: true,
    }});
  }}

  if (navigator.permissions && navigator.permissions.query) {{
    const originalQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (parameters) => {{
      if (parameters && parameters.name === 'notifications') {{
        return Promise.resolve({{ state: Notification.permission }});
      }}
      return originalQuery(parameters);
    }};
  }}

  define(Navigator.prototype, 'plugins', () => [
    {{ name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }},
    {{ name: 'Chromium PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' }},
    {{ name: 'Microsoft Edge PDF Viewer', filename: 'edge-pdf-viewer', description: '' }},
  ]);

  define(Screen.prototype, 'width', () => cfg.screenWidth);
  define(Screen.prototype, 'height', () => cfg.screenHeight);
  define(Screen.prototype, 'availWidth', () => cfg.screenWidth);
  define(Screen.prototype, 'availHeight', () => cfg.screenHeight - 40);
  define(Screen.prototype, 'colorDepth', () => 24);
  define(Screen.prototype, 'pixelDepth', () => 24);
  define(window, 'innerWidth', () => cfg.screenWidth - 16);
  define(window, 'innerHeight', () => cfg.screenHeight - 88);
  define(window, 'outerWidth', () => cfg.screenWidth);
  define(window, 'outerHeight', () => cfg.screenHeight);

  const getParameterProxy = (prototype) => {{
    if (!prototype || !prototype.getParameter) return;
    const originalGetParameter = prototype.getParameter;
    prototype.getParameter = new Proxy(originalGetParameter, {{
      apply(target, thisArg, args) {{
        const param = args && args[0];
        if (param === 37445) return cfg.webglVendor;
        if (param === 37446) return cfg.webglRenderer;
        return Reflect.apply(target, thisArg, args);
      }},
    }});
  }};
  getParameterProxy(window.WebGLRenderingContext && window.WebGLRenderingContext.prototype);
  getParameterProxy(window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype);

  if (window.CanvasRenderingContext2D && window.CanvasRenderingContext2D.prototype.getImageData) {{
    const originalGetImageData = window.CanvasRenderingContext2D.prototype.getImageData;
    window.CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
      const imageData = originalGetImageData.apply(this, args);
      if (imageData && imageData.data && imageData.data.length >= 4) {{
        imageData.data[0] = (imageData.data[0] + cfg.canvasNoise[0]) % 255;
        imageData.data[1] = (imageData.data[1] + cfg.canvasNoise[1]) % 255;
        imageData.data[2] = (imageData.data[2] + cfg.canvasNoise[2]) % 255;
      }}
      return imageData;
    }};
  }}

  const NativeRTCPeerConnection = window.RTCPeerConnection || window.webkitRTCPeerConnection;
  if (NativeRTCPeerConnection) {{
    const scrubSdp = (sdp) => String(sdp || '')
      .split('\\n')
      .filter((line) => !line.includes(' typ host '))
      .join('\\n');
    class WrappedRTCPeerConnection extends NativeRTCPeerConnection {{
      async createOffer(...args) {{
        const offer = await super.createOffer(...args);
        if (offer && offer.sdp) {{
          offer.sdp = scrubSdp(offer.sdp);
        }}
        return offer;
      }}
      async createAnswer(...args) {{
        const answer = await super.createAnswer(...args);
        if (answer && answer.sdp) {{
          answer.sdp = scrubSdp(answer.sdp);
        }}
        return answer;
      }}
      async setLocalDescription(description) {{
        if (description && description.sdp) {{
          description = {{
            type: description.type,
            sdp: scrubSdp(description.sdp),
          }};
        }}
        return super.setLocalDescription(description);
      }}
      addIceCandidate(candidate) {{
        if (candidate && candidate.candidate && String(candidate.candidate).includes(' typ host ')) {{
          return Promise.resolve();
        }}
        return super.addIceCandidate(candidate);
      }}
    }}
    window.RTCPeerConnection = WrappedRTCPeerConnection;
    window.webkitRTCPeerConnection = WrappedRTCPeerConnection;
  }}
}})();
"""


def apply_stealth(context_or_page: Any, *, fingerprint: BrowserFingerprint | None = None) -> None:
    """Apply stealth measures to a Playwright BrowserContext or Page."""
    if stealth_sync is not None:
        try:
            stealth_sync(context_or_page)
            logger.debug("playwright-stealth applied successfully.")
        except Exception as exc:
            logger.warning("playwright-stealth apply failed (non-blocking): %s", exc)
    else:
        logger.debug("playwright-stealth not installed; using local stealth patch only.")

    if fingerprint is None:
        fingerprint = build_account_fingerprint(account_key="default", profile_dir=".runtime/default")
    script = _build_stealth_script(fingerprint)
    try:
        context_or_page.add_init_script(script)
        logger.debug("Local stealth init script injected successfully.")
    except Exception as exc:
        logger.warning("Stealth init script injection failed (non-blocking): %s", exc)


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


def safe_click(
    page: Any,
    selector: str,
    timeout: int = 5000,
    retries: int = 1,
) -> bool:
    """Safely click an element with retry logic."""
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
    """Safely fill an input element with retry logic."""
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
    """Safely click an element using a list of fallback selectors."""
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
    """Safely fill an input element using a list of fallback selectors."""
    for selector in selectors:
        if safe_fill(page, selector, value, timeout=timeout, retries=retries, force=force):
            return True
    logger.warning(
        "safe_fill_with_fallbacks: all %d selectors failed for value '%s'.",
        len(selectors), value[:50],
    )
    return False
