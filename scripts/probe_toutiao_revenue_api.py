"""Phase 2B：头条「单篇内容收益」专项探测（只读，不污染生产流程）。

为什么单独一个脚本（2026-09-17 Phase 2）：
- `probe_toutiao_creator_api.py` 解决的是**账号级**收益（权益、逐日、分类型）。
- 本阶段要回答的是**「单篇内容能不能查到收益」**（Q3）。这不是猜出来的，
  必须两路取证：
    1) 作品管理页的行文本里到底有没有「收益」列
       —— 生产代码 `PerformanceService._extract_platform_revenue()` 就是靠正则在这段文本里
       找「收益/创作收益/预计收益/平台收益」。列表里没这个字段 ⇒ 单篇收益永远 None
       ⇒ `revenue_status=unknown`。这是 Q4 的第一嫌疑点，必须实测。
    2) 收益数据页 / 单篇数据页到底请求了哪些 XHR，有没有按 content_id 的收益接口。

本脚本只读：不发布、不修改、不写任何平台数据。
产物：`.runtime/toutiao_revenue_probe.json`

用法：
    .venv/Scripts/python.exe scripts/probe_toutiao_revenue_api.py [--headless] [--account robot_notes]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from toutiao_content_factory.config import load_settings  # noqa: E402
from toutiao_content_factory.services.performance_service import (  # noqa: E402
    PerformanceService,
)

CONTENT_ALL_URL = "https://mp.toutiao.com/profile_v4/manage/content/all"
INCOME_OVERVIEW_URL = "https://mp.toutiao.com/profile_v4/analysis/income-overview"
INCOME_LEGACY_URL = "https://mp.toutiao.com/profile_v4/income/"

# 候选「单篇收益」接口：命名是**按头条接口规律推测的**，不是已证实存在的。
# 探测的价值在于：404/405 能证明「这条路走不通」，200 才证明存在。
# 结论一律以实测状态码为准，绝不把"没试过"写成"不存在"。
CANDIDATE_PER_ITEM_APIS = (
    (
        "income_article_detail",
        "https://mp.toutiao.com/mp/agw/income/article/income_detail?item_id={item_id}&app_id=1231",
    ),
    (
        "income_content_detail",
        "https://mp.toutiao.com/mp/agw/income/overview/income/article_details?item_id={item_id}&app_id=1231",
    ),
    (
        "statistic_article_data",
        "https://mp.toutiao.com/mp/agw/statistic/v2/get_article_data?item_id={item_id}&app_id=1231",
    ),
    (
        "content_income_list",
        "https://mp.toutiao.com/mp/agw/income/overview/content/income/list?page=1&size=20&app_id=1231",
    ),
)

KNOWN_INCOME_APIS = (
    (
        "income_overview",
        "https://mp.toutiao.com/mp/agw/income/overview/income/details"
        "?start_date={start}&end_date={end}&only_mid_income=0&app_id=1231",
    ),
    (
        "income_abstract",
        "https://mp.toutiao.com/mp/agw/income/income_statement_abstract"
        "?only_mid_income=false&days=30&app_id=1231",
    ),
    (
        "benefit_page",
        "https://mp.toutiao.com/mp/agw/creator_project/get_benefit_page_info?app_id=1231",
    ),
)

REVENUE_LABEL_RE = re.compile(r"(预计收益|创作收益|平台收益|收益)")

STATIC_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".woff2", ".ico", ".map")


def _is_noise(url: str) -> bool:
    return url.endswith(STATIC_SUFFIXES) or "toutiaostatic" in url or "bytegoofy" in url


def _item_id_from_url(url: str) -> str:
    match = re.search(r"/(?:item|article|group|w)/(\d+)", str(url or ""))
    if match:
        return match.group(1)
    match = re.search(r"[?&](?:item_id|group_id|pgc_id|article_id)=(\d+)", str(url or ""))
    return match.group(1) if match else ""


def _candidate_item_ids(metrics_path: Path) -> list[str]:
    """从已有采集结果里取几个真实 item_id 用来探测（没有就用空串，接口会返回参数缺失）。"""
    if not metrics_path.exists():
        return []
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("records") if isinstance(payload, dict) else None
    ids: list[str] = []
    for row in records if isinstance(records, list) else []:
        local = row.get("local_record") if isinstance(row.get("local_record"), dict) else {}
        for source in (row.get("url"), local.get("url"), local.get("publish_url"), local.get("article_url")):
            found = _item_id_from_url(str(source or ""))
            if found and found not in ids:
                ids.append(found)
                break
        if len(ids) >= 3:
            break
    return ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="无头运行（默认有头，便于人工过验证码）")
    parser.add_argument("--account", default="robot_notes")
    parser.add_argument("--wait-ms", type=int, default=9000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings(ROOT, account_key=args.account)
    logger = logging.getLogger("revenue_probe")
    service = PerformanceService(settings, logger)
    profile = service._resolve_toutiao_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    metrics_path = settings.output_dir / "performance_metrics.json"
    item_ids = _candidate_item_ids(metrics_path)

    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile": str(profile),
        "item_ids_probed": item_ids,
        "content_rows": [],
        "row_text_has_revenue_label": None,
        "sniff": [],
        "apis": {},
        "errors": [],
    }

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

            def on_response(resp) -> None:
                url = resp.url
                if _is_noise(url):
                    return
                result["sniff"].append({"status": resp.status, "method": resp.request.method, "url": url})

            page.on("response", on_response)

            # ① 作品管理页：看列表行文本里有没有「收益」字段。
            # ⚠️ 必须用生产同一套选择器 + 同一套进场动作，否则抓到 0 行会把
            #    「我抓不到」误判成「页面没有这个字段」（2026-09-17 第一次探测就犯过）。
            try:
                page.goto(CONTENT_ALL_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(args.wait_ms)
                service._enter_content_manage(page)
                page.wait_for_timeout(args.wait_ms)
                rows = service._extract_metric_rows_from_frames(page, service.TOUTIAO_ROW_SELECTORS)
                if not rows:
                    rows = service._collect_rows_with_pagination(
                        page=page, row_selectors=service.TOUTIAO_ROW_SELECTORS, max_pages=1
                    )
                result["row_selectors_used"] = list(service.TOUTIAO_ROW_SELECTORS)
                for row in rows[:8]:
                    text = str(row.get("text", "")).replace("\n", " | ")[:300]
                    result["content_rows"].append(
                        {
                            "title": str(row.get("title", ""))[:80],
                            "url": str(row.get("url", ""))[:120],
                            "text": text,
                            "has_revenue_label": bool(REVENUE_LABEL_RE.search(str(row.get("text", "")))),
                        }
                    )
                result["row_text_has_revenue_label"] = any(
                    item["has_revenue_label"] for item in result["content_rows"]
                )
                print(f"[rows] 抓到 {len(rows)} 行；含「收益」字样: {result['row_text_has_revenue_label']}")
            except Exception as exc:
                result["errors"].append(f"content_all: {exc}")
                print(f"[rows] 失败: {exc}")

            # ② 收益数据页（新版 + 旧版）：sniff 所有 XHR，找按篇接口。
            for url in (INCOME_OVERVIEW_URL, INCOME_LEGACY_URL):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(args.wait_ms)
                    print(f"[sniff] {url} -> 停在 {page.url}")
                except Exception as exc:
                    result["errors"].append(f"sniff {url}: {exc}")
                    print(f"[sniff] {url} 失败: {exc}")

            # ③ 已知收益接口（刷新 T+1）+ 候选单篇接口
            today = datetime.now().date()
            targets: list[tuple[str, str]] = [
                (
                    name,
                    url.format(start=(today - timedelta(days=30)).isoformat(), end=today.isoformat()),
                )
                for name, url in KNOWN_INCOME_APIS
            ]
            probe_id = item_ids[0] if item_ids else ""
            for name, url in CANDIDATE_PER_ITEM_APIS:
                targets.append((name, url.format(item_id=probe_id)))

            for name, url in targets:
                try:
                    resp = ctx.request.get(url, timeout=30000)
                    try:
                        body: Any = resp.json()
                    except Exception:
                        body = resp.text()[:800]
                    result["apis"][name] = {"status": resp.status, "url": url, "body": body}
                    print(f"[api] {name}: HTTP {resp.status}")
                except Exception as exc:
                    result["apis"][name] = {"status": None, "url": url, "error": str(exc)}
                    print(f"[api] {name} 失败: {exc}")
        finally:
            ctx.close()

    out = ROOT / ".runtime" / "toutiao_revenue_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整产物：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
