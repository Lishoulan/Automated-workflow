from __future__ import annotations

import json
import logging
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

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
TARGET_PROCESS_NAMES = {"msedge.exe", "chromium", "chromium-browser", "chrome", "google-chrome"}
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
        + list(resolved.glob("Default/Cookies")) + list(resolved.glob("Default/Cookies-journal"))
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


def backup_browser_storage_state(
    browser_context: Any,
    *,
    platform: str,
    profile_dir: Path,
    account_runtime_dir: Path,
    logger: logging.Logger,
) -> dict[str, Any]:
    backup_root = account_runtime_dir / "automation" / "browser_state"
    backup_root.mkdir(parents=True, exist_ok=True)
    latest_path = backup_root / f"{platform}_storage_state.json"
    timestamp_path = backup_root / f"{platform}_storage_state_{time.strftime('%Y%m%d_%H%M%S')}.json"
    try:
        payload = browser_context.storage_state()
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        latest_path.write_text(serialized, encoding="utf-8")
        timestamp_path.write_text(serialized, encoding="utf-8")
        logger.info(
            "Browser session backup saved for %s: latest=%s snapshot=%s",
            platform,
            latest_path,
            timestamp_path,
        )
        return {
            "saved": True,
            "latest_path": str(latest_path),
            "snapshot_path": str(timestamp_path),
            "cookie_count": len(payload.get("cookies", [])) if isinstance(payload, dict) else 0,
            "profile_dir": str(profile_dir),
        }
    except Exception as exc:
        logger.warning("Failed to back up browser storage state for %s: %s", platform, exc)
        return {
            "saved": False,
            "error": str(exc),
            "profile_dir": str(profile_dir),
        }


def restore_cookies_from_backup(
    browser_context: Any,
    *,
    platform: str,
    profile_dir: Path,
    account_runtime_dir: Path,
    project_root: Path,
    logger: logging.Logger,
) -> dict[str, Any]:
    candidates = _cookie_backup_candidates(
        platform=platform,
        profile_dir=profile_dir,
        account_runtime_dir=account_runtime_dir,
        project_root=project_root,
    )
    for candidate in candidates:
        payload = _load_cookie_backup_payload(candidate, platform=platform, logger=logger)
        cookies = payload.get("cookies", [])
        if not cookies:
            continue
        try:
            with suppress(Exception):
                browser_context.clear_cookies()
            browser_context.add_cookies(cookies)
            logger.info("Restored %d cookies for %s from %s", len(cookies), platform, candidate)
            return {
                "restored": True,
                "source": str(candidate),
                "cookie_count": len(cookies),
                "format": payload.get("format", "unknown"),
            }
        except Exception as exc:
            logger.warning("Cookie restore failed from %s: %s", candidate, exc)
    return {
        "restored": False,
        "source": "",
        "cookie_count": 0,
    }


def ensure_browser_session_ready(
    *,
    browser_context: Any,
    page: Any,
    platform: str,
    profile_dir: Path,
    account_runtime_dir: Path,
    project_root: Path,
    logger: logging.Logger,
    probe_url: str,
    login_detected_fn: Callable[[Any], bool],
    session_ready_fn: Callable[[Any], bool],
    notification_service: Any | None = None,
    navigate_fn: Callable[[Any, str], None] | None = None,
    goto_timeout_ms: int = 30000,
) -> dict[str, Any]:
    summary = {
        "platform": platform,
        "probe_url": probe_url,
        "profile_dir": str(profile_dir),
        "restored": False,
        "cookie_restore_source": "",
        "cookie_restore_count": 0,
        "current_url": "",
        "login_detected": False,
        "session_ready": False,
        "healed": False,
    }
    if probe_url:
        _navigate(page, probe_url, navigate_fn=navigate_fn, timeout_ms=goto_timeout_ms)
        with suppress(Exception):
            page.wait_for_timeout(1200)
    summary.update(_inspect_session_state(page, login_detected_fn=login_detected_fn, session_ready_fn=session_ready_fn))
    if summary["session_ready"] and not summary["login_detected"]:
        backup_browser_storage_state(
            browser_context,
            platform=platform,
            profile_dir=profile_dir,
            account_runtime_dir=account_runtime_dir,
            logger=logger,
        )
        return summary

    restore_result = restore_cookies_from_backup(
        browser_context,
        platform=platform,
        profile_dir=profile_dir,
        account_runtime_dir=account_runtime_dir,
        project_root=project_root,
        logger=logger,
    )
    summary["restored"] = bool(restore_result.get("restored"))
    summary["cookie_restore_source"] = str(restore_result.get("source", "") or "")
    summary["cookie_restore_count"] = int(restore_result.get("cookie_count", 0) or 0)
    if summary["restored"]:
        if probe_url:
            _navigate(page, probe_url, navigate_fn=navigate_fn, timeout_ms=goto_timeout_ms)
            with suppress(Exception):
                page.wait_for_timeout(1500)
        summary.update(_inspect_session_state(page, login_detected_fn=login_detected_fn, session_ready_fn=session_ready_fn))
        if summary["session_ready"] and not summary["login_detected"]:
            summary["healed"] = True
            backup_browser_storage_state(
                browser_context,
                platform=platform,
                profile_dir=profile_dir,
                account_runtime_dir=account_runtime_dir,
                logger=logger,
            )
            return summary

    message = (
        f"{platform} browser session is invalid for account profile {profile_dir}.\n"
        f"Probe URL: {probe_url}\n"
        f"Current URL: {summary.get('current_url', '')}\n"
        f"Cookie restore attempted: {'yes' if summary['restored'] else 'no'}\n"
        f"Restore source: {summary.get('cookie_restore_source', '') or 'none'}\n"
        "Action required: operator should reopen the platform and scan login QR code."
    )
    logger.warning(message)
    if notification_service is not None:
        try:
            notification_service.notify_urgent(f"[URGENT] {platform} session invalid", message)
        except Exception as exc:
            logger.warning("Failed to send %s session notification: %s", platform, exc)
    return summary


