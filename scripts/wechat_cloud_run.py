"""Run the reviewed WeChat workflow on an ephemeral cloud runner."""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services import cross_channel_state as xstate
from wechat_cloud_session import restore_session, save_session
from wechat_run_report import cloud_exit_code, fresh_json

logger = logging.getLogger("wechat_cloud")
ACCEPTED_STATUSES = {"submitted", "submitted_for_review", "published", "success"}


def configure_environment(account: str) -> None:
    from toutiao_content_factory.config import account_env_name

    overrides = {
        "TARGET_PLATFORM": "wechat", "WECHAT_ENABLED": "1", "WECHAT_AUTO_PUBLISH": "1",
        "WECHAT_EDITOR_135_ENABLED": "0", "TOUTIAO_PUBLISH_ENABLED": "0",
        "TOUTIAO_AUTO_PUBLISH": "0", "TOUTIAO_WEITOUTIAO_ENABLED": "0",
        "DOUYIN_ENABLED": "0", "DOUYIN_AUTO_PUBLISH": "0",
        "TOUTIAO_PUBLISH_HEADLESS": "1", "TOUTIAO_PUBLISH_CHANNEL": "",
        "TOUTIAO_BROWSER_EXECUTABLE": "", "TOUTIAO_LOCAL_FALLBACK_AFTER_HOUR": "-1",
        "LETTA_ENABLED": "0", "DEERFLOW_ENABLED": "0", "PROMPT_OPTIMIZER_ENABLED": "0",
        "WECHAT_TECH_TOPICS_ONLY": "1", "TOUTIAO_QUALITY_REVIEW_MAX_AUTO_REVISIONS": "2",
        "TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS": "5",
    }
    for name, value in overrides.items():
        os.environ[name] = value
        os.environ[account_env_name(name, account)] = value


def latest_accepted_record(history: Path, today: str) -> dict[str, Any] | None:
    if not history.exists():
        return None
    payload = json.loads(history.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("history", []))
    if not isinstance(payload, list):
        raise ValueError("Unrecognized WeChat publication history.")
    for record in reversed(payload):
        if (
            isinstance(record, dict) and record.get("platform") == "wechat"
            and record.get("status") in ACCEPTED_STATUSES
            and str(record.get("published_at", "")).startswith(today)
        ):
            return record
    return None


def record_publication(settings: Any, record: dict[str, Any], today: str) -> None:
    state = xstate.read_remote_state(ROOT, branch=xstate.WECHAT_STATE_BRANCH, strict=True)
    publish_url = str(record.get("publish_url", ""))
    if urlsplit(publish_url).path.startswith("/cgi-bin/"):
        publish_url = ""
    state = xstate.merge_entry(
        state, day=today, platform="wechat", account=settings.account_slug,
        record={
            "channel": "cloud", "status": "submitted_for_review" if record["status"] == "submitted" else record["status"],
            "title": record.get("title", ""), "publish_url": publish_url,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }, updated_at=datetime.now(timezone.utc).isoformat(),
    )
    repo = os.environ["GITHUB_REPOSITORY"]
    ref_url = f"repos/{repo}/git/ref/heads/{xstate.WECHAT_STATE_BRANCH}"
    probe = subprocess.run(["gh", "api", ref_url], capture_output=True, text=True, timeout=30)
    if probe.returncode:
        if "404" not in probe.stderr:
            raise RuntimeError("Cannot check the WeChat state branch.")
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        subprocess.run([
            "gh", "api", "--method", "POST", f"repos/{repo}/git/refs",
            "-f", f"ref=refs/heads/{xstate.WECHAT_STATE_BRANCH}", "-f", f"sha={base}",
        ], capture_output=True, text=True, check=True, timeout=30)
    content_url = f"repos/{repo}/contents/{xstate.STATE_PATH}"
    existing = subprocess.run(["gh", "api", f"{content_url}?ref={xstate.WECHAT_STATE_BRANCH}"], capture_output=True, text=True, timeout=30)
    if existing.returncode and "404" not in existing.stderr:
        raise RuntimeError("Cannot check the current WeChat state file.")
    payload = {
        "message": "chore(wechat): record accepted cloud publication [skip ci]",
        "branch": xstate.WECHAT_STATE_BRANCH,
        "content": base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode(),
    }
    if not existing.returncode:
        payload["sha"] = json.loads(existing.stdout)["sha"]
    subprocess.run(["gh", "api", "--method", "PUT", content_url, "--input", "-"],
                   input=json.dumps(payload), capture_output=True, text=True, check=True, timeout=30)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", choices=["default"], default="default")
    parser.add_argument("--mode", choices=["check", "publish"], default="check")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    configure_environment(args.account)
    settings = load_settings(ROOT, account_key=args.account)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.automation_dir.mkdir(parents=True, exist_ok=True)
    encrypted = settings.account_runtime_dir / "automation" / "wechat_cloud_session.enc"
    result_path = settings.automation_dir / "wechat_cloud_result.json"
    key = os.environ.get("WECHAT_SESSION_KEY", "")
    publication_started = False
    record = None
    try:
        restore_session(settings, encrypted, key, os.environ.get("WECHAT_SESSION_SEED", ""))
        # 先读持久化去重状态，网络异常时停止，不把未知当作“今天没发”。
        # Fail closed if the durable publication guard cannot be read.
        state = xstate.read_remote_state(ROOT, branch=xstate.WECHAT_STATE_BRANCH, strict=True)
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        recorded = xstate.published_on(state, today, platform="wechat", account=settings.account_slug)
        if args.mode == "publish" and recorded:
            if recorded["status"] == "publish_uncertain":
                result_path.write_text(json.dumps({"status": "publish_uncertain", "wechat_status": "publish_uncertain"}), encoding="utf-8")
                return 1
            result_path.write_text(json.dumps({"status": "already_published", "wechat_status": recorded["status"]}), encoding="utf-8")
            return 0
        command = [sys.executable, str(ROOT / "scripts" / "scheduled_wechat_publish.py"), "--account", args.account]
        checked = subprocess.run(command + ["--check-login"], cwd=ROOT, timeout=120)
        if checked.returncode:
            result_path.write_text(json.dumps({"status": "publish_login_required", "exit_code": checked.returncode}), encoding="utf-8")
            return checked.returncode
        save_session(settings, encrypted, key)
        if args.mode == "check":
            result_path.write_text(json.dumps({"status": "ready", "publication_requested": False}), encoding="utf-8")
            return 0
        started = time.time_ns()
        publication_started = True
        result = subprocess.run(command + ["--enable-auto-publish"], cwd=ROOT, timeout=2700)
        save_session(settings, encrypted, key)
        record = latest_accepted_record(settings.output_dir / settings.wechat.history_filename, today)
        if record:
            record_publication(settings, record, today)
        report = fresh_json(settings.automation_dir / "wechat_scheduled_result.json", started)
        if not report:
            raise RuntimeError("Missing current WeChat run report.")
        if record:
            report["publication_attempted"] = True
        elif report.get("publication_attempted") is not False:
            record_publication(settings, {"status": "publish_uncertain"}, today)
        result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return cloud_exit_code(report, result.returncode)
    except Exception as exc:
        logger.error("WeChat cloud run stopped: %s", type(exc).__name__)
        if publication_started and not record:
            try:
                record_publication(settings, {"status": "publish_uncertain"}, today)
            except Exception as guard_exc:
                logger.error("Cannot persist uncertain publication guard: %s", type(guard_exc).__name__)
        result_path.write_text(json.dumps({"status": "error", "error_type": type(exc).__name__}), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
