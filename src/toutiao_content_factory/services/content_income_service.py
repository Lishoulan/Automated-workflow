"""Read content income exposed by the logged-in creator UI, without guessing APIs.

Account totals, date rows, ambiguous numeric units and unmatched titles never
become content revenue. Diagnostics retain paths and schemas, not signed URLs.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, parse_qs


def platform_item_id(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    if parsed.hostname not in {"mp.toutiao.com", "www.toutiao.com", "toutiao.com"}:
        return ""
    match = re.search(r"/(?:item|article|group|w)/(\d+)", parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    for key in ("item_id", "group_id", "pgc_id", "article_id"):
        value = query.get(key, [""])[0]
        if value.isdigit() and value != "0":
            return value
    return ""


def exact_yuan(value: Any, unit: str = "") -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "")
    # Explicit money text or an explicit unit is required. Bare API numbers
    # can be yuan, fen, or cumulative account totals.
    match = re.fullmatch(r"(?:¥|￥)?\s*(\d+(?:\.\d+)?)\s*元", text)
    if not match and unit not in {"元", "CNY", "yuan"}:
        return None
    try:
        amount = float(match.group(1) if match else text)
    except (ValueError, TypeError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def content_income_rows(payload: Any) -> list[dict[str, Any]]:
    """Extract only records carrying an explicit platform ID and yuan amount."""
    out: list[dict[str, Any]] = []
    def visit(value: Any, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(value, list):
            for child in value[:500]:
                visit(child, depth + 1)
        elif isinstance(value, dict):
            gid = next((str(value[k]) for k in ("item_id", "group_id", "pgc_id", "article_id", "gid")
                        if str(value.get(k, "")).isdigit() and str(value[k]) != "0"), "")
            unit = str(value.get("unit") or value.get("currency") or "")
            pending = value.get("is_income_ready") is False or any(
                str(value.get(k, "")) in {"pending", "pending_t1", "待更新", "更新中"}
                for k in ("income_status", "status", "revenue_status")
            )
            # A day/range amount must not be mistaken for lifetime work income.
            total_scope = str(value.get("income_scope", "")) in {"total", "lifetime", "cumulative"}
            fields = ("total_income", "income", "revenue", "income_amount") if total_scope else ("total_income",)
            amounts = [exact_yuan(value[k], unit) for k in fields if k in value] if not pending else []
            known = {amount for amount in amounts if amount is not None}
            if gid and len(known) == 1:
                out.append({"gid": gid, "amount": known.pop()})
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)
    visit(payload)
    return out


class ContentIncomeCollector:
    def __init__(self) -> None:
        self.amounts: dict[str, float] = {}
        self.conflicts: set[str] = set()
        self.sources: dict[str, str] = {}
        self.schemas: list[dict[str, Any]] = []

    def observe_response(self, response: Any) -> None:
        parsed = urlsplit(response.url)
        if parsed.hostname != "mp.toutiao.com" or "/income/" not in parsed.path or response.status != 200:
            return
        try:
            if "json" not in response.headers.get("content-type", ""):
                return
            payload = response.json()
            if not isinstance(payload, (dict, list)):
                return
            if isinstance(payload, dict) and any(payload.get(k, 0) not in (0, "0", None) for k in ("code", "err_no", "status_code")):
                return
            schemas: set[tuple[str, ...]] = set()
            def keys(value: Any, depth: int = 0) -> None:
                if depth > 5:
                    return
                if isinstance(value, dict):
                    schemas.add(tuple(sorted(str(k) for k in value)[:40]))
                    for child in value.values():
                        if isinstance(child, (dict, list)):
                            keys(child, depth + 1)
                elif isinstance(value, list):
                    for child in value[:3]:
                        keys(child, depth + 1)
            keys(payload)
            diagnostic = {"path": parsed.path, "schemas": [list(s) for s in sorted(schemas)]}
            if diagnostic not in self.schemas and len(self.schemas) < 30:
                self.schemas.append(diagnostic)
            for row in content_income_rows(payload):
                self.add(row["gid"], row["amount"], parsed.path)
        except Exception:
            # An unrelated or unreadable response must not break collection.
            return

    def add(self, gid: str, amount: float, source: str) -> None:
        if gid in self.amounts and self.amounts[gid] != amount:
            self.conflicts.add(gid)
        self.amounts[gid] = amount
        self.sources[gid] = source

    def amount_for(self, url: str) -> float | None:
        gid = platform_item_id(url)
        return self.amounts.get(gid) if gid and gid not in self.conflicts else None

    def collect_ui(self, page: Any, timeout_ms: int) -> None:
        from toutiao_content_factory.services.creator_account_service import INCOME_OVERVIEW_URL
        page.goto(INCOME_OVERVIEW_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(4000)
        # Use only controls actually present on the income page.
        for label in ("作品收益", "单篇收益"):
            control = page.get_by_text(label, exact=True)
            if control.count() == 1 and control.is_visible():
                control.click(timeout=3000)
                page.wait_for_timeout(3000)
                break
        for frame in page.frames:
            rows = frame.evaluate("""() => Array.from(document.querySelectorAll('table tbody tr, [class*="article-card"], [class*="income-item"]')).slice(0,100).map(row => ({text: row.innerText || '', urls: Array.from(row.querySelectorAll('a[href]')).map(a => a.href)}))""")
            for row in rows if isinstance(rows, list) else []:
                ids = {platform_item_id(url) for url in row.get("urls", [])} - {""}
                amounts = re.findall(r"(?:累计收益|总收益)\s*[：:]?\s*(\d+(?:\.\d+)?\s*元)", row.get("text", ""))
                known = {exact_yuan(value) for value in amounts} - {None}
                if len(ids) == 1 and len(known) == 1:
                    self.add(ids.pop(), known.pop(), "income_page_dom")

    def write_report(self, path: Path, *, matched: int, error: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "available" if matched else "unknown",
            "matched_works": matched,
            "identified_works": len(self.amounts),
            "conflicting_works": len(self.conflicts),
            "observed_schemas": self.schemas,
            "error_type": error,
            "reason": "Only explicit platform work IDs with explicit yuan amounts are attributed; missing or ambiguous values remain unknown.",
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
