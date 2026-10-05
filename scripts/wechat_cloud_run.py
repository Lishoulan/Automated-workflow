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
from toutiao_content_factory.services import wechat_metrics as metrics
from toutiao_content_factory.services.wechat_publication_verifier import verify_pending

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
        "WECHAT_ACCOUNT_NAME": "小椰科技",
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
            **({"expected_publication": record["expected_publication"]} if record.get("expected_publication") else {}),
        }, updated_at=datetime.now(timezone.utc).isoformat(),
    )
    write_state_file(state, xstate.STATE_PATH, "chore(wechat): record accepted cloud publication [skip ci]")


def write_state_file(state: dict, path: str, message: str) -> None:
    """Write one owned file without replacing the guard or the branch tree."""
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
    content_url = f"repos/{repo}/contents/{path}"
    existing = subprocess.run(["gh", "api", f"{content_url}?ref={xstate.WECHAT_STATE_BRANCH}"], capture_output=True, text=True, timeout=30)
    if existing.returncode and "404" not in existing.stderr:
        raise RuntimeError("Cannot check the current WeChat state file.")
    payload = {
        "message": message,
        "branch": xstate.WECHAT_STATE_BRANCH,
        "content": base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode(),
    }
    if not existing.returncode:
        payload["sha"] = json.loads(existing.stdout)["sha"]
    subprocess.run(["gh", "api", "--method", "PUT", content_url, "--input", "-"],
                   input=json.dumps(payload), capture_output=True, text=True, check=True, timeout=30)


