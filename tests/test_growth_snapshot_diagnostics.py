"""Growth snapshot failures must say *why* (2026-10-21 production run).

Run 39595846813 (collection-only slot, 2026-10-21 20:00 Beijing) failed with
``Toutiao growth snapshot unavailable: RuntimeError``. The script only printed
the exception *type*, threw away the creator-center response and the cookie
state, and the request was not retried — so a transient HTTP error and an
expired login look identical in the logs, and today's 24h/72h checkpoints were
lost without a diagnosis.

These tests pin:
* the failure message carries the creator-center ``code``/``message`` and the
  cookie state (count + how many are already expired);
* transient HTTP errors are retried once before giving up;
* the runner-level message includes the reason, not just the type.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import URLError

import pytest

from scripts import collect_growth_snapshot as collector


def _cookie_file(tmp_path: Path) -> Path:
    now = int(__import__("time").time())
    payload = {
        "platforms": {
            "toutiao": {
                "cookies": [
                    {"name": "sessionid", "value": "abc", "expires": now + 3600},
                    {"name": "stale", "value": "def", "expires": now - 3600},
                ]
            }
        }
    }
    path = tmp_path / "exported_cookies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _page(contents: list[dict] | None = None, *, code: int = 0, message: str = "") -> dict:
    return {"code": code, "message": message, "contents": contents or [], "has_more": False}


def test_rejected_list_reports_code_message_and_cookie_state(tmp_path, monkeypatch) -> None:
    cookie_path = _cookie_file(tmp_path)

    def fake_urlopen(_request, timeout=30):
        return _Response(_page(code=1001, message="not login"))

    monkeypatch.setattr(collector, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as excinfo:
        collector.fetch_published(
            cookie_path, max_pages=2, cutoff=collector.datetime.now(collector.BEIJING)
        )

    message = str(excinfo.value)
    assert "code=1001" in message
    assert "not login" in message
    assert "cookies=2" in message
    assert "expired=1" in message


def test_transient_http_error_is_retried_once(tmp_path, monkeypatch) -> None:
    cookie_path = _cookie_file(tmp_path)
    calls = {"count": 0}

    def flaky_urlopen(_request, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            raise URLError("temporary TLS failure")
        return _Response(_page(contents=[{"article_attr": {"create_time": 1}}]))

    monkeypatch.setattr(collector, "urlopen", flaky_urlopen)

    contents = collector.fetch_published(
        cookie_path, max_pages=2, cutoff=collector.datetime.now(collector.BEIJING)
    )

    assert calls["count"] == 2
    assert len(contents) == 1


def test_success_path_is_unchanged(tmp_path, monkeypatch) -> None:
    cookie_path = _cookie_file(tmp_path)
    monkeypatch.setattr(
        collector, "urlopen", lambda _request, timeout=30: _Response(_page(contents=[{"a": 1}]))
    )

    assert collector.fetch_published(
        cookie_path, max_pages=2, cutoff=collector.datetime.now(collector.BEIJING)
    ) == [{"a": 1}]


def test_runner_message_keeps_the_reason() -> None:
    described = collector._describe_failure(RuntimeError("Creator center rejected: code=1001"))
    assert "RuntimeError" in described
    assert "code=1001" in described
