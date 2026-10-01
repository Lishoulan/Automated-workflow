from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace

from toutiao_content_factory.services.app_service import ContentFactoryAppService


def _stub(app_settings, *, enabled: bool):
    publishing = dataclasses.replace(
        app_settings.publishing,
        enabled=True,
        auto_publish=True,
        draft_only=False,
        weitoutiao_enabled=True,
        weitoutiao_standalone_enabled=True,
        weitoutiao_priority_enabled=enabled,
    )
    calls: list[int] = []
    stub = SimpleNamespace(
        settings=dataclasses.replace(app_settings, publishing=publishing),
        logger=logging.getLogger("test.weitoutiao.priority"),
    )

    def top_up(*, max_count: int):
        calls.append(max_count)
        return {
            "standalone_topup_status": "success",
            "standalone_topup_summary": "accepted=1",
        }

    stub._top_up_standalone_weitoutiao = top_up
    return stub, calls


def test_priority_strategy_publishes_one_weitoutiao_before_article(app_settings) -> None:
    stub, calls = _stub(app_settings, enabled=True)

    result = ContentFactoryAppService._publish_priority_weitoutiao(stub, dry_run=False)

    assert calls == [1]
    assert result["standalone_priority_status"] == "success"


def test_priority_strategy_never_publishes_during_dry_run(app_settings) -> None:
    stub, calls = _stub(app_settings, enabled=True)

    result = ContentFactoryAppService._publish_priority_weitoutiao(stub, dry_run=True)

    assert calls == []
    assert result["standalone_priority_status"] in {"preview_ready", "preview_failed", "no_topics"}


def test_priority_strategy_never_publishes_in_draft_only_mode(app_settings) -> None:
    stub, calls = _stub(app_settings, enabled=True)
    object.__setattr__(stub.settings.publishing, "draft_only", True)

    result = ContentFactoryAppService._publish_priority_weitoutiao(stub, dry_run=False)

    assert result == {}
    assert calls == []
