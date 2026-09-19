"""检查 cookie 是否使用 Chrome 新版 app-bound 加密（v20 风格）。

app-bound 密文结构：v10 前缀 + 32 字节加密密钥 + 12B nonce + ct + 16B tag，
其外层用 App-Bound 服务密钥加密（Chrome 127+，Windows）。这与普通 v10
（直接 AES-GCM 主密钥）完全不同，无法用 Local State 主密钥解开。
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / ".runtime" / "accounts" / "robot-notes" / "toutiao_profile" / "Default" / "Network" / "Cookies"

tmp = ROOT / ".runtime" / "_ck3.db"
shutil.copy2(DB, tmp)
try:
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    rows = con.execute("SELECT name, host_key, encrypted_value FROM cookies "
                       "WHERE name IN ('sessionid','sid_guard','odin_tt','ttwid','passport_csrf_token')"
                       ).fetchall()
    con.close()
finally:
    tmp.unlink(missing_ok=True)

for name, host, blob in rows:
    b = bytes(blob)
    prefix = b[:3]
    body = b[3:]
    print(f"{name:<20} prefix={prefix!r} body_len={len(body)}")
    print(f"    head32={body[:32].hex()}")
    print(f"    after32_nonce={body[32:44].hex()}  (若为 app-bound，这里才是 nonce)")
    # 普通 v10：nonce(12) + ct + tag(16) -> body_len = 12 + pt + 16
    print(f"    plain_v10 推测明文长度 = {len(body) - 12 - 16}")
    print(f"    appbound  推测明文长度 = {len(body) - 32 - 12 - 16}")
