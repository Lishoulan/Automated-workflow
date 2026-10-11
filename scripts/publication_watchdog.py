"""Cloud-only, read-only publication checks with durable, quota-aware alerts.

This script never generates, submits, or retries content. Only the notification
ledger is written, on a separate branch shared with the nightly digest.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import sys
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from toutiao_content_factory.services.notification_service import NotificationService, NOTIFICATION_TIMEZONE
from toutiao_content_factory.services.cross_channel_state import published_on

LEDGER_BRANCH = "publication-monitor-state"
LEDGER_PATH = "state/notification_log.jsonl"
LOCAL_LEDGER = ROOT / "output/automation/notification_log.jsonl"
WORKFLOWS = {"Daily Run": "今日头条", "WeChat Daily Run": "公众号", "WeChat Content Metrics": "公众号"}
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
RESULT_NAMES = {"wechat_cloud_result.json", "last_daily_run_result.json", "notification_log.jsonl"}


def parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=NOTIFICATION_TIMEZONE) if parsed.tzinfo is None else parsed.astimezone(NOTIFICATION_TIMEZONE)
    except ValueError:
        return None


class GitHub:
    def __init__(self):
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.client = httpx.Client(headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                                            "Accept": "application/vnd.github+json"}, timeout=25)

    def close(self):
        self.client.close()

    def request(self, path, *, method="GET", payload=None, missing_ok=False):
        response = self.client.request(method, f"https://api.github.com/repos/{self.repo}/{path}", json=payload)
        if response.status_code == 404 and missing_ok:
            return None
        if response.is_error:
            # Never print provider responses, credentials, or signed URLs.
            raise RuntimeError(f"GitHub API HTTP {response.status_code}")
        return response.json()

    def file(self, branch, path, *, optional=False):
        row = self.request(f"contents/{path}?ref={branch}", missing_ok=optional)
        return None if row is None else base64.b64decode(row["content"])

    def state(self, branch, path):
        # Existing publication state must be readable; an API error is not
        # evidence of an empty day and must not produce a false missed-run alert.
        body = self.file(branch, path)
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Invalid publication state")
        return result

    def results(self, run):
        artifacts = self.request(f"actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
        results = []
        for artifact in artifacts:
            if artifact.get("expired") or not artifact["name"].startswith(("wechat-outcome-", "daily-output-")):
                continue
            if artifact["size_in_bytes"] > MAX_ARCHIVE_BYTES:
                raise ValueError("Outcome archive exceeds read-only inspection limit")
            # Follow the signed redirect without forwarding the GitHub token.
            response = self.client.get(f"https://api.github.com/repos/{self.repo}/actions/artifacts/{artifact['id']}/zip")
            if response.status_code != 302:
                raise RuntimeError("Outcome archive unavailable")
            with httpx.stream("GET", response.headers["location"], timeout=25, follow_redirects=True) as download:
                if download.is_error:
                    raise RuntimeError("Outcome download failed")
                data = bytearray()
                for chunk in download.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_ARCHIVE_BYTES:
                        raise ValueError("Outcome download exceeds limit")
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for member in archive.infolist():
                    if Path(member.filename).name not in RESULT_NAMES or member.file_size > 1024 * 1024:
                        continue
                    if Path(member.filename).name == "notification_log.jsonl":
                        for line in archive.read(member).decode("utf-8").splitlines():
                            entry = json.loads(line)
                            stamp = parse_time(entry.get("timestamp", ""))
                            start = parse_time(run.get("run_started_at", run["created_at"]))
                            end = parse_time(run.get("updated_at", ""))
                            title = str(entry.get("title", "")).lower()
                            if (stamp and start and end and start <= stamp <= end + timedelta(minutes=1)
                                    and entry.get("metadata", {}).get("queued_for_cloud_monitor") is True
                                    and any(term in title for term in ("session invalid", "session expired", "login required", "login expired"))):
                                results.append({"status": "publish_login_required"})
                        continue
                    result = json.loads(archive.read(member))
                    if not isinstance(result, dict):
                        continue
                    # Toutiao caches old run results. Only a fresh timestamp
                    # can bind that result to the completed workflow run.
                    if Path(member.filename).name == "last_daily_run_result.json":
                        stamp = parse_time(result.get("updated_at", ""))
                        start = parse_time(run.get("run_started_at", run["created_at"]))
                        end = parse_time(run.get("updated_at", ""))
                        if not stamp or not start or not end or not start <= stamp <= end + timedelta(minutes=1):
                            continue
                    results.append(result)
        return results


def restore_ledger(api, destination=LOCAL_LEDGER):
    shared = api.file(LEDGER_BRANCH, LEDGER_PATH, optional=True)
    if shared is None and api.request(f"git/ref/heads/{LEDGER_BRANCH}", missing_ok=True) is not None:
        raise ValueError("Shared notification ledger is missing")
    paths = [destination, *ROOT.glob("output/accounts/*/automation/notification_log.jsonl")]
    bodies = [shared.decode("utf-8") if shared is not None else ""]
    bodies.extend(path.read_text(encoding="utf-8") for path in paths if path.exists())
    entries = {}
    for body in bodies:
        for line in body.splitlines():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("Invalid notification ledger")
            key = json.dumps(row, ensure_ascii=False, sort_keys=True)
            entries[key] = row
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in entries.values()), encoding="utf-8")


def save_ledger(api, source=LOCAL_LEDGER):
    existing = api.request(f"contents/{LEDGER_PATH}?ref={LEDGER_BRANCH}", missing_ok=True)
    if existing is None and api.request(f"git/ref/heads/{LEDGER_BRANCH}", missing_ok=True) is None:
        base = api.request("git/ref/heads/main")["object"]["sha"]
        api.request("git/refs", method="POST", payload={"ref": f"refs/heads/{LEDGER_BRANCH}", "sha": base})
    content = base64.b64encode(source.read_bytes()).decode()
    if existing is not None and base64.b64decode(existing["content"]) == source.read_bytes():
        return
    payload = {"branch": LEDGER_BRANCH, "message": "chore: record publication alerts [skip ci]", "content": content}
    if existing:
        payload["sha"] = existing["sha"]
    api.request(f"contents/{LEDGER_PATH}", method="PUT", payload=payload)


def outcome_alert(platform, result):
    status = str(result.get("status", ""))
    delivery = result.get("draft_delivery", {})
    if status in {"publish_login_required", "login_required", "login_expired"}:
        return f"【{platform}】登录失效", "登录状态已失效，自动任务已停止。请重新授权登录；不会重复提交文章。"
    if status in {"publish_uncertain", "pending_reconciliation", "draft_reconciliation_required"} or delivery.get("status") == "uncertain":
        return f"【{platform}】提交结果待核", "平台可能已收到提交，结果尚未确认。请核对后台；自动流程保留防重复状态，不再次提交。"
    if result.get("retry_status") == "daily_budget_exhausted":
        return f"【{platform}】今日自动处理额度已用完", "今天尚未取得新草稿成功记录，运行次数或处理预算已耗尽。已停止补跑，下一执行窗口继续；请查看本次选题和审核报告。"
    if status in {"no_topics", "review_required", "draft_only", "degraded_to_draft"}:
        return f"【{platform}】本次未完成交付", "本次没有合格内容完成交付，可能是素材不足、审核未通过或降为草稿。请查看报告；不会为凑数量绕过审核。"
    if status in {"failed", "error", "blocked", "blocked_by_ip", "partial_success"} or result.get("metrics_error") or result.get("retry_state_error"):
        return f"【{platform}】任务异常", "自动任务或结果记录异常，请查看运行报告。提交状态不确定时先核对后台，避免重复发文。"
    return None


def daily_alerts(now, toutiao, wechat, retry, account="robot_notes"):
    day = now.date().isoformat()
    alerts = []
    # Allow ninety minutes after the scheduled morning slot, including queues.
    toutiao_deadline = (9, 30) if now.weekday() >= 5 else (8, 30)
    if (now.hour, now.minute) >= toutiao_deadline:
        records = [published_on(toutiao, day, platform, account=account.replace("_", "-"))
                   for platform in ("toutiao", "toutiao_weitoutiao")]
        if not any(records):
            alerts.append(("【今日头条】今日尚无提交记录", "已超过早间发文窗口，目前没有今天的头条文章或微头条提交记录。可能是定时任务漏跑、素材或审核受阻；请检查云端运行记录。"))
        elif any(row and row.get("status") == "publish_uncertain" for row in records):
            alerts.append(("【今日头条】提交结果待核", "今日有提交结果不确定的记录，请只读核对后台，避免重复发表。"))
    if (now.hour, now.minute) >= (8, 47):
        rows = [row for row in wechat.get("runs", [])
                if row.get("mode") == "draft" and (parse_time(row.get("started_at", "")) or datetime.min.replace(tzinfo=NOTIFICATION_TIMEZONE)).date().isoformat() == day]
        reservations = retry.get("days", {}).get(day, {}).get("runs", {})
        rows += list(reservations.values())
        if any(row.get("draft_delivery", {}).get("status") == "uncertain" for row in rows):
            alerts.append(("【公众号】提交结果待核", "今日存草稿结果不确定，自动流程已停止重复写入。请核对草稿箱。"))
        elif not any(row.get("draft_delivery", {}).get("status") == "saved" for row in rows):
            alerts.append(("【公众号】今日尚无新草稿", "已超过早间存稿窗口，目前没有今天的新草稿保存成功证据。13:17定时入口会在剩余预算允许时继续；请检查登录、选题和审核报告。"))
    return alerts


def inspect_run(api, run, now):
    platform = WORKFLOWS.get(run.get("name"))
    if not platform or run.get("head_branch") != "main":
        return []
    created = parse_time(run.get("created_at", ""))
    if not created or now - created > timedelta(hours=24):
        return []  # An old completion must not claim a failure today.
    link = run.get("html_url", "")
    if run.get("status") != "completed":
        if now - created > timedelta(minutes=90):
            return [(f"【{platform}】云端任务积压", "任务排队或执行已超过90分钟，请检查云端队列。\n" + link)]
        return []
    results = api.results(run) if run.get("name") != "WeChat Content Metrics" else []
    alerts = [alert for result in results if (alert := outcome_alert(platform, result))]
    if run.get("conclusion") not in {"success", "skipped", "neutral"} and not alerts:
        alerts.append((f"【{platform}】任务异常", "云端任务失败、超时或被取消，请查看步骤记录；登录失效需重新授权，提交结果不确定需先核对后台。"))
    return [(title, message + "\n" + link) for title, message in alerts]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", choices=["restore", "save"])
    parser.add_argument("--test-notification", action="store_true")
    args = parser.parse_args()
    api = GitHub()
    try:
        if args.ledger:
            (restore_ledger if args.ledger == "restore" else save_ledger)(api)
            return 0
        restore_ledger(api)
        notifier = NotificationService(dict(os.environ), fallback_log_dir=str(LOCAL_LEDGER.parent))
        if not notifier.is_configured:
            raise RuntimeError("No cloud notification channel configured")
        now = datetime.now(NOTIFICATION_TIMEZONE)
        alerts, errors = [], []
        if args.test_notification:
            alerts.append(("【发文巡检】云端提醒测试", "公众号和今日头条的云端发文巡检已启用：登录失效、任务异常、当日缺少交付记录会提醒你。公众号仍自动存草稿，最终发表由你操作。"))
        else:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
            if event.get("workflow_run"):
                runs = [event["workflow_run"]]
            else:
                recent = api.request("actions/runs?branch=main&per_page=30")["workflow_runs"]
                latest = {}
                runs = []
                for run in recent:
                    if run.get("name") not in WORKFLOWS:
                        continue
                    if run.get("status") != "completed":
                        runs.append(run)
                    elif run["name"] not in latest:
                        latest[run["name"]] = run
                runs.extend(latest.values())
            # An hourly sweep catches missed workflow_run hooks and stuck jobs.
            for run in runs:
                try:
                    alerts.extend(inspect_run(api, run, now))
                except (RuntimeError, ValueError, httpx.HTTPError, zipfile.BadZipFile) as exc:
                    errors.append(type(exc).__name__)
            try:
                alerts.extend(daily_alerts(now,
                    api.state("publish-state", "state/daily_publish_state.json"),
                    api.state("wechat-publish-state", "state/wechat_metrics.json"),
                    api.state("wechat-publish-state", "state/wechat_retry_state.json"),
                    os.environ.get("WATCH_TOUTIAO_ACCOUNT", "robot_notes")))
            except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                errors.append(type(exc).__name__)
            if errors:
                alerts.append(("【发文巡检】证据读取失败", "巡检未能完整读取结果证据，不能判断为已完成或未提交。请查看巡检任务；保留现有防重复状态。"))
        # Group same-category failures; durable title dedup suppresses repeated
        # scans while distinct issues remain visible within the daily quota.
        grouped = {}
        for title, message in alerts:
            grouped.setdefault(title, []).append(message)
        for title, messages in grouped.items():
            notifier.notify_urgent(title, "\n\n".join(dict.fromkeys(messages)) +
                f"\n\n巡检时间：{now:%Y-%m-%d %H:%M}（北京时间）。全部在云端运行，无需保持电脑开机。")
        save_ledger(api)
        entries = notifier._read_log_entries()
        receipts = [{"title": title, "acknowledged": notifier._has_pushed_title_today(title)} for title in grouped]
        failed = any(not row["acknowledged"] for row in receipts)
        # Quota/dedup suppression is visible, not reported as provider success.
        report = {"checked_at": now.isoformat(), "alerts": receipts, "inspection_errors": errors,
                  "pushed_today": notifier._count_pushed_today(now.date()), "ledger_saved": True,
                  "last_delivery_quota_exhausted": bool(entries and entries[-1].get("metadata", {}).get("quota_exhausted"))}
        destination = ROOT / "output/automation/publication_watchdog_result.json"
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 1 if errors or failed else 0
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