def _cookie_backup_candidates(
    *,
    platform: str,
    profile_dir: Path,
    account_runtime_dir: Path,
    project_root: Path,
) -> list[Path]:
    backup_root = account_runtime_dir / "automation" / "browser_state"
    candidates = [
        backup_root / f"{platform}_storage_state.json",
        account_runtime_dir / "automation" / "exported_cookies.json",
        project_root / ".runtime" / "automation" / "exported_cookies.json",
        profile_dir / "storage_state.json",
        profile_dir / "cookie_backup.json",
    ]
    if backup_root.exists():
        candidates.extend(sorted(backup_root.glob(f"{platform}_storage_state_*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
    return [candidate for candidate in candidates if candidate.exists()]


def _load_cookie_backup_payload(path: Path, *, platform: str, logger: logging.Logger) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Skipping unreadable cookie backup %s: %s", path, exc)
        return {}

    if isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        return {
            "cookies": _normalize_cookies(payload.get("cookies", [])),
            "format": "storage_state",
        }
    if isinstance(payload, dict):
        platform_data = payload.get("platforms", {}).get(platform, {})
        if isinstance(platform_data, dict) and isinstance(platform_data.get("cookies"), list):
            return {
                "cookies": _normalize_cookies(platform_data.get("cookies", [])),
                "format": "exported_cookies",
            }
    return {}


def _normalize_cookies(raw_cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_cookies:
        if not isinstance(item, dict):
            continue
        cookie = {
            "name": str(item.get("name", "")).strip(),
            "value": str(item.get("value", "")).strip(),
            "domain": str(item.get("domain", "")).strip(),
            "path": str(item.get("path", "/") or "/").strip(),
        }
        if not cookie["name"] or not cookie["domain"]:
            continue
        expires = item.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = expires
        if item.get("httpOnly"):
            cookie["httpOnly"] = True
        if item.get("secure"):
            cookie["secure"] = True
        same_site = str(item.get("sameSite", "")).strip()
        if same_site in {"Lax", "None", "Strict"}:
            cookie["sameSite"] = same_site
        normalized.append(cookie)
    return normalized


def _inspect_session_state(
    page: Any,
    *,
    login_detected_fn: Callable[[Any], bool],
    session_ready_fn: Callable[[Any], bool],
) -> dict[str, Any]:
    current_url = ""
    with suppress(Exception):
        current_url = str(getattr(page, "url", "") or "")
    login_detected = False
    session_ready = False
    with suppress(Exception):
        login_detected = bool(login_detected_fn(page))
    with suppress(Exception):
        session_ready = bool(session_ready_fn(page))
    return {
        "current_url": current_url,
        "login_detected": login_detected,
        "session_ready": session_ready,
    }


def _default_navigate(page: Any, url: str, *, timeout_ms: int = 30000) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)


def _navigate(page: Any, url: str, *, navigate_fn: Callable[[Any, str], None] | None, timeout_ms: int) -> None:
    if navigate_fn is None:
        _default_navigate(page, url, timeout_ms=timeout_ms)
        return
    try:
        navigate_fn(page, url, timeout_ms=timeout_ms)
    except TypeError:
        navigate_fn(page, url)
