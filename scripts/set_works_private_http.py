"""把头条号作品批量「设为仅我可见」——HTTP 直连版本。

## 为什么会有这个脚本

原方案 `scripts/set_works_private.py`（Playwright + 浏览器）是端到端验证过的，
但在**受限沙箱**里跑不起来：Chromium 需要命名管道（mojo），而沙箱拒绝
`CreateFile`，报 `PermissionError: [WinError 5]`，脚本在 `sync_playwright()`
启动前就崩了。这与脚本本身无关（已独立最小复现：仅 `chromium.launch()` 同样失败）。

本脚本改走**平台自己的 HTTP 接口 + 账号自己的登录 cookie**，做的仍是同一件事
（`POST /mp/agw/creator_center/hide`），因此不依赖浏览器。

## 接口契约（均从 `.runtime/mpjs` 的前端 bundle 读出，并经实机验证）

- 枚举：`GET /mp/agw/creator_center/list/v2`
  - ⚠️ **`page_num` 被服务端忽略**，反复请求只会拿到同一批 20 条；
    真正能翻页的是 `cursor=<上一页返回的 end_cursor>`（实机验证：每页 20 条不重复）。
  - v1 的 `/creator_center/list` 的 `end_cursor`/`start_cursor` **完全不推进**，不能用。
- 隐藏：`POST /mp/agw/creator_center/hide?type=&gid=&cancel=`
  - `type`：`weitoutiao=1`、`article=2`（传 0 或未知值会返回 `1002 无效题材类型`）
  - `cancel` 留空 = 设为仅我可见；`cancel=1` = 解除隐藏（可逆，用于回滚）
  - 成功返回 `{"code":0,"data":{"itemStatus":40}}`

## 判据

每条以**接口返回 `code==0` 且复核该条 `status` 变为 4（仅我可见）**为准，
只有复核通过才计入进度。所有请求间隔 `--min-delay ~ --max-delay` 秒，避免触发风控。

用法：
    python scripts/set_works_private_http.py --dry-run
    python scripts/set_works_private_http.py --limit 40
    python scripts/set_works_private_http.py              # 全量
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
COOKIE_FILE = ROOT / ".runtime" / "accounts" / "robot-notes" / "automation" / "exported_cookies.json"
PROGRESS = ROOT / ".runtime" / "visibility_progress_http.json"
BASE = "https://mp.toutiao.com"
MANAGE_URL = f"{BASE}/profile_v4/manage/content/all"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

#: 前端 bundle 里的题材枚举（All=0 不是合法题材，传了会报 1002）
TYPE_MAP = {"weitoutiao": 1, "pgc": 2, "article": 2, "video": 3,
            "short_video": 4, "wenda": 5, "plog": 10}
STATUS_PRIVATE = 4


def log(msg: str) -> None:
    print(msg, flush=True)


def build_client() -> httpx.Client:
    raw = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    items = raw["platforms"]["toutiao"]["cookies"]
    cookies = {c["name"]: c.get("value", "") for c in items if c.get("name")}
    if "sessionid" not in cookies:
        raise SystemExit("ERROR: cookie 里没有 sessionid，无法登录")
    log(f"已载入 cookie {len(cookies)} 个（含 sessionid）")
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/json", "Referer": MANAGE_URL},
        cookies=cookies, timeout=60, follow_redirects=True)


def check_login(c: httpx.Client) -> bool:
    d = c.get(f"{BASE}/mp/agw/creator_center/user_info").json()
    if d.get("message") == "user not login" or d.get("code") == 100004:
        return False
    return True


def tab_counts(c: httpx.Client) -> dict:
    out = {}
    for st in (0, 2, 3, 4):
        try:
            d = c.get(f"{BASE}/mp/agw/creator_center/list/v2",
                      params={"type": 0, "status": st, "page_num": 1,
                              "page_size": 1}).json()
            out[st] = d.get("total_count")
        except Exception:
            out[st] = None
    return out


def enumerate_all(c: httpx.Client, status: int = 0) -> dict:
    """用 cursor 翻页枚举全部作品。⚠️ page_num 无效，必须用 cursor。"""
    out: dict = {}
    cursor = None
    for _ in range(60):
        params = {"type": 0, "status": status, "page_num": 1, "page_size": 20,
                  "need_stat": "false", "need_forward": "false"}
        if cursor is not None:
            params["cursor"] = cursor
        d = c.get(f"{BASE}/mp/agw/creator_center/list/v2", params=params).json()
        cont = d.get("contents") or []
        for it in cont:
            a = it["article_attr"]
            out[a["gid"]] = {
                "gid": a["gid"],
                "title": (a.get("title") or "").replace("\n", " ")[:60],
                "status": a.get("status"),
                "status_desc": a.get("status_desc"),
                "article_type": a.get("article_type"),
                "show_hide": (a.get("menu_list") or {}).get("show_hide"),
            }
        if not cont or not d.get("has_more"):
            break
        cursor = d.get("end_cursor")
    return out


def hide_one(c: httpx.Client, item: dict) -> tuple[bool, str]:
    """把一条设为仅我可见。返回 (接口是否成功, 说明)。"""
    tval = TYPE_MAP.get(item.get("article_type"))
    if tval is None:
        return False, f"未知题材类型 {item.get('article_type')!r}"
    if not item.get("show_hide"):
        return False, "menu_list.show_hide=false，平台不允许切换可见性"
    try:
        r = c.post(f"{BASE}/mp/agw/creator_center/hide",
                   params={"type": tval, "gid": item["gid"], "cancel": ""}, json={})
        d = r.json()
    except Exception as e:
        return False, f"请求异常 {e}"
    if d.get("code") == 0:
        return True, "ok"
    return False, f"code={d.get('code')} {d.get('message')}"


def verify(c: httpx.Client, gid: int) -> int | None:
    """复核某条当前 status（按 tab 查，避免依赖列表顺序）。"""
    for st in (STATUS_PRIVATE, 2, 3):
        cursor = None
        for _ in range(20):
            params = {"type": 0, "status": st, "page_num": 1, "page_size": 20}
            if cursor is not None:
                params["cursor"] = cursor
            d = c.get(f"{BASE}/mp/agw/creator_center/list/v2", params=params).json()
            for it in d.get("contents") or []:
                if it["article_attr"]["gid"] == gid:
                    return it["article_attr"].get("status")
            if not d.get("has_more"):
                break
            cursor = d.get("end_cursor")
    return None


def load_progress() -> dict:
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_progress(payload: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="HTTP 版：批量把作品设为仅我可见")
    ap.add_argument("--dry-run", action="store_true", help="只列出会处理哪些，不写")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少条（0=不限）")
    ap.add_argument("--title-match", default=None,
                    help="只处理标题匹配该正则的作品（默认全部）")
    ap.add_argument("--min-delay", type=float, default=1.5)
    ap.add_argument("--max-delay", type=float, default=3.0)
    args = ap.parse_args()

    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
        import os
        os.environ.pop(k, None)

    c = build_client()

    if not check_login(c):
        log("ERROR: 未登录（cookie 已失效）")
        return 2

    before = tab_counts(c)
    log(f"平台口径（写前）：全部={before.get(0)} 已发布={before.get(2)} "
        f"未通过={before.get(3)} 仅我可见={before.get(4)}")

    items = enumerate_all(c)
    log(f"枚举到 {len(items)} 条作品")

    private_now = sum(1 for v in items.values() if v["status"] == STATUS_PRIVATE)
    targets = [v for v in items.values()
               if v["status"] != STATUS_PRIVATE and v.get("show_hide")]
    blocked = [v for v in items.values()
               if v["status"] != STATUS_PRIVATE and not v.get("show_hide")]
    targets.sort(key=lambda v: v.get("gid") or 0)

    if args.title_match:
        import re
        pattern = re.compile(args.title_match)
        kept = [t for t in targets if pattern.search(t.get("title") or "")]
        log(f"标题筛选 {args.title_match!r}：命中 {len(kept)}/{len(targets)} 条")
        targets = kept

    log(f"已私有={private_now}  待处理(可隐藏)={len(targets)}  不可隐藏={len(blocked)}")
    for b in blocked:
        log(f"  [跳过] status={b['status']} {b['status_desc']} | {b['title'][:40]}")

    if args.dry_run:
        for t in targets if not args.limit else targets[: args.limit]:
            log(f"  [将处理] type={t['article_type']:10s} gid={t['gid']} | {t['title'][:44]}")
        return 0

    progress = load_progress()
    done = progress.get("done", {})
    done_titles = progress.get("titles", {})

    ok_n = fail_n = 0
    for i, t in enumerate(targets):
        if args.limit and (ok_n + fail_n) >= args.limit:
            log(f"已达本次上限 {args.limit}，停止。")
            break
        key = str(t["gid"])
        if done.get(key) == "private":
            continue
        ok, note = hide_one(c, t)
        done_titles[key] = t["title"]
        if ok:
            done[key] = "private"
            ok_n += 1
        else:
            done[key] = "failed"
            fail_n += 1
        save_progress({"done": done, "titles": done_titles})
        log(f"  [{'OK ' if ok else 'FAIL'}] {t['title'][:40]}  ({note})  "
            f"本次 OK={ok_n} FAIL={fail_n}")
        time.sleep(random.uniform(args.min_delay, args.max_delay))

    after = tab_counts(c)
    log(f"\n平台口径（写后）：全部={after.get(0)} 已发布={after.get(2)} "
        f"未通过={after.get(3)} 仅我可见={after.get(4)}")

    log(f"\n=== 本次汇总 ===\n  新增成功 {ok_n} 条，失败 {fail_n} 条")
    log(f"  已发布 {before.get(2)} -> {after.get(2)}"
        f"    仅我可见 {before.get(4)} -> {after.get(4)}")
    total_private = sum(1 for v in done.values() if v == "private")
    log(f"  累计（进度文件口径）成功 {total_private} 条")
    log(f"  进度文件: {PROGRESS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
