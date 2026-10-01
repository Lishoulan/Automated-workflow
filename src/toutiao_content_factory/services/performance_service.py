from __future__ import annotations

import json
import logging
import math
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import ArticlePerformance, PublishRecord, WorkflowResult
from toutiao_content_factory.services import creator_account_service
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe
from toutiao_content_factory.services.t1_policy import infer_availability_status

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional dependency
    sync_playwright = None


class PerformanceService:
    HUMAN_WAIT_RANGE_MS = (2000, 5000)
    # 作品管理翻页深度：每页约 10-20 条，5 页覆盖近 7 天发布量（2026-08-27）
    MAX_PAGES = 5
    TOUTIAO_ROW_SELECTORS = (
        "[class*='article-card-bone']",
        "[class*='article-card']",
        "[class*='article-item']",
        "table tbody tr",
        ".article-item",
        ".content-list-item",
        ".publish-item",
        ".list-item",
        "[data-testid*='article']",
    )
    WECHAT_ROW_SELECTORS = (
        "table tbody tr",
        ".weui-desktop-card",
        ".publish-card",
        ".appmsg_item",
        ".publish-item",
        ".list_item",
    )
    TOUTIAO_URLS = (
        "https://mp.toutiao.com/profile_v4/manage/content/all",
        "https://mp.toutiao.com/profile_v4/manage/content/weitoutiao",
        "https://mp.toutiao.com/profile_v4/graphic/manage",
        "https://mp.toutiao.com/",
    )
    WECHAT_URLS = (
        "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_list_v2&action=list_ex",
        "https://mp.weixin.qq.com/",
    )

    def __init__(
        self,
        settings: AppSettings,
        logger: logging.Logger,
        *,
        collect_account_snapshot: bool = True,
    ) -> None:
        self.settings = settings
        self.logger = logger
        # 账号级快照（粉丝/总阅读/累计收益/创作权益）只在头条采集的同一个浏览器
        # 会话里顺带取一次，不额外再起一个浏览器（见 _collect_toutiao_account_snapshot）。
        self.collect_account_snapshot = collect_account_snapshot
        self.account_snapshot: dict[str, Any] | None = None

    def collect_performance_data(
        self,
        headless: bool | None = None,
        *,
        observed_at: datetime | None = None,
        earnings_date: date | str | None = None,
        target_publish_date: date | str | None = None,
    ) -> dict[str, Any]:
        # Default to the configured browser mode. Actions uses headless Chromium;
        # local runs can retain the existing headed mode when required.
        if headless is None:
            headless = self.settings.publishing.headless
        observed_at = observed_at or datetime.now()
        resolved_earnings_date = self._resolve_date_argument(earnings_date) or (
            observed_at.date() - timedelta(days=1)
        )
        resolved_target_publish_date = self._resolve_date_argument(target_publish_date) or resolved_earnings_date
        errors: list[dict[str, str]] = []
        raw_platforms: dict[str, list[ArticlePerformance]] = {"toutiao": [], "wechat": []}

        for platform, collector in (
            ("toutiao", self._collect_toutiao_metrics),
            ("wechat", self._collect_wechat_metrics),
        ):
            # 微信渠道已停用（135 编辑器退役，`WECHAT_ENABLED=0`），但采集仍在跑并且
            # **每次都失败**（`wechat performance page opened but target elements are
            # still missing`）—— 纯噪音，还白白多起一次浏览器。
            # 让采集跟随渠道开关：渠道不用了就不采；哪天重启微信，把 WECHAT_ENABLED 打开即可。
            if platform == "wechat" and not bool(getattr(self.settings.wechat, "enabled", False)):
                self.logger.info(
                    "Skipping wechat performance collection: wechat channel is disabled "
                    "(WECHAT_ENABLED=0). Enable the channel to collect its metrics again."
                )
                continue
            try:
                raw_platforms[platform] = collector(headless=headless)
            except Exception as exc:  # pragma: no cover - browser/runtime dependent
                self.logger.exception("Performance collection failed for %s", platform)
                errors.append({"platform": platform, "message": str(exc)})

        matched_records: list[dict[str, Any]] = []
        matched_records.extend(self._match_metrics_to_local_records("toutiao", raw_platforms["toutiao"]))
        matched_records.extend(self._match_metrics_to_local_records("wechat", raw_platforms["wechat"]))

        self._annotate_collection_semantics(
            matched_records,
            observed_at=observed_at,
            earnings_date=resolved_earnings_date,
            target_publish_date=resolved_target_publish_date,
        )
        platform_payloads: dict[str, list[dict[str, Any]]] = {}
        for platform, metrics in raw_platforms.items():
            rows = [metric.to_dict() for metric in metrics]
            for row in rows:
                row["platform"] = platform
            self._annotate_collection_semantics(
                rows,
                observed_at=observed_at,
                earnings_date=resolved_earnings_date,
                target_publish_date=resolved_target_publish_date,
            )
            platform_payloads[platform] = rows
        payload = {
            "generated_at": observed_at.isoformat(timespec="seconds"),
            "observed_at": observed_at.isoformat(timespec="seconds"),
            "collection_date": observed_at.date().isoformat(),
            "earnings_date": resolved_earnings_date.isoformat(),
            "data_date": resolved_earnings_date.isoformat(),
            "target_publish_date": resolved_target_publish_date.isoformat(),
            "earnings_policy": "t_plus_1",
            "records": matched_records,
            "platforms": platform_payloads,
            "errors": errors,
        }
        payload["attribution"] = self._sync_attribution_snapshots(
            matched_records,
            observed_at=observed_at,
            earnings_date=resolved_earnings_date,
            target_publish_date=resolved_target_publish_date,
        )
        payload["reports"] = self._build_automatic_reports()
        # 账号级快照：本次没采到就回退到上次落盘的值，避免报告里凭空消失。
        persisted = creator_account_service.load_account_snapshot(self.settings)
        payload["account_snapshot"] = (
            self.account_snapshot or persisted.get("latest") or {}
        )
        payload["account_revenue_total"] = persisted.get("revenue_total")
        payload["account_revenue_delta_vs_last_observation"] = persisted.get(
            "revenue_delta_vs_last_observation"
        )
        output_path = self._save_metrics(payload)
        payload["output_path"] = str(output_path)
        return payload

    def _collect_toutiao_metrics(self, *, headless: bool = False) -> list[ArticlePerformance]:
        return self._collect_platform_metrics(
            platform="toutiao",
            profile_dir=self._resolve_toutiao_profile_dir(),
            candidate_urls=self.TOUTIAO_URLS,
            base_url=self.settings.publishing.base_url,
            row_selectors=self.TOUTIAO_ROW_SELECTORS,
            channel=self.settings.publishing.channel,
            executable_path=self.settings.publishing.executable_path,
            timeout_ms=max(self.settings.publishing.wait_timeout_ms, 15000),
            headless=headless,
        )

    def _collect_wechat_metrics(self, *, headless: bool = False) -> list[ArticlePerformance]:
        return self._collect_platform_metrics(
            platform="wechat",
            profile_dir=self._resolve_wechat_profile_dir(),
            candidate_urls=self.WECHAT_URLS,
            base_url=self.settings.wechat.base_url,
            row_selectors=self.WECHAT_ROW_SELECTORS,
            channel=self.settings.publishing.channel,
            executable_path=self.settings.publishing.executable_path,
            timeout_ms=max(self.settings.publishing.wait_timeout_ms, 20000),
            headless=headless,
        )

    def _collect_platform_metrics(
        self,
        *,
        platform: str,
        profile_dir: Path,
        candidate_urls: tuple[str, ...],
        base_url: str,
        row_selectors: tuple[str, ...],
        channel: str,
        executable_path: str,
        timeout_ms: int,
        headless: bool = False,
    ) -> list[ArticlePerformance]:
        if sync_playwright is None:
            raise RuntimeError("Playwright is not installed.")

        profile_dir.mkdir(parents=True, exist_ok=True)
        guard_summary = prepare_browser_profile(
            profile_dir=profile_dir,
            project_root=self.settings.project_root,
            logger=self.logger,
            label=f"performance:{platform}",
            extra_paths=[self.settings.account_runtime_dir],
        )
        metrics: list[ArticlePerformance] = []
        urls = list(dict.fromkeys([*(candidate_urls or ()), base_url]))
        self.logger.info(
            "Launching performance collector browser for %s with headless=%s. profile=%s guard_elapsed_ms=%s",
            platform,
            headless,
            profile_dir,
            guard_summary.get("elapsed_ms"),
        )

        def _run():
            with sync_playwright() as playwright:  # pragma: no cover - browser/runtime dependent
                browser = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=headless,
                    channel=channel or None,
                    executable_path=executable_path or None,
                )
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    page.set_default_timeout(timeout_ms)
                    # 账号级快照必须先取：下面的 url 循环一旦拿到作品行就会 return，
                    # 挂在循环之后会永远执行不到。
                    if platform == "toutiao" and self.collect_account_snapshot:
                        self._collect_toutiao_account_snapshot(page=page, context=browser, timeout_ms=timeout_ms)
                    for url in urls:
                        if not url:
                            continue
                        if not self._open_target_with_retry(
                            page=page,
                            platform=platform,
                            url=url,
                            timeout_ms=timeout_ms,
                        ):
                            continue

                        # 头条：点击「作品管理」进入文章列表 iframe 页
                        if platform == "toutiao":
                            self._enter_content_manage(page)

                        # 全量采集：翻页聚合（2026-08-27 覆盖率优化）。
                        # 头条作品管理分页列表，翻页能采集近 7 天全部图文+微头条行，
                        # 否则只读到第一页（约 10-20 条），绩效闭环数据量偏少。
                        all_rows: list[dict[str, str]] = []
                        if platform == "toutiao":
                            all_rows = self._collect_rows_with_pagination(
                                page=page,
                                row_selectors=row_selectors,
                                max_pages=self.MAX_PAGES,
                            )
                            # 微头条的行不在图文列表页里：它们挂在独立的列表 URL 上，
                            # 而这个循环拿到第一批作品行就会 return ⇒ 永远停在图文页
                            # ⇒ 每次采集 platform 全是 toutiao，已发布的 21 条微头条指标为 0
                            # （2026-09-18 实测确认）。这里补采一次，失败不影响主流程。
                            extra_rows = self._collect_weitoutiao_rows(
                                page=page, row_selectors=row_selectors
                            )
                            if extra_rows:
                                seen_row_keys = {
                                    f"{row.get('title', '')}|{row.get('url', '')}|{row.get('text', '')[:120]}"
                                    for row in all_rows
                                }
                                added = 0
                                for row in extra_rows:
                                    key = f"{row.get('title', '')}|{row.get('url', '')}|{row.get('text', '')[:120]}"
                                    if key in seen_row_keys:
                                        continue
                                    seen_row_keys.add(key)
                                    all_rows.append(row)
                                    added += 1
                                if added:
                                    self.logger.info(
                                        "Merged %s rows from the weitoutiao list page.", added
                                    )
                        else:
                            rows = self._extract_metric_rows(page, row_selectors)
                            if not rows:
                                rows = self._extract_metric_rows_from_frames(page, row_selectors)
                            all_rows = list(rows)

                        if not all_rows:
                            self.logger.warning(
                                "%s performance page did not expose metric rows yet. If a captcha is visible, complete it in the browser window and rerun.",
                                platform,
                            )
                        parsed = self._normalize_metric_rows(all_rows)
                        recent = [item for item in parsed if self._is_recent_or_unknown(item.publish_date)]
                        if recent:
                            return self._dedupe_metrics(recent)
                        if parsed:
                            return self._dedupe_metrics(parsed)
                finally:
                    browser.close()
            return metrics

        return run_sync_playwright_safe(_run, timeout=300)

    def _collect_toutiao_account_snapshot(self, *, page: Any, context: Any, timeout_ms: int) -> None:
        """在头条采集的同一个浏览器会话里顺带取账号级数据（收益/权益/信用分）。

        为什么不放在 `TOUTIAO_URLS` 里：那个循环**拿到第一页作品行就会 return**，
        挂在后面永远执行不到；收益也不在作品管理页，而在首页看板。

        全程 non-fatal：任何一步失败都只写日志，不影响作品级采集。
        """
        from toutiao_content_factory.services import creator_account_service as account_service

        errors: list[str] = []
        board: dict[str, list[str]] = {}
        api_payload: dict[str, Any] = {}
        try:
            page.goto(account_service.TOUTIAO_INDEX_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            self._wait_for_dashboard_render(page, timeout_ms=timeout_ms)
            board = account_service.extract_board_snapshot(page)
            if not board:
                errors.append("account_board_empty")
                self.logger.warning(
                    "Account board did not expose any [class*='data-board-item'] node; "
                    "收益金额可能取不到（页面改版或登录态失效）。"
                )
        except Exception as exc:  # pragma: no cover - browser/runtime dependent
            errors.append(f"board:{exc}")
            self.logger.warning("Account board collection failed (non-fatal): %s", exc)

        try:
            api_payload = account_service.fetch_creator_apis(context)
        except Exception as exc:  # pragma: no cover - browser/runtime dependent
            errors.append(f"api:{exc}")
            self.logger.warning("Creator API collection failed (non-fatal): %s", exc)

        income_payload: dict[str, Any] = {}
        try:
            # 先走一遍收益数据页：实测必须先访问该路由，收益接口的汇总才稳定
            # （单独调用会间歇性 404，见 creator_account_service 里的说明）。
            try:
                page.goto(
                    account_service.INCOME_OVERVIEW_URL,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                page.wait_for_timeout(6000)
            except Exception as exc:  # pragma: no cover - browser/runtime dependent
                self.logger.warning("Income overview navigation failed (non-fatal): %s", exc)
            income_payload = account_service.fetch_income_apis(context, logger=self.logger)
        except Exception as exc:  # pragma: no cover - browser/runtime dependent
            errors.append(f"income:{exc}")
            self.logger.warning("Income API collection failed (non-fatal): %s", exc)

        snapshot = account_service.build_account_snapshot(
            self.settings,
            board=board,
            api_payload=api_payload,
            income_payload=income_payload,
            errors=errors,
        )
        self.account_snapshot = snapshot
        try:
            account_service.save_account_snapshot(self.settings, self.logger, snapshot)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            self.logger.warning("Account snapshot persistence failed (non-fatal): %s", exc)

        revenue = snapshot.get("revenue") or {}
        benefits = snapshot.get("benefits") or {}
        if revenue.get("total") is None:
            self.logger.warning("累计收益未取到（保持 unknown，不按 0 处理）。")
        if benefits and benefits.get("semantics_ok") is False:
            self.logger.warning(
                "创作权益状态码语义可能已变更：declared=%s derived=%s，请复核 status_code=%s 的含义。",
                benefits.get("activated_benefit_num_declared"),
                benefits.get("derived_active_count"),
                benefits.get("status_code_active"),
            )

    def _open_target_with_retry(
        self,
        *,
        page: Any,
        platform: str,
        url: str,
        timeout_ms: int,
    ) -> bool:
        for attempt in range(1, 3):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # 必须等内容真正渲染完再判断元素是否存在 —— 详见方法注释。
                self._wait_for_dashboard_render(page, timeout_ms=timeout_ms)
                if self._page_has_metric_signal(page):
                    return True
                self.logger.warning(
                    "%s performance page opened but target elements are still missing after attempt %s: %s. "
                    "页面可能仍在加载，或登录态已失效。",
                    platform,
                    attempt,
                    url,
                )
            except Exception as exc:
                self.logger.warning(
                    "%s performance navigation attempt %s failed for %s: %s",
                    platform,
                    attempt,
                    url,
                    exc,
                )
            if attempt < 2:
                self._human_pause(page, lower_ms=3000, upper_ms=6000)
        return False

    def _page_has_metric_signal(self, page: Any) -> bool:
        hints = (
            "text=阅读",
            "text=浏览",
            "text=展现",
            "text=草稿箱",
            "text=发表记录",
            "text=作品管理",
            "table",
        )
        for selector in hints:
            try:
                if page.locator(selector).first.count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _wait_for_dashboard_render(self, page: Any, *, timeout_ms: int) -> None:
        """等创作后台把内容真正渲染出来，再判断元素是否存在。

        ⚠️ 2026-09-16 定位的根因：这里此前只调用一次 `_human_pause()`（几百毫秒），
        而头条创作中心是 **React 单页应用** —— `domcontentloaded` 触发时页面骨架
        已就绪、数据尚未回填，此时查元素必然为空。于是每条候选 URL 都被判
        「target elements are still missing」，日志还建议"如有验证码请手动完成"，
        把排查方向**错误地引向登录态与风控**。

        实测（**同一 profile、同样 headless**）：只要等到页面渲染完
        - 信号元素正常：「阅读」/「展现」/「草稿箱」/「作品管理」命中数均为 1
        - 行选择器正常：`[class*='article-card']` 命中 15 行
        也就是说采集器本身没坏，**缺的只是等待**。这个缺失正是"表现数据在
        2026-09-14 之后再没回流过"的直接原因。
        """
        budget_ms = max(2000, min(int(timeout_ms), 20000))
        try:
            # 首屏数据请求通常在 networkidle 附近完成；后台可能有轮询，
            # 所以这里只作加速用，超时就继续轮询，不当失败处理。
            page.wait_for_load_state("networkidle", timeout=min(budget_ms, 8000))
        except Exception:
            pass
        waited = 0
        step = 500
        while waited < budget_ms:
            if self._page_has_metric_signal(page):
                # 骨架已在，再给列表一点落地时间，避免"框架到了、行还没来"。
                page.wait_for_timeout(step)
                return
            page.wait_for_timeout(step)
            waited += step

    def _enter_content_manage(self, page: Any) -> None:
        """头条创作中心：点击「作品管理」进入文章管理列表。

        文章数据渲染在 iframe 内（profile_v4/manage/content/all），
        必须先点击左侧「作品管理」入口，否则列表区域为空。
        """
        for selector in (
            "text=作品管理",
            "a:has-text('作品管理')",
            "span:has-text('作品管理')",
            "[class*='manage']:has-text('作品管理')",
        ):
            try:
                locator = page.locator(selector).first
                if locator.count() > 0 and locator.is_visible(timeout=4000):
                    locator.click(timeout=4000)
                    page.wait_for_timeout(3000)
                    self.logger.info("Clicked Toutiao content manage entry: %s", selector)
                    return
            except Exception:
                continue
        # 兜底：直接导航到文章管理 iframe 页
        try:
            page.goto("https://mp.toutiao.com/profile_v4/manage/content/all", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            self.logger.info("Navigated directly to Toutiao content manage iframe page.")
            return
        except Exception as exc:
            self.logger.warning("Direct navigation to content manage page failed: %s", exc)
        self.logger.warning("Toutiao content manage entry (作品管理) not found; continuing with page body.")

    def _extract_metric_rows_from_frames(self, page: Any, row_selectors: tuple[str, ...]) -> list[dict[str, str]]:
        """从页面的 iframe 中提取文章行（头条文章管理列表渲染在 iframe 内）。

        注意：头条管理页的文章列表就在主 frame（manage/content/all）中，
        不能跳过主 frame——否则永远提取不到数据（2026-08-25 实测修复）。
        """
        results: list[dict[str, str]] = []
        frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
        for frame in frames:
            try:
                frame.wait_for_selector("body", timeout=5000)
            except Exception:
                continue
            rows = frame.evaluate(
                """
                ({ rowSelectors }) => {
                  const seen = new Set();
                  const items = [];
                  for (const selector of rowSelectors) {
                    for (const node of document.querySelectorAll(selector)) {
                      const text = (node.innerText || "").trim();
                      if (!text || text.length < 8) {
                        continue;
                      }
                      const anchor = node.querySelector("a[href]");
                      const titleNode = node.querySelector("[title], .title, h1, h2, h3, h4") || anchor;
                      const title =
                        (titleNode && (titleNode.getAttribute("title") || titleNode.innerText || "")) ||
                        (anchor && anchor.innerText) ||
                        "";
                      const publishNode = node.querySelector("time, .time, .date, .publish-time, .publish_date, [data-time]");
                      const publishText =
                        (publishNode &&
                          (publishNode.getAttribute("datetime") ||
                            publishNode.getAttribute("title") ||
                            publishNode.getAttribute("data-time") ||
                            publishNode.innerText ||
                            "")) ||
                        "";
                      const href = anchor ? anchor.href || "" : "";
                      const key = `${title}|${href}|${text.slice(0, 120)}`;
                      if (!title.trim() || seen.has(key)) {
                        continue;
                      }
                      seen.add(key);
                      items.push({
                        title: title.trim(),
                        url: href.trim(),
                        publish_text: publishText.trim(),
                        text,
                      });
                    }
                  }
                  return items.slice(0, 200);
                }
                """,
                {"rowSelectors": list(row_selectors)},
            )
            for item in rows:
                if isinstance(item, dict) and item not in results:
                    results.append(item)
        return results

    def _resolve_date_argument(self, value: date | str | None) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            parsed = self._parse_datetime(text)
            return parsed.date() if parsed is not None else None

    def _availability_status(
        self,
        *,
        platform: str,
        published_date: date | None,
        observed_date: date,
        earnings_date: date,
    ) -> str:
        if published_date is None:
            return "not_available"
        if published_date > observed_date:
            return "not_available"
        if platform.lower() in {"toutiao", "toutiao_weitoutiao"} and published_date > earnings_date:
            return "pending_t1"
        return "available"

    def _annotate_collection_semantics(
        self,
        records: list[dict[str, Any]],
        *,
        observed_at: datetime,
        earnings_date: date,
        target_publish_date: date,
    ) -> None:
        """Attach explicit T+1 dates before any persistence or analysis."""
        observed_iso = observed_at.isoformat(timespec="seconds")
        observed_date = observed_at.date()
        for item in records:
            local_record = item.get("local_record") if isinstance(item.get("local_record"), dict) else {}
            platform = str(item.get("platform") or local_record.get("platform") or "toutiao").strip()
            published_at = str(
                local_record.get("published_at")
                or item.get("published_at")
                or item.get("publish_date")
                or ""
            ).strip()
            published_date = self._resolve_date_argument(published_at)
            if published_date is None:
                published_date = self._resolve_date_argument(item.get("publish_date"))
            availability = self._availability_status(
                platform=platform,
                published_date=published_date,
                observed_date=observed_date,
                earnings_date=earnings_date,
            )
            has_platform_revenue = "platform_revenue" in item and item.get("platform_revenue") is not None
            if availability == "pending_t1":
                revenue_status = "pending_t1"
                platform_revenue_status = "pending_t1"
            elif has_platform_revenue:
                revenue_status = "available"
                platform_revenue_status = "available"
            elif availability == "not_available":
                revenue_status = "not_available"
                platform_revenue_status = "not_available"
            else:
                # The article is eligible for yesterday's query, but an absent
                # amount is still unknown and must not be treated as zero.
                revenue_status = "unknown"
                platform_revenue_status = "unknown"

            item["published_date"] = published_date.isoformat() if published_date else ""
            item["observed_at"] = observed_iso
            item["collection_date"] = observed_date.isoformat()
            item["earnings_date"] = earnings_date.isoformat()
            item["data_date"] = earnings_date.isoformat()
            item["target_publish_date"] = target_publish_date.isoformat()
            item["is_earnings_target"] = bool(published_date == target_publish_date)
            item["availability_status"] = availability
            item["revenue_status"] = revenue_status
            item["platform_revenue_status"] = platform_revenue_status
            if availability != "available":
                # A same-day Toutiao row may expose a provisional amount in the
                # dashboard, but it is not an earnings value until T+1.
                item["platform_revenue"] = None
            item["revenue_amount_known"] = has_platform_revenue or bool(
                str(item.get("revenue_status", "")).strip() in {"available", "partial"}
                and item.get("revenue_amount") is not None
            )
            if availability != "available":
                item["revenue_amount_known"] = False
            item["analysis_eligible"] = availability == "available"

    def _sync_attribution_snapshots(
        self,
        records: list[dict[str, Any]],
        *,
        observed_at: datetime,
        earnings_date: date | str | None = None,
        target_publish_date: date | str | None = None,
    ) -> dict[str, Any]:
        """Persist matured performance windows and join them to content economics."""
        from toutiao_content_factory.services.revenue_attribution_service import (
            PerformanceSnapshot,
            RevenueAttributionService,
        )

        earnings_date = self._resolve_date_argument(earnings_date) or (observed_at.date() - timedelta(days=1))
        target_publish_date = self._resolve_date_argument(target_publish_date) or earnings_date
        self._annotate_collection_semantics(
            records,
            observed_at=observed_at,
            earnings_date=earnings_date,
            target_publish_date=target_publish_date,
        )
        attribution = RevenueAttributionService(self.settings.output_dir, self.logger)
        summary: dict[str, Any] = {
            "observed_at": observed_at.isoformat(timespec="seconds"),
            "collection_date": observed_at.date().isoformat(),
            "earnings_date": earnings_date.isoformat(),
            "data_date": earnings_date.isoformat(),
            "target_publish_date": target_publish_date.isoformat(),
            "earnings_policy": "t_plus_1",
            "recorded_count": 0,
            "pending_count": 0,
            "pending_t1_count": 0,
            "available_count": 0,
            "not_available_count": 0,
            "skipped_count": 0,
            "snapshot_labels": {label: 0 for label in attribution.SNAPSHOT_LABELS},
            "records": [],
            "snapshot_ledger": str(attribution.snapshot_ledger_path),
        }
        growth_ops = None
        try:
            from toutiao_content_factory.services.growth_ops_service import GrowthOpsService

            growth_ops = GrowthOpsService(self.settings, self.logger)
        except Exception as exc:  # pragma: no cover - legacy/minimal settings
            self.logger.debug("Growth performance ledger sync unavailable: %s", exc)

        for item in records:
            availability = str(item.get("availability_status", "not_available")).strip() or "not_available"
            if availability == "pending_t1":
                summary["pending_t1_count"] += 1
            elif availability == "available":
                summary["available_count"] += 1
            else:
                summary["not_available_count"] += 1
            local_record = item.get("local_record")
            if not isinstance(local_record, dict):
                summary["skipped_count"] += 1
                continue
            run_dir_text = str(local_record.get("run_dir", "")).strip()
            if not run_dir_text:
                summary["skipped_count"] += 1
                continue
            run_dir = Path(run_dir_text)
            try:
                identity_record = attribution.find_content_record_by_run_dir(run_dir)
                if identity_record is None:
                    identity_record = self._register_attribution_identity(
                        attribution,
                        platform=str(local_record.get("platform") or item.get("platform") or "toutiao"),
                        run_dir=run_dir,
                        local_record=local_record,
                        metric=item,
                    )
                content_id = str(identity_record.get("content_id", "")).strip()
                if not content_id:
                    summary["skipped_count"] += 1
                    continue

                item["content_id"] = content_id
                published_at = str(
                    local_record.get("published_at")
                    or identity_record.get("published_at")
                    or item.get("publish_date", "")
                ).strip()
                label, elapsed_hours = self._resolve_snapshot_window(
                    published_at,
                    observed_at=observed_at,
                )
                item["hours_since_publish"] = elapsed_hours
                item["snapshot_label"] = label or ""
                if not label:
                    item["snapshot_status"] = (
                        "pending_t1"
                        if availability == "pending_t1"
                        else "pending_1h"
                        if elapsed_hours is not None
                        else "published_at_unknown"
                    )
                    summary["pending_count"] += 1
                    continue

                revenue_summary = attribution.summarize_revenue_events(content_id)
                direct_platform_revenue = item.get("platform_revenue")
                has_direct_platform_revenue = direct_platform_revenue is not None
                if has_direct_platform_revenue:
                    try:
                        direct_platform_revenue = float(direct_platform_revenue or 0.0)
                    except (TypeError, ValueError):
                        direct_platform_revenue = 0.0
                        has_direct_platform_revenue = False
                has_platform_event = bool(revenue_summary.get("platform_revenue_known"))
                has_revenue_event = bool(revenue_summary.get("event_count"))
                if availability == "pending_t1":
                    platform_revenue_status = "pending_t1"
                    revenue_status = "pending_t1"
                elif has_direct_platform_revenue or has_platform_event:
                    platform_revenue_status = "available"
                    revenue_status = "available"
                elif has_revenue_event:
                    platform_revenue_status = "unknown"
                    revenue_status = "partial"
                else:
                    platform_revenue_status = str(item.get("platform_revenue_status", "unknown")) or "unknown"
                    revenue_status = str(item.get("revenue_status", "unknown")) or "unknown"
                revenue_amount = float(revenue_summary.get("revenue_amount", 0.0) or 0.0)
                if has_direct_platform_revenue:
                    revenue_amount += float(direct_platform_revenue or 0.0)
                snapshot = PerformanceSnapshot(
                    snapshot_label=label,
                    snapshot_at=observed_at.isoformat(timespec="seconds"),
                    collection_date=observed_at.date().isoformat(),
                    published_date=str(item.get("published_date", "")),
                    earnings_date=earnings_date.isoformat(),
                    availability_status=availability,
                    revenue_status=revenue_status,
                    platform_revenue_status=platform_revenue_status,
                    revenue_amount_known=(
                        has_direct_platform_revenue or has_platform_event
                    ) and availability != "pending_t1",
                    platform_revenue=(
                        float(direct_platform_revenue)
                        if has_direct_platform_revenue
                        else None
                    ),
                    impression_count=int(item.get("impression_count", 0) or 0),
                    read_count=int(item.get("read_count", item.get("views", 0)) or 0),
                    recommendation_read_count=int(item.get("recommendation_read_count", 0) or 0),
                    like_count=int(item.get("like_count", 0) or 0),
                    share_count=int(item.get("share_count", 0) or 0),
                    comment_count=int(item.get("comment_count", 0) or 0),
                    follower_gain=int(item.get("follower_gain", 0) or 0),
                    lead_count=int(revenue_summary.get("lead_count", 0) or 0),
                    revenue_amount=revenue_amount,
                )
                attribution.record_snapshot(content_id, snapshot)
                stored_economics = attribution._load_json(attribution.unit_economics_path, default={})
                existing_economics = stored_economics.get(content_id, {})
                platform_revenue = (
                    float(existing_economics.get("platform_revenue", 0.0) or 0.0)
                    if isinstance(existing_economics, dict)
                    else 0.0
                )
                economics = attribution.compute_unit_economics(
                    content_id,
                    platform_revenue=(
                        float(direct_platform_revenue)
                        if has_direct_platform_revenue
                        else platform_revenue
                    ),
                )
                item["snapshot_status"] = "pending_t1" if availability == "pending_t1" else "recorded"
                item["lead_count"] = snapshot.lead_count
                item["revenue_amount"] = snapshot.revenue_amount
                item["revenue_amount_known"] = snapshot.revenue_amount_known
                item["revenue_status"] = snapshot.revenue_status
                item["platform_revenue_status"] = snapshot.platform_revenue_status
                item["unit_economics"] = economics.to_dict()
                summary["recorded_count"] += 1
                summary["snapshot_labels"][label] += 1
                summary["records"].append(
                    {
                        "content_id": content_id,
                        "run_dir": str(run_dir),
                        "snapshot_label": label,
                        "hours_since_publish": elapsed_hours,
                        "published_date": item.get("published_date", ""),
                        "earnings_date": earnings_date.isoformat(),
                        "availability_status": availability,
                        "revenue_status": revenue_status,
                    }
                )

                if growth_ops is not None:
                    try:
                        growth_ops.record_performance(
                            run_dir=run_dir,
                            platform=str(local_record.get("platform") or item.get("platform") or "toutiao"),
                            snapshot_date=observed_at.date().isoformat(),
                            snapshot_label=label,
                            snapshot_at=observed_at.isoformat(timespec="seconds"),
                            collection_date=observed_at.date().isoformat(),
                            published_date=str(item.get("published_date", "")),
                            earnings_date=earnings_date.isoformat(),
                            availability_status=availability,
                            revenue_status=revenue_status,
                            platform_revenue_status=platform_revenue_status,
                            revenue_amount_known=snapshot.revenue_amount_known,
                            platform_revenue=(
                                float(direct_platform_revenue)
                                if has_direct_platform_revenue
                                else None
                            ),
                            content_id=content_id,
                            impression_count=int(item.get("impression_count", 0) or 0),
                            read_count=int(item.get("read_count", item.get("views", 0)) or 0),
                            recommendation_read_count=int(item.get("recommendation_read_count", 0) or 0),
                            like_count=int(item.get("like_count", 0) or 0),
                            share_count=int(item.get("share_count", 0) or 0),
                            comment_count=int(item.get("comment_count", 0) or 0),
                            follower_gain=int(item.get("follower_gain", 0) or 0),
                            lead_count=snapshot.lead_count,
                            revenue_amount=snapshot.revenue_amount,
                        )
                    except Exception as exc:  # pragma: no cover - ledger compatibility
                        item["growth_ledger_sync_error"] = str(exc)
                        self.logger.warning("Growth performance ledger sync failed for %s: %s", content_id, exc)
            except Exception as exc:  # pragma: no cover - malformed legacy record
                item["attribution_error"] = str(exc)
                summary["skipped_count"] += 1
                self.logger.warning("Performance attribution failed for %s: %s", run_dir_text, exc)
        return summary

    def _build_automatic_reports(self) -> dict[str, str]:
        """Refresh the operator-facing reports after each collection run."""
        reports: dict[str, str] = {}
        try:
            from toutiao_content_factory.services.growth_ops_service import GrowthOpsService

            performance_report = GrowthOpsService(self.settings, self.logger).build_performance_report()
            reports["performance_report"] = str(performance_report["performance_report"])
        except Exception as exc:  # pragma: no cover - malformed legacy ledger
            self.logger.warning("Automatic performance report build failed: %s", exc)
        try:
            from toutiao_content_factory.services.revenue_attribution_service import RevenueAttributionService

            revenue_service = RevenueAttributionService(self.settings.output_dir, self.logger)
            revenue_config = getattr(self.settings, "revenue_optimization", None)
            business_report = revenue_service.build_business_report(
                lookback_days=int(getattr(revenue_config, "lookback_days", 30)),
                min_samples=int(getattr(revenue_config, "min_samples", 10)),
                weak_rpm_threshold=float(getattr(revenue_config, "weak_rpm_threshold", 0.0)),
                weak_read_rate_threshold=float(getattr(revenue_config, "weak_read_rate_threshold", 3.0)),
                cooldown_weak_ratio=float(getattr(revenue_config, "cooldown_weak_ratio", 0.5)),
                stop_weak_ratio=float(getattr(revenue_config, "stop_weak_ratio", 0.8)),
                cooldown_penalty=int(getattr(revenue_config, "cooldown_penalty", 12)),
            )
            reports["business_report"] = str(business_report)
        except Exception as exc:  # pragma: no cover - malformed legacy ledger
            self.logger.warning("Automatic revenue report build failed: %s", exc)
        return reports

    def _register_attribution_identity(
        self,
        attribution: Any,
        *,
        platform: str,
        run_dir: Path,
        local_record: dict[str, Any],
        metric: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = self._load_publish_metadata(run_dir)
        selected_topic = metadata.get("selected_topic") or {}
        experiment_tags = metadata.get("experiment_tags") or {}
        if not experiment_tags:
            publish_dirname = getattr(getattr(self.settings, "review", None), "publish_dirname", "publish")
            tags_path = run_dir / publish_dirname / "experiment_tags.json"
            if tags_path.exists():
                try:
                    experiment_tags = json.loads(tags_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    experiment_tags = {}
        identity = attribution.register_published_run(
            account_key=str(getattr(self.settings, "account_key", "default")),
            platform=platform,
            run_dir=run_dir,
            publish_dirname=str(getattr(getattr(self.settings, "review", None), "publish_dirname", "publish")),
            topic_title=str(selected_topic.get("title") or local_record.get("topic_title") or "").strip(),
            title=str(local_record.get("title") or metric.get("title") or "").strip(),
            published_at=str(local_record.get("published_at") or metric.get("publish_date") or "").strip(),
            commercial_intent=str(
                selected_topic.get("commercial_intent")
                or experiment_tags.get("commercial_intent")
                or "medium"
            ),
            content_mix=str(
                selected_topic.get("content_mix")
                or experiment_tags.get("content_mix")
                or "professional"
            ),
            cta_variant=str(selected_topic.get("cta_variant") or experiment_tags.get("cta_variant") or ""),
            cta_source_code=str(
                selected_topic.get("cta_source_code")
                or experiment_tags.get("cta_source_code")
                or ""
            ),
            title_variant=str(selected_topic.get("title_variant") or experiment_tags.get("title_variant") or ""),
            cover_variant=str(selected_topic.get("cover_variant") or experiment_tags.get("cover_variant") or ""),
            platform_variant=str(
                selected_topic.get("platform_variant")
                or experiment_tags.get("platform_variant")
                or ""
            ),
            topic_bucket=str(selected_topic.get("topic_bucket") or experiment_tags.get("topic_bucket") or ""),
            topic=selected_topic,
        )
        return {
            "content_id": identity.content_id,
            "published_at": identity.published_at,
        }

    def _load_publish_metadata(self, run_dir: Path) -> dict[str, Any]:
        publish_dirname = getattr(getattr(self.settings, "review", None), "publish_dirname", "publish")
        path = run_dir / publish_dirname / "publish_meta.json"
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _resolve_snapshot_window(
        self,
        published_at: str,
        *,
        observed_at: datetime,
    ) -> tuple[str | None, float | None]:
        published = self._parse_datetime(published_at)
        if published is None:
            return None, None
        elapsed_hours = (observed_at - published).total_seconds() / 3600
        if elapsed_hours < 1:
            return None, round(max(0.0, elapsed_hours), 2)
        label: str | None = None
        for candidate, hours in (("1h", 1), ("6h", 6), ("24h", 24), ("72h", 72)):
            if elapsed_hours >= hours:
                label = candidate
        return label, round(elapsed_hours, 2)

    def _human_pause(self, page: Any, *, lower_ms: int | None = None, upper_ms: int | None = None) -> None:
        wait_lower, wait_upper = self.HUMAN_WAIT_RANGE_MS
        page.wait_for_timeout(random.randint(lower_ms or wait_lower, upper_ms or wait_upper))

    WEITOUTIAO_LIST_URL = "https://mp.toutiao.com/profile_v4/manage/content/weitoutiao"

    def _collect_weitoutiao_rows(
        self,
        *,
        page: Any,
        row_selectors: tuple[str, ...],
    ) -> list[dict[str, str]]:
        """补采微头条列表页的作品行（2026-09-18 新增）。

        为什么必须单独这一步：`_collect_platform_metrics` 的候选 URL 循环是
        「拿到第一批作品行就 return」，所以它永远停在第一条 URL（作品管理 all 页，
        该列表只渲染图文 tab）。结果是 21 条已发布微头条的**指标一条都采不到**，
        `performance_metrics.json` 里 platform 恒为 `toutiao`。

        实测（2026-09-18）：访问微头条列表页可抓到行，其中包含当天发布的微头条条目。

        全程 non-fatal：任何异常都返回空列表，图文采集不受影响。
        """
        try:
            page.goto(self.WEITOUTIAO_LIST_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            self._enter_content_manage(page)
            page.wait_for_timeout(6000)
            rows = self._extract_metric_rows_from_frames(page, row_selectors)
            self.logger.info("Weitoutiao list page exposed %s rows.", len(rows) if rows else 0)
            return rows or []
        except Exception as exc:  # pragma: no cover - browser/runtime dependent
            self.logger.warning("Weitoutiao row collection failed (non-fatal): %s", exc)
            return []

    def _collect_rows_with_pagination(
        self,
        *,
        page: Any,
        row_selectors: tuple[str, ...],
        max_pages: int,
    ) -> list[dict[str, str]]:
        """翻页聚合头条作品管理列表（2026-08-27 覆盖率优化）。

        逐页提取行 → 点击"下一页" → 去重合并，直到翻完 max_pages 或
        无下一页可点。分页按钮兼容常用 class/text 形式；找不到下一页
        说明已到末页，正常结束。
        """
        collected: list[dict[str, str]] = []
        seen_keys: set[str] = set()
        next_selectors = (
            "button[class*='next']",
            ".next",
            ".pagination .next",
            "[class*='pagination'] [class*='next']",
            "a:has-text('下一页')",
            "button:has-text('下一页')",
            ".page-next",
            "li.next",
        )
        # 头条作品管理页用的是**自定义分页控件**：既没有 `button.next`，
        # 正文里也没有"下一页"三个字 —— 上面那串通用选择器对它**一个都命中不了**，
        # 于是采集永远停在第一页（2026-09-16 定位；实测改按页码点击后一次拿到 97 条）。
        toutiao_next_page_js = """
        () => {
          const items = [...document.querySelectorAll('[class*="fake-pagination-item"]')]
            .filter(el => /^\\d+$/.test((el.innerText || '').trim()));
          if (!items.length) return null;
          const active = items.find(el => String(el.className || '').includes('active'));
          const current = active ? Number((active.innerText || '').trim()) : 0;
          const target = items.find(el => (el.innerText || '').trim() === String(current + 1));
          if (!target) return null;
          target.scrollIntoView();
          target.click();
          return String(current + 1);
        }
        """

        for _ in range(max_pages):
            rows = self._extract_metric_rows(page, row_selectors)
            if not rows:
                rows = self._extract_metric_rows_from_frames(page, row_selectors)
            for row in rows:
                key = f"{row.get('title', '')}|{row.get('url', '')}|{row.get('text', '')[:120]}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                collected.append(row)

            # 尝试点击下一页（主 frame 与 iframe 都试）
            clicked = False
            # 先走头条的自定义分页控件（`fake-pagination-item`）
            try:
                if page.evaluate(toutiao_next_page_js):
                    clicked = True
                    self.logger.info("Advanced to next page via Toutiao pagination control.")
            except Exception:
                clicked = False
            if clicked:
                frames_iter: tuple = ()
            else:
                frames_iter = (page.main_frame,) + tuple(
                    f for f in page.frames if f != page.main_frame
                )
            for frame in frames_iter:
                try:
                    next_btn = None
                    for selector in next_selectors:
                        locator = frame.query_selector(selector)
                        if locator is not None:
                            next_btn = locator
                            break
                    if next_btn is None:
                        continue
                    disabled = next_btn.evaluate(
                        "el => el.disabled || el.classList.contains('disabled') || el.getAttribute('aria-disabled') === 'true'"
                    )
                    if disabled:
                        return collected
                    next_btn.click()
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                break
            self._human_pause(page)
            # 等新一页渲染
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass

        return collected

    def _extract_metric_rows(self, page: Any, row_selectors: tuple[str, ...]) -> list[dict[str, str]]:
        rows = page.evaluate(
            """
            ({ rowSelectors }) => {
              const seen = new Set();
              const items = [];
              for (const selector of rowSelectors) {
                for (const node of document.querySelectorAll(selector)) {
                  const text = (node.innerText || "").trim();
                  if (!text || text.length < 8) {
                    continue;
                  }
                  const anchor = node.querySelector("a[href]");
                  const titleNode = node.querySelector("[title], .title, h1, h2, h3, h4") || anchor;
                  const title =
                    (titleNode && (titleNode.getAttribute("title") || titleNode.innerText || "")) ||
                    (anchor && anchor.innerText) ||
                    "";
                  const publishNode = node.querySelector("time, .time, .date, .publish-time, .publish_date, [data-time]");
                  const publishText =
                    (publishNode &&
                      (publishNode.getAttribute("datetime") ||
                        publishNode.getAttribute("title") ||
                        publishNode.getAttribute("data-time") ||
                        publishNode.innerText ||
                        "")) ||
                    "";
                  const href = anchor ? anchor.href || "" : "";
                  const key = `${title}|${href}|${text.slice(0, 120)}`;
                  if (!title.trim() || seen.has(key)) {
                    continue;
                  }
                  seen.add(key);
                  items.push({
                    title: title.trim(),
                    url: href.trim(),
                    publish_text: publishText.trim(),
                    text,
                  });
                }
              }
              return items.slice(0, 200);
            }
            """,
            {"rowSelectors": list(row_selectors)},
        )
        return [item for item in rows if isinstance(item, dict)]

    def _normalize_metric_rows(self, rows: list[dict[str, str]]) -> list[ArticlePerformance]:
        metrics: list[ArticlePerformance] = []
        for row in rows:
            title = self._clean_text(row.get("title", ""))
            if not title:
                # 微头条行通常无独立标题（内容即首行），用正文前 20 字作占位标题，
                # 保证微头条绩效数据进入反馈闭环（2026-08-27 微头条高频化）。
                body_text = self._clean_text(row.get("text", ""))
                title = body_text[:20] or "(微头条)"
            source_text = "\n".join(
                part for part in (row.get("publish_text", ""), row.get("text", "")) if str(part).strip()
            )
            publish_date = self._normalize_publish_date(source_text)
            views = self._extract_views(source_text)
            impressions = self._extract_impressions(source_text)
            platform_revenue = self._extract_platform_revenue(source_text)
            breakdown = self._extract_interaction_breakdown(source_text)
            interactions = breakdown["like_count"] + breakdown["comment_count"] + breakdown["share_count"]
            ctr = round(views * 100 / impressions, 2) if impressions > 0 else 0.0
            metrics.append(
                ArticlePerformance(
                    url=str(row.get("url", "")).strip(),
                    title=title,
                    publish_date=publish_date,
                    views=views,
                    interactions=interactions,
                    roi_score=self._compute_roi_score(views),
                    like_count=breakdown["like_count"],
                    comment_count=breakdown["comment_count"],
                    share_count=breakdown["share_count"],
                    impression_count=impressions,
                    read_count=views,
                    ctr=ctr,
                    platform_revenue=platform_revenue,
                )
            )
        return metrics

    def _match_metrics_to_local_records(
        self,
        platform: str,
        metrics: list[ArticlePerformance],
    ) -> list[dict[str, Any]]:
        local_entries = self._load_local_entries(platform)
        results: list[dict[str, Any]] = []

        for metric in metrics:
            matched = self._find_best_local_entry(metric, local_entries)
            workflow = self._load_workflow_result(matched.get("run_dir")) if matched else None
            # 保留 matched 记录的 platform：微头条行匹配到 toutiao_weitoutiao 记录时
            # 标记为微头条，反馈闭环可按平台区分学习（2026-08-27）。
            record_platform = str(matched.get("platform") or platform) if matched else platform
            results.append(
                {
                    "platform": record_platform,
                    "title": metric.title,
                    "url": metric.url,
                    "publish_date": metric.publish_date,
                    "platform_revenue": metric.platform_revenue,
                    "views": metric.views,
                    "read_count": metric.read_count or metric.views,
                    "impression_count": metric.impression_count,
                    "ctr": metric.ctr
                    or (
                        round(metric.views * 100 / metric.impression_count, 2)
                        if metric.impression_count > 0
                        else 0.0
                    ),
                    "like_count": metric.like_count,
                    "comment_count": metric.comment_count,
                    "share_count": metric.share_count,
                    "interactions": metric.interactions,
                    "roi_score": metric.roi_score,
                    "matched": bool(matched),
                    "local_record": matched or {},
                    "workflow_result": self._workflow_summary(workflow),
                }
            )

        return results

    def _load_local_entries(self, platform: str) -> list[dict[str, Any]]:
        if platform == "toutiao":
            records = self._load_publish_records(self.settings.output_dir / self.settings.publishing.history_filename)
            # 图文 + 微头条一起纳入匹配（2026-08-27）：微头条行无独立标题、
            # 靠正文占位标题 + 发布日匹配，须允许 platform=toutiao_weitoutiao 记录参与。
            entries = [
                {
                    "platform": record.platform or "toutiao",
                    "title": record.title,
                    "published_at": record.published_at,
                    "run_dir": record.run_dir,
                    "topic_title": record.topic_title,
                    "topic_url": record.topic_url,
                    "external_url": record.external_url,
                    "status": record.status,
                }
                for record in records
                if (record.platform or "toutiao") in ("toutiao", "toutiao_weitoutiao")
            ]
            archived = self._load_archived_toutiao_entries()
            seen = {
                f"{self._normalize_text(str(item.get('title', '')))}|"
                f"{self._normalize_url(str(item.get('external_url', '')))}|"
                f"{str(item.get('run_dir', ''))}"
                for item in entries
            }
            for item in archived:
                key = (
                    f"{self._normalize_text(str(item.get('title', '')))}|"
                    f"{self._normalize_url(str(item.get('external_url', '')))}|"
                    f"{str(item.get('run_dir', ''))}"
                )
                if key not in seen:
                    seen.add(key)
                    entries.append(item)
            return entries

        history_path = self.settings.output_dir / self.settings.wechat.history_filename
        if not history_path.exists():
            return []
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        entries: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            entries.append(
                {
                    "platform": "wechat",
                    "title": str(item.get("title", "")),
                    "published_at": str(item.get("published_at", "")),
                    "run_dir": str(item.get("run_dir", "")),
                    "topic_title": "",
                    "topic_url": "",
                    "external_url": "",
                    "status": "submitted",
                }
            )
        return entries

    def _load_archived_toutiao_entries(self) -> list[dict[str, Any]]:
        archive_dirname = str(
            getattr(getattr(self.settings, "automation", None), "archive_dirname", "runs")
        )
        roots = [
            self.settings.output_dir / archive_dirname,
            self.settings.output_dir / "wtt_standalone",
        ]
        accepted = {"published", "submitted", "submitted_for_review", "success", "publish_uncertain"}
        entries: list[dict[str, Any]] = []
        for root in roots:
            if not root.exists():
                continue
            for meta_path in root.rglob("publish_meta.json"):
                publish_dir = meta_path.parent
                run_dir = publish_dir.parent
                meta = self._load_json_dict(meta_path)
                topic = meta.get("selected_topic") if isinstance(meta.get("selected_topic"), dict) else {}
                for result_name, default_platform in (
                    ("publish_result.json", "toutiao"),
                    ("weitoutiao_publish_result.json", "toutiao_weitoutiao"),
                ):
                    result_path = publish_dir / result_name
                    if not result_path.exists():
                        continue
                    result = self._load_json_dict(result_path)
                    record = result.get("record") if isinstance(result.get("record"), dict) else {}
                    status = str(result.get("status") or record.get("status") or "").strip().lower()
                    if status not in accepted:
                        continue
                    entries.append(
                        {
                            "platform": str(result.get("platform") or record.get("platform") or default_platform),
                            "title": str(meta.get("selected_title") or record.get("title") or ""),
                            "published_at": str(
                                result.get("published_at")
                                or record.get("published_at")
                                or meta.get("approved_at")
                                or ""
                            ),
                            "run_dir": str(run_dir),
                            "topic_title": str(topic.get("title") or record.get("topic_title") or ""),
                            "topic_url": str(topic.get("url") or record.get("topic_url") or ""),
                            "external_url": str(
                                result.get("external_url") or record.get("external_url") or ""
                            ),
                            "status": status,
                        }
                    )
        return entries

    @staticmethod
    def _load_json_dict(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _find_best_local_entry(
        self,
        metric: ArticlePerformance,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        normalized_title = self._normalize_text(metric.title)
        normalized_url = self._normalize_url(metric.url)
        metric_content_id = self._toutiao_content_id(metric.url)
        best_entry: dict[str, Any] | None = None
        best_score = -1

        for entry in entries:
            score = 0
            entry_title = self._normalize_text(str(entry.get("title", "")))
            entry_topic_title = self._normalize_text(str(entry.get("topic_title", "")))
            if normalized_title and normalized_title in {entry_title, entry_topic_title}:
                score += 5

            for url_value in (
                str(entry.get("external_url", "")),
                str(entry.get("topic_url", "")),
            ):
                if normalized_url and normalized_url == self._normalize_url(url_value):
                    score += 4
                if metric_content_id and metric_content_id == self._toutiao_content_id(url_value):
                    score += 6

            if metric.publish_date and self._same_publish_day(metric.publish_date, str(entry.get("published_at", ""))):
                score += 2

            if score > best_score:
                best_entry = entry
                best_score = score

        if best_score <= 0:
            return None
        return best_entry

    def _load_publish_records(self, history_path: Path) -> list[PublishRecord]:
        if not history_path.exists():
            return []
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [PublishRecord.from_dict(item) for item in payload if isinstance(item, dict)]

    def _load_workflow_result(self, run_dir: str | None) -> WorkflowResult | None:
        candidates: list[Path] = []
        if run_dir:
            candidates.append(Path(run_dir) / "workflow_result.json")
        candidates.append(self.settings.output_dir / "workflow_result.json")

        for path in candidates:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return WorkflowResult.from_dict(payload)
            except Exception:
                self.logger.debug("Failed to load workflow result: %s", path, exc_info=True)
        return None

    def _workflow_summary(self, workflow: WorkflowResult | None) -> dict[str, Any]:
        if workflow is None:
            return {}
        return {
            "created_at": workflow.created_at,
            "topic_title": workflow.extraction.topic_title,
            "style_label": workflow.style_label,
            "model_used": workflow.model_used,
        }

    def _save_metrics(self, payload: dict[str, Any]) -> Path:
        target = self.settings.output_dir / "performance_metrics.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        merged_payload = dict(payload)
        previous_payload: dict[str, Any] = {}
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    previous_payload = loaded
            except (OSError, json.JSONDecodeError):
                self.logger.warning("Previous performance payload could not be read; writing the new collection.")

        if previous_payload:
            merged_payload["records"] = self._merge_collected_rows(
                previous_payload.get("records", []),
                payload.get("records", []),
            )
            merged_payload["platforms"] = {
                platform: self._merge_collected_rows(
                    previous_payload.get("platforms", {}).get(platform, [])
                    if isinstance(previous_payload.get("platforms"), dict)
                    else [],
                    payload.get("platforms", {}).get(platform, [])
                    if isinstance(payload.get("platforms"), dict)
                    else [],
                )
                for platform in { 
                    *(
                        previous_payload.get("platforms", {}).keys()
                        if isinstance(previous_payload.get("platforms"), dict)
                        else set()
                    ),
                    *(
                        payload.get("platforms", {}).keys()
                        if isinstance(payload.get("platforms"), dict)
                        else set()
                    ),
                }
            }
            merged_payload["previous_generated_at"] = str(previous_payload.get("generated_at", ""))
        target.write_text(json.dumps(merged_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def _merge_collected_rows(self, previous: object, current: object) -> list[dict[str, Any]]:
        previous_rows = [item for item in previous if isinstance(item, dict)] if isinstance(previous, list) else []
        current_rows = [item for item in current if isinstance(item, dict)] if isinstance(current, list) else []
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        availability_rank = {"not_available": 0, "pending_t1": 1, "available": 2}
        status_rank = {"unknown": 0, "pending_t1": 1, "not_available": 1, "partial": 2, "available": 3}
        numeric_fields = {
            "impression_count",
            "read_count",
            "views",
            "recommendation_read_count",
            "like_count",
            "wow_count",
            "share_count",
            "comment_count",
            "interactions",
            "follower_gain",
        }
        for row in previous_rows + current_rows:
            key = self._collected_row_key(row)
            if key not in by_key:
                order.append(key)
                by_key[key] = dict(row)
                continue
            previous_row = by_key[key]
            merged = dict(previous_row)
            old_availability = infer_availability_status(previous_row)
            new_availability = infer_availability_status(row)
            old_availability_rank = availability_rank.get(old_availability, -1)
            new_availability_rank = availability_rank.get(new_availability, -1)
            if new_availability == "pending_t1":
                merged["availability_status"] = "pending_t1"
            elif new_availability and new_availability_rank >= old_availability_rank:
                merged["availability_status"] = new_availability

            for field in ("revenue_status", "platform_revenue_status"):
                old_status = str(previous_row.get(field, "unknown")).strip() or "unknown"
                new_status = str(row.get(field, "unknown")).strip() or "unknown"
                old_rank = status_rank.get(old_status, 0)
                new_rank = status_rank.get(new_status, 0)
                if new_availability == "pending_t1":
                    merged[field] = "pending_t1"
                elif new_rank > old_rank or (new_rank == old_rank and old_status == "unknown" and new_status != "unknown"):
                    merged[field] = new_status

            for field, value in row.items():
                if field in {"availability_status", "revenue_status", "platform_revenue_status", "revenue_amount_known"}:
                    continue
                if field in numeric_fields:
                    try:
                        merged[field] = max(int(previous_row.get(field, 0) or 0), int(value or 0))
                    except (TypeError, ValueError):
                        if value not in (None, ""):
                            merged[field] = value
                    continue
                if field == "platform_revenue":
                    if value is not None:
                        merged[field] = value
                    continue
                if field == "revenue_amount":
                    continue
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                if isinstance(value, (dict, list, tuple, set)) and not value:
                    continue
                merged[field] = value

            old_local = previous_row.get("local_record")
            new_local = row.get("local_record")
            if isinstance(old_local, dict) and isinstance(new_local, dict):
                merged["local_record"] = {**old_local, **new_local}
            elif isinstance(new_local, dict) and new_local:
                merged["local_record"] = dict(new_local)

            old_platform_revenue = previous_row.get("platform_revenue")
            new_platform_revenue = row.get("platform_revenue")
            old_platform_status = str(previous_row.get("platform_revenue_status", "")).strip()
            new_platform_status = str(row.get("platform_revenue_status", "")).strip()
            old_known = bool(previous_row.get("revenue_amount_known", False)) or (
                old_platform_revenue is not None and old_platform_status in {"", "available"}
            )
            new_known = bool(row.get("revenue_amount_known", False)) or (
                new_platform_revenue is not None and new_platform_status in {"", "available"}
            )
            if new_availability == "pending_t1":
                merged["platform_revenue"] = None
            elif new_known and new_platform_revenue is not None:
                merged["platform_revenue"] = new_platform_revenue
            elif old_known and old_platform_revenue is not None:
                merged["platform_revenue"] = old_platform_revenue

            old_revenue_amount = previous_row.get("revenue_amount")
            new_revenue_amount = row.get("revenue_amount")
            if new_availability == "pending_t1":
                merged["revenue_amount"] = None
            elif new_known and new_revenue_amount is not None:
                merged["revenue_amount"] = new_revenue_amount
            elif old_known and old_revenue_amount is not None:
                merged["revenue_amount"] = old_revenue_amount
            merged["revenue_amount_known"] = False if new_availability == "pending_t1" else old_known or new_known
            merged["analysis_eligible"] = merged.get("availability_status", "available") == "available"
            by_key[key] = merged
        return [by_key[key] for key in order]

    def _collected_row_key(self, row: dict[str, Any]) -> str:
        local_record = row.get("local_record") if isinstance(row.get("local_record"), dict) else {}
        content_id = str(row.get("content_id", "") or local_record.get("content_id", "")).strip()
        run_dir = str(local_record.get("run_dir", "")).strip()
        platform = str(row.get("platform", "")).strip()
        url = str(row.get("url", "")).strip()
        title = self._normalize_text(str(row.get("title", "")))
        publish_date = str(row.get("published_date") or row.get("publish_date", "")).strip()
        return "|".join((content_id, run_dir, platform, url, title, publish_date))

    def _resolve_toutiao_profile_dir(self) -> Path:
        path = Path(self.settings.publishing.user_data_dir)
        if not path.is_absolute():
            path = self.settings.project_root / path
        return path

    def _resolve_wechat_profile_dir(self) -> Path:
        return self.settings.account_runtime_dir / "wechat_profile"

    def _compute_roi_score(self, views: int) -> float:
        # views = 阅读量（真正的浏览量），是风格进化的核心指标。
        # 新文章阅读可能为 0（刚发布），保留小基数避免全部 0 分。
        if views <= 0:
            return 1.0
        return round(min(100.0, math.log10(max(views, 1)) * 25.0), 2)

    def _extract_views(self, text: str) -> int:
        """提取阅读量（头条管理页"阅读"字段 = 真正的浏览量）。

        注意：头条管理页文本形如「展现 70 阅读 0 点赞 0 评论 1」，
        展现（推荐曝光）在前、阅读（真实点击）在后，必须精确匹配"阅读"而非"展现"。
        """
        patterns = (
            r"阅读量?\s*[:：]?\s*([\d.,万wWkK]+)",
            r"([\d.,万wWkK]+)\s*阅读量?",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self._parse_number(match.group(1))
        return 0

    def _extract_impressions(self, text: str) -> int:
        """提取展现量（头条管理页"展现"字段 = 推荐曝光次数，不是浏览量）。"""
        patterns = (
            r"展现量?\s*[:：]?\s*([\d.,万wWkK]+)",
            r"([\d.,万wWkK]+)\s*展现量?",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self._parse_number(match.group(1))
        return 0

    def _extract_platform_revenue(self, text: str) -> float | None:
        """Extract an article-level earnings amount when the dashboard exposes it."""
        label = r"(?:\u9884\u4f30\u6536\u76ca|\u521b\u4f5c\u6536\u76ca|\u5e73\u53f0\u6536\u76ca|\u6536\u76ca)"
        amount = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
        unit = r"([\u4e07\u5343wkWK]?)"
        patterns = (
            rf"{label}\s*[:\uff1a]?\s*(?:[\u00a5\uffe5]|CNY)?\s*{amount}\s*{unit}",
            rf"(?:[\u00a5\uffe5]|CNY)\s*{amount}\s*{unit}\s*{label}",
        )
        for pattern in patterns:
            match = re.search(pattern, str(text or ""), re.IGNORECASE)
            if not match:
                continue
            try:
                value = float(match.group(1).replace(",", ""))
            except (TypeError, ValueError):
                continue
            multiplier = {"\u4e07": 10000.0, "\u5343": 1000.0, "w": 10000.0, "k": 1000.0}.get(
                str(match.group(2) or "").lower(),
                1.0,
            )
            return round(value * multiplier, 4)
        return None

    def _extract_interaction_breakdown(self, text: str) -> dict[str, int]:
        """Extract like/comment/share counts as independent fields."""
        breakdown = {"like_count": 0, "comment_count": 0, "share_count": 0}
        patterns = {
            "like_count": r"(?:点赞|赞)\s*[:：]?\s*([\d.,万wWkK]+)",
            "comment_count": r"评论\s*[:：]?\s*([\d.,万wWkK]+)",
            "share_count": r"(?:转发|分享)\s*[:：]?\s*([\d.,万wWkK]+)",
        }
        for field, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                breakdown[field] = self._parse_number(match.group(1))

        # Fallback: if all zero, try combined "互动" field and leave breakdown at 0
        if breakdown["like_count"] == 0 and breakdown["comment_count"] == 0 and breakdown["share_count"] == 0:
            interaction_match = re.search(r"(?:互动|互动量)\s*[:：]?\s*([\d.,万wWkK]+)", text, re.IGNORECASE)
            if interaction_match:
                # Cannot split combined number; put into like_count as approximation
                breakdown["like_count"] = self._parse_number(interaction_match.group(1))
        return breakdown

    def _extract_interactions(self, text: str) -> int:
        breakdown = self._extract_interaction_breakdown(text)
        return breakdown["like_count"] + breakdown["comment_count"] + breakdown["share_count"]

    def _parse_number(self, raw_value: str) -> int:
        text = str(raw_value or "").strip().replace(",", "")
        if not text:
            return 0
        multiplier = 1
        if text.endswith(("万", "w", "W")):
            multiplier = 10000
            text = text[:-1]
        elif text.endswith(("k", "K")):
            multiplier = 1000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0

    def _normalize_publish_date(self, text: str) -> str:
        today = datetime.now().date()
        compact = str(text or "").strip()
        if not compact:
            return ""
        if "今天" in compact:
            return today.isoformat()
        if "昨天" in compact:
            return (today - timedelta(days=1)).isoformat()
        if "前天" in compact:
            return (today - timedelta(days=2)).isoformat()

        for pattern in (
            r"(\d{4}-\d{1,2}-\d{1,2})",
            r"(\d{4}/\d{1,2}/\d{1,2})",
            r"(\d{1,2}-\d{1,2})",
            r"(\d{1,2}/\d{1,2})",
            r"(\d{1,2})月(\d{1,2})日",
        ):
            match = re.search(pattern, compact)
            if not match:
                continue
            if len(match.groups()) == 1:
                value = match.group(1).replace("/", "-")
                if len(value.split("-")) == 3:
                    try:
                        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
                    except ValueError:
                        continue
                month, day = value.split("-", 1)
                try:
                    return date(today.year, int(month), int(day)).isoformat()
                except ValueError:
                    continue
            if len(match.groups()) == 2:
                try:
                    return date(today.year, int(match.group(1)), int(match.group(2))).isoformat()
                except ValueError:
                    continue
        return ""

    def _same_publish_day(self, left: str, right: str) -> bool:
        left_day = self._date_portion(left)
        right_day = self._date_portion(right)
        return bool(left_day and right_day and left_day == right_day)

    def _date_portion(self, text: str) -> str:
        candidate = self._normalize_publish_date(text)
        if candidate:
            return candidate
        stripped = str(text or "").strip()
        if not stripped:
            return ""
        try:
            return datetime.fromisoformat(stripped.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return ""

    def _is_recent_or_unknown(self, publish_date: str) -> bool:
        if not publish_date:
            return True
        parsed = self._date_portion(publish_date)
        if not parsed:
            return True
        try:
            day = datetime.strptime(parsed, "%Y-%m-%d").date()
        except ValueError:
            return True
        return day >= (datetime.now().date() - timedelta(days=2))

    def _dedupe_metrics(self, metrics: list[ArticlePerformance]) -> list[ArticlePerformance]:
        deduped: list[ArticlePerformance] = []
        seen: set[str] = set()
        for metric in metrics:
            key = f"{self._normalize_text(metric.title)}|{self._normalize_url(metric.url)}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(metric)
        return deduped

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"[\W_]+", "", str(text or "").lower())

    def _normalize_url(self, url: str) -> str:
        return str(url or "").strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")

    @staticmethod
    def _toutiao_content_id(url: str) -> str:
        text = str(url or "").strip()
        for pattern in (
            r"/(?:article|group)/(\d{8,})",
            r"(?:^|[?&])(?:item_id|group_id|gid)=(\d{8,})",
        ):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return ""

    def _parse_datetime(self, value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    def _clean_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())


def collect_performance_data(
    settings: AppSettings,
    logger: logging.Logger,
    *,
    headless: bool | None = None,
    observed_at: datetime | None = None,
    earnings_date: date | str | None = None,
    target_publish_date: date | str | None = None,
) -> dict[str, Any]:
    return PerformanceService(settings, logger).collect_performance_data(
        headless=headless,
        observed_at=observed_at,
        earnings_date=earnings_date,
        target_publish_date=target_publish_date,
    )