def complete_run(settings, report: dict, exit_code: int, *, metrics_state: dict | None,
                 run_key: str, mode: str, started_at: str, excluded: bool = False) -> int:
    """Metrics errors are separate from the real result and never retry a publish."""
    if run_key and metrics_state is not None:
        try:
            from wechat_run_report import safe_text
            diagnostics_path = settings.automation_dir / "wechat_review_diagnostics.json"
            diagnostics = {}
            if diagnostics_path.exists() and diagnostics_path.stat().st_mtime >= metrics.parse_time(started_at).timestamp():
                diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            attempts = diagnostics.get("attempts", [])
            audited = [row for row in attempts if row.get("quality_review")]
            approved = [row for row in audited if row["quality_review"].get("verdict") == "approved"
                        and row["quality_review"].get("used_fallback") is False]
            run_record = {"run_key": run_key, "started_at": started_at, "mode": mode,
                          "excluded": excluded or mode == "check", "outcome": metrics.outcome(report),
                          "reason_codes": list(report.get("reason_codes", [])),
                          "attempt_count": report.get("attempt_count", 0),
                          "enrichment_count": diagnostics.get("enrichment_count", 0),
                          "eligible_candidate_count": diagnostics.get("eligible_candidate_count", 0),
                          "audited_candidate_count": len(audited), "review_pass_count": len(approved),
                          "rejection_reasons": [safe_text(reason) for row in attempts
                                                for field in ("fact_issues", "logic_issues", "structure_issues", "compliance_issues", "hard_blockers")
                                                for reason in row.get(field, [])],
                          "publication": report.get("publication", {}),
                          "expected_publication": report.get("expected_publication", {})}
            run_record["runtime_status"] = "error" if exit_code or report.get("status") == "error" else "ok"
            run_record["native_exit_code"] = report.get("native_exit_code")
            if report.get("wechat_status"):
                run_record["submission_outcome"] = metrics.outcome({"wechat_status": report["wechat_status"]})
            metrics_state = metrics.merge_run(metrics_state, run_record)
            write_state_file(metrics_state, metrics.METRICS_PATH, "chore(wechat): persist publication metrics [skip ci]")
            report["metrics"] = metrics.summary(metrics_state)
            report["formal_publication_confirmed"] = bool(report.get("formal_publication_confirmed") or report.get("publication", {}).get("confirmed") is True)
            (settings.automation_dir / "wechat_metrics_report.json").write_text(
                json.dumps({"runs": metrics_state["runs"], "summary": report["metrics"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            report["metrics_error"] = type(exc).__name__
            report["reason_codes"] = list(dict.fromkeys([*report.get("reason_codes", []), "metrics_persistence_failed"]))
            logger.error("WeChat metrics persistence failed: %s", type(exc).__name__)
            exit_code = exit_code or 1
    (settings.automation_dir / "wechat_cloud_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


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
    metrics_state = None
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_key = run_id + ":" + os.environ.get("GITHUB_RUN_ATTEMPT", "1") if run_id else ""
    started_at = datetime.now(timezone.utc).isoformat()
    confirmations = {}
    def finish(report, code, *, excluded=False):
        if confirmations:
            report["confirmed_publications"] = list(confirmations.values())
            report["formal_publication_confirmed"] = True
        return complete_run(settings, report, code, metrics_state=metrics_state, run_key=run_key,
                            mode=args.mode, started_at=started_at, excluded=excluded)
    try:
        restore_session(settings, encrypted, key, os.environ.get("WECHAT_SESSION_SEED", ""))
        # 先读持久化去重状态，网络异常时停止，不把未知当作“今天没发”。
        # Fail closed if the durable publication guard cannot be read.
        state = xstate.read_remote_state(ROOT, branch=xstate.WECHAT_STATE_BRANCH, strict=True)
        if run_key:
            metrics_state = metrics.validate_metrics(xstate.read_remote_state(
                ROOT, branch=xstate.WECHAT_STATE_BRANCH, path=metrics.METRICS_PATH, strict=True))
            pending = [{**row["expected_publication"], "run_key": row["run_key"]}
                       for row in metrics_state["runs"] if (row.get("outcome") in metrics.PENDING or row.get("submission_outcome") in metrics.PENDING)
                       and row.get("outcome") != "published_confirmed"
                       and row.get("expected_publication")]
            confirmations = verify_pending(settings, pending)
            for row in metrics_state["runs"]:
                if row.get("run_key") in confirmations:
                    row.update(outcome="published_confirmed", publication=confirmations[row["run_key"]])
            unresolved = [row for row in metrics_state["runs"] if (row.get("outcome") in metrics.PENDING or row.get("submission_outcome") in metrics.PENDING)
                          and row.get("outcome") != "published_confirmed"
                          and not row.get("excluded")]
            if args.mode == "publish" and unresolved:
                uncertain = any(row.get("outcome") == "submit_uncertain" or row.get("submission_outcome") == "submit_uncertain" for row in unresolved)
                return finish({"status": "pending_reconciliation", "wechat_status": "publish_uncertain" if uncertain else "submitted_for_review",
                               "publication_attempted": False, "pending_count": len(unresolved)}, 1 if uncertain else 0, excluded=True)
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        recorded = xstate.published_on(state, today, platform="wechat", account=settings.account_slug)
        if args.mode == "publish" and recorded:
            if recorded["status"] == "publish_uncertain":
                return finish({"status": "publish_uncertain", "wechat_status": "publish_uncertain"}, 1, excluded=True)
            return finish({"status": "already_published", "wechat_status": recorded["status"]}, 0, excluded=True)
        command = [sys.executable, str(ROOT / "scripts" / "scheduled_wechat_publish.py"), "--account", args.account]
        checked = subprocess.run(command + ["--check-login"], cwd=ROOT, timeout=120)
        if checked.returncode:
            return finish({"status": "publish_login_required", "exit_code": checked.returncode,
                           "native_exit_code": checked.returncode}, checked.returncode)
        save_session(settings, encrypted, key)
        if args.mode == "check":
            return finish({"status": "ready", "publication_requested": False}, 0)
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
            expected = record.get("expected_publication", {})
            if expected and run_key:
                report["expected_publication"] = expected
                confirmation = verify_pending(settings, [{**expected, "run_key": run_key}])
                if run_key in confirmation:
                    report["publication"] = confirmation[run_key]
        elif report.get("publication_attempted") is not False:
            record_publication(settings, {"status": "publish_uncertain"}, today)
        return finish(report, cloud_exit_code(report, result.returncode))
    except Exception as exc:
        logger.error("WeChat cloud run stopped: %s", type(exc).__name__)
        if publication_started and not record:
            try:
                record_publication(settings, {"status": "publish_uncertain"}, today)
            except Exception as guard_exc:
                logger.error("Cannot persist uncertain publication guard: %s", type(guard_exc).__name__)
        error_report = {"status": "error", "error_type": type(exc).__name__}
        if record:
            error_report.update(wechat_status=record["status"], publication_attempted=True,
                                expected_publication=record.get("expected_publication", {}),
                                verification_error=type(exc).__name__)
        elif publication_started:
            error_report.update(wechat_status="publish_uncertain", publication_attempted=True)
        return finish(error_report, 1)


if __name__ == "__main__":
    raise SystemExit(main())
