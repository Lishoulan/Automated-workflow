from __future__ import annotations

import json
import logging
from pathlib import Path

from toutiao_content_factory.services.browser_launch_guard import (
    ensure_browser_session_ready,
    restore_cookies_from_backup,
)


class _FakeContext:
    def __init__(self) -> None:
        self.cookies: list[dict] = []

    def clear_cookies(self) -> None:
        self.cookies.clear()

    def add_cookies(self, cookies: list[dict]) -> None:
        self.cookies.extend(cookies)

    def storage_state(self) -> dict:
        return {"cookies": list(self.cookies), "origins": []}


class _FakePage:
    def __init__(self, context: _FakeContext) -> None:
        self.context = context
        self.url = ""

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> None:
        self.url = url

    def wait_for_timeout(self, _ms: int) -> None:
        return None


class _FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify_urgent(self, title: str, message: str) -> None:
        self.messages.append((title, message))


def _write_exported_cookies(path: Path, *, platform: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "platforms": {
                    platform: {
                        "cookies": [
                            {
                                "name": "sessionid",
                                "value": "abc",
                                "domain": ".example.com",
                                "path": "/",
                            }
                        ]
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_restore_cookies_from_backup_reads_exported_cookie_file(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    account_runtime_dir = tmp_path / "runtime"
    project_root = tmp_path / "project"
    exported = account_runtime_dir / "automation" / "exported_cookies.json"
    _write_exported_cookies(exported, platform="toutiao")
    context = _FakeContext()

    result = restore_cookies_from_backup(
        context,
        platform="toutiao",
        profile_dir=profile_dir,
        account_runtime_dir=account_runtime_dir,
        project_root=project_root,
        logger=logging.getLogger("test"),
    )

    assert result["restored"] is True
    assert len(context.cookies) == 1
    assert context.cookies[0]["name"] == "sessionid"


def test_ensure_browser_session_ready_restores_cookie_and_heals(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    account_runtime_dir = tmp_path / "runtime"
    project_root = tmp_path / "project"
    exported = account_runtime_dir / "automation" / "exported_cookies.json"
    _write_exported_cookies(exported, platform="wechat")
    context = _FakeContext()
    page = _FakePage(context)
    notifier = _FakeNotifier()

    def login_detected(fake_page: _FakePage) -> bool:
        return not bool(fake_page.context.cookies)

    def session_ready(fake_page: _FakePage) -> bool:
        return bool(fake_page.context.cookies)

    summary = ensure_browser_session_ready(
        browser_context=context,
        page=page,
        platform="wechat",
        profile_dir=profile_dir,
        account_runtime_dir=account_runtime_dir,
        project_root=project_root,
        logger=logging.getLogger("test"),
        probe_url="https://mp.weixin.qq.com",
        login_detected_fn=login_detected,
        session_ready_fn=session_ready,
        notification_service=notifier,
    )

    assert summary["restored"] is True
    assert summary["healed"] is True
    assert notifier.messages == []


def test_ensure_browser_session_ready_notifies_when_restore_fails(tmp_path: Path) -> None:
    context = _FakeContext()
    page = _FakePage(context)
    notifier = _FakeNotifier()

    summary = ensure_browser_session_ready(
        browser_context=context,
        page=page,
        platform="toutiao",
        profile_dir=tmp_path / "profile",
        account_runtime_dir=tmp_path / "runtime",
        project_root=tmp_path / "project",
        logger=logging.getLogger("test"),
        probe_url="https://mp.toutiao.com",
        login_detected_fn=lambda _page: True,
        session_ready_fn=lambda _page: False,
        notification_service=notifier,
    )

    assert summary["session_ready"] is False
    assert notifier.messages
