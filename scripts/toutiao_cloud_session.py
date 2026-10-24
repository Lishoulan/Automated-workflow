"""Restore an account-bound cookie Secret without publishing credentials to a Release."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def restore_seed(seed: str, account: str, output: Path) -> None:
    payload = json.loads(seed)
    if not isinstance(payload, dict) or payload.get("account") != account:
        raise ValueError("Account mismatch")
    platform = payload["platforms"]["toutiao"]
    cookies = platform["cookies"]
    if not isinstance(cookies, list) or not cookies:
        raise ValueError("Missing cookies")
    allowed = {"toutiao.com", "snssdk.com", "bytedance.com", "byteimg.com"}
    has_toutiao = False
    for cookie in cookies:
        domain = str(cookie.get("domain", "")).lstrip(".")
        if not any(domain == item or domain.endswith("." + item) for item in allowed):
            raise ValueError("Unrelated cookie domain")
        if (not all(isinstance(cookie.get(key), str) and cookie[key] for key in ("name", "path"))
                or not isinstance(cookie.get("value"), str)):
            raise ValueError("Invalid cookie")
        has_toutiao |= domain == "toutiao.com" or domain.endswith(".toutiao.com")
    if not has_toutiao:
        raise ValueError("Missing Toutiao cookies")
    restored = {"account": account, "exported_at": payload.get("exported_at"),
                "platforms": {"toutiao": {"cookies": cookies, "cookie_count": len(cookies)}}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        os.chmod(output, 0o600)
        json.dump(restored, handle, ensure_ascii=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        restore_seed(os.environ.get("TOUTIAO_COOKIE_SEED", ""), args.account, args.output)
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        print("Invalid account-bound Toutiao session; credentials were not logged.", file=sys.stderr)
        return 2
    print("Account-bound Toutiao session restored from Secret.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
