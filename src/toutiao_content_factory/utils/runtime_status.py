from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


HEARTBEAT_TIMEOUT_SECONDS = 30 * 60


def utcnow_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_runtime_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_runtime_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_stale_heartbeat(last_heartbeat: str, *, timeout_seconds: int = HEARTBEAT_TIMEOUT_SECONDS) -> bool:
    if not last_heartbeat:
        return True
    try:
        parsed = datetime.fromisoformat(last_heartbeat)
    except ValueError:
        return True
    return parsed < datetime.now() - timedelta(seconds=max(30, timeout_seconds))


def normalize_runtime_status(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    now = utcnow_iso()
    normalized.setdefault("updated_at", now)
    normalized.setdefault("last_run_time", normalized.get("updated_at", now))
    normalized.setdefault("overall_status", normalized.get("status", "idle"))
    normalized.setdefault("overall_message", normalized.get("message", ""))
    normalized.setdefault("pid", 0)
    normalized.setdefault("last_heartbeat", "")
    platforms = normalized.get("platforms", {})
    if not isinstance(platforms, dict):
        platforms = {}
    normalized_platforms: dict[str, dict[str, str]] = {}
    for key in ("toutiao", "wechat"):
        item = platforms.get(key, {}) if isinstance(platforms.get(key), dict) else {}
        normalized_platforms[key] = {
            "status": str(item.get("status", "idle") or "idle"),
            "last_run": str(item.get("last_run", "") or ""),
            "message": str(item.get("message", "") or ""),
        }
    normalized["platforms"] = normalized_platforms
    return normalized


def refresh_heartbeat(path: Path, *, pid: int | None = None) -> dict[str, Any]:
    payload = normalize_runtime_status(load_runtime_status(path))
    payload["pid"] = int(pid or os.getpid())
    payload["last_heartbeat"] = utcnow_iso()
    payload["updated_at"] = payload["last_heartbeat"]
    save_runtime_status(path, payload)
    return payload


def mark_status_aborted(
    path: Path,
    payload: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    normalized = normalize_runtime_status(payload)
    timestamp = utcnow_iso()
    normalized["status"] = "aborted"
    normalized["overall_status"] = "aborted"
    normalized["message"] = reason
    normalized["overall_message"] = reason
    normalized["updated_at"] = timestamp
    normalized["last_run_time"] = timestamp
    normalized["pid"] = 0
    normalized["last_heartbeat"] = ""
    save_runtime_status(path, normalized)
    return normalized


def recover_stale_running_status(
    path: Path,
    *,
    timeout_seconds: int = HEARTBEAT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = normalize_runtime_status(load_runtime_status(path))
    current_state = str(payload.get("overall_status") or payload.get("status") or "").strip().lower()
    if current_state != "running":
        return payload

    pid = int(payload.get("pid", 0) or 0)
    last_heartbeat = str(payload.get("last_heartbeat", "") or "")
    if not process_exists(pid):
        return mark_status_aborted(path, payload, reason="Scheduled pipeline was marked running, but the process no longer exists.")
    if is_stale_heartbeat(last_heartbeat, timeout_seconds=timeout_seconds):
        return mark_status_aborted(path, payload, reason="Scheduled pipeline heartbeat timed out and was reset automatically.")
    return payload
