"""Read content income exposed by the logged-in creator UI, without guessing APIs.

Account totals, date rows, ambiguous numeric units and unmatched titles never
become content revenue. Diagnostics retain paths and schemas, not signed URLs.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, parse_qs

BEIJING = timezone(timedelta(hours=8))
ARTICLE_INCOME_PATH = "/mp/agw/statistic/v2/toutiao_income/article_income"


def verified_article_income_rows(payload: dict) -> list[dict[str, Any]]:
    """Adapter for the actual single-work income table, observed 2026-10-05.

    Its column tip explicitly says cumulative work income. Nested amounts are
    fen: an actual amount=3 displays 0.03 yuan in the corresponding UI row.
    Never apply this conversion to other APIs or account-level income.
    """
    data = payload.get("data")
    if payload.get("code") not in (0, "0") or not isinstance(data, dict):
        return []
    columns = data.get("columns") or []
    if not any(isinstance(c, dict) and c.get("key") == "total_income" and c.get("tip") == "单篇内容的累计收益" for c in columns):
        return []
    out = []
    for row in data.get("toutiao_article_income") or []:
        if not isinstance(row, dict):
            continue
        gid = str(row.get("item_id", ""))
        nested = row.get("income_data") or {}
        total = nested.get("total_income") if isinstance(nested, dict) else None
        amount = str(total.get("amount", "")) if isinstance(total, dict) else ""
        if not gid.isdigit() or gid == "0" or not re.fullmatch(r"\d+", amount):
            continue
        if row.get("article_income") != int(amount):
            continue
        try:
            created = datetime.fromtimestamp(int(row["create_time"]) / 1000, BEIJING)
        except (KeyError, ValueError, TypeError, OSError, OverflowError):
            continue
        out.append({"gid": gid, "amount": int(amount) / 100, "created_at": created.isoformat(timespec="seconds"),
                    "type": "weitoutiao" if row.get("genre_type") == 3 else "unknown",
                    "title": str(row.get("title", "")).splitlines()[0] if row.get("title") else "",
                    "source": ARTICLE_INCOME_PATH, "scope": "cumulative_through_ready_date"})
    return out


def platform_item_id(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    if parsed.hostname not in {"mp.toutiao.com", "www.toutiao.com", "toutiao.com"}:
        return ""
    match = re.search(r"/(?:item|article|group|w)/(\d+)", parsed.path)
    if not match:
        match = re.fullmatch(r"/a(\d+)/?", parsed.path)
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
        self.ui: dict[str, Any] = {}
        self.work_data_ui: dict[str, Any] = {}
        self.work_rows: dict[str, dict[str, Any]] = {}
        self.ready_date: str = ""

    def observe_response(self, response: Any) -> None:
        parsed = urlsplit(response.url)
        # Work analytics can use paths outside /income/ and /statistic/.
        # Observe first-party JSON actually requested by the UI; the record
        # parser still requires an explicit work ID, amount, unit and scope.
        if parsed.hostname != "mp.toutiao.com" or response.status != 200:
            return
        try:
            if "json" not in response.headers.get("content-type", "") and response.request.resource_type not in {"xhr", "fetch"}:
                return
            payload = response.json()
            if not isinstance(payload, (dict, list)):
                return
            if isinstance(payload, dict) and any(payload.get(k, 0) not in (0, "0", None) for k in ("code", "err_no", "status_code")):
                return
            if parsed.path == "/mp/agw/statistic/v2/get_ready_date" and isinstance(payload, dict):
                value = (payload.get("data") or {}).get("date")
                try:
                    parsed_date = date.fromisoformat(str(value))
                    if parsed_date < datetime.now(BEIJING).date():
                        self.ready_date = parsed_date.isoformat()
                except ValueError:
                    pass
            if parsed.path == ARTICLE_INCOME_PATH and isinstance(payload, dict):
                for row in verified_article_income_rows(payload):
                    gid = row["gid"]
                    if gid in self.work_rows and self.work_rows[gid]["amount"] != row["amount"]:
                        self.conflicts.add(gid)
                    self.work_rows[gid] = row
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
        row = self.work_rows.get(gid)
        if row and self.ready_date and row["created_at"][:10] <= self.ready_date and gid not in self.conflicts:
            return row["amount"]
        return self.amounts.get(gid) if gid and gid not in self.conflicts else None

    def collect_ui(self, page: Any, timeout_ms: int) -> None:
        from toutiao_content_factory.services.creator_account_service import INCOME_OVERVIEW_URL
        page.goto(INCOME_OVERVIEW_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(4000)
        # Keep only visible labels and same-site paths for diagnosing a changed
        # UI. No cookies, response values, signed URLs or account balances.
        self.ui = page.evaluate("""() => ({
          labels: Array.from(document.querySelectorAll('a,button,[role="tab"],[role="menuitem"]')).map(e => (e.innerText || '').trim()).filter(t => t.length < 40 && /收益|收入|作品/.test(t)).slice(0,60),
          income_links: Array.from(document.querySelectorAll('a[href]')).map(e => {try {const u = new URL(e.href); return u.hostname === 'mp.toutiao.com' && /income/.test(u.pathname) ? {label: (e.innerText || '').trim().slice(0,40), path: u.pathname} : null;} catch {return null;}}).filter(Boolean).slice(0,30)
        })""")
        # Use only controls actually present on the income page.
        for label in ("作品收益", "单篇收益"):
            control = page.get_by_text(label, exact=True)
            if control.count() == 1 and control.is_visible():
                control.click(timeout=3000)
                page.wait_for_timeout(3000)
                break
        tab = page.get_by_text("图文创作收益", exact=True)
        if tab.count() == 1 and tab.is_visible():
            tab.click(timeout=3000)
            page.locator(".single-income-wrap").wait_for(state="visible", timeout=15000)
            section = page.locator(".single-income-wrap")
            if section.get_by_text("单篇创作收益", exact=True).count() == 1:
                for kind in ("文章", "微头条"):
                    section.get_by_text(kind, exact=True).click(timeout=3000)
                    section.get_by_text("30天", exact=True).click(timeout=3000)
                    page.wait_for_timeout(2500)
                    for _ in range(4):
                        more = section.get_by_text("加载更多", exact=True)
                        if more.count() != 1 or not more.is_visible():
                            break
                        more.click(timeout=3000)
                        page.wait_for_timeout(1500)
        for frame in page.frames:
            rows = frame.evaluate("""() => Array.from(document.querySelectorAll('table tbody tr, [class*="article-card"], [class*="income-item"]')).slice(0,100).map(row => ({text: row.innerText || '', urls: Array.from(row.querySelectorAll('a[href]')).map(a => a.href)}))""")
            for row in rows if isinstance(rows, list) else []:
                ids = {platform_item_id(url) for url in row.get("urls", [])} - {""}
                amounts = re.findall(r"(?:累计收益|总收益)\s*[：:]?\s*(\d+(?:\.\d+)?\s*元)", row.get("text", ""))
                known = {exact_yuan(value) for value in amounts} - {None}
                if len(ids) == 1 and len(known) == 1:
                    self.add(ids.pop(), known.pop(), "income_page_dom")
        if not self.amounts and not self.work_rows:
            # The live sidebar exposes this read-only route even when the
            # income page has no per-work tab. Inspect its actual API responses.
            control = page.get_by_role("link", name="作品数据", exact=True)
            if control.count() == 1 and control.is_visible():
                control.click(timeout=3000)
                page.wait_for_timeout(4000)
                self.work_data_ui = page.evaluate("""() => ({
                  labels: Array.from(document.querySelectorAll('a,button,[role="tab"]')).map(e => (e.innerText || '').trim()).filter(t => t.length < 40 && /收益|收入|数据/.test(t)).slice(0,60),
                  detail_links: Array.from(document.querySelectorAll('a[href]')).map(e => {try {const u = new URL(e.href); const label = (e.innerText || '').trim(); return u.hostname === 'mp.toutiao.com' && /收益|数据/.test(label) ? {label: label.slice(0,40), path: u.pathname} : null;} catch {return null;}}).filter(Boolean).slice(0,30)
                })""")

    def write_report(self, path: Path, *, matched: int, error: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        works = []
        for gid, row in self.work_rows.items():
            known = bool(self.ready_date and row["created_at"][:10] <= self.ready_date and gid not in self.conflicts)
            works.append({**row, "amount": row["amount"] if known else None,
                          "status": "available" if known else "pending_or_unknown", "revenue_as_of_date": self.ready_date})
        report = {
            "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "available" if matched or any(r["status"] == "available" for r in works) else "unknown",
            "matched_works": matched,
            "identified_works": len(self.amounts),
            "conflicting_works": len(self.conflicts),
            "observed_schemas": self.schemas,
            "income_ui": self.ui,
            "work_data_ui": self.work_data_ui,
            "works": works,
            "revenue_as_of_date": self.ready_date,
            "error_type": error,
            "reason": "Only explicit platform work IDs with explicit yuan amounts are attributed; missing or ambiguous values remain unknown.",
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
