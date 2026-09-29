"""头条创作者「账号级」快照：粉丝 / 总阅读 / 累计收益 / 创作权益 / 信用分。

为什么单独一层（2026-09-16 建立）：
- `PerformanceService` 采的是**作品级**指标（逐篇展现/阅读），`TOUTIAO_URLS`
  全是「作品管理」页，天然取不到**账号级**的收益金额 —— 这就是
  `performance_report.json` 里收益恒为 `pending_t1` / `unknown` 的原因。
- 收益金额渲染在**首页看板**（`/profile_v4/index`）的 `[class*='data-board-item']`
  节点里。已用网络嗅探证实**没有独立 XHR 接口**，只能读 DOM。
- 「有没有分成资格」的权威来源是 `creator_project/get_benefit_page_info`（实测 200）。
  publish preflight 抓到的 `is_creator=false` / `has_auth=false` / `ads_permission=0`
  **与收益资格无关**，不要再拿它们下结论（详见 `BENEFIT_STATUS_NOTE`）。

本模块只读，不修改平台上的任何数据。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings

# 首页看板：把每个 [class*='data-board-item'] 拆成「标签 -> 值行数组」。
# 实测结构：<div class="data-board-item">
#              <div class="data-board-item-title"><span class="board-title">累计收益</span></div>
#              ...  36.07元
#            </div>
INCOME_BOARD_JS = """
() => {
  const out = {};
  document.querySelectorAll("[class*='data-board-item']").forEach(item => {
    const titleNode = item.querySelector("[class*='data-board-item-title']");
    const label = titleNode ? (titleNode.innerText || '').trim() : '';
    if (!label) return;
    let body = (item.innerText || '').trim();
    if (body.startsWith(label)) body = body.slice(label.length).trim();
    out[label] = body.split('\\n').map(s => s.trim()).filter(Boolean);
  });
  return out;
}
"""

TOUTIAO_INDEX_URL = "https://mp.toutiao.com/profile_v4/index"
# 收益数据页。实测：**先访问这个路由再调收益接口**才能稳定拿到汇总
# （单独调用 `pgc/mp/income/income_statement_abstract` 会间歇性 404）。
INCOME_OVERVIEW_URL = "https://mp.toutiao.com/profile_v4/analysis/income-overview"

BENEFIT_API = "https://mp.toutiao.com/mp/agw/creator_project/get_benefit_page_info?app_id=1231"
USER_INFO_API = "https://mp.toutiao.com/mp/agw/creator_center/user_info?app_id=1231"

# 收益接口（2026-09-17 实测）。收益数据页 `/profile_v4/analysis/income-overview` 真正调的是这些，
# 所以**不必读 DOM**：这里能拿到逐日、分作品类型、可提现、累计的全套数字。
# （首页看板那个「累计收益」仍在用，用来兜底 + 拿粉丝/总阅读——那两个只在看板上。）
INCOME_DETAIL_API = (
    "https://mp.toutiao.com/mp/agw/income/overview/income/details"
    "?start_date={start}&end_date={end}&only_mid_income=0&app_id=1231"
)
INCOME_ABSTRACT_API = (
    "https://mp.toutiao.com/mp/agw/income/income_statement_abstract"
    "?only_mid_income=false&days={days}&app_id=1231"
)
INCOME_WINDOW_DAYS = 30
# 平台把未结算的当天写成这个字面量，**必须当 None（未知）而不是 0**。
PENDING_INCOME_MARKERS = ("待更新", "更新中", "")

# `status_code == 3` 表示「权益已开通」。
# 依据不是猜的：2026-09-16 实测返回 `activated_benefit_num = 7`，
# 而全量 benefit_list 里 status_code == 3 的条目正好 7 条（5 项基础权益 + 2 项千粉权益），
# 唯一一条 status_code == 0 的是万粉权益「头条抽奖」。计数吻合即可确证语义。
# → 因此下方 `derived_active_count` 会与 `activated_benefit_num` 交叉校验，
#   两者不等说明平台改了语义，届时会置 `semantics_ok=false` 提醒，而不是静默给错结论。
ACTIVE_STATUS_CODE = 3

BENEFIT_STATUS_NOTE = (
    "preflight 的 is_creator / has_auth / ads_permission 与本账号的收益资格无关："
    "该账号创作者权益页返回 7 项已开通权益（含「文章创作收益」「微头条创作收益」），"
    "信用分满分区间，且 is_low_quality=false。"
)

REVENUE_BENEFIT_NAMES = ("文章创作收益", "微头条创作收益", "视频创作收益", "小视频创作收益")


def parse_amount(text: str) -> float | None:
    """从「36.07元」这类文本里取出金额；取不到返回 None（**不要当成 0**）。"""
    if not text:
        return None
    match = re.search(r"(-?\d[\d,]*(?:\.\d+)?)\s*元", str(text))
    if not match:
        return None
    try:
        return round(float(match.group(1).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def parse_int(text: str) -> int | None:
    """从「14,960」这类文本里取出整数；取不到返回 None。"""
    if text is None:
        return None
    match = re.search(r"(-?\d[\d,]*)", str(text))
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_yesterday_delta(hint: str) -> float | None:
    """「昨日无变化」-> 0.0；「昨日 +1.23」-> 1.23；无法解析 -> None。

    None 与 0.0 语义不同：None 表示**这条增量看不出来**，不能当成「没涨」。
    """
    if not hint:
        return None
    text = str(hint).strip()
    if "无变化" in text:
        return 0.0
    amount = parse_amount(text)
    if amount is not None:
        return amount
    match = re.search(r"([+-]\s*\d+(?:\.\d+)?)", text)
    if match:
        try:
            return round(float(match.group(1).replace(" ", "")), 2)
        except (TypeError, ValueError):
            return None
    return None


def extract_board_snapshot(page: Any) -> dict[str, list[str]]:
    """读首页看板，返回 {标签: [值行...]}。"""
    try:
        board = page.evaluate(INCOME_BOARD_JS)
    except Exception:
        return {}
    return board if isinstance(board, dict) else {}


def fetch_creator_apis(ctx: Any) -> dict[str, Any]:
    """用浏览器上下文的 cookie 直连创作者后台内部接口（零签名，实测 200）。"""
    out: dict[str, Any] = {}
    for name, url in (("benefit_page", BENEFIT_API), ("user_info", USER_INFO_API)):
        try:
            resp = ctx.request.get(url, timeout=30000)
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = {"_non_json_prefix": str(resp.text())[:500]}
            out[name] = {"status": resp.status, "body": body}
        except Exception as exc:  # pragma: no cover - 网络/运行时相关
            out[name] = {"status": None, "error": str(exc)}
    return out


def fetch_income_apis(
    ctx: Any,
    *,
    days: int = INCOME_WINDOW_DAYS,
    today: date | None = None,
    logger: logging.Logger | None = None,
    attempts: int = 3,
) -> dict[str, Any]:
    """取收益接口：逐日明细 + 汇总（累计/可提现/本月/区间）。

    ⚠️ 实测这两个接口**会间歇性返回 404**（2026-09-17：单独调用
    `income_statement_abstract` 拿到过 404，同一进程里紧接着再调就是 200，
    与 Referer/XHR 头无关）。所以：
    1) 必须重试，不能一次失败就放弃（否则汇总字段整批为空）；
    2) 失败必须**说出来** —— 之前这里静默吞掉非 200，结果是
       `daily_days_with_income` 有值、`window_income` 却是 None，很难查。
    """
    today = today or datetime.now().date()
    targets = (
        (
            "income_overview",
            INCOME_DETAIL_API.format(start=(today - timedelta(days=days)).isoformat(), end=today.isoformat()),
        ),
        ("income_abstract", INCOME_ABSTRACT_API.format(days=days)),
    )
    out: dict[str, Any] = {}
    for name, url in targets:
        last: dict[str, Any] = {"status": None, "error": "not attempted"}
        for attempt in range(1, max(1, attempts) + 1):
            try:
                resp = ctx.request.get(url, timeout=30000)
                status = int(resp.status)
                if status == 200:
                    try:
                        body: Any = resp.json()
                    except Exception:
                        body = {"_non_json_prefix": str(resp.text())[:500]}
                    last = {"status": status, "body": body}
                    break
                last = {"status": status, "body": None, "error": f"http_{status}"}
            except Exception as exc:  # pragma: no cover - 网络/运行时相关
                last = {"status": None, "error": str(exc)}
            if logger is not None and attempt < attempts:
                # 该汇总接口已确认会间歇性 404；重试是正常恢复路径，只在全部
                # 尝试失败后记 warning，避免一次采集刷出多条误报警。
                logger.info(
                    "Income API %s attempt %s/%s failed (%s); retrying.",
                    name,
                    attempt,
                    attempts,
                    last.get("error"),
                )
        out[name] = last
        if logger is not None and last.get("status") != 200:
            logger.warning("Income API %s unavailable: status=%s error=%s", name, last.get("status"), last.get("error"))
    return out


def _income_amount(raw: Any) -> float | None:
    """收益金额解析：`待更新`/空串 → None（未知），**绝不返回 0**。"""
    text = str(raw if raw is not None else "").strip()
    if text in PENDING_INCOME_MARKERS or any(marker in text for marker in ("待更新", "更新中")):
        return None
    try:
        return round(float(text.replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def summarize_income(
    income_payload: dict[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """把两个收益接口的返回整理成可直接落盘/上报的结构。

    ⚠️ `income_statement_abstract`（累计/可提现/本月/区间）**实测会间歇性 404**
    （2026-09-17，重试 3 次仍失败过；同一进程里换个时机调用又是 200）。
    所以区间与本月两个数**优先自己从逐日明细里加总**，不把可用性押在那个接口上；
    abstract 只用来补「可提现」「结算构成」这类逐日明细里没有的口径，拿不到就留 None。
    """
    abstract_entry = income_payload.get("income_abstract") if isinstance(income_payload, dict) else None
    abstract_body = abstract_entry.get("body") if isinstance(abstract_entry, dict) else None
    rows = abstract_body.get("data") if isinstance(abstract_body, dict) else None
    rows = rows if isinstance(rows, list) else []

    summary: dict[str, Any] = {
        "total": None,
        "can_withdraw": None,
        "month_income": None,
        "window_days": INCOME_WINDOW_DAYS,
        "window_income": None,
        "yesterday_income": None,
        "yesterday_ready": None,
        "settle_breakdown": {},
        "source": "mp/agw/income/income_statement_abstract",
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("type", "")).strip()
        if kind == "total_income":
            summary["total"] = _income_amount(row.get("total"))
        elif kind == "can_withdraw_amount":
            summary["can_withdraw"] = _income_amount(row.get("total"))
            settle = row.get("settle_info") if isinstance(row.get("settle_info"), dict) else {}
            detail = settle.get("settle_detail") if isinstance(settle.get("settle_detail"), dict) else {}
            summary["settle_breakdown"] = {
                str(k): _income_amount(v) for k, v in detail.items() if str(k).strip()
            }
        elif kind == "monthly_income":
            summary["month_income"] = _income_amount(row.get("total"))
        elif kind == "period_income":
            # 同一条里带两个数：`lastday` 是昨日，`period` 是所选区间（默认 30 天）合计。
            summary["yesterday_income"] = _income_amount(row.get("lastday"))
            summary["window_income"] = _income_amount(row.get("period"))
            summary["yesterday_ready"] = bool(row.get("is_yesterday_income_ready"))

    overview_entry = income_payload.get("income_overview") if isinstance(income_payload, dict) else None
    overview_body = overview_entry.get("body") if isinstance(overview_entry, dict) else None
    data = overview_body.get("data") if isinstance(overview_body, dict) else None
    detail = data.get("detail") if isinstance(data, dict) else None

    daily: list[dict[str, Any]] = []
    for row in detail if isinstance(detail, list) else []:
        if not isinstance(row, dict):
            continue
        columns = row.get("columns_detail") if isinstance(row.get("columns_detail"), dict) else {}
        tuwen = columns.get("tuwen_income") if isinstance(columns.get("tuwen_income"), dict) else {}
        composition = {
            str(item.get("name", "")).strip(): item.get("amount")
            for item in (tuwen.get("composition") or [])
            if isinstance(item, dict)
        }
        amounts = {name: _income_amount(raw) for name, raw in composition.items()}
        # ⚠️ 未结算的那天，行级 `total` 字面是 "0"，而 composition 写「待更新」。
        # 直接采信 total 会把它当成"那天没赚到钱"，与"还不知道"完全不是一回事，
        # 所以只要有任一组成待更新，就把这天整行标成未结算。
        pending = any(
            any(marker in str(raw) for marker in ("待更新", "更新中"))
            for raw in composition.values()
        ) or (not composition and not str(row.get("total", "")).strip())
        daily.append(
            {
                "day": str(row.get("day", "")).strip(),
                "pending": pending,
                "total": None if pending else _income_amount(row.get("total")),
                "article": None if pending else amounts.get("文章收益"),
                "video": None if pending else amounts.get("视频收益"),
                "weitoutiao": None if pending else amounts.get("微头条收益"),
            }
        )
    summary["daily"] = daily
    summary["daily_days_with_income"] = sum(
        1 for item in daily if isinstance(item.get("total"), (int, float)) and float(item["total"]) > 0
    )

    # 区间/本月自己算：只算**已结算**（非 pending）的天，避免把「待更新」当 0 拉低均值。
    settled = [
        item
        for item in daily
        if not item.get("pending") and isinstance(item.get("total"), (int, float))
    ]
    if summary["window_income"] is None and settled:
        summary["window_income"] = round(sum(float(item["total"]) for item in settled), 2)
        summary["window_income_source"] = "derived_from_daily"
    reference_day = today or datetime.now().date()
    month_prefix = f"{reference_day.year:04d}-{reference_day.month:02d}"
    month_rows = [item for item in settled if str(item.get("day", "")).startswith(month_prefix)]
    if summary["month_income"] is None and month_rows:
        summary["month_income"] = round(sum(float(item["total"]) for item in month_rows), 2)
        summary["month_income_source"] = "derived_from_daily"
    summary["settled_days"] = len(settled)
    return summary


def summarize_benefits(api_payload: dict[str, Any]) -> dict[str, Any]:
    """把 `get_benefit_page_info` 的返回整理成「哪些收益权益已开通」。"""
    entry = api_payload.get("benefit_page") if isinstance(api_payload, dict) else None
    body = entry.get("body") if isinstance(entry, dict) else None
    data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else {}

    items: list[dict[str, Any]] = []
    for group in data.get("benefit_level_info_list", []) or []:
        if not isinstance(group, dict):
            continue
        level_desc = str(group.get("describe", "")).strip()
        for benefit in group.get("benefit_list", []) or []:
            if not isinstance(benefit, dict):
                continue
            status_code = benefit.get("status_code")
            items.append(
                {
                    "name": str(benefit.get("name", "")).strip(),
                    "level": level_desc,
                    "status_code": status_code,
                    "active": status_code == ACTIVE_STATUS_CODE,
                    "describe": str(benefit.get("describe", "")).strip(),
                }
            )

    declared = data.get("activated_benefit_num")
    derived = sum(1 for item in items if item["active"])
    revenue_benefits = {
        name: next((item["active"] for item in items if item["name"] == name), None)
        for name in REVENUE_BENEFIT_NAMES
    }
    return {
        "activated_benefit_num_declared": declared,
        "derived_active_count": derived,
        "semantics_ok": (declared is None) or (int(declared) == derived),
        "revenue_benefits": revenue_benefits,
        "can_earn_article_revenue": bool(revenue_benefits.get("文章创作收益")),
        "items": items,
        "status_code_active": ACTIVE_STATUS_CODE,
        "note": BENEFIT_STATUS_NOTE,
    }


def build_account_snapshot(
    settings: AppSettings,
    *,
    board: dict[str, list[str]] | None,
    api_payload: dict[str, Any] | None,
    income_payload: dict[str, Any] | None = None,
    observed_at: datetime | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """把看板 + 接口返回合并成一份可持久化的账号快照。"""
    observed_at = observed_at or datetime.now()
    board = board or {}
    api_payload = api_payload or {}

    def board_values(label: str) -> list[str]:
        values = board.get(label)
        return [str(item) for item in values] if isinstance(values, list) else []

    fans = parse_int(" ".join(board_values("粉丝数"))) if board_values("粉丝数") else None
    reads = (
        parse_int(" ".join(board_values("总阅读(播放)量")))
        if board_values("总阅读(播放)量")
        else None
    )
    revenue_values = board_values("累计收益")
    revenue_total = parse_amount(" ".join(revenue_values)) if revenue_values else None
    yesterday_hint = ""
    if len(revenue_values) > 1:
        yesterday_hint = revenue_values[1]

    user_entry = api_payload.get("user_info") if isinstance(api_payload, dict) else None
    user_body = user_entry.get("body") if isinstance(user_entry, dict) else None
    user_body = user_body if isinstance(user_body, dict) else {}

    benefits = summarize_benefits(api_payload) if api_payload else {}
    income = summarize_income(income_payload) if income_payload else {}
    credit_info = {}
    benefit_entry = api_payload.get("benefit_page") if isinstance(api_payload, dict) else None
    benefit_body = benefit_entry.get("body") if isinstance(benefit_entry, dict) else None
    if isinstance(benefit_body, dict) and isinstance(benefit_body.get("data"), dict):
        raw_data = benefit_body["data"]
        credit_info = raw_data.get("credit_info") if isinstance(raw_data.get("credit_info"), dict) else {}
        low_quality = raw_data.get("is_low_quality")
        benefit_fans = raw_data.get("fans_num")
    else:
        low_quality = None
        benefit_fans = None

    return {
        "generated_at": observed_at.isoformat(timespec="seconds"),
        "observed_date": observed_at.date().isoformat(),
        "account_key": settings.account_key,
        "account": {
            "name": str(user_body.get("name", "")).strip(),
            "media_id": user_body.get("media_id"),
            "fans": fans if fans is not None else (benefit_fans if isinstance(benefit_fans, int) else None),
            "total_reads": reads,
            "auth_type": user_body.get("auth_type"),
            "is_creator_flag": user_body.get("is_creator"),
            "claim_origin_permission": (user_body.get("extra") or {}).get("claim_origin_permission")
            if isinstance(user_body.get("extra"), dict)
            else None,
            "welcome_msg": str(user_body.get("welcome_msg", "")).strip(),
        },
        "revenue": {
            # 累计收益优先用收益接口的总数（权威且与可提现同源）；接口拿不到才退回首页看板。
            "total": income.get("total") if income.get("total") is not None else revenue_total,
            "total_source": (
                income.get("source") if income.get("total") is not None else f"{TOUTIAO_INDEX_URL} 首页看板「累计收益」"
            ),
            "currency": "CNY",
            "yesterday_hint": yesterday_hint,
            "yesterday_delta": parse_yesterday_delta(yesterday_hint),
            "is_total_not_daily": True,
            "income": income,
        },
        "quality": {
            "credit_score": credit_info.get("score"),
            "credit_score_change": credit_info.get("score_change"),
            "is_low_quality": low_quality,
        },
        "benefits": benefits,
        "errors": list(errors or []),
    }


def snapshot_path(settings: AppSettings) -> Path:
    return settings.output_dir / "growth_ops" / "performance" / "account_snapshot.json"


def load_account_snapshot(settings: AppSettings) -> dict[str, Any]:
    path = snapshot_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_account_snapshot(
    settings: AppSettings,
    logger: logging.Logger,
    snapshot: dict[str, Any],
) -> Path:
    """落盘为 {latest, history}：history 按天一条，用来算「较上次 +X 元」。"""
    path = snapshot_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = load_account_snapshot(settings)
    history = payload.get("history") if isinstance(payload.get("history"), list) else []

    day = str(snapshot.get("observed_date", "")).strip()
    revenue = snapshot.get("revenue") if isinstance(snapshot.get("revenue"), dict) else {}
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    quality = snapshot.get("quality") if isinstance(snapshot.get("quality"), dict) else {}

    history = [row for row in history if str(row.get("observed_date", "")).strip() != day]
    history.append(
        {
            "observed_date": day,
            "revenue_total": revenue.get("total"),
            "fans": account.get("fans"),
            "total_reads": account.get("total_reads"),
            "credit_score": quality.get("credit_score"),
            "is_low_quality": quality.get("is_low_quality"),
        }
    )
    history.sort(key=lambda row: str(row.get("observed_date", "")))

    # 「较上次」只与**更早的日期**比，避免同一天重复采集时自己和自己比出 0。
    previous = [
        row
        for row in history
        if str(row.get("observed_date", "")) < day and isinstance(row.get("revenue_total"), (int, float))
    ]
    delta = None
    if isinstance(revenue.get("total"), (int, float)) and previous:
        delta = round(float(revenue["total"]) - float(previous[-1]["revenue_total"]), 2)

    payload = {
        "latest": snapshot,
        "revenue_total": revenue.get("total"),
        "revenue_delta_vs_last_observation": delta,
        "previous_observation_date": str(previous[-1]["observed_date"]) if previous else "",
        "history": history,
        "updated_at": snapshot.get("generated_at"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Account snapshot saved: revenue_total=%s delta_vs_last=%s fans=%s credit_score=%s "
        "article_revenue_benefit=%s",
        revenue.get("total"),
        delta,
        account.get("fans"),
        quality.get("credit_score"),
        (snapshot.get("benefits") or {}).get("can_earn_article_revenue"),
    )
    return path
