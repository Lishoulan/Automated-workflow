"""只读检查 Chrome cookie 库里的头条登录态（login/state 探针）。

不启动浏览器、不写任何东西到 profile（复制副本再读，避免锁冲突）。
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".runtime" / "accounts" / "robot-notes" / "toutiao_profile"
DB = PROFILE / "Default" / "Network" / "Cookies"

SESSION_HINTS = ("sessionid", "session", "passport", "sid", "ssid", "uid_tt",
                 "odin_tt", "csrftoken", "csrf", "login", "auth", "token")


def main() -> int:
    print(f"db: {DB}  exists={DB.exists()}")
    if not DB.exists():
        print("NO_COOKIES_DB -> 未登录")
        return 2
    tmp = ROOT / ".runtime" / "_cookies_probe_copy.db"
    shutil.copy2(DB, tmp)
    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT name, host_key, path, expires_utc, is_secure, "
                    "is_httponly, length(encrypted_value) FROM cookies ORDER BY host_key")
        rows = cur.fetchall()
        con.close()
    finally:
        tmp.unlink(missing_ok=True)

    now_chrome = int((time.time() + 11644473600) * 1_000_000)  # Chrome epoch (1601)
    ttoutiao = [r for r in rows if "toutiao" in (r[1] or "") or "bytedance" in (r[1] or "")]
    print(f"total={len(rows)}  toutiao/bytedance={len(ttoutiao)}")

    def exp_str(e: int) -> str:
        if not e:
            return "session-cookie"
        secs = e / 1_000_000 - 11644473600
        left = secs - time.time()
        return f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(secs))} ({left/3600:+.1f}h)"

    live = 0
    for name, host, path, exp, sec, http, elen in ttoutiao:
        expired = bool(exp) and exp < now_chrome
        if not expired:
            live += 1
        mark = "EXPIRED" if expired else "ok"
        print(f"  [{mark:7}] {name:<28} {host:<20} len={elen:<4} exp={exp_str(exp)}")

    key_like = [r for r in ttoutiao
                if any(h in (r[0] or "").lower() for h in SESSION_HINTS)
                and (not r[3] or r[3] >= now_chrome)]
    print(f"\nlive cookies: {live}")
    print(f"live session-like: {len(key_like)} -> "
          f"{[r[0] for r in key_like][:12]}")
    if key_like:
        print("STATE: 已登录（存在未过期的会话类 cookie）")
        return 0
    print("STATE: 未登录或会话已过期")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
