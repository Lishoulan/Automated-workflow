"""Run-scoped publication evidence and bounded subprocess ownership."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone

import psutil

PREPARATION_MIN_SECONDS = 1200
SUBMISSION_MIN_SECONDS = 600
FINISH_RESERVE_SECONDS = 90


def write_phase(settings, phase, *, fingerprint=""):
    path = settings.automation_dir / "wechat_publish_phase.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"account_key": settings.account_key, "phase": phase,
               "publication_attempted": phase == "submitting",
               "run_id": os.environ.get("WECHAT_EXECUTION_ID", ""),
               "recorded_at": datetime.now(timezone.utc).isoformat(),
               "fingerprint": fingerprint}
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def phase_attempted(settings, started):
    """None means unknown, never evidence that a submit is safe to repeat."""
    path = settings.automation_dir / "wechat_publish_phase.json"
    try:
        if path.stat().st_mtime_ns < started:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        identity = os.environ.get("WECHAT_EXECUTION_ID", "")
        if (not isinstance(value, dict) or value.get("account_key") != settings.account_key
                or (identity and value.get("run_id") != identity)):
            return None
        if value.get("phase") in {"preparing", "saving_draft"} and value.get("publication_attempted") is False:
            return False
        if value.get("phase") == "submitting" and value.get("publication_attempted") is True:
            return True
    except (OSError, ValueError):
        pass
    return None


def draft_delivery_state(settings, started):
    """Only a current preparing phase proves draft saving has not started."""
    if not started:
        return "not_saved"
    path = settings.automation_dir / "wechat_publish_phase.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (path.stat().st_mtime_ns >= started and isinstance(value, dict)
                and value.get("account_key") == settings.account_key
                and value.get("run_id") == os.environ.get("WECHAT_EXECUTION_ID", "")
                and value.get("phase") == "preparing"
                and value.get("publication_attempted") is False):
            return "not_saved"
    except (OSError, ValueError):
        pass
    return "uncertain"


def require_submission_budget():
    deadline = os.environ.get("WECHAT_RUN_DEADLINE_EPOCH")
    # The cloud deadline already excludes the 90-second completion reserve.
    if deadline and float(deadline) - time.time() < SUBMISSION_MIN_SECONDS:
        raise RuntimeError("Insufficient WeChat submission budget; approved manuscript retained.")


def generation_remaining():
    deadlines = [float(os.environ[key]) for key in
                 ("WECHAT_RUN_DEADLINE_EPOCH", "WECHAT_GENERATION_DEADLINE_EPOCH")
                 if os.environ.get(key)]
    return min(deadlines) - time.time() if deadlines else float("inf")


def run_process_tree(command, *, timeout, **kwargs):
    """On timeout stop descendants before returning to publication reconciliation."""
    options = dict(kwargs)
    if os.name != "nt":
        options["start_new_session"] = True
    with subprocess.Popen(command, **options) as process:
        try:
            process.wait(timeout=timeout)
        except BaseException:
            try:
                descendants = psutil.Process(process.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            # Kill each descendant's group too: nested owners may create sessions.
            for child in reversed(descendants):
                if os.name != "nt":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, check=False, timeout=15)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)
            _, alive = psutil.wait_procs(descendants, timeout=5)
            if any(child.is_running() and child.status() != psutil.STATUS_ZOMBIE for child in alive):
                raise RuntimeError("WeChat descendant shutdown could not be confirmed")
            raise
        return subprocess.CompletedProcess(command, process.returncode)
