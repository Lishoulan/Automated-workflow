#!/usr/bin/env python3
"""Diagnose a persisted Toutiao login session without changing credentials."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from toutiao_content_factory.config import load_settings  # noqa: E402
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile  # noqa: E402
from toutiao_content_factory.services.compliance_service import get_proxy_server  # noqa: E402
from toutiao_content_factory.utils.browser_utils import (  # noqa: E402
    apply_stealth,
    build_persistent_context_options,
)


LOGIN_URL_MARKERS = ("/login", "/passport", "/auth/", "sso.toutiao.com")
LOGIN_TEXT_MARKERS = ("扫码登录", "立即登录", "重新登录", "登录失效")
READY_TEXT_MARKERS = ("创作中心", "作品管理", "数据中心", "发布")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose a persisted Toutiao login session.")
    parser.add_argument(
        "--account",
        default=os.getenv("TOUTIAO_ACCOUNT", "default"),
        help="Account key from config.yaml (default: TOUTIAO_ACCOUNT or default).",
    )
    parser.add_argument(
        "--url",
        default="https://mp.toutiao.com/profile_v4/graphic/publish",
        help="Toutiao page to probe.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window while probing. Use init_publish_login.ps1 for QR login.",
    )
    parser.add_argument("--timeout-ms", type=int, default=45000, help="Navigation timeout in milliseconds.")
    parser.add_argument("--settle-ms", type=int, default=4000, help="Wait after navigation in milliseconds.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Diagnostic JSON output path. Defaults to the selected account automation directory.",
    )
    return parser


def _resolve_profile_dir(settings) -> Path:
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = settings.project_root / profile_dir
    return profile_dir.resolve()


def _classify_page(url: str, body: str) -> str:
    url_lower = (url or "").lower()
    body_text = body or ""
    if any(marker in url_lower for marker in LOGIN_URL_MARKERS):
        return "login_required"
    if any(marker in body_text for marker in LOGIN_TEXT_MARKERS):
        return "login_required"
    if "mp.toutiao.com" in url_lower and (
        "profile_v4" in url_lower or any(marker in body_text for marker in READY_TEXT_MARKERS)
    ):
        return "ready"
    return "unknown"


def _read_body(page) -> str:
    try:
        return str(page.locator("body").inner_text(timeout=5000) or "")[:1200]
    except Exception:
        return ""


def run_diagnostic(args: argparse.Namespace) -> dict[str, Any]:
    settings = load_settings(PROJECT_ROOT, account_key=args.account)
    profile_dir = _resolve_profile_dir(settings)
    profile_dir.mkdir(parents=True, exist_ok=True)
    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logging.getLogger("diag_toutiao_login"),
        label="diagnose-login",
        extra_paths=[settings.account_runtime_dir],
    )

    output_path = args.output or (settings.automation_dir / "toutiao_login_diagnostic.json")
    output_path = output_path if output_path.is_absolute() else (PROJECT_ROOT / output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_path.parent / f"toutiao_login_{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"

    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "account_key": settings.account_key,
        "account_slug": settings.account_slug,
        "profile_dir": str(profile_dir),
        "probe_url": args.url,
        "final_url": "",
        "title": "",
        "state": "error",
        "body_excerpt": "",
        "screenshot_path": str(screenshot_path),
        "error": "",
    }

    launch_options, fingerprint = build_persistent_context_options(
        account_key=settings.account_key,
        profile_dir=profile_dir,
        proxy_server=get_proxy_server(),
        headless=not args.headed,
        channel=settings.publishing.channel or None,
        executable_path=settings.publishing.executable_path or None,
        timezone_id="Asia/Shanghai",
        locale="zh-CN",
    )

    context = None
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                **launch_options,
            )
            apply_stealth(context, fingerprint=fingerprint)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=max(5000, args.timeout_ms))
            page.wait_for_timeout(max(0, args.settle_ms))
            body = _read_body(page)
            result.update(
                {
                    "final_url": str(page.url or ""),
                    "title": str(page.title() or ""),
                    "body_excerpt": body,
                }
            )
            result["state"] = _classify_page(result["final_url"], body)
            try:
                page.screenshot(path=str(screenshot_path), full_page=False)
            except Exception as exc:
                result["screenshot_error"] = str(exc)
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

    result["output_path"] = str(output_path)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_diagnostic(args)
    except Exception as exc:
        print(f"Diagnostic failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["state"] == "ready":
        return 0
    if result["state"] == "login_required":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
