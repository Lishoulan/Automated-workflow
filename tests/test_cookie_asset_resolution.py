"""测试：云端的 cookie 资产名必须按账号区分。

背景（2026-09-18）：云端注入 cookie 的步骤从 Release 下载**固定名字**的
`exported_cookies.json`，再注入给当次运行的账号。单账号时没问题，但第二个头条
账号一旦上线，两次运行会共享同一个 cookie 文件 —— 把 A 账号的登录态注进 B 账号，
发布必然失败，还可能触发平台风控。

这里同时锁住两件事：
1. 候选名顺序正确（账号专属在前，历史遗留兜底在后）；
2. `scripts/resolve_cookie_asset.py` 的 slug 规则与
   `toutiao_content_factory.config.sanitize_account_slug` **逐字一致**。

第 2 点是真正的风险所在：脚本为了不依赖 src 包而自带了一份正则，
两处规则一旦漂移，资产名就会对不上，云端会静默回退到 legacy 资产，
再次把别的账号 cookie 注进来 —— 而且不会有任何报错。
"""

from __future__ import annotations

import pytest

from scripts.resolve_cookie_asset import (
    LEGACY_ASSET_NAME,
    account_asset_name,
    build_parser,
    resolve_asset_name,
    sanitize_slug,
)
from toutiao_content_factory.config import sanitize_account_slug

ACCOUNT_KEYS = [
    "default",
    "robot_notes",
    "story_notes",
    "politics_notes",
    "sports_notes",
    "hardware_notes",
    "automation_notes",
]


@pytest.mark.parametrize("account_key", ACCOUNT_KEYS)
def test_slug_rule_matches_the_config_implementation(account_key: str) -> None:
    """脚本自带的 slug 规则必须与 config 的完全一致，否则资产名会对不上。"""
    assert sanitize_slug(account_key) == sanitize_account_slug(account_key)


def test_account_asset_is_tried_before_the_legacy_asset() -> None:
    candidates = resolve_asset_name("story_notes")

    assert candidates[0] == "exported_cookies_story-notes.json"
    assert candidates[-1] == LEGACY_ASSET_NAME


def test_two_accounts_never_resolve_to_the_same_primary_asset() -> None:
    """这是整个改动要防的事：两个账号不能拿到同一个 cookie 文件。"""
    primary = {resolve_asset_name(key)[0] for key in ACCOUNT_KEYS}

    assert len(primary) == len(ACCOUNT_KEYS)


def test_legacy_fallback_is_kept_for_the_existing_account() -> None:
    """robot_notes 现在依赖 legacy 资产，这个兜底不能被删掉。"""
    assert LEGACY_ASSET_NAME in resolve_asset_name("robot_notes")


def test_underscores_become_dashes_not_kept() -> None:
    """下划线会被换成横线 —— 这正是容易写错的一处。"""
    assert account_asset_name("story_notes") == "exported_cookies_story-notes.json"
    assert "story_notes" not in account_asset_name("story_notes")


def test_blank_account_key_falls_back_to_default() -> None:
    assert sanitize_slug("") == "default"
    assert sanitize_slug("   ") == "default"


def test_parser_requires_an_account_and_supports_formats() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])

    args = build_parser().parse_args(["--account", "story_notes", "--format", "primary"])
    assert args.account == "story_notes"
    assert args.format == "primary"


def test_primary_format_prints_only_the_account_asset(capsys) -> None:
    from scripts.resolve_cookie_asset import main

    assert main(["--account", "story_notes", "--format", "primary"]) == 0
    assert capsys.readouterr().out.strip() == "exported_cookies_story-notes.json"


def test_list_format_prints_every_candidate(capsys) -> None:
    from scripts.resolve_cookie_asset import main

    assert main(["--account", "story_notes"]) == 0
    assert capsys.readouterr().out.split() == [
        "exported_cookies_story-notes.json",
        LEGACY_ASSET_NAME,
    ]
