"""Initialize or check the fixed-device browser; never create or submit content."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import backup_browser_storage_state
from toutiao_content_factory.services.wechat_publication_verifier import BACKEND_RECORDS_JS
from toutiao_content_factory.services.wechat_service import WechatPublishService


def confirmed_identity(snapshot: dict, expected_account: str) -> bool:
    account = str(snapshot.get("account", "")).strip()
    if not expected_account:
        raise ValueError("The fixed device must have an expected WeChat account name")
    if account and account != expected_account:
        raise RuntimeError("Scanned WeChat account does not match the configured account")
    return account == expected_account


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default="default")
    parser.add_argument("--login", action="store_true", help="Show a QR when login is expired")
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    if not 1 <= args.wait_seconds <= 600:
        raise ValueError("Login wait must be between 1 and 600 seconds")
    if os.environ.get("WECHAT_EXECUTION_HOST") != "fixed":
        raise ValueError("This command requires fixed-device execution")
    settings = load_settings(ROOT, account_key=args.account)
    if settings.platform != "wechat" or not settings.wechat.account_name:
        raise ValueError("Configure the WeChat platform and expected account name first")
    service = WechatPublishService(settings, logging.getLogger(__name__))
    profile = service._resolve_browser_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    evidence = settings.account_runtime_dir / "automation" / "fixed_login"
    evidence.mkdir(parents=True, exist_ok=True)
    report_path = evidence / "status.json"

    def report(status, **fields):
        payload = {"status": status, "account": settings.wechat.account_name,
                   "publication_attempted": False, "execution_host": "fixed",
                   "checked_at": datetime.now(timezone.utc).isoformat(), **fields}
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    from playwright.sync_api import Error, sync_playwright
    try:
        with sync_playwright() as playwright:
            # A busy profile fails at launch; never terminate another run to obtain it.
            context = service._launch_persistent_browser(playwright, profile_dir=profile, headless=True)
            try:
                page = context.new_page()
                page.goto("https://mp.weixin.qq.com/", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                deadline = time.monotonic() + (args.wait_seconds if args.login else 0)
                qr_shown = False
                while True:
                    try:
                        snapshot = page.evaluate(BACKEND_RECORDS_JS)
                    except Error as exc:
                        # Confirming a scan navigates away from the QR page.
                        if "Execution context was destroyed" not in str(exc):
                            raise
                        if not args.login or time.monotonic() >= deadline:
                            report("login_required" if not args.login else "scan_window_expired")
                            return 2
                        page.wait_for_timeout(250)
                        continue
                    if confirmed_identity(snapshot, settings.wechat.account_name):
                        backup = backup_browser_storage_state(context, platform="WeChat", profile_dir=profile,
                            account_runtime_dir=settings.account_runtime_dir, logger=logging.getLogger(__name__))
                        if not backup.get("saved"):
                            raise RuntimeError("Login confirmed but session backup failed")
                        pending = page.evaluate("""() => [...document.querySelectorAll('.weui-desktop-mass__content,.publish_hover_content')]
                            .filter(x=>/审核中|待审核|发表中|提交中/.test(x.innerText)).length""")
                        (evidence / "backend-proof.json").write_text(json.dumps({**snapshot, "pending_count": pending,
                            "read_only": True, "checked_at": datetime.now(timezone.utc).isoformat()},
                            ensure_ascii=False, indent=2), encoding="utf-8")
                        report("ready", profile_dir=str(profile))
                        print("Fixed-device login confirmed and saved; no publication.", flush=True)
                        return 0
                    if not args.login or time.monotonic() >= deadline:
                        report("login_required" if not args.login else "scan_window_expired")
                        return 2
                    if not qr_shown:
                        for candidate in page.locator("img").all():
                            if "qrcode" in (candidate.get_attribute("src") or "").lower() and candidate.is_visible():
                                qr_path = evidence / "administrator-qr.png"
                                candidate.screenshot(path=str(qr_path))
                                report("awaiting_administrator_scan", qr_path=str(qr_path), expires_in_seconds=args.wait_seconds)
                                print("Administrator QR ready on the fixed device; no publication.", flush=True)
                                qr_shown = True
                                break
                    page.wait_for_timeout(1000)
            finally:
                context.close()
    except Exception as exc:
        report("error", error_type=type(exc).__name__)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
