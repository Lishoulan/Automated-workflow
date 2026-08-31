from __future__ import annotations

from scripts.diag_toutiao_login import _classify_page, build_parser


def test_login_diagnostic_classifies_login_url() -> None:
    assert _classify_page("https://sso.toutiao.com/login", "") == "login_required"


def test_login_diagnostic_classifies_login_body() -> None:
    assert _classify_page("https://mp.toutiao.com/", "请扫码登录创作中心") == "login_required"


def test_login_diagnostic_classifies_creator_page() -> None:
    assert _classify_page("https://mp.toutiao.com/profile_v4/graphic/publish", "作品管理") == "ready"


def test_login_diagnostic_keeps_unknown_pages_explicit() -> None:
    assert _classify_page("https://www.toutiao.com/", "") == "unknown"


def test_login_diagnostic_parser_supports_account_and_output() -> None:
    args = build_parser().parse_args(["--account", "robot_notes", "--output", "tmp/diag.json"])
    assert args.account == "robot_notes"
    assert str(args.output).replace("\\", "/") == "tmp/diag.json"
