"""只读：列出头条号最近的作品及其真实状态（含「仅我可见」）。

复用 `verify_works_visibility.py` 的接口口径：
    GET https://mp.toutiao.com/profile_v4/manage/content/all
    page_num 被服务端忽略，翻页必须用 cursor。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
import httpx

ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = ROOT / ".runtime/accounts/robot-notes/automation/exported_cookies.json"
BASE = "https://mp.toutiao.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
STATUS_DESC = {2: "已发布", 3: "审核未通过", 4: "仅我可见"}


def build_client() -> httpx.Client:
    raw = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    items = raw["platforms"]["toutiao"]["cookies"]
    cookies = {c["name"]: c.get("value", "") for c in items if c.get("name")}
    return httpx.Client(
        base_url=BASE,
        cookies=cookies,
        headers={"User-Agent": UA, "Referer": f"{BASE}/profile_v4/manage/content/all"},
        timeout=30.0,
    )


def fetch(c: httpx.Client, status: int, cursor: str | None = None) -> dict:
    params = {
        "type": 0,
        "status": status,
        "page_num": 1,
        "page_size": 20,
        "need_stat": "false",
        "need_forward": "false",
    }
    if cursor is not None:
        params["cursor"] = cursor
    return c.get(f"{BASE}/mp/agw/creator_center/list/v2", params=params).json()


def main() -> int:
    c = build_client()
    collected: list[dict] = []
    cursor: str | None = None
    for _ in range(20):
        payload = fetch(c, 0, cursor)
        contents = payload.get("contents") or []
        for item in contents:
            attr = item.get("article_attr") or {}
            collected.append(
                {
                    "gid": attr.get("gid"),
                    "title": (attr.get("title") or "").replace("\n", " "),
                    "status": attr.get("status"),
                    "status_desc": attr.get("status_desc"),
                    "article_type": attr.get("article_type"),
                    "create_time": attr.get("create_time") or attr.get("publish_time") or "",
                }
            )
        if not contents or not payload.get("has_more"):
            break
        cursor = payload.get("end_cursor")

    print(f"共枚举 {len(collected)} 条\n")
    print("最近 16 条（按接口顺序）：")
    for it in collected[:16]:
        st = it["status"]
        desc = it["status_desc"] or STATUS_DESC.get(st, st)
        print(f"  [{desc}] gid={str(it['gid']):22s} {str(it['create_time']):12s} {it['title'][:40]}")

    targets = {
        "7687678730147316251": "今天 robot-notes：老人去世，房产过户：兄弟姐妹为何反目",
        "7687304540994978358": "09-20：独居老人摔倒两天",
        "7687264638244831784": "09-19：养老困境",
    }
    print("\n按 gid 查找历史 publish_result 里的 pgc_id：")
    for pgc, label in targets.items():
        hit = [it for it in collected if str(it["gid"]) == pgc]
        if hit:
            it = hit[0]
            desc = it["status_desc"] or STATUS_DESC.get(it["status"], it["status"])
            print(f"  找到 {pgc} → [{desc}] {it['title'][:36]}")
        else:
            print(f"  未找到 {pgc}  ({label})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
