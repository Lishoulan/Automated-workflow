"""Transfer account-bound WeChat sessions without caching plaintext credentials."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise ValueError("Invalid WeChat session structure.")
    cookies = [
        cookie for cookie in state["cookies"]
        if isinstance(cookie, dict)
        and (str(cookie.get("domain", "")).lstrip(".") in {"qq.com", "weixin.qq.com", "mp.weixin.qq.com"})
    ]
    if not any(str(cookie.get("domain", "")).lstrip(".") == "mp.weixin.qq.com" for cookie in cookies):
        raise ValueError("Session does not contain WeChat backend cookies.")
    origins = [
        origin for origin in state.get("origins", [])
        if isinstance(origin, dict) and origin.get("origin") == "https://mp.weixin.qq.com"
    ]
    return {"cookies": cookies, "origins": origins}


def encrypt_state(state: dict[str, Any], key: str, account: str) -> bytes:
    payload = {"schema": 1, "account": account, "storage_state": validate_state(state)}
    return Fernet(key.encode()).encrypt(json.dumps(payload, ensure_ascii=False).encode())


def decrypt_state(encrypted: bytes, key: str, account: str) -> dict[str, Any]:
    try:
        payload = json.loads(Fernet(key.encode()).decrypt(encrypted))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Cannot decrypt the WeChat session; credentials are not logged.") from exc
    if payload.get("schema") != 1 or payload.get("account") != account:
        raise ValueError("WeChat session belongs to a different account or schema.")
    return validate_state(payload.get("storage_state"))


def restore_session(settings: Any, encrypted_path: Path, key: str, seed: str) -> Path:
    # 缓存失效时才用初始种子，避免每次把新登录状态覆盖成旧状态。
    # Prefer refreshed encrypted state over the initial seed.
    encrypted = encrypted_path.read_bytes() if encrypted_path.exists() else seed.encode()
    if not encrypted:
        raise ValueError("WeChat cloud session is not configured.")
    state = decrypt_state(encrypted, key, settings.account_key)
    target = settings.account_runtime_dir / "automation" / "browser_state" / "WeChat_storage_state.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.chmod(target, 0o600)
    from toutiao_content_factory.services.wechat_service import WechatPublishService

    WechatPublishService(settings, logging.getLogger(__name__))._resolve_browser_profile_dir().mkdir(parents=True, exist_ok=True)
    return target


def save_session(settings: Any, encrypted_path: Path, key: str) -> None:
    source = settings.account_runtime_dir / "automation" / "browser_state" / "WeChat_storage_state.json"
    state = json.loads(source.read_text(encoding="utf-8"))
    encrypted = encrypt_state(state, key, settings.account_key)
    encrypted_path.parent.mkdir(parents=True, exist_ok=True)
    encrypted_path.write_bytes(encrypted)
