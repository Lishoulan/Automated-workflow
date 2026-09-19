"""从 runtime/bootstrap 里提取 chunk 映射，找出内容管理页的异步 chunk 名。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".runtime" / "mpjs"


def main() -> int:
    for name in ("runtime~index_ae8de1f65ee5fd5b.js", "bootstrap_79be5c876d1ce9b2.js",
                 "wowpack-set-module.js", "__wowpackAmdLoader_0.0.29__.js"):
        f = OUT / name
        if not f.exists():
            print(f"[missing] {name}")
            continue
        t = f.read_text(encoding="utf-8", errors="replace")
        print(f"\n===== {name} (len={len(t)}) =====")
        # 形如 {123:"abc123def456"} 的 chunk 哈希表
        for m in re.finditer(r'\{[0-9]{2,5}:"[0-9a-f]{8,}"(?:,[0-9]{2,5}:"[0-9a-f]{8,}")+\}', t):
            print("  [hashmap]", m.group(0)[:400])
        # 目录/文件名模板
        for kw in ("resource/", ".js", "miniCssF", "u=function", "p="):
            idx = t.find(kw)
            if idx >= 0:
                print(f"  [{kw}] ...{t[max(0,idx-140):idx+160]}...".replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
