"""PlatformHealthService 基础测试用例。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.services.platform_health_service import PlatformHealthService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_toutiao_service() -> MagicMock:
    svc = MagicMock()
    svc._resolve_profile_dir.return_value = Path("/tmp/toutiao_profile")
    svc.collect_preflight_summary.return_value = {
        "blocking_flags": [],
        "warnings": [],
        "diagnostics": {},
    }
    return svc


@pytest.fixture()
def mock_wechat_service() -> MagicMock:
    svc = MagicMock()
    svc._resolve_browser_profile_dir.return_value = Path("/tmp/wechat_profile")
    svc._get_access_token.return_value = "fake_token"
    return svc


@pytest.fixture()
def mock_notification_service() -> MagicMock:
    svc = MagicMock()
    svc.notify_urgent = MagicMock()
    return svc


@pytest.fixture()
def health_service(
    app_settings: AppSettings,
    mock_toutiao_service: MagicMock,
    mock_wechat_service: MagicMock,
    mock_notification_service: MagicMock,
) -> PlatformHealthService:
    return PlatformHealthService(
        settings=app_settings,
        logger=logging.getLogger("test_health"),
        toutiao_publish_service=mock_toutiao_service,
        wechat_publish_service=mock_wechat_service,
        notification_service=mock_notification_service,
    )


# ---------------------------------------------------------------------------
# quick_health_check 测试
# ---------------------------------------------------------------------------


class TestQuickHealthCheck:
    """测试轻量级健康检查方法。"""

    @patch("toutiao_content_factory.services.platform_health_service.check_cookie_health")
    def test_all_platforms_disabled(self, mock_cookie_health, health_service):
        """当所有平台都禁用时，不应有阻塞平台。"""
        mock_cookie_health.return_value = {"healthy": True}
        # 默认 app_settings 中 publishing.enabled=False, wechat.enabled=False
        result = health_service.quick_health_check()

        assert result["blocking_platforms"] == []
        assert result["blocking_flags"] == []
        for platform_data in result["platforms"].values():
            assert platform_data["status"] == "disabled"

    @patch("toutiao_content_factory.services.platform_health_service.check_cookie_health")
    def test_toutiao_enabled_auto_publish_cookie_expired(self, mock_cookie_health, health_service, app_settings):
        """Toutiao 启用自动发布但 Cookie 过期时，应阻塞。"""
        mock_cookie_health.return_value = {"healthy": False, "reason": "expired", "age_hours": 72}
        # 启用 toutiao 自动发布
        object.__setattr__(app_settings.publishing, "enabled", True)
        object.__setattr__(app_settings.publishing, "auto_publish", True)

        result = health_service.quick_health_check()

        assert "toutiao" in result["blocking_platforms"]
        toutiao = result["platforms"]["toutiao"]
        assert toutiao["status"] == "blocked"
        assert toutiao["required"] is True
        assert len(toutiao["blocking_flags"]) > 0

    @patch("toutiao_content_factory.services.platform_health_service.check_cookie_health")
    def test_toutiao_enabled_cookie_healthy(self, mock_cookie_health, health_service, app_settings):
        """Toutiao 启用且 Cookie 健康时，不应阻塞。"""
        mock_cookie_health.return_value = {"healthy": True}
        object.__setattr__(app_settings.publishing, "enabled", True)
        object.__setattr__(app_settings.publishing, "auto_publish", True)

        result = health_service.quick_health_check()

        assert "toutiao" not in result["blocking_platforms"]
        assert result["platforms"]["toutiao"]["status"] == "ready"

    @patch("toutiao_content_factory.services.platform_health_service.check_cookie_health")
    def test_wechat_enabled_token_ok(self, mock_cookie_health, health_service, app_settings):
        """WeChat 启用且 token 正常时，不应阻塞。"""
        mock_cookie_health.return_value = {"healthy": True}
        object.__setattr__(app_settings.wechat, "enabled", True)
        object.__setattr__(app_settings.wechat, "editor_135_enabled", False)
        object.__setattr__(app_settings.wechat, "app_id", "test-app-id")
        object.__setattr__(app_settings.wechat, "app_secret", "test-app-secret")

        result = health_service.quick_health_check()

        assert "wechat" not in result["blocking_platforms"]
        assert result["platforms"]["wechat"]["status"] == "ready"

    @patch("toutiao_content_factory.services.platform_health_service.check_cookie_health")
    def test_wechat_enabled_token_fail(self, mock_cookie_health, health_service, app_settings, mock_wechat_service):
        """WeChat 启用但 token 获取失败时，应阻塞。"""
        mock_cookie_health.return_value = {"healthy": True}
        mock_wechat_service._get_access_token.side_effect = Exception("app_id invalid")
        object.__setattr__(app_settings.wechat, "enabled", True)
        object.__setattr__(app_settings.wechat, "auto_publish", True)
        object.__setattr__(app_settings.wechat, "editor_135_enabled", False)
        object.__setattr__(app_settings.wechat, "app_id", "test-app-id")
        object.__setattr__(app_settings.wechat, "app_secret", "test-app-secret")

        result = health_service.quick_health_check()

        assert "wechat" in result["blocking_platforms"]
        assert result["platforms"]["wechat"]["status"] == "blocked"

    @patch("toutiao_content_factory.services.platform_health_service.check_cookie_health")
    def test_wechat_token_fail_without_auto_publish_is_warning(
        self, mock_cookie_health, health_service, app_settings, mock_wechat_service
    ):
        """WeChat is enabled for optional/manual use, so token failure must not block the run."""
        mock_cookie_health.return_value = {"healthy": True}
        mock_wechat_service._get_access_token.side_effect = Exception("app_id invalid")
        object.__setattr__(app_settings.wechat, "enabled", True)
        object.__setattr__(app_settings.wechat, "auto_publish", False)
        object.__setattr__(app_settings.wechat, "editor_135_enabled", False)
        object.__setattr__(app_settings.wechat, "app_id", "test-app-id")
        object.__setattr__(app_settings.wechat, "app_secret", "test-app-secret")

        result = health_service.quick_health_check()

        assert "wechat" not in result["blocking_platforms"]
        assert result["platforms"]["wechat"]["status"] == "blocked"
        assert result["platforms"]["wechat"]["required"] is False



# ---------------------------------------------------------------------------
# _unique_texts 测试
# ---------------------------------------------------------------------------


class TestUniqueTexts:
    """测试去重和空值过滤逻辑。"""

    def test_deduplication(self, health_service):
        result = health_service._unique_texts(["a", "b", "a", "c", "b"])
        assert result == ["a", "b", "c"]

    def test_empty_and_whitespace_filtered(self, health_service):
        result = health_service._unique_texts(["a", "", "  ", None, "b"])
        assert result == ["a", "b"]

    def test_empty_list(self, health_service):
        result = health_service._unique_texts([])
        assert result == []


# ---------------------------------------------------------------------------
# _collect_required_auth_failures 测试
# ---------------------------------------------------------------------------


class TestCollectRequiredAuthFailures:
    """测试必需平台认证失败收集逻辑。"""

    def test_no_blocking_platforms(self, health_service):
        platforms = {
            "toutiao": {"enabled": True, "required": True, "blocking_flags": []},
            "wechat": {"enabled": False, "required": False, "blocking_flags": []},
        }
        result = health_service._collect_required_auth_failures(platforms)
        assert result == []

    def test_blocking_required_platform(self, health_service):
        platforms = {
            "toutiao": {"enabled": True, "required": True, "blocking_flags": ["cookie expired"]},
            "wechat": {"enabled": True, "required": True, "blocking_flags": []},
        }
        result = health_service._collect_required_auth_failures(platforms)
        assert result == ["toutiao"]

    def test_blocking_non_required_platform_ignored(self, health_service):
        platforms = {
            "toutiao": {"enabled": True, "required": False, "blocking_flags": ["cookie expired"]},
        }
        result = health_service._collect_required_auth_failures(platforms)
        assert result == []

    def test_disabled_platform_ignored(self, health_service):
        platforms = {
            "toutiao": {"enabled": False, "required": True, "blocking_flags": ["cookie expired"]},
        }
        result = health_service._collect_required_auth_failures(platforms)
        assert result == []


# ---------------------------------------------------------------------------
# _collect_global_blockers 测试
# ---------------------------------------------------------------------------


class TestCollectGlobalBlockers:
    """测试全局阻塞标记收集逻辑。"""

    def test_collects_from_enabled_platforms(self, health_service):
        platforms = {
            "toutiao": {"enabled": True, "blocking_flags": ["cookie expired"]},
            "wechat": {"enabled": True, "blocking_flags": ["token invalid"]},
        }
        result = health_service._collect_global_blockers(platforms)
        assert "toutiao: cookie expired" in result
        assert "wechat: token invalid" in result

    def test_no_blockers(self, health_service):
        platforms = {
            "toutiao": {"enabled": True, "blocking_flags": []},
        }
        result = health_service._collect_global_blockers(platforms)
        assert result == []
