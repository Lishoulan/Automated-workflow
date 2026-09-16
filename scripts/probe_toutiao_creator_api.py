"""探测头条创作者后台内部接口：账号权益、收益金额、数据颗粒度。

动机（2026-09-16）：
- 系统里 `performance_report.json` 的收益恒为 `pending_t1` / `unknown`，
  因为 `TOUTIAO_URLS` 只覆盖「作品管理」页，而累计收益在首页 / 收益数据页。
- `publish_service` 的 preflight 抓到 `is_creator=false` / `has_auth=false` /
  `ads_permission=0` 三个标记，但**没人知道它们的真实语义**，一直被当成
  「可能没有分成资格」的 suspICIOUS flag。真正权威的是「创作权益」接口。

本脚本只读，不修改任何数据。两个阶段：
  1. sniff  —— 打开作品管理页 / 收益数据页，记录所有 XHR，用来发现收益走哪个接口；
  2. dump   —— 用浏览器上下文的 cookie 直接调已知的 `/mp/agw/` 接口，打印真实响应体。

用法：
    .venv/Scripts/python.exe scripts/probe_toutiao_creator_api.py [--headless]
产物：
    .runtime/toutiao_creator_probe.json （完整响应，供后续接线时对照字段）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from toutiao_content_factory.config import load_settings  # noqa: E402
from toutiao_content_factory.services.performance_service import (  # noqa: E402
    PerformanceService,
)

SNIFF_URLS = (
    "https://mp.toutiao.com/profile_v4/manage/content/all",
    "https://mp.toutiao.com/profile_v4/income/",
    "https://mp.toutiao.com/profile_v4/manage/data/content",
)

# 已在 2026-09-16 实测返回 200 的接口（带 cookie，零签名）。
KNOWN_APIS = (
    ("user_info", "https://mp.toutiao.com/mp/agw/creator_center/user_info?app_id=1231"),
    (
        "benefit_page",
        "https://mp.toutiao.com/mp/agw/creator_project/get_benefit_page_info?app_id=1231",
    ),
    ("draft_count", "https://mp.toutiao.com/mp/agw/creator_center/draft_count?app_id=1231"),
    ("personal_panel", "https://mp.toutiao.com/mp/agw/deliver/personal_panel?app_id=1231"),
)

# 收益相关关键词：命中即认为该 XHR 与收益有关，重点记录。
MONEY_HINTS = (
    "income",
    "revenue",
    "settle",
    "earn",
    "money",
    "finance",
    "withdraw",
    "bill",
    "statistic",
    "benefit",
)

STATIC_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ico", ".map")


def _is_noise(url: str) -> bool:
    if url.endswith(STATIC_SUFFIXES):
        return True
    return "toutiaostatic" in url or "bytegoofy" in url


def _summarize_json(payload: Any, *, limit: int = 1200) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"...(+{len(text) - limit} chars)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="无头运行（默认有头，便于人工过验证码）")
    parser.add_argument("--account", default="robot_notes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings(ROOT, account_key=args.account)
    logger = logging.getLogger("probe")
    service = PerformanceService(settings, logger)
    profile = service._resolve_toutiao_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {"profile": str(profile), "sniff": [], "apis": {}}

    with sync_playwright() as playwright:
        ctx = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=bool(args.headless),
            channel=settings.publishing.channel or None,
            executable_path=settings.publishing.executable_path or None,
            viewport={"width": 1600, "height": 1000},
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            seen: set[str] = set()

            def on_response(resp) -> None:
                url = resp.url
                if _is_noise(url):
                    return
                key = url.split("?")[0]
                hit = any(h in url.lower() for h in MONEY_HINTS)
                if key in seen and not hit:
                    return
                seen.add(key)
                result["sniff"].append(
                    {"status": resp.status, "method": resp.request.method, "url": url, "money": hit}
                )

            page.on("response", on_response)
            for url in SNIFF_URLS:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(9000)
                    print(f"[sniff] {url} -> 停在 {page.url}")
                except Exception as exc:
                    print(f"[sniff] 访问失败 {url}: {exc}")

            # 用上下文 cookie 直连接口（context.request 自动携带 cookie 存储）
            for name, url in KNOWN_APIS:
                try:
                    resp = ctx.request.get(url, timeout=30000)
                    body: Any
                    try:
                        body = resp.json()
                    except Exception:
                        body = resp.text()[:2000]
                    result["apis"][name] = {"status": resp.status, "url": url, "body": body}
                    print(f"[api] {name}: HTTP {resp.status} {_summarize_json(body, limit=300)}")
                except Exception as exc:
                    result["apis"][name] = {"status": None, "url": url, "error": str(exc)}
                    print(f"[api] {name} 失败: {exc}")
        finally:
            ctx.close()

    out = ROOT / ".runtime" / "toutiao_creator_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    money_calls = [item for item in result["sniff"] if item["money"]]
    print(f"\n=== 疑似收益接口 {len(money_calls)} 个 ===")
    for item in money_calls:
        print(f"  [{item['status']}] {item['method']} {item['url'][:170]}")
    print(f"\n完整产物：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
