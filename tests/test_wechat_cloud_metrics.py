import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from cryptography.fernet import Fernet

from scripts import collect_wechat_growth as collector
from toutiao_content_factory import config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cloud_metrics(app_settings, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import wechat_cloud_session as sessions

    monkeypatch.setenv("WECHAT_EXECUTION_HOST", "cloud")
    monkeypatch.setenv("WECHAT_FIXED_DEVICE_DIR", "unused-relative-local-path")
    key = Fernet.generate_key().decode()
    seed = sessions.encrypt_state({"cookies": [{"name": "session", "value": "private-cookie",
        "domain": "mp.weixin.qq.com", "path": "/"}], "origins": []}, key, "default").decode()
    monkeypatch.setenv("WECHAT_SESSION_KEY", key)
    monkeypatch.setenv("WECHAT_SESSION_SEED", seed)
    monkeypatch.setattr(config, "load_settings", lambda *a, **kw: app_settings)
    monkeypatch.setattr(collector.xstate, "read_remote_state", lambda *a, **kw: {})
    observed = MagicMock(return_value={"account": "小椰科技", "followers": 69,
        "observed_at": "2026-10-11T09:00:00+08:00", "articles": []})
    monkeypatch.setattr(collector.growth, "collect_home_observation", observed)
    monkeypatch.setattr(sys, "argv", ["collector", "--read-only", "--output", str(tmp_path / "reports")])
    return app_settings, observed, sessions


def test_cloud_metrics_restores_encrypted_login_without_local_device(cloud_metrics):
    settings, observed, sessions = cloud_metrics
    collector.main()
    storage = observed.call_args.args[0]
    assert observed.call_args.kwargs["channel"] is None
    assert storage == settings.account_runtime_dir / "automation/browser_state/WeChat_storage_state.json"
    encrypted = settings.account_runtime_dir / "automation/wechat_cloud_session.enc"
    assert encrypted.is_file() and b"private-cookie" not in encrypted.read_bytes()


def test_invalid_cloud_credentials_cannot_collect_or_fall_back_to_local(cloud_metrics, monkeypatch):
    _settings, observed, _sessions = cloud_metrics
    monkeypatch.setenv("WECHAT_SESSION_SEED", "invalid")
    with pytest.raises(ValueError, match="Cannot decrypt"):
        collector.main()
    observed.assert_not_called()


def test_metrics_account_mismatch_does_not_save_or_overwrite_history(cloud_metrics, monkeypatch):
    _settings, observed, sessions = cloud_metrics
    observed.return_value["account"] = "其他账号"
    save = MagicMock()
    monkeypatch.setattr(sessions, "save_session", save)
    with pytest.raises(ValueError):
        collector.main()
    save.assert_not_called()


def test_cloud_workflows_share_session_cache_and_never_require_self_hosted():
    workflows = [yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for name in ["wechat-daily-run.yml", "wechat-content-metrics.yml"]]
    prefixes = []
    for workflow in workflows:
        job = next(iter(workflow["jobs"].values()))
        assert job["runs-on"] == "ubuntu-latest"
        assert job["env"]["WECHAT_EXECUTION_HOST"] == "cloud"
        assert "WECHAT_FIXED_DEVICE_DIR" not in job["env"]
        assert workflow["concurrency"]["group"] == "wechat-default"
        for step in job["steps"]:
            if "actions/cache" in step.get("uses", ""):
                paths = step["with"]["path"]
                assert "browser_state" not in paths and "wechat_profile" not in paths
                if "wechat_cloud_session.enc" in paths:
                    assert "WECHAT_SESSION_CACHE_VERSION" in step["with"]["key"]
                    if "restore-keys" in step["with"]:
                        prefixes.append(step["with"]["restore-keys"])
    assert len(prefixes) == 2 and prefixes[0] == prefixes[1]
    # 2026-10-21 用户决策 C1：定时运行改为正式发表（publish）。\n    assert workflows[0]["jobs"]["wechat"]["env"]["WECHAT_DELIVERY_MODE"] == "publish"
