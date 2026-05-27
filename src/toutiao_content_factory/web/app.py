from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from toutiao_content_factory.config import DEFAULT_ACCOUNT_KEY, AppSettings, load_settings, normalize_account_key
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.services.automation import collect_performance_data as scheduled_collect_performance_data
from toutiao_content_factory.services.automation import daily_run as scheduled_daily_run
from toutiao_content_factory.utils.logging_utils import configure_logging
from toutiao_content_factory.web.view_service import WebViewService


@dataclass
class AccountContext:
    settings: AppSettings
    app_service: ContentFactoryAppService
    view_service: WebViewService


def create_app(project_root: Path, account_key: str | None = None) -> FastAPI:
    bootstrap_settings = load_settings(project_root, account_key=account_key)
    bootstrap_settings.ensure_directories()

    app = FastAPI(title="手蓝内容工厂", version="1.4.0")
    app.state.project_root = project_root
    app.state.default_account_key = bootstrap_settings.account_key
    app.state.available_account_keys = bootstrap_settings.available_account_keys
    app.state.account_contexts: dict[str, AccountContext] = {}
    app.state.background_jobs = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-actions")
    app.state.job_lock = threading.Lock()
    app.state.scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1}, daemon=True)

    static_dir = (project_root / "static").resolve()
    templates = Jinja2Templates(directory=str(project_root / "templates" / "web"))

    @app.get("/static/{asset_path:path}")
    def serve_static_asset(asset_path: str):
        target = (static_dir / asset_path).resolve()
        if not target.exists() or not target.is_file() or (target.parent != static_dir and static_dir not in target.parents):
            raise HTTPException(status_code=404, detail="Static asset not found")

        media_type = "application/octet-stream"
        suffix = target.suffix.lower()
        if suffix == ".css":
            media_type = "text/css; charset=utf-8"
        elif suffix == ".js":
            media_type = "application/javascript; charset=utf-8"
        elif suffix == ".svg":
            media_type = "image/svg+xml"
        elif suffix == ".ico":
            media_type = "image/x-icon"
        elif suffix == ".png":
            media_type = "image/png"
        elif suffix in {".jpg", ".jpeg"}:
            media_type = "image/jpeg"
        elif suffix == ".html":
            media_type = "text/html; charset=utf-8"
        return Response(content=target.read_bytes(), media_type=media_type)

    @app.get("/favicon.ico")
    def favicon():
        target = (static_dir / "favicon.ico").resolve()
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="favicon not found")
        return Response(content=target.read_bytes(), media_type="image/x-icon")

    def get_account_context(target_account: str | None) -> AccountContext:
        normalized_key = normalize_account_key(target_account or app.state.default_account_key)
        if normalized_key not in app.state.available_account_keys:
            raise HTTPException(status_code=404, detail=f"Unknown account: {normalized_key}")
        existing = app.state.account_contexts.get(normalized_key)
        if existing is not None:
            return existing

        settings = load_settings(project_root, account_key=normalized_key)
        settings.ensure_directories()
        logger = configure_logging(
            settings.logs_dir,
            settings.logging_level,
            logger_name=f"toutiao_content_factory.{settings.account_slug}",
        )
        context = AccountContext(
            settings=settings,
            app_service=ContentFactoryAppService(settings, logger),
            view_service=WebViewService(settings),
        )
        app.state.account_contexts[normalized_key] = context
        return context

    def current_context(request: Request, account: str | None) -> AccountContext:
        query_account = account or request.query_params.get("account") or app.state.default_account_key
        return get_account_context(query_account)

    def list_account_options() -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for key in app.state.available_account_keys:
            ctx = get_account_context(key)
            result.append({"key": ctx.settings.account_key, "label": ctx.settings.account_label})
        return result

    def build_manager_payload(current_account_key: str) -> dict[str, object]:
        task_catalog = WebViewService.load_scheduled_task_catalog()
        accounts: list[dict[str, object]] = []
        for key in app.state.available_account_keys:
            ctx = get_account_context(key)
            accounts.append(
                ctx.view_service.build_account_snapshot(
                    task_catalog=task_catalog,
                    is_current=ctx.settings.account_key == current_account_key,
                )
            )

        summary = {
            "total_accounts": len(accounts),
            "toutiao_ready_accounts": sum(
                1 for item in accounts if bool(item.get("toutiao", {}).get("schedule", {}).get("ready"))
            ),
            "wechat_enabled_accounts": sum(1 for item in accounts if bool(item.get("wechat", {}).get("enabled"))),
            "pending_review_accounts": sum(1 for item in accounts if bool(item.get("has_pending_review"))),
            "attention_accounts": sum(1 for item in accounts if str(item.get("overall_tone", "")) in {"warn", "error"}),
        }
        return {"summary": summary, "accounts": accounts}

    def render(request: Request, template_name: str, ctx: AccountContext, **context: object):
        base_context = {
            "request": request,
            "styles": ctx.view_service.list_styles(),
            "default_style": ctx.settings.default_style,
            "account_key": ctx.settings.account_key,
            "account_label": ctx.settings.account_label,
            "available_accounts": ctx.settings.available_account_keys,
            "account_options": list_account_options(),
            "message": request.query_params.get("message", ""),
            "level": request.query_params.get("level", "info"),
            "account_query": f"account={ctx.settings.account_key}",
        }
        base_context.update(context)
        return templates.TemplateResponse(request, template_name, base_context)

    def redirect(url: str, message: str, account_key_value: str, level: str = "info") -> RedirectResponse:
        query = urlencode({"message": message, "level": level, "account": account_key_value})
        separator = "&" if "?" in url else "?"
        return RedirectResponse(url=f"{url}{separator}{query}", status_code=303)

    def append_notification(
        ctx: AccountContext,
        *,
        level: str,
        title: str,
        message: str,
        notification_type: str,
        data: dict[str, object] | None = None,
    ) -> None:
        notifications_path = ctx.settings.automation_dir / "notifications.json"
        notifications_path.parent.mkdir(parents=True, exist_ok=True)
        with app.state.job_lock:
            items: list[dict[str, object]] = []
            if notifications_path.exists():
                try:
                    existing = json.loads(notifications_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = []
                if isinstance(existing, list):
                    items = [item for item in existing if isinstance(item, dict)]
                elif isinstance(existing, dict):
                    items = [existing]

            entry = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "level": level,
                "type": notification_type,
                "title": title,
                "message": message,
                "data": data or {},
            }
            payload = [entry, *items[:49]]
            notifications_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _parse_daily_run_time(raw_value: str) -> tuple[int, int]:
        try:
            hour_text, minute_text = raw_value.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except ValueError as exc:
            raise ValueError(
                f"Invalid automation.daily_run_times value: {raw_value!r}. Expected HH:MM format."
            ) from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(
                f"Invalid automation.daily_run_times value: {raw_value!r}. Hour or minute is out of range."
            )
        return hour, minute

    def _scheduler_state_label() -> str:
        scheduler: BackgroundScheduler = app.state.scheduler
        if scheduler.state == STATE_RUNNING:
            return "running"
        if scheduler.state == STATE_PAUSED:
            return "paused"
        return "stopped"

    def _windows_schedule_owns_performance_collection() -> bool:
        return str(os.environ.get("TOUTIAO_SCHEDULE_SOURCE", "")).strip().lower() == "windows"

    def _scheduler_jobs() -> list[dict[str, object]]:
        scheduler: BackgroundScheduler = app.state.scheduler
        jobs: list[dict[str, object]] = []
        for job in scheduler.get_jobs():
            if not (job.id.startswith("daily_run:") or job.id.startswith("collect_performance_data:")):
                continue
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                }
            )
        return jobs

    def run_scheduled_daily_run_for_account(account_key_value: str) -> None:
        ctx = get_account_context(account_key_value)
        try:
            scheduled_daily_run(ctx.settings, ctx.app_service.logger)
            append_notification(
                ctx,
                level="info",
                title="scheduled daily_run finished",
                message="Scheduled daily run finished successfully.",
                notification_type="scheduled_daily_run_finished",
                data={"account_key": ctx.settings.account_key},
            )
        except Exception as exc:
            append_notification(
                ctx,
                level="error",
                title="scheduled daily_run failed",
                message=str(exc),
                notification_type="scheduled_daily_run_failed",
                data={"account_key": ctx.settings.account_key},
            )
            raise

    def run_scheduled_performance_collection_for_account(account_key_value: str) -> None:
        ctx = get_account_context(account_key_value)
        try:
            scheduled_collect_performance_data(ctx.settings, ctx.app_service.logger)
            append_notification(
                ctx,
                level="info",
                title="scheduled performance collection finished",
                message="Scheduled performance collection finished successfully.",
                notification_type="scheduled_performance_collection_finished",
                data={"account_key": ctx.settings.account_key},
            )
        except Exception as exc:
            append_notification(
                ctx,
                level="error",
                title="scheduled performance collection failed",
                message=str(exc),
                notification_type="scheduled_performance_collection_failed",
                data={"account_key": ctx.settings.account_key},
            )
            raise

    def register_scheduler_jobs() -> list[str]:
        scheduler: BackgroundScheduler = app.state.scheduler
        expected_job_ids: set[str] = set()

        for account_key_value in app.state.available_account_keys:
            ctx = get_account_context(account_key_value)
            for index, raw_time in enumerate(ctx.settings.automation.daily_run_times, start=1):
                hour, minute = _parse_daily_run_time(raw_time)
                job_id = f"daily_run:{ctx.settings.account_key}:{index}"
                scheduler.add_job(
                    run_scheduled_daily_run_for_account,
                    CronTrigger(hour=hour, minute=minute),
                    id=job_id,
                    name=f"daily_run[{ctx.settings.account_key}] {raw_time}",
                    replace_existing=True,
                    kwargs={"account_key_value": ctx.settings.account_key},
                    misfire_grace_time=1800,
                )
                expected_job_ids.add(job_id)

            performance_job_id = f"collect_performance_data:{ctx.settings.account_key}"
            if not _windows_schedule_owns_performance_collection():
                scheduler.add_job(
                    run_scheduled_performance_collection_for_account,
                    CronTrigger(hour=1, minute=0),
                    id=performance_job_id,
                    name=f"collect_performance_data[{ctx.settings.account_key}] 01:00",
                    replace_existing=True,
                    kwargs={"account_key_value": ctx.settings.account_key},
                    misfire_grace_time=1800,
                )
                expected_job_ids.add(performance_job_id)

        for job in scheduler.get_jobs():
            if (
                job.id.startswith("daily_run:")
                or job.id.startswith("collect_performance_data:")
            ) and job.id not in expected_job_ids:
                scheduler.remove_job(job.id)

        return sorted(expected_job_ids)

    def run_in_background(
        ctx: AccountContext,
        *,
        job_name: str,
        action,
        kwargs: dict[str, object] | None = None,
    ) -> None:
        job_kwargs = kwargs or {}

        def worker() -> None:
            try:
                action(**job_kwargs)
                append_notification(
                    ctx,
                    level="info",
                    title=f"{job_name} finished",
                    message=f"{job_name} finished successfully.",
                    notification_type="background_job_finished",
                    data={"account_key": ctx.settings.account_key, "job_name": job_name},
                )
            except Exception as exc:
                append_notification(
                    ctx,
                    level="error",
                    title=f"{job_name} failed",
                    message=str(exc),
                    notification_type="background_job_failed",
                    data={"account_key": ctx.settings.account_key, "job_name": job_name},
                )
                raise

        app.state.background_jobs.submit(worker)

    def _collect_log_files(ctx: AccountContext, *, limit: int = 5) -> list[Path]:
        if not ctx.settings.logs_dir.exists():
            return []
        files = [item for item in ctx.settings.logs_dir.rglob("*.log") if item.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return files[:limit]

    @app.on_event("startup")
    def start_scheduler_on_startup() -> None:
        scheduler: BackgroundScheduler = app.state.scheduler
        schedule_mode = "windows" if _windows_schedule_owns_performance_collection() else "web"
        for account_key_value in app.state.available_account_keys:
            ctx = get_account_context(account_key_value)
            ctx.app_service.logger.info(
                "Web scheduler startup summary: mode=%s account=%s",
                schedule_mode,
                ctx.settings.account_key,
            )
        register_scheduler_jobs()
        if scheduler.state == STATE_STOPPED:
            scheduler.start()

    @app.on_event("shutdown")
    def stop_scheduler_on_shutdown() -> None:
        scheduler: BackgroundScheduler = app.state.scheduler
        if scheduler.state != STATE_STOPPED:
            scheduler.shutdown(wait=False)

    @app.get("/", name="dashboard")
    def dashboard(request: Request, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        return render(request, "accounts.html", ctx, manager=build_manager_payload(ctx.settings.account_key))

    @app.get("/dashboard", name="single_dashboard")
    def single_dashboard(request: Request, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        manager = build_manager_payload(ctx.settings.account_key)
        account_overviews = manager.get("accounts", []) if isinstance(manager, dict) else []
        dashboard_payload = ctx.view_service.load_dashboard()
        if isinstance(dashboard_payload, dict):
            dashboard_payload["account_overviews"] = account_overviews
        return render(
            request,
            "dashboard_console.html",
            ctx,
            dashboard=dashboard_payload,
            account_overviews=account_overviews,
        )

    @app.get("/accounts", name="accounts_page")
    def accounts_page(request: Request, account: str | None = Query(default=None)):
        return dashboard(request, account)

    @app.post("/actions/refresh-dashboard")
    def refresh_dashboard(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        return redirect("/dashboard", "首页数据已刷新。", account)

    @app.post("/actions/publish-preflight")
    def publish_preflight(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        ctx.app_service.collect_publish_preflight()
        return redirect("/dashboard", "发布前置检查已刷新。", ctx.settings.account_key)

    @app.post("/actions/collect-platform-health")
    def collect_platform_health(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        ctx.app_service.collect_platform_health()
        return redirect("/dashboard", "平台健康检查已刷新。", ctx.settings.account_key)

    @app.post("/actions/init-login")
    def init_login(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        script = (ctx.settings.project_root / "scripts" / "init_publish_login.ps1").resolve()
        subprocess.Popen(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Account",
                ctx.settings.account_key,
            ],
            cwd=str(ctx.settings.project_root),
        )
        return redirect("/dashboard", "已唤起多平台登录窗口。", ctx.settings.account_key)

    @app.get("/topics", name="topics_page")
    def topics_page(request: Request, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        return render(request, "topics.html", ctx, topics=ctx.view_service.load_daily_topics() or {})

    @app.get("/runs", name="runs_page")
    def runs_page(request: Request, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        return render(request, "runs.html", ctx, runs=ctx.view_service.list_runs())

    @app.get("/published", name="published_page")
    def published_page(request: Request, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        return render(request, "published.html", ctx, published_articles=ctx.view_service.list_published_articles(limit=12))

    @app.get("/runs/{run_name}", name="run_detail")
    def run_detail(request: Request, run_name: str, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        return render(request, "run_detail.html", ctx, detail=ctx.view_service.load_run_detail(run_name))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/api/scheduler/start")
    def api_scheduler_start():
        scheduler: BackgroundScheduler = app.state.scheduler
        register_scheduler_jobs()
        if scheduler.state == STATE_STOPPED:
            scheduler.start()
        elif scheduler.state == STATE_PAUSED:
            scheduler.resume()
        return {"ok": True, "status": _scheduler_state_label(), "jobs": _scheduler_jobs()}

    @app.post("/api/scheduler/pause")
    def api_scheduler_pause():
        scheduler: BackgroundScheduler = app.state.scheduler
        if scheduler.state == STATE_STOPPED:
            scheduler.start(paused=True)
        elif scheduler.state == STATE_RUNNING:
            scheduler.pause()
        return {"ok": True, "status": _scheduler_state_label(), "jobs": _scheduler_jobs()}

    @app.post("/actions/collect-topics")
    def collect_topics(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        run_in_background(ctx, job_name="collect_topics", action=ctx.app_service.collect_topics)
        return redirect("/topics", "热点采集已加入后台任务。", ctx.settings.account_key)

    @app.post("/actions/manual-run")
    def manual_run(
        account: str = Form(default=DEFAULT_ACCOUNT_KEY),
        paste_text: str = Form(""),
        style: str = Form(""),
        dry_run: bool = Form(False),
        refresh_cache: bool = Form(False),
        include_input_dir: bool = Form(False),
    ):
        ctx = get_account_context(account)
        run_in_background(
            ctx,
            job_name="manual_run",
            action=ctx.app_service.manual_run,
            kwargs={
                "paste_text": paste_text or None,
                "style_override": style or None,
                "dry_run": dry_run,
                "refresh_cache": refresh_cache,
                "include_input_dir": include_input_dir,
            },
        )
        return redirect("/dashboard", "手动生成已加入后台任务。", ctx.settings.account_key)

    @app.post("/actions/auto-run")
    def auto_run(
        account: str = Form(default=DEFAULT_ACCOUNT_KEY),
        style: str = Form(""),
        dry_run: bool = Form(False),
        refresh_cache: bool = Form(False),
        topic_rank: str = Form(""),
        topic_title: str = Form(""),
        collect_topics: bool = Form(False),
        no_archive: bool = Form(False),
    ):
        ctx = get_account_context(account)
        run_in_background(
            ctx,
            job_name="auto_run",
            action=ctx.app_service.auto_run,
            kwargs={
                "style_override": style or None,
                "dry_run": dry_run,
                "refresh_cache": refresh_cache,
                "topic_rank": int(topic_rank) if topic_rank.strip() else None,
                "topic_title": topic_title or None,
                "collect_if_missing": True,
                "force_collect": collect_topics,
                "no_archive": no_archive,
            },
        )
        return redirect("/dashboard", "自动选题生成已加入后台任务。", ctx.settings.account_key)

    @app.post("/actions/daily-run")
    def daily_run(
        account: str = Form(default=DEFAULT_ACCOUNT_KEY),
        style: str = Form(""),
        dry_run: bool = Form(False),
        refresh_cache: bool = Form(False),
        topic_rank: str = Form(""),
        topic_title: str = Form(""),
        no_archive: bool = Form(False),
    ):
        ctx = get_account_context(account)
        run_in_background(
            ctx,
            job_name="daily_run",
            action=ctx.app_service.daily_run,
            kwargs={
                "style_override": style or None,
                "dry_run": dry_run,
                "refresh_cache": refresh_cache,
                "topic_rank": int(topic_rank) if topic_rank.strip() else None,
                "topic_title": topic_title or None,
                "no_archive": no_archive,
            },
        )
        return redirect("/dashboard", "完整日更流程已加入后台任务。", ctx.settings.account_key)

    @app.post("/runs/{run_name}/prepare-review")
    def prepare_review(run_name: str, account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        ctx.app_service.prepare_review(str(ctx.settings.runs_dir / run_name))
        return redirect(f"/runs/{run_name}", "审核包已更新。", ctx.settings.account_key)

    @app.post("/runs/{run_name}/refresh-publish-readiness")
    def refresh_publish_readiness(run_name: str, account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        ctx.app_service.evaluate_latest_run_publish_readiness(str(ctx.settings.runs_dir / run_name))
        return redirect(f"/runs/{run_name}", "发布前预检与就绪评分已刷新。", ctx.settings.account_key)

    @app.post("/runs/{run_name}/approve")
    def approve_review(
        run_name: str,
        account: str = Form(default=DEFAULT_ACCOUNT_KEY),
        selected_title_index: str = Form(""),
        selected_title: str = Form(""),
        review_notes: str = Form(""),
        auto_publish: bool = Form(False),
    ):
        ctx = get_account_context(account)
        ctx.app_service.approve_run(
            str(ctx.settings.runs_dir / run_name),
            selected_title=selected_title or None,
            selected_title_index=int(selected_title_index) if selected_title_index.strip() else None,
            review_notes=review_notes or None,
            auto_publish=auto_publish,
        )
        return redirect(f"/runs/{run_name}", "审核已通过，发布包已导出。", ctx.settings.account_key)

    @app.post("/runs/{run_name}/publish")
    def publish_run(run_name: str, account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        run_in_background(
            ctx,
            job_name="publish_run",
            action=ctx.app_service.publish_run,
            kwargs={"run_dir": str(ctx.settings.runs_dir / run_name)},
        )
        return redirect(f"/runs/{run_name}", "发布任务已加入后台队列。", ctx.settings.account_key)

    @app.post("/runs/{run_name}/reject")
    def reject_review(run_name: str, account: str = Form(default=DEFAULT_ACCOUNT_KEY), review_notes: str = Form("")):
        ctx = get_account_context(account)
        ctx.app_service.reject_run(str(ctx.settings.runs_dir / run_name), review_notes=review_notes or None)
        return redirect(f"/runs/{run_name}", "审核已驳回。", ctx.settings.account_key, level="warn")

    @app.post("/runs/{run_name}/retry")
    def retry_run_publish(run_name: str, account: str = Form(default=DEFAULT_ACCOUNT_KEY), platform: str = Form(...)):
        ctx = get_account_context(account)
        run_in_background(
            ctx,
            job_name=f"retry_{platform}_publish",
            action=ctx.app_service.retry_platform_publish,
            kwargs={"run_dir": str(ctx.settings.runs_dir / run_name), "platform": platform},
        )
        return redirect(f"/runs/{run_name}", f"{platform} 补发已加入后台队列。", ctx.settings.account_key)

    @app.post("/api/runs/{run_name}/retry")
    def retry_run_publish_api(run_name: str, account: str = Form(default=DEFAULT_ACCOUNT_KEY), platform: str = Form(...)):
        ctx = get_account_context(account)
        result = ctx.app_service.retry_platform_publish(str(ctx.settings.runs_dir / run_name), platform=platform)
        return {"ok": True, "platform": platform, "run_name": run_name, "result": result}

    @app.post("/api/publish/retry")
    def retry_publish_by_query(
        run_id: str = Form(...),
        account: str = Form(default=DEFAULT_ACCOUNT_KEY),
        platform: str = Form(...),
    ):
        ctx = get_account_context(account)
        result = ctx.app_service.retry_platform_publish(str(ctx.settings.runs_dir / run_id), platform=platform)
        return {"ok": True, "platform": platform, "run_name": run_id, "result": result}

    @app.get("/api/logs/list")
    def api_logs_list(account: str = Query(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        files = _collect_log_files(ctx, limit=5)
        items = []
        for path in files:
            relative = path.relative_to(ctx.settings.logs_dir.resolve()).as_posix()
            stat = path.stat()
            items.append(
                {
                    "name": relative,
                    "display_name": path.name,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "url": f"/artifacts/{ctx.settings.account_key}/logs/{relative}",
                }
            )
        return {"ok": True, "items": items}

    @app.get("/api/logs/view")
    def api_logs_view(
        file_name: str = Query(...),
        account: str = Query(default=DEFAULT_ACCOUNT_KEY),
        keyword: str = Query(default=""),
    ):
        ctx = get_account_context(account)
        root = ctx.settings.logs_dir.resolve()
        target = (ctx.settings.logs_dir / file_name).resolve()
        if not str(target).startswith(str(root)):
            raise HTTPException(status_code=400, detail="Log path is outside the logs directory.")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Log file not found.")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        if keyword.strip():
            filtered = [line for line in lines if keyword.lower() in line.lower()]
            lines = filtered[-100:]
        else:
            lines = lines[-100:]
        return {"ok": True, "file_name": file_name, "content": "\n".join(lines)}

    @app.get("/artifacts/{account_key_value}/output/{path:path}")
    def account_output_artifact(account_key_value: str, path: str):
        ctx = get_account_context(account_key_value)
        target = (ctx.settings.output_dir / path).resolve()
        if not target.exists() or not str(target).startswith(str(ctx.settings.output_dir.resolve())):
            raise HTTPException(status_code=404, detail="Artifact not found.")
        return FileResponse(target)

    @app.get("/artifacts/{account_key_value}/runs/{run_name}/{path:path}")
    def account_run_artifact(account_key_value: str, run_name: str, path: str):
        ctx = get_account_context(account_key_value)
        root = (ctx.settings.runs_dir / run_name).resolve()
        target = (root / path).resolve()
        if not target.exists() or not str(target).startswith(str(root)):
            raise HTTPException(status_code=404, detail="Artifact not found.")
        return FileResponse(target)

    @app.get("/artifacts/{account_key_value}/logs/{path:path}")
    def account_log_artifact(account_key_value: str, path: str):
        ctx = get_account_context(account_key_value)
        root = ctx.settings.logs_dir.resolve()
        target = (ctx.settings.logs_dir / path).resolve()
        if not target.exists() or not str(target).startswith(str(root)):
            raise HTTPException(status_code=404, detail="Artifact not found.")
        return FileResponse(target)

    @app.get("/api/logs/stream")
    async def stream_logs(
        request: Request,
        account: str = Query(default=DEFAULT_ACCOUNT_KEY),
        path: str | None = Query(default=None),
    ):
        ctx = get_account_context(account)
        root = ctx.settings.logs_dir.resolve()

        if path:
            target = (ctx.settings.logs_dir / path).resolve()
            if not str(target).startswith(str(root)):
                raise HTTPException(status_code=400, detail="Log path is outside the logs directory.")
            if not target.exists():
                raise HTTPException(status_code=404, detail="Log file not found.")
        else:
            candidates = sorted(
                [item for item in ctx.settings.logs_dir.rglob("*.log") if item.is_file()],
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                raise HTTPException(status_code=404, detail="No log files found.")
            target = candidates[0]

        async def event_stream():
            position = 0
            while True:
                if await request.is_disconnected():
                    break
                if not target.exists():
                    yield "event: error\ndata: Log file no longer exists.\n\n"
                    break
                try:
                    current_size = target.stat().st_size
                    if current_size < position:
                        position = 0
                    with target.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position = handle.tell()
                    if chunk:
                        payload = json.dumps(
                            {
                                "path": str(target.relative_to(ctx.settings.logs_dir)),
                                "content": chunk,
                            },
                            ensure_ascii=False,
                        )
                        yield f"data: {payload}\n\n"
                    else:
                        yield ": keep-alive\n\n"
                except Exception as exc:
                    payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    yield f"event: error\ndata: {payload}\n\n"
                    break
                await asyncio.sleep(1.0)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app
