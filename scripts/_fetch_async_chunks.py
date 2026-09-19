"""按 runtime 里的 chunk 映射下载全部异步 chunk，grep 内容管理相关接口。"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".runtime" / "mpjs"
BASE = ("https://lf-content-ecology.toutiaostatic.com/obj/"
        "tt-content-ecology-fe/pgcfe/mp/web/resource/")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


def parse_map(text: str, field: str) -> dict[int, str]:
    m = re.search(r'\{(\d+:"[^"]+"(?:,\d+:"[^"]+")*)\}\[e\]\+"\."\+?', text)
    if not m:
        m = re.search(r'\{(\d+:"[^"]+"(?:,\d+:"[^"]+")*)\}\[e\]', text)
    if not m:
        return {}
    return {int(k): v for k, v in re.findall(r'(\d+):"([^"]+)"', m.group(1))}


def main() -> int:
    rt = (OUT / "runtime_chunk.js").read_text(encoding="utf-8", errors="replace")
    name_map = parse_map(rt, "name")
    hash_map = parse_map(rt, "hash")
    print(f"name_map={len(name_map)} hash_map={len(hash_map)}")
    print("chunk ids:", sorted(name_map)[:60])

    ok = 0
    for cid in sorted(name_map):
        nm = name_map[cid]
        h = hash_map.get(cid)
        if not h:
            continue
        fn = f"async_{cid}_{nm}_{h}.js"
        dst = OUT / fn
        if not dst.exists():
            try:
                req = urllib.request.Request(f"{BASE}{nm}_{h}.js",
                                             headers={"User-Agent": UA})
                dst.write_bytes(urllib.request.urlopen(req, timeout=60).read())
            except Exception as e:
                print(f"  [err] {cid} {nm}: {type(e).__name__}")
                continue
        ok += 1
    print(f"downloaded {ok} async chunks")

    print("\n=== 内容管理相关命中 ===")
    for f in sorted(OUT.glob("async_*.js")):
        t = f.read_text(encoding="utf-8", errors="replace")
        if not any(k in t for k in ("仅我可见", "content/list", "article/list",
                                    "item/list", "media/article")):
            continue
        print(f"\n--- {f.name} (len={len(t)}) ---")
        for kw in ("/mp/agw/", "?status=", "only_me", "visible"):
            for m in list(re.finditer(re.escape(kw), t))[:2]:
                s = max(0, m.start() - 200)
                print(f"   [{kw}] ...{t[s:m.start()+240]}...".replace("\n", " "))
        for m in list(re.finditer("仅我可见", t))[:1]:
            s = max(0, m.start() - 600)
            print("   [仅我可见] ..." + t[s:m.start() + 200].replace("\n", " ") + "...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
