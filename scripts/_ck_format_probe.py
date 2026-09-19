"""查看 cookie 密文格式（v10/v11/v20 前缀与长度），并检查 Local State 是否含 os_crypt 密钥。

只读，判定用哪种解密路径。
"""
from __future__ import annotations

import json
import sqlite3
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".runtime" / "accounts" / "robot-notes" / "toutiao_profile"
DB = PROFILE / "Default" / "Network" / "Cookies"


def main() -> int:
    tmp = ROOT / ".runtime" / "_ck.db"
    shutil.copy2(DB, tmp)
    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT name, host_key, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%toutiao%' ORDER BY name").fetchall()
        con.close()
    finally:
        tmp.unlink(missing_ok=True)

    from collections import Counter
    prefixes = Counter()
    for name, host, blob in rows:
        p = bytes(blob[:3]) if blob else b""
        prefixes[p] += 1
        if name in ("sessionid", "sid_guard", "odin_tt", "ttwid"):
            print(f"  {name:<16} prefix={p!r} total_len={len(blob)} "
                  f"body_len={max(0, len(blob)-3)}")
    print("prefix histogram:", {k.decode('latin1'): v for k, v in prefixes.items()})

    ls = PROFILE / "Local State"
    print(f"\nLocal State exists={ls.exists()}")
    if ls.exists():
        data = json.loads(ls.read_text(encoding="utf-8", errors="replace"))
        oc = data.get("os_crypt", {})
        print("os_crypt keys:", list(oc))
        for k, v in oc.items():
            if isinstance(v, str):
                print(f"  {k}: len={len(v)} head={v[:12]}")
        info = data.get("os_crypt", {}).get("encrypted_key")
        print("encrypted_key present:", bool(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
