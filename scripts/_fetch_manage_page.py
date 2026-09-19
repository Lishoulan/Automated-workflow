"""抓取头条管理页 HTML 与 JS bundle 列表，定位「设为仅我可见」走的接口。

纯 HTTP，不启动任何浏览器/子进程（本会话沙箱禁止 Chrome 的 mojo IPC）。
用法: python scripts/_fetch_manage_page.py
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://mp.toutiao.com/profile_v4/manage/content/all"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


def main() -> int:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        html = r.read().decode("utf-8", "replace")
    out = ROOT / ".runtime" / "manage_page.html"
    out.write_text(html, encoding="utf-8")
    print(f"html len={len(html)} -> {out.name}")

    js = sorted(set(re.findall(r'src="([^"]+\.js[^"]*)"', html)))
    print(f"js bundles: {len(js)}")
    for u in js[:60]:
        print("  ", u[:160])
    print("--- markers ---")
    for m in ("login", "sessionid", "profile_v4"):
        print(f"  {m}: {m in html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
