"""云端 cookie 资产的按账号解析。

背景（2026-09-18）：云端 `daily-run.yml` 的「Inject cross-platform cookies」步骤
从 Release `browser-profiles-v1` 下载**固定名字**的 `exported_cookies.json`，然后
注入给当次运行的账号。这在单账号时没问题，但一旦有第二个头条账号，
两次运行会拿到同一个 cookie 文件 —— 等于把 A 账号的登录态注进 B 账号，
轻则发布失败，重则可能触发平台风控。

本模块只负责「该找哪个资产名」这件事，把命名规则从 YAML 里抽出来，
这样它可以被测试覆盖（YAML 里的 shell 逻辑没法单测）。

命名规则（先精确、后兜底）：

1. `exported_cookies_<slug>.json` —— 账号专属资产，例如 `exported_cookies_story-notes.json`
2. `exported_cookies.json`          —— 历史遗留的单账号资产，仅当没有专属资产时使用

`resolve_asset_name()` 会按顺序返回候选名；调用方找到第一个存在的就用它。
第二个候选是**有意保留**的：现有 robot_notes 账号依赖它，
贸然去掉会让线上立即失去登录态。
"""

from __future__ import annotations

import argparse
import json
import re

DEFAULT_RELEASE_TAG = "browser-profiles-v1"
LEGACY_ASSET_NAME = "exported_cookies.json"
DEFAULT_ACCOUNT_KEY = "default"
# ⚠️ 必须与 `src/toutiao_content_factory/config.py` 的 ACCOUNT_SLUG_PATTERN 完全一致。
# 它是 `[^a-zA-Z0-9]+`，也就是**下划线也会被换成横线** —— `story_notes` 的 slug 是
# `story-notes`，不是 `story_notes`。两处规则一旦漂移，资产名就会对不上，
# 云端会静默回退到 legacy 资产并把别的账号 cookie 注进来。
# 本模块刻意不 import src 的包（scripts/ 不一定在 sys.path 上），
# 所以用一条测试锁住两边一致，见 tests/test_cookie_asset_resolution.py。
ACCOUNT_SLUG_PATTERN = re.compile(r"[^a-zA-Z0-9]+")


def sanitize_slug(account_key: str) -> str:
    """把账号 key 规范成 slug，规则与 config.sanitize_account_slug 一致。"""
    normalized = str(account_key or "").strip() or DEFAULT_ACCOUNT_KEY
    sanitized = ACCOUNT_SLUG_PATTERN.sub("-", normalized).strip("-").lower()
    return sanitized or DEFAULT_ACCOUNT_KEY


def account_asset_name(account_key: str) -> str:
    """账号专属的 cookie 资产名。"""
    return f"exported_cookies_{sanitize_slug(account_key)}.json"


def resolve_asset_name(account_key: str) -> list[str]:
    """按优先级返回候选资产名，第一个是账号专属，第二个是历史遗留。"""
    candidates = [account_asset_name(account_key)]
    if candidates[0] != LEGACY_ASSET_NAME:
        candidates.append(LEGACY_ASSET_NAME)
    return candidates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve the per-account cookie asset name for the cloud run.",
    )
    parser.add_argument("--account", required=True, help="Account key, e.g. story_notes")
    parser.add_argument(
        "--format",
        choices=("list", "primary", "json"),
        default="list",
        help="list = one candidate per line; primary = first only; json = JSON array",
    )
    parser.add_argument(
        "--release-tag",
        default=DEFAULT_RELEASE_TAG,
        help="Release tag that holds the cookie assets (informational).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = resolve_asset_name(args.account)
    if args.format == "primary":
        print(candidates[0])
    elif args.format == "json":
        print(json.dumps({"release_tag": args.release_tag, "assets": candidates}))
    else:
        for name in candidates:
            print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
