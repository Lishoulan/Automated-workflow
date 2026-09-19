"""从 CDN 下载头条管理页 JS bundle，grep 出「设为仅我可见」对应的接口。

纯 HTTP。产物落在 .runtime/mpjs/ 。
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".runtime" / "mpjs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

BUNDLES = [
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/index_cb569d31ef189274.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/vendors~5724fad0_f3821931ff752749.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/vendors~5fd074ad_bc8e577aedc75f9d.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/vendors~b6c15f30_b01d258df76c56ad.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/vendors~dde893e5_9a8a69d01a3a355d.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/vendors~ec9014f3_6926e0b1f5e98ca9.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/common_vendors_41457f749c63ac9e.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/utils_vendor_cf67e94e72580382.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/constant_vendor_5f7eb429c2f76560.js",
    "https://lf-content-ecology.toutiaostatic.com/obj/tt-content-ecology-fe/pgcfe/mp/web/resource/bootstrap_79be5c876d1ce9b2.js",
]

HINTS = ("only_me", "onlyMe", "visibility", "set_visible", "article/visible",
         "modify_visible", "private", "status_change", "content/manage")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for url in BUNDLES:
        name = url.rsplit("/", 1)[-1]
        dst = OUT / name
        if not dst.exists():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                dst.write_bytes(data)
                print(f"[dl] {name} {len(data)} bytes")
            except Exception as e:
                print(f"[dl-ERR] {name}: {type(e).__name__} {e}")
                continue
        else:
            print(f"[cached] {name}")

    # 在下载到的 bundle 里找接口路径
    print("\n=== 接口路径命中 ===")
    pat = re.compile(r'["\'`](/mp/agw/[A-Za-z0-9_\-/\.]+)["\'`]')
    hits: dict[str, set[str]] = {}
    for f in sorted(OUT.glob("*.js")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in pat.findall(text):
            hits.setdefault(m, set()).add(f.name)
    for path in sorted(hits):
        if any(h in path.lower() for h in ("visible", "article", "content", "item", "manage", "status")):
            print(f"  {path}   <- {sorted(hits[path])}")
    print(f"\n共 {len(hits)} 个 /mp/agw 路径，全部写入 .runtime/mpjs/_endpoints.txt")
    (OUT / "_endpoints.txt").write_text(
        "\n".join(f"{p}\t{sorted(v)}" for p, v in sorted(hits.items())), encoding="utf-8")

    print("\n=== 关键词上下文（仅我可见 相关） ===")
    for f in sorted(OUT.glob("*.js")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for kw in ("仅我可见", "only_me", "onlyMe", "setVisibility", "visibility"):
            for m in re.finditer(re.escape(kw), text):
                s = max(0, m.start() - 200)
                seg = text[s:m.start() + 220].replace("\n", " ")
                print(f"  [{f.name}] ...{seg}...")
                break  # 每个关键词只打印第一处，避免刷屏
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
