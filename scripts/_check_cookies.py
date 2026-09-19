"""读取头条浏览器 cookie，检查是否存在会话类 cookie（登录态的直接证据）。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from toutiao_content_factory.config import load_settings

SESSION_COOKIE_HINTS = (
    "sessionid",
    "session",
    "passport",
    "sid",
    "uid",
    "token",
    "ssid",
    "login",
    "auth",
    "csrftoken",
    "tt_webid",
    "odin_tt",
    "passport_csrf_token",
)


def main() -> int:
    account_key = sys.argv[1] if len(sys.argv) > 1 else "default"
    settings = load_settings(PROJECT_ROOT, account_key=account_key)
    profile_dir = Path(settings.publishing.user_data_dir)
    if not profile_dir.is_absolute():
        profile_dir = PROJECT_ROOT / profile_dir

    cookies_db = profile_dir / "Default" / "Network" / "Cookies"
    print(f"cookies db: {cookies_db} exists={cookies_db.exists()}")
    if not cookies_db.exists():
        print("NO_COOKIES_DB")
        return 1

    # 复制数据库避免锁冲突
    import shutil
    tmp = profile_dir / "Default" / "Network" / "_cookies_copy.db"
    shutil.copy2(cookies_db, tmp)
    try:
        con = sqlite3.connect(str(tmp))
        cur = con.cursor()
        cur.execute("SELECT name, host_key, path, expires_utc, length(encrypted_value) FROM cookies ORDER BY host_key")
        rows = cur.fetchall()
        con.close()
    except Exception as e:
        print(f"READ_FAIL: {e}")
        return 1
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass

    toutiao_cookies = [r for r in rows if "toutiao" in (r[1] or "")]
    session_cookies = [
        r for r in toutiao_cookies
        if any(hint in (r[0] or "").lower() for hint in SESSION_COOKIE_HINTS)
    ]
    print(f"total cookies: {len(rows)}, toutiao cookies: {len(toutiao_cookies)}, session-like: {len(session_cookies)}")
    for r in session_cookies[:15]:
        print(f"  {r[0]} @ {r[1]} (path={r[2]}, exp={r[3]}, len={r[4]})")
    if not session_cookies:
        print("NO_SESSION_COOKIES -> 未登录")
        return 1
    print("HAS_SESSION_COOKIES -> 已登录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
