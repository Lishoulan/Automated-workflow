from __future__ import annotations

import json
import logging
import math
import random
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from toutiao_content_factory.config import AppSettings
from toutiao_content_factory.models import ArticlePerformance, PublishRecord, WorkflowResult
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.services.rpa_utils import run_sync_playwright_safe

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional dependency
    sync_playwright = None


class PerformanceService:
    HUMAN_WAIT_RANGE_MS = (2000, 5000)
    TOUTIAO_ROW_SELECTORS = (
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
        "https://mp.toutiao.com/profile_v4/graphic/publish",
        "https://mp.toutiao.com/profile_v4/graphic/manage",
        "https://mp.toutiao.com/",
    )
    WECHAT_URLS = (
        "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_list_v2&action=list_ex",
        "https://mp.weixin.qq.com/",
    )

    def __init__(self, settings: AppSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

    def collect_performance_data(self) -> dict[str, Any]:
        errors: list[dict[str, str]] = []
        raw_platforms: dict[str, list[ArticlePerformance]] = {"toutiao": [], "wechat": []}

        for platform, collector in (
            ("toutiao", self._collect_toutiao_metrics),
            ("wechat", self._collect_wechat_metrics),
        ):
            try:
                raw_platforms[platform] = collector()
            except Exception as exc:  # pragma: no cover - browser/runtime dependent
                self.logger.exception("Performance collection failed for %s", platform)
                errors.append({"platform": platform, "message": str(exc)})

        matched_records: list[dict[str, Any]] = []
        matched_records.extend(self._match_metrics_to_local_records("toutiao", raw_platforms["toutiao"]))
        matched_records.extend(self._match_metrics_to_local_records("wechat", raw_platforms["wechat"]))

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "records": matched_records,
            "platforms": {
                name: [metric.to_dict() for metric in metrics]
                for name, metrics in raw_platforms.items()
            },
            "errors": errors,
        }
        output_path = self._save_metrics(payload)
        payload["output_path"] = str(output_path)
        return payload

    def _collect_toutiao_metrics(self) -> list[ArticlePerformance]:
        return self._collect_platform_metrics(
            platform="toutiao",
            profile_dir=self._resolve_toutiao_profile_dir(),
            candidate_urls=self.TOUTIAO_URLS,
            base_url=self.settings.publishing.base_url,
            row_selectors=self.TOUTIAO_ROW_SELECTORS,
            channel=self.settings.publishing.channel,
            executable_path=self.settings.publishing.executable_path,
            timeout_ms=max(self.settings.publishing.wait_timeout_ms, 15000),
        )

    def _collect_wechat_metrics(self) -> list[ArticlePerformance]:
        return self._collect_platform_metrics(
            platform="wechat",
            profile_dir=self._resolve_wechat_profile_dir(),
            candidate_urls=self.WECHAT_URLS,
            base_url=self.settings.wechat.base_url,
            row_selectors=self.WECHAT_ROW_SELECTORS,
            channel=self.settings.publishing.channel,
            executable_path=self.settings.publishing.executable_path,
            timeout_ms=max(self.settings.publishing.wait_timeout_ms, 20000),
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
            "Launching performance collector browser for %s with visible mode. profile=%s guard_elapsed_ms=%s",
            platform,
            profile_dir,
            guard_summary.get("elapsed_ms"),
        )

        def _run():
            with sync_playwright() as playwright:  # pragma: no cover - browser/runtime dependent
                browser = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    channel=channel or None,
                    executable_path=executable_path or None,
                )
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()
                    page.set_default_timeout(timeout_ms)
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

                        rows = self._extract_metric_rows(page, row_selectors)
                        if not rows:
                            self.logger.warning(
                                "%s performance page did not expose metric rows yet. If a captcha is visible, complete it in the browser window and rerun.",
                                platform,
                            )
                        parsed = self._normalize_metric_rows(rows)
                        recent = [item for item in parsed if self._is_recent_or_unknown(item.publish_date)]
                        if recent:
                            return self._dedupe_metrics(recent)
                        if parsed:
                            return self._dedupe_metrics(parsed)
                finally:
                    browser.close()
            return metrics

        return run_sync_playwright_safe(_run, timeout=300)

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
                self._human_pause(page)
                if self._page_has_metric_signal(page):
                    return True
                self.logger.warning(
                    "%s performance page opened but target elements are still missing after attempt %s: %s. If a captcha is visible, complete it in the browser window.",
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
            "table",
        )
        for selector in hints:
            try:
                if page.locator(selector).first.count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _human_pause(self, page: Any, *, lower_ms: int | None = None, upper_ms: int | None = None) -> None:
        wait_lower, wait_upper = self.HUMAN_WAIT_RANGE_MS
        page.wait_for_timeout(random.randint(lower_ms or wait_lower, upper_ms or wait_upper))

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
                continue
            source_text = "\n".join(
                part for part in (row.get("publish_text", ""), row.get("text", "")) if str(part).strip()
            )
            publish_date = self._normalize_publish_date(source_text)
            views = self._extract_views(source_text)
            interactions = self._extract_interactions(source_text)
            metrics.append(
                ArticlePerformance(
                    url=str(row.get("url", "")).strip(),
                    title=title,
                    publish_date=publish_date,
                    views=views,
                    interactions=interactions,
                    roi_score=self._compute_roi_score(views),
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
            results.append(
                {
                    "platform": platform,
                    "title": metric.title,
                    "url": metric.url,
                    "publish_date": metric.publish_date,
                    "views": metric.views,
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
            return [
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
                if (record.platform or "toutiao") == "toutiao"
            ]

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

    def _find_best_local_entry(
        self,
        metric: ArticlePerformance,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        normalized_title = self._normalize_text(metric.title)
        normalized_url = self._normalize_url(metric.url)
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
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def _resolve_toutiao_profile_dir(self) -> Path:
        path = Path(self.settings.publishing.user_data_dir)
        if not path.is_absolute():
            path = self.settings.project_root / path
        return path

    def _resolve_wechat_profile_dir(self) -> Path:
        return self.settings.account_runtime_dir / "wechat_profile"

    def _compute_roi_score(self, views: int) -> float:
        if views <= 0:
            return 0.0
        return round(min(100.0, math.log10(max(views, 1)) * 25.0), 2)

    def _extract_views(self, text: str) -> int:
        patterns = (
            r"(?:阅读量|阅读|浏览量|浏览|展现量|展现)\s*[:：]?\s*([\d.,万wWkK]+)",
            r"([\d.,万wWkK]+)\s*(?:阅读量|阅读|浏览量|浏览|展现量|展现)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return self._parse_number(match.group(1))
        return 0

    def _extract_interactions(self, text: str) -> int:
        total = 0
        for pattern in (
            r"(?:点赞|赞)\s*[:：]?\s*([\d.,万wWkK]+)",
            r"评论\s*[:：]?\s*([\d.,万wWkK]+)",
            r"(?:转发|分享)\s*[:：]?\s*([\d.,万wWkK]+)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                total += self._parse_number(match.group(1))
        if total > 0:
            return total

        interaction_match = re.search(r"(?:互动|互动量)\s*[:：]?\s*([\d.,万wWkK]+)", text, re.IGNORECASE)
        if interaction_match:
            return self._parse_number(interaction_match.group(1))
        return 0

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
        return str(url or "").strip().rstrip("/")

    def _clean_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())


def collect_performance_data(settings: AppSettings, logger: logging.Logger) -> dict[str, Any]:
    return PerformanceService(settings, logger).collect_performance_data()
