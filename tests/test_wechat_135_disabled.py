from __future__ import annotations

import logging
from pathlib import Path

import pytest

from toutiao_content_factory.config import WechatSettings, load_settings
from toutiao_content_factory.services.wechat_135editor_service import Editor135Service
from toutiao_content_factory.services.wechat_service import (
    WechatPublishService,
)


def test_legacy_wechat_config_cannot_enable_135() -> None:
    settings = WechatSettings.from_dict({"editor_135_enabled": True})

    assert settings.editor_135_enabled is False


def test_legacy_environment_cannot_enable_135(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_EDITOR_135_ENABLED", "1")

    settings = load_settings(Path.cwd(), account_key="default")

    assert settings.wechat.editor_135_enabled is False


def test_wechat_service_rejects_legacy_135_routes(app_settings) -> None:
    service = WechatPublishService(app_settings, logging.getLogger("test_wechat_135_disabled"))

    with pytest.raises(RuntimeError, match="135 Editor publishing is disabled"):
        service.publish_run_via_135(Path("missing-run"))

    with pytest.raises(RuntimeError, match="135 Editor publishing is disabled"):
        service.publish_run(Path("missing-run"), force_editor_135=True)

    with pytest.raises(RuntimeError, match="135 Editor publishing is disabled"):
        service.initialize_135editor_login_session()


def test_editor_service_rejects_before_playwright(app_settings) -> None:
    service = Editor135Service(
        app_settings,
        logging.getLogger("test_editor_135_disabled"),
    )

    with pytest.raises(RuntimeError, match="135 Editor integration is disabled"):
        service.publish_via_135editor(title="title", article_html="<p>body</p>")


def test_scheduler_entries_hard_disable_135() -> None:
    workflow = Path(".github/workflows/daily-run.yml").read_text(encoding="utf-8")
    scheduled_runner = Path("scripts/scheduled_task_runner.ps1").read_text(encoding="utf-8")
    standalone = Path("scripts/standalone_pipeline.py").read_text(encoding="utf-8")

    assert "CLOUD_WECHAT_135_ENABLED" not in workflow
    assert 'WECHAT_EDITOR_135_ENABLED: "0"' in workflow
    assert '$env:WECHAT_EDITOR_135_ENABLED = "0"' in scheduled_runner
    assert 'os.environ["WECHAT_EDITOR_135_ENABLED"] = "0"' in standalone


def test_mcp_135_requests_are_rejected() -> None:
    from scripts.mcp_browser_publish import (
        generate_login_check_steps,
        generate_mcp_publish_steps,
    )

    assert "disabled" in generate_login_check_steps("135editor")[0]["error"]
    assert "disabled" in generate_mcp_publish_steps(
        "135editor",
        title="title",
        content_html="<p>body</p>",
    )[0]["error"]
