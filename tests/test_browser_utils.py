from __future__ import annotations

from toutiao_content_factory.utils.browser_utils import (
    build_account_fingerprint,
    build_persistent_context_options,
)


def test_build_account_fingerprint_is_stable_per_account() -> None:
    first = build_account_fingerprint(account_key="account-a", profile_dir=".runtime/a")
    second = build_account_fingerprint(account_key="account-a", profile_dir=".runtime/a")
    other = build_account_fingerprint(account_key="account-b", profile_dir=".runtime/b")

    assert first == second
    assert first.user_agent != ""
    assert first.viewport["width"] > 0
    assert first != other


def test_build_persistent_context_options_includes_proxy_and_webrtc_flags() -> None:
    options, fingerprint = build_persistent_context_options(
        account_key="account-a",
        profile_dir=".runtime/a",
        proxy_server="http://127.0.0.1:7890",
        headless=True,
    )

    assert options["proxy"]["server"] == "http://127.0.0.1:7890"
    assert "--enable-automation" in options["ignore_default_args"]
    assert any("webrtc-ip-handling-policy" in arg for arg in options["args"])
    assert options["user_agent"] == fingerprint.user_agent
