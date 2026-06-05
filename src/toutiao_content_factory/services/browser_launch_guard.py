from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - optional dependency at runtime
    psutil = None


LOCK_FILENAMES = (
    "lockfile",
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
)
TARGET_PROCESS_NAMES = {"python.exe", "msedge.exe", "python", "chromium", "chromium-browser", "chrome", "google-chrome"}
PROCESS_WHITELIST_MARKERS = (
    "webapp.py",
    "uvicorn",
    "fastapi",
    "flask",
    "gunicorn",
    "django",
    "streamlit",
    "gradio",
)


def prepare_browser_profile(
    *,
    profile_dir: Path,
    project_root: Path,
    logger: logging.Logger,
    label: str,
    extra_paths: list[Path] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    resolved_profile_dir = profile_dir.resolve(strict=False)
    resolved_project_root = project_root.resolve(strict=False)
    tracked_paths = [resolved_project_root, resolved_profile_dir]
    tracked_paths.extend(path.resolve(strict=False) for path in extra_paths or [])

    matched_processes = _find_related_processes(tracked_paths)
    cleanup_result = _stop_processes(matched_processes, timeout_seconds=timeout_seconds, logger=logger)
    deleted_locks = _delete_stale_locks(resolved_profile_dir)

    summary = {
        "label": label,
        "profile_dir": str(resolved_profile_dir),
        "project_root": str(resolved_project_root),
        "matched_processes": matched_processes,
        "terminated_processes": cleanup_result["terminated_processes"],
        "killed_processes": cleanup_result["killed_processes"],
        "deleted_lock_files": deleted_locks,
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "psutil_available": psutil is not None,
    }
    logger.info("Playwright startup guard summary: %s", json.dumps(summary, ensure_ascii=False))
    return summary


def _find_related_processes(tracked_paths: list[Path]) -> list[dict[str, Any]]:
    if psutil is None:
        return []

    current_pid = os.getpid()
    protected_pids = {current_pid}
    try:
        proc = psutil.Process(current_pid)
        for parent in proc.parents():
            protected_pids.add(parent.pid)
    except (psutil.Error, OSError):
        pass

    normalized_paths = [str(path).lower() for path in tracked_paths if str(path).strip()]
    candidates: dict[int, dict[str, Any]] = {}
    direct_matches: set[int] = set()

    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "cwd"]):
        try:
            pid = proc.info.get("pid") or proc.pid
            if pid in protected_pids:
                continue
            name = str(proc.info.get("name") or "").strip().lower()
            if name not in TARGET_PROCESS_NAMES:
                continue
            cmdline = [str(item or "").strip() for item in proc.info.get("cmdline") or [] if str(item or "").strip()]
            cwd = str(proc.info.get("cwd") or "").strip()
            payload = {
                "pid": int(proc.info["pid"]),
                "ppid": int(proc.info.get("ppid") or 0),
                "name": name,
                "cmdline": cmdline,
                "cwd": cwd,
            }
            candidates[payload["pid"]] = payload
            haystacks = [cwd, *cmdline]
            if any(_contains_tracked_path(item, normalized_paths) for item in haystacks):
                direct_matches.add(payload["pid"])
        except (psutil.Error, OSError, ValueError, TypeError):
            continue

    related = set(direct_matches)
    changed = True
    while changed:
        changed = False
        for pid, payload in candidates.items():
            if pid in related:
                continue
            if payload["ppid"] in related:
                related.add(pid)
                changed = True
                continue
            if any(child["ppid"] == pid and child_pid in related for child_pid, child in candidates.items()):
                related.add(pid)
                changed = True

    return [candidates[pid] for pid in sorted(related)]


def _contains_tracked_path(raw_value: str, normalized_paths: list[str]) -> bool:
    candidate = str(raw_value or "").strip().lower()
    if not candidate:
        return False
    return any(path in candidate for path in normalized_paths)


def _stop_processes(
    matched_processes: list[dict[str, Any]],
    *,
    timeout_seconds: float,
    logger: logging.Logger,
) -> dict[str, list[int]]:
    if psutil is None or not matched_processes:
        return {"terminated_processes": [], "killed_processes": []}

    filtered_processes = _filter_whitelisted_processes(matched_processes, logger=logger)

    process_handles: list[Any] = []
    for item in filtered_processes:
        try:
            process_handles.append(psutil.Process(int(item["pid"])))
        except (psutil.Error, OSError, ValueError):
            continue

    terminated: list[int] = []
    killed: list[int] = []

    for proc in process_handles:
        try:
            proc.terminate()
            terminated.append(proc.pid)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            logger.debug("Failed to terminate pid=%s: %s", proc.pid, exc)

    _, alive = psutil.wait_procs(process_handles, timeout=timeout_seconds)
    for proc in alive:
        try:
            proc.kill()
            killed.append(proc.pid)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            logger.debug("Failed to kill pid=%s: %s", proc.pid, exc)

    if alive:
        psutil.wait_procs(alive, timeout=max(1.0, timeout_seconds / 2))

    return {"terminated_processes": terminated, "killed_processes": killed}


def _filter_whitelisted_processes(
    processes: list[dict[str, Any]],
    *,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for proc in processes:
        name = str(proc.get("name", "")).strip().lower()
        cmdline = proc.get("cmdline", [])
        if isinstance(cmdline, list):
            cmdline_str = " ".join(str(item) for item in cmdline).lower()
        else:
            cmdline_str = str(cmdline).lower()

        python_names = {"python.exe", "python", "python3"}
        if name in python_names and any(marker in cmdline_str for marker in PROCESS_WHITELIST_MARKERS):
            logger.info(
                "Browser launch guard: skipping whitelisted web service process pid=%s cmdline=%s",
                proc.get("pid"),
                cmdline_str[:120],
            )
            continue

        kept.append(proc)
    return kept


def _delete_stale_locks(profile_dir: Path) -> list[str]:
    deleted: list[str] = []
    for filename in LOCK_FILENAMES:
        target = profile_dir / filename
        try:
            if target.exists():
                target.unlink()
                deleted.append(str(target))
        except OSError:
            continue
    return deleted


def check_cookie_health(profile_dir: Path, *, max_age_hours: float = 72.0) -> dict[str, Any]:
    resolved = profile_dir.resolve(strict=False)
    if not resolved.exists():
        return {
            "healthy": False,
            "reason": "profile_dir_not_found",
            "profile_dir": str(resolved),
        }

    cookie_files = (
        list(resolved.glob("Cookies")) + list(resolved.glob("Cookies-journal"))
        + list(resolved.glob("Default/Network/Cookies")) + list(resolved.glob("Default/Network/Cookies-journal"))
    )
    if not cookie_files:
        return {
            "healthy": False,
            "reason": "no_cookie_files",
            "profile_dir": str(resolved),
        }

    newest_mtime = max(f.stat().st_mtime for f in cookie_files if f.exists())
    age_hours = (time.time() - newest_mtime) / 3600.0

    if age_hours > max_age_hours:
        return {
            "healthy": False,
            "reason": "cookie_stale",
            "age_hours": round(age_hours, 1),
            "max_age_hours": max_age_hours,
            "profile_dir": str(resolved),
        }

    local_storage = resolved / "Local Storage" / "leveldb"
    has_local_storage = local_storage.exists() and any(local_storage.iterdir())

    return {
        "healthy": True,
        "reason": "ok",
        "age_hours": round(age_hours, 1),
        "cookie_files": [f.name for f in cookie_files],
        "has_local_storage": has_local_storage,
        "profile_dir": str(resolved),
    }
