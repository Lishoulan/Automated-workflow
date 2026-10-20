"""Local regression tests: no GitHub calls, real account calls, or publication."""
from __future__ import annotations

import pytest

from scripts import operating_preflight as guard
from toutiao_content_factory.services.wechat_official_publish import (
    publication_status, submission_receipt,
)


def test_unchanged_production_checkout_is_clean(monkeypatch, tmp_path):
    values = iter([(0, "a" * 40), (0, "codex/work"), (0, ""),
                   (0, "a" * 40)])
    monkeypatch.setattr(guard, "_run", lambda args: next(values))
    report = guard.inspect_checkout(tmp_path)
    assert report["status"] == "aligned_clean"
    assert report["aligned"] is True
    assert report["dirty"] is False


def test_different_checkout_is_blocked(monkeypatch, tmp_path):
    values = iter([(0, "b" * 40), (0, "main"), (0, ""), (0, "a" * 40)])
    monkeypatch.setattr(guard, "_run", lambda args: next(values))
    report = guard.inspect_checkout(tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "checkout_head_differs_from_production"


def test_unknown_remote_is_never_assumed_clean(monkeypatch, tmp_path):
    values = iter([(0, "a" * 40), (0, "branch"), (0, ""), (1, "")])
    monkeypatch.setattr(guard, "_run", lambda args: next(values))
    assert guard.inspect_checkout(tmp_path)["status"] == "unverified"


def test_submit_is_pending_not_published():
    receipt = submission_receipt({"errcode": 0, "publish_id": 12345},
                                 draft_media_id="draft-1")
    assert receipt["status"] == "submitted_for_review"
    assert receipt["publication_confirmed"] is False
    assert receipt["publish_url"] == ""


@pytest.mark.parametrize("response", [{}, {"errcode": 48001},
                                       {"publish_id": ""}])
def test_failed_submit_never_claims_success(response):
    with pytest.raises(RuntimeError):
        submission_receipt(response, draft_media_id="draft-1")


def test_status_is_not_public_body_verification():
    p = publication_status({"publish_status": 0,
                            "article_detail": {"item": [{"article_url":
                                "https://mp.weixin.qq.com/s/example"}]}},
                           publish_id="pid")
    assert p["status"] == "platform_reported_published"
    assert p["publication_confirmed"] is False
    assert p["article_urls"] == ["https://mp.weixin.qq.com/s/example"]


@pytest.mark.parametrize("value", [None, "0", 7, -1, True])
def test_unknown_status_fails_closed(value):
    with pytest.raises(ValueError):
        publication_status({"publish_status": value}, publish_id="pid")
