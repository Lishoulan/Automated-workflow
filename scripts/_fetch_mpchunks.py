"""下载 webpack manifest + 所有 chunk，grep 出「设为仅我可见」的写接口。

背景：主 bundle 里只有状态常量（Hide=4），写接口在异步 chunk 中。
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".runtime" / "mpjs"
BASE = "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    mf = OUT / "manifest.js"
    if not mf.exists():
        mf.write_bytes(get(BASE + "manifest.60b9519eec06e6d397e3a462559a33d2.js"))
    text = mf.read_text(encoding="utf-8", errors="replace")
    names = sorted(set(re.findall(r'"([A-Za-z0-9_~\-]+)":"([0-9a-f]{16})"', text)))
    print(f"manifest chunks: {len(names)}")

    # 下载所有 chunk（限速：文件不大）
    ok = 0
    for name, h in names:
        dst = OUT / f"chunk_{name}_{h}.js"
        if dst.exists():
            ok += 1
            continue
        try:
            dst.write_bytes(get(f"{BASE}{name}_{h}.js"))
            ok += 1
        except Exception as e:
            print(f"  [err] {name}: {type(e).__name__}")
    print(f"downloaded/cached: {ok}/{len(names)}")

    # grep 写接口
    print("\n=== 命中「仅我可见」/可见性写接口的 chunk ===")
    for f in sorted(OUT.glob("chunk_*.js")):
        t = f.read_text(encoding="utf-8", errors="replace")
        if "仅我可见" in t or "only_me" in t or "set_visible" in t or "visible_type" in t:
            print(f"\n--- {f.name} (len={len(t)}) ---")
            for m in re.finditer(r"仅我可见", t):
                s = max(0, m.start() - 500)
                print("   ..." + t[s:m.start() + 300].replace("\n", " ") + "...")
                break
            for kw in ("/mp/agw/", "only_me", "visible"):
                for m in re.finditer(re.escape(kw), t):
                    s = max(0, m.start() - 250)
                    print(f"   [{kw}] ..." + t[s:m.start() + 250].replace("\n", " ") + "...")
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
