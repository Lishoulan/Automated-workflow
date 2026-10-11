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
from toutiao_content_factory.services import wechat_retry as retry
from toutiao_content_factory.services.wechat_publication_verifier import verify_pending

logger = logging.getLogger("wechat_cloud")
ACCEPTED_STATUSES = {"submitted", "submitted_for_review", "published", "success"}


def configure_environment(account: str, *, draft_only: bool = False) -> None:
    from toutiao_content_factory.config import account_env_name

    host = os.environ.get("WECHAT_EXECUTION_HOST", "cloud")
    if host not in {"cloud", "fixed"}:
        raise ValueError("Unknown WeChat execution host")

    overrides = {
        "TARGET_PLATFORM": "wechat", "WECHAT_ENABLED": "1", "WECHAT_AUTO_PUBLISH": "1",
        "WECHAT_EDITOR_135_ENABLED": "0", "TOUTIAO_PUBLISH_ENABLED": "0",
        "TOUTIAO_AUTO_PUBLISH": "0", "TOUTIAO_WEITOUTIAO_ENABLED": "0",
        "DOUYIN_ENABLED": "0", "DOUYIN_AUTO_PUBLISH": "0",
        "TOUTIAO_PUBLISH_HEADLESS": "1", "TOUTIAO_PUBLISH_CHANNEL": "msedge" if host == "fixed" and os.name == "nt" else "",
        "TOUTIAO_BROWSER_EXECUTABLE": "", "TOUTIAO_LOCAL_FALLBACK_AFTER_HOUR": "-1",
        "LETTA_ENABLED": "0", "DEERFLOW_ENABLED": "0", "PROMPT_OPTIMIZER_ENABLED": "0",
        "WECHAT_TECH_TOPICS_ONLY": "1", "TOUTIAO_QUALITY_REVIEW_MAX_AUTO_REVISIONS": "2",
        "TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS": "5",
        "WECHAT_ACCOUNT_NAME": "小椰科技",
        "WECHAT_DRAFT_ONLY": "1" if draft_only else "0",
        "TOUTIAO_SKIP_IMAGE_GENERATION": "0",
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
    for field, default in {"daily_run_count": None, "daily_processing_seconds": None,
                           "processing_elapsed_seconds": None, "retry_parent_run_id": "", "retry_status": ""}.items():
        report.setdefault(field, default)
    report["manual_validation_id"] = os.environ.get("WECHAT_VALIDATION_ID", "")
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
                        and row["quality_review"].get("used_fallback") is False
                        and row["quality_review"].get("audit_integrity_status") == "complete"]
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
            if mode == "draft":
                run_record["draft_delivery"] = report.get("draft_delivery", {})
            run_record["elapsed_seconds"] = report.get("processing_elapsed_seconds")
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


def seed_retry_day(state: dict, metrics_state: dict, day: str) -> dict:
    """Count existing runs on rollout; missing durations require GitHub proof."""
    state = retry.validate(state)
    entry = retry.today_entry(state, day)
    for row in metrics_state.get("runs", []):
        if row.get("excluded") or row.get("mode") not in {"publish", "draft"} or retry.day_of(row["started_at"]) != day:
            continue
        key = row["run_key"]
        if key in entry["runs"]:
            continue
        elapsed = row.get("elapsed_seconds")
        if type(elapsed) not in {int, float}:
            run_id, attempt = key.split(":", 1)
            response = subprocess.run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}/attempts/{attempt}/jobs"],
                                      capture_output=True, text=True, timeout=30, check=True)
            jobs = json.loads(response.stdout)["jobs"]
            starts = [metrics.parse_time(job["started_at"]) for job in jobs]
            ends = [metrics.parse_time(job["completed_at"]) for job in jobs]
            if not starts or any(stamp is None for stamp in [*starts, *ends]):
                raise RuntimeError("Cannot establish previous WeChat processing time")
            elapsed = (max(ends) - min(starts)).total_seconds()
        entry["runs"][key] = {"status": "completed", "started_at": row["started_at"], "parent": "",
                               "elapsed_seconds": elapsed, "outcome": row.get("outcome", "")}
    return retry.validate(state)


