from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from toutiao_content_factory.config import DEFAULT_ACCOUNT_KEY, AppSettings, load_settings, normalize_account_key
from toutiao_content_factory.services.app_service import ContentFactoryAppService
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

    app = FastAPI(title="?????????", version="1.3.0")
    app.state.project_root = project_root
    app.state.default_account_key = bootstrap_settings.account_key
    app.state.available_account_keys = bootstrap_settings.available_account_keys
    app.state.account_contexts: dict[str, AccountContext] = {}

    app.mount("/static", StaticFiles(directory=str(project_root / "static")), name="static")

    templates = Jinja2Templates(directory=str(project_root / "templates" / "web"))

    def get_account_context(target_account: str | None) -> AccountContext:
        normalized_key = normalize_account_key(target_account or app.state.default_account_key)
        if normalized_key not in app.state.available_account_keys:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown account: {normalized_key}",
            )
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

    def inject_account(url: str, account_key_value: str) -> str:
        if "?" in url:
            return f"{url}&account={account_key_value}"
        return f"{url}?account={account_key_value}"

    def list_account_options() -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for key in app.state.available_account_keys:
            ctx = get_account_context(key)
            result.append(
                {
                    "key": ctx.settings.account_key,
                    "label": ctx.settings.account_label,
                }
            )
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
            "attention_accounts": sum(
                1 for item in accounts if str(item.get("overall_tone", "")) in {"warn", "error"}
            ),
        }
        return {"summary": summary, "accounts": accounts}

    def render(
        request: Request,
        template_name: str,
        ctx: AccountContext,
        **context: object,
    ):
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

    @app.get("/", name="dashboard")
    def dashboard(request: Request, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        return render(
            request,
            "accounts.html",
            ctx,
            manager=build_manager_payload(ctx.settings.account_key),
        )

    @app.get("/accounts", name="accounts_page")
    def accounts_page(request: Request, account: str | None = Query(default=None)):
        return dashboard(request, account)

    @app.post("/actions/refresh-dashboard")
    def refresh_dashboard(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        return redirect("/", "首页数据已刷新。", account)

    @app.post("/actions/publish-preflight")
    def publish_preflight(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        ctx.app_service.collect_publish_preflight()
        return redirect("/", "发布前置检查已刷新。", ctx.settings.account_key)

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
        return render(
            request,
            "published.html",
            ctx,
            published_articles=ctx.view_service.list_published_articles(limit=12),
        )

    @app.get("/runs/{run_name}", name="run_detail")
    def run_detail(request: Request, run_name: str, account: str | None = Query(default=None)):
        ctx = current_context(request, account)
        return render(request, "run_detail.html", ctx, detail=ctx.view_service.load_run_detail(run_name))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/actions/collect-topics")
    def collect_topics(account: str = Form(default=DEFAULT_ACCOUNT_KEY)):
        ctx = get_account_context(account)
        ctx.app_service.collect_topics()
        return redirect("/topics", "热点采集完成。", ctx.settings.account_key)

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
        ctx.app_service.manual_run(
            paste_text=paste_text or None,
            style_override=style or None,
            dry_run=dry_run,
            refresh_cache=refresh_cache,
            include_input_dir=include_input_dir,
        )
        return redirect("/", "手动生成完成，结果已写入当前账号 output。", ctx.settings.account_key)

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
        result = ctx.app_service.auto_run(
            style_override=style or None,
            dry_run=dry_run,
            refresh_cache=refresh_cache,
            topic_rank=int(topic_rank) if topic_rank.strip() else None,
            topic_title=topic_title or None,
            collect_if_missing=True,
            force_collect=collect_topics,
            no_archive=no_archive,
        )
        archive_root = result.get("archive_root")
        if archive_root:
            return redirect(f"/runs/{Path(archive_root).name}", "自动选题生成完成。", ctx.settings.account_key)
        return redirect("/", "自动选题生成完成。", ctx.settings.account_key)

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
        result = ctx.app_service.daily_run(
            style_override=style or None,
            dry_run=dry_run,
            refresh_cache=refresh_cache,
            topic_rank=int(topic_rank) if topic_rank.strip() else None,
            topic_title=topic_title or None,
            no_archive=no_archive,
        )
        archive_root = result.get("archive_root")
        if archive_root:
            return redirect(f"/runs/{Path(archive_root).name}", "完整日更流程执行完成。", ctx.settings.account_key)
        return redirect("/", "完整日更流程执行完成。", ctx.settings.account_key)

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
        ctx.app_service.publish_run(str(ctx.settings.runs_dir / run_name))
        return redirect(f"/runs/{run_name}", "已触发自动发布。", ctx.settings.account_key)

    @app.post("/runs/{run_name}/reject")
    def reject_review(
        run_name: str,
        account: str = Form(default=DEFAULT_ACCOUNT_KEY),
        review_notes: str = Form(""),
    ):
        ctx = get_account_context(account)
        ctx.app_service.reject_run(
            str(ctx.settings.runs_dir / run_name),
            review_notes=review_notes or None,
        )
        return redirect(f"/runs/{run_name}", "审核已驳回。", ctx.settings.account_key, level="warn")

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

    return app