def finish_retry(settings, state, day, key, started, report, code):
    """Persist completion and dispatch intent before crossing the API boundary."""
    report["processing_elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["retry_parent_run_id"] = os.environ.get("WECHAT_RETRY_PARENT", "")
    diagnostics_path = settings.automation_dir / "wechat_review_diagnostics.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8")) if diagnostics_path.exists() else {}
    state = retry.complete(state, day, key, report["processing_elapsed_seconds"], report, diagnostics)
    entry = retry.today_entry(state, day)
    report["daily_run_count"] = len(entry["runs"])
    report["daily_budget_seconds"] = retry.budget_seconds(entry)
    report["daily_processing_seconds"] = round(sum(row.get("elapsed_seconds", 0) for row in entry["runs"].values()), 3)
    report["retry_status"] = "stopped"
    write_state_file(state, retry.STATE_PATH, "chore(wechat): finish daily reservation [skip ci]")
    if retry.day_of(datetime.now(timezone.utc).isoformat()) != day:
        report["retry_status"] = "stale_relay"
    elif retry.can_relay(entry, report, code):
        state = retry.dispatch_intent(state, day, key)
        write_state_file(state, retry.STATE_PATH, "chore(wechat): persist relay intent [skip ci]")
        report["retry_status"] = "dispatch_uncertain"
        # A transport failure may still have reached GitHub. Never send twice.
        subprocess.run(["gh", "workflow", "run", "wechat-daily-run.yml", "--ref", "main", "-f", "mode=" + ("draft" if report.get("delivery_mode") == "draft" else "publish"),
                        "-f", f"retry_parent_run_id={key}", "-f", f"retry_day={day}"],
                       capture_output=True, text=True, timeout=30, check=True)
        state["days"][day]["dispatches"][key]["status"] = "dispatched"
        write_state_file(state, retry.STATE_PATH, "chore(wechat): record dispatched relay [skip ci]")
        report["retry_status"] = "dispatched"
    elif (report.get("status") == "no_topics" and len(entry["runs"]) < retry.MAX_RUNS
          and retry.remaining(entry) >= retry.PREPARATION_MIN_SECONDS):
        report["retry_status"] = "waiting_for_material"
        report["retry_message"] = "素材不足，保留实际次数与耗时，等待下一定时窗口；未保存新草稿。"
    elif (report.get("publication_attempted") is False and report.get("status") in {"review_required", "no_topics"}
          and (len(entry["runs"]) >= retry.MAX_RUNS or retry.remaining(entry) < retry.PREPARATION_MIN_SECONDS)):
        report["retry_status"] = "daily_budget_exhausted"
        report["retry_message"] = "当天未发表，自动补跑已结束"
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", choices=["default"], default="default")
    parser.add_argument("--mode", choices=["check", "publish", "draft"], default="check")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if args.mode == "publish" and (os.environ.get("WECHAT_DELIVERY_MODE") == "draft"
                                  or load_settings(ROOT, account_key=args.account).wechat.draft_only):
        args.mode = "draft"
    if args.mode == "draft":
        configure_environment(args.account, draft_only=True)
    else:
        configure_environment(args.account)
    settings = load_settings(ROOT, account_key=args.account)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.automation_dir.mkdir(parents=True, exist_ok=True)
    encrypted = settings.account_runtime_dir / "automation" / "wechat_cloud_session.enc"
    result_path = settings.automation_dir / "wechat_cloud_result.json"
    key = os.environ.get("WECHAT_SESSION_KEY", "")
    fixed_device = os.environ.get("WECHAT_EXECUTION_HOST") == "fixed"
    publication_started = False
    workflow_started = None
    workflow_completed = False
    previous_execution_id = os.environ.get("WECHAT_EXECUTION_ID")
    record = None
    metrics_state = None
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_key = run_id + ":" + os.environ.get("GITHUB_RUN_ATTEMPT", "1") if run_id else ""
    started_at = datetime.now(timezone.utc).isoformat()
    confirmations = {}
    retry_state = None
    reserved_started = None
    def finish(report, code, *, excluded=False):
        report["execution_host"] = "fixed" if fixed_device else "cloud"
        report["delivery_mode"] = args.mode
        if args.mode == "draft":
            report["publication_attempted"] = False
            report["formal_publication_confirmed"] = False
            report["manual_publication_required"] = True
        if confirmations:
            report["confirmed_publications"] = list(confirmations.values())
            report["formal_publication_confirmed"] = True
        if reserved_started is not None:
            report["processing_elapsed_seconds"] = round(time.monotonic() - reserved_started, 3)
        code = complete_run(settings, report, code, metrics_state=metrics_state, run_key=run_key,
                            mode=args.mode, started_at=started_at, excluded=excluded)
        if retry_state is not None and reserved_started is not None:
            try:
                code = finish_retry(settings, retry_state, today, run_key, reserved_started, report, code)
            except Exception as exc:
                report["retry_state_error"] = type(exc).__name__
                report["reason_codes"] = list(dict.fromkeys([*report.get("reason_codes", []), "retry_persistence_or_dispatch_failed"]))
                code = code or 1
            result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if previous_execution_id is None:
            os.environ.pop("WECHAT_EXECUTION_ID", None)
        else:
            os.environ["WECHAT_EXECUTION_ID"] = previous_execution_id
        return code
    try:
        if fixed_device and args.mode != "check" and (not run_key or os.environ.get("GITHUB_ACTIONS") != "true"):
            raise ValueError("Fixed-device publication requires the GitHub workflow and its shared reservation")
        refresh_session = os.environ.get("WECHAT_SESSION_REFRESH") == "1"
        if refresh_session and args.mode != "check":
            raise ValueError("Session refresh requires a read-only login check")
        restore_options = {"prefer_seed": True} if refresh_session else {}
        if fixed_device and refresh_session:
            raise ValueError("Fixed device login cannot be overwritten by a cloud seed")
        if not fixed_device:
            restore_session(settings, encrypted, key, os.environ.get("WECHAT_SESSION_SEED", ""), **restore_options)
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
            confirmations = verify_pending(settings, pending) if args.mode != "draft" else {}
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
        if args.mode == "draft" and metrics_state is not None:
            deliveries = [row for row in metrics_state["runs"] if row.get("mode") == "draft" and not row.get("excluded")]
            retry_state = retry.validate(xstate.read_remote_state(ROOT, branch=xstate.WECHAT_STATE_BRANCH,
                                                                  path=retry.STATE_PATH, strict=True))
            # Reservations survive a metrics-write failure and remain a delivery guard.
            deliveries.extend(row for entry in retry_state["days"].values() for row in entry["runs"].values()
                              if row.get("draft_delivery"))
            if any(row.get("draft_delivery", {}).get("status") == "uncertain" for row in deliveries):
                return finish({"status": "draft_reconciliation_required", "draft_delivery": {"status": "uncertain"}}, 1, excluded=True)
            saved = [row for row in deliveries if retry.day_of(row["started_at"]) == today and row.get("draft_delivery", {}).get("status") == "saved"]
            if saved:
                return finish({"status": "already_drafted", "wechat_status": "draft_saved",
                               "draft_delivery": saved[-1]["draft_delivery"]}, 0, excluded=True)
        recorded = xstate.published_on(state, today, platform="wechat", account=settings.account_slug)
        if args.mode == "publish" and recorded:
            if recorded["status"] == "publish_uncertain":
                return finish({"status": "publish_uncertain", "wechat_status": "publish_uncertain"}, 1, excluded=True)
            return finish({"status": "already_published", "wechat_status": recorded["status"]}, 0, excluded=True)
        context_path = settings.automation_dir / "wechat_retry_context.json"
        if context_path.exists():
            context_path.unlink()  # An old local context cannot authorize a relay.
        if run_key and args.mode != "check":
            retry_state = seed_retry_day(retry_state if retry_state is not None else xstate.read_remote_state(
                ROOT, branch=xstate.WECHAT_STATE_BRANCH, path=retry.STATE_PATH, strict=True), metrics_state, today)
            retry_state, reservation = retry.reserve(retry_state, today, run_key, started_at,
                parent=os.environ.get("WECHAT_RETRY_PARENT", ""), parent_day=os.environ.get("WECHAT_RETRY_DAY", ""),
                validation_id=os.environ.get("WECHAT_VALIDATION_ID", ""), head_sha=os.environ.get("GITHUB_SHA", ""))
            if reservation != "reserved":
                if reservation == "daily_budget_exhausted":
                    write_state_file(retry_state, retry.STATE_PATH, "chore(wechat): retain imported daily usage [skip ci]")
                return finish({"status": "daily_retry_stopped", "publication_attempted": False, "native_exit_code": None, "retry_status": reservation,
                               "daily_run_count": len(retry.today_entry(retry_state, today)["runs"]),
                               "daily_processing_seconds": sum(row.get("elapsed_seconds", 0) for row in retry.today_entry(retry_state, today)["runs"].values()),
                               "daily_budget_seconds": retry.budget_seconds(retry.today_entry(retry_state, today)),
                               "retry_message": "当天未发表，自动补跑已结束"}, 0, excluded=True)
            write_state_file(retry_state, retry.STATE_PATH, "chore(wechat): reserve daily publish run [skip ci]")
            reserved_started = time.monotonic()
            entry = retry.today_entry(retry_state, today)
            prior_inputs = [item for prior in retry_state["days"].values() for item in prior.get("failed_inputs", [])]
            context_path.write_text(json.dumps(retry.retry_context({"failed_inputs": prior_inputs}), ensure_ascii=False), encoding="utf-8")
            os.environ["WECHAT_REGENERATING"] = "1" if prior_inputs else "0"
            from toutiao_content_factory.services.wechat_execution import FINISH_RESERVE_SECONDS, SUBMISSION_MIN_SECONDS
            remaining = min(2700, retry.remaining(entry) - FINISH_RESERVE_SECONDS)
            os.environ["WECHAT_RUN_DEADLINE_EPOCH"] = str(time.time() + remaining)
            os.environ["WECHAT_GENERATION_DEADLINE_EPOCH"] = str(time.time() + remaining - SUBMISSION_MIN_SECONDS)
            os.environ["TOUTIAO_DAILY_RUN_BUDGET_SECONDS"] = str(min(1980, max(1, remaining - SUBMISSION_MIN_SECONDS)))
        command = [sys.executable, str(ROOT / "scripts" / "scheduled_wechat_publish.py"), "--account", args.account]
        checked = subprocess.run(command + ["--check-login"], cwd=ROOT, timeout=120)
        if checked.returncode:
            return finish({"status": "publish_login_required", "exit_code": checked.returncode,
                           "native_exit_code": checked.returncode}, checked.returncode)
        if not fixed_device:
            save_session(settings, encrypted, key)
        if args.mode == "check":
            report = {"status": "ready", "publication_requested": False}
            storage = settings.account_runtime_dir / "automation" / "browser_state" / "WeChat_storage_state.json"
            if storage.exists():
                from toutiao_content_factory.services.wechat_publication_verifier import inspect_backend
                report["backend_snapshot"] = inspect_backend(settings)
            return finish(report, 0)
        started = time.time_ns()
        workflow_started = started
        import uuid
        from toutiao_content_factory.services.wechat_execution import run_process_tree, write_phase
        os.environ["WECHAT_EXECUTION_ID"] = run_key or uuid.uuid4().hex
        write_phase(settings, "preparing")
        publication_started = True
        deadline = float(os.environ.get("WECHAT_RUN_DEADLINE_EPOCH", time.time() + 2700))
        result = run_process_tree(command + (["--save-draft"] if args.mode == "draft" else ["--enable-auto-publish"]), cwd=ROOT, timeout=max(1, min(2700, deadline - time.time())))
        workflow_completed = True
        if not fixed_device:
            save_session(settings, encrypted, key)
        record = latest_accepted_record(settings.output_dir / settings.wechat.history_filename, today) if args.mode != "draft" else None
        if record:
            record_publication(settings, record, today)
        report = fresh_json(settings.automation_dir / "wechat_scheduled_result.json", started)
        if not report:
            raise RuntimeError("Missing current WeChat run report.")
        if args.mode == "draft":
            delivery = report.get("draft_delivery", {})
            saved = (report.get("wechat_status") == "draft_saved" and delivery.get("status") == "saved"
                     and bool(delivery.get("draft_media_id") or delivery.get("app_msg_id")))
            if not saved:
                from toutiao_content_factory.services.wechat_execution import draft_delivery_state
                report["draft_delivery"] = {"status": draft_delivery_state(settings, started)}
                if report.get("wechat_status") == "draft_saved":
                    report.update(status="error", error_type="MissingDraftEvidence")
                    report.pop("wechat_status", None)
                if report["draft_delivery"]["status"] == "uncertain":
                    report["status"] = "error"
            return finish(report, 1 if report.get("draft_delivery", {}).get("status") == "uncertain"
                          else cloud_exit_code(report, result.returncode))
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
        if args.mode == "draft":
            from toutiao_content_factory.services.wechat_execution import draft_delivery_state
            return finish({"status": "error", "error_type": type(exc).__name__,
                "draft_delivery": {"status": draft_delivery_state(settings, workflow_started)}}, 1)
        from toutiao_content_factory.services.wechat_execution import phase_attempted
        not_submitted = (publication_started and workflow_started is not None
                         and (workflow_completed or isinstance(exc, subprocess.TimeoutExpired))
                         and phase_attempted(settings, workflow_started) is False)
        if publication_started and not record and not not_submitted:
            try:
                record_publication(settings, {"status": "publish_uncertain"}, today)
            except Exception as guard_exc:
                logger.error("Cannot persist uncertain publication guard: %s", type(guard_exc).__name__)
        error_report = {"status": "error", "error_type": type(exc).__name__}
        if record:
            error_report.update(wechat_status=record["status"], publication_attempted=True,
                                expected_publication=record.get("expected_publication", {}),
                                verification_error=type(exc).__name__)
        elif not_submitted:
            error_report.update(publication_attempted=False, reason_codes=["generation_failed_before_submission"])
        elif publication_started:
            error_report.update(wechat_status="publish_uncertain", publication_attempted=True)
        return finish(error_report, 1)


if __name__ == "__main__":
    raise SystemExit(main())
