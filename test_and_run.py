from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.browser_launch_guard import prepare_browser_profile
from toutiao_content_factory.services.compliance_service import get_proxy_server
from toutiao_content_factory.services.wechat_service import WechatPublishService
from toutiao_content_factory.utils.browser_utils import (
    BrowserTimeout,
    apply_stealth,
    build_persistent_context_options,
)
from toutiao_content_factory.utils.logging_utils import configure_logging

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover
    sync_playwright = None


DUMMY_MARKDOWN = """# 手蓝 AI 内容工厂验证稿

## 先看结论

过去 **30天**，内容转化率提升到 **18.7%**，私域线索成本下降 **32%**。如果这个判断成立，接下来最关键的是验证渠道质量，而不是盲目加预算。

### 关键数据表

| 指标 | 优化前 | 优化后 | 变化 |
| --- | --- | --- | --- |
| 阅读完成率 | 24% | 37% | +13% |
| 留资率 | 3.8% | 5.1% | +1.3% |
| 私域加粉成本 | 86元 | 58元 | -32% |

:::data
# 数据看板
阅读完成率: 37%
留资率: 5.1%
私域加粉成本: 58元
说明：这些数据说明排版密度与转化节奏之间存在明显关联。
:::

:::alert
# 风险提醒
如果行业关键词堆叠过多，或者句子连续出现高频百分比，平台可能会把正文判定为低质量营销内容。
:::

### 下一步动作

1. 先检查标题与首屏摘要是否足够克制。
2. 再看高亮数字有没有过度使用主题色。
3. 最后验证微信页脚 CTA 是否与正文风格一致。
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate WeChat layout, stealth browser, and safely start the pipeline.")
    parser.add_argument("--account", type=str, default=None, help="Account key in config.yaml. Defaults to configured default account.")
    parser.add_argument(
        "--stealth-url",
        type=str,
        default="https://bot.sannysoft.com/",
        help="Public anti-bot detection page to open for stealth verification.",
    )
    parser.add_argument(
        "--session-headless",
        action="store_true",
        help="Run WeChat session health check in headless mode. Default is headed for visibility.",
    )
    return parser


def resolve_profile_dir(raw_value: str, project_root: Path) -> Path:
    path = Path(raw_value)
    if not path.is_absolute():
        path = project_root / path
    return path


def prompt_yes_no(message: str, *, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{message} [{suffix}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def wait_for_manual_preview(path: Path) -> bool:
    print("")
    print("排版预览已生成。")
    print(f"请手动打开 HTML 文件检查微信排版效果：{path}")
    input("预览完成后按回车继续...")
    return prompt_yes_no("排版效果是否通过？", default=False)


def write_preview_html(path: Path, title: str, body_html: str, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
</head>
<body style="margin:0;background:#f3f5f7;color:#1f2329;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
  <div style="max-width:860px;margin:0 auto;padding:32px 16px 48px;">
    <div style="margin-bottom:18px;padding:14px 16px;border-radius:12px;background:#ffffff;border:1px solid #dbe1e8;box-shadow:0 6px 18px rgba(0,0,0,0.05);">
      <p style="margin:0 0 6px;font-size:18px;font-weight:800;">微信排版引擎测试输出</p>
      <p style="margin:0;font-size:13px;color:#5f6b7a;">render provider: {meta.get("provider", "unknown")} | metadata: {json.dumps(meta, ensure_ascii=False)}</p>
    </div>
    <div style="padding:28px 20px;border-radius:18px;background:#ffffff;box-shadow:0 18px 44px rgba(0,0,0,0.08);">
      {body_html}
    </div>
  </div>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def run_layout_preview(settings, logger) -> bool:
    logger.info("Step 1/4: validating WeChat layout engine with dummy markdown.")
    service = WechatPublishService(settings, logger)
    title = "手蓝 AI 内容工厂验证稿"
    html_output, layout_meta = service._render_article_html_via_backend(
        title=title,
        article=DUMMY_MARKDOWN,
        article_markdown=DUMMY_MARKDOWN,
        inline_image_urls=[],
    )
    output_path = settings.project_root / ".runtime" / "test_wechat_output.html"
    write_preview_html(output_path, title, html_output, layout_meta)
    logger.info("WeChat layout preview saved to %s", output_path)
    return wait_for_manual_preview(output_path)


def run_stealth_verification(settings, logger, target_url: str) -> bool:
    if sync_playwright is None:
        raise RuntimeError("Playwright is not installed. Run `pip install playwright` and `playwright install chromium` first.")

    logger.info("Step 2/4: validating stealth browser startup against %s", target_url)
    profile_dir = resolve_profile_dir(settings.publishing.user_data_dir, settings.project_root)
    profile_dir.mkdir(parents=True, exist_ok=True)
    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logger,
        label="test-and-run:stealth",
        extra_paths=[settings.account_runtime_dir],
    )
    proxy_server = get_proxy_server()
    launch_options, fingerprint = build_persistent_context_options(
        account_key=f"{settings.account_key}-stealth-check",
        profile_dir=profile_dir,
        proxy_server=proxy_server,
        headless=False,
        channel=settings.publishing.channel or None,
        executable_path=settings.publishing.executable_path or None,
        timezone_id="Asia/Shanghai",
        locale="zh-CN",
    )
    logger.info("Stealth browser fingerprint: viewport=%s ua=%s proxy=%s", fingerprint.viewport, fingerprint.user_agent, proxy_server or "(none)")

    with BrowserTimeout(seconds=90.0):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                **launch_options,
            )
            try:
                apply_stealth(browser, fingerprint=fingerprint)
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(target_url, wait_until="domcontentloaded", timeout=max(settings.publishing.wait_timeout_ms, 30000))
                print("")
                print("Stealth 验证页面已打开。")
                print("请重点检查页面中的 WebDriver Detection / Stealth Check / Fingerprint 暴露项。")
                print("目标状态通常应接近：WebDriver Detection = Failed 或未命中自动化特征。")
                for remaining in range(15, 0, -1):
                    print(f"页面将在 {remaining:02d} 秒后关闭，请观察检测结果...", end="\r", flush=True)
                    page.wait_for_timeout(1000)
                print("")
                return prompt_yes_no("Stealth 检查是否通过？", default=False)
            finally:
                browser.close()


def run_wechat_session_health_check(settings, logger, *, headless: bool) -> bool:
    if sync_playwright is None:
        raise RuntimeError("Playwright is not installed. Run `pip install playwright` and `playwright install chromium` first.")

    logger.info("Step 3/4: validating WeChat session health.")
    service = WechatPublishService(settings, logger)
    profile_dir = service._resolve_browser_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    prepare_browser_profile(
        profile_dir=profile_dir,
        project_root=settings.project_root,
        logger=logger,
        label="test-and-run:wechat-session",
        extra_paths=[settings.account_runtime_dir],
    )
    launch_options, fingerprint = build_persistent_context_options(
        account_key=f"{settings.account_key}-wechat-session",
        profile_dir=profile_dir,
        headless=headless,
        channel=settings.publishing.channel or None,
        executable_path=settings.publishing.executable_path or None,
        timezone_id="Asia/Shanghai",
        locale="zh-CN",
    )

    with BrowserTimeout(seconds=120.0):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                **launch_options,
            )
            try:
                apply_stealth(browser, fingerprint=fingerprint)
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(
                    settings.wechat.base_url,
                    wait_until="domcontentloaded",
                    timeout=max(settings.publishing.wait_timeout_ms, 20000),
                )
                page.wait_for_timeout(2500)
                if service._browser_session_ready(page):
                    print("【WeChat Session Active】")
                    logger.info("WeChat session is active. Current URL: %s", page.url)
                    return True

                print("【WeChat Session Expired】 当前登录态已失效，请扫码重新登录。")
                logger.warning("WeChat session appears inactive. Current URL: %s", page.url)
                if not headless:
                    if prompt_yes_no("是否现在在已打开的页面完成扫码，并在完成后重新检测？", default=False):
                        input("扫码完成后按回车重新检测...")
                        page.wait_for_timeout(2000)
                        page.reload(wait_until="domcontentloaded", timeout=max(settings.publishing.wait_timeout_ms, 20000))
                        page.wait_for_timeout(2500)
                        if service._browser_session_ready(page):
                            print("【WeChat Session Active】")
                            logger.info("WeChat session became active after manual login.")
                            return True
                return False
            finally:
                browser.close()


def run_pipeline_subprocess(settings, *, dry_run: bool) -> int:
    command = [
        sys.executable,
        "main.py",
        "--account",
        settings.account_key,
        "--daily-run",
        "--refresh-cache",
    ]
    if dry_run:
        command.append("--dry-run")
    print("")
    print("即将执行命令：")
    print(" ".join(command))
    return subprocess.call(command, cwd=str(settings.project_root))


def choose_next_action(settings) -> int:
    print("")
    print("全部验证通过。请选择下一步：")
    print("[1] 仅预览排版与指纹")
    print("[2] 模拟跑一次 Dry Run（只生成不发布）")
    print("[3] 正式开始 Daily Run")

    while True:
        choice = input("请输入 1 / 2 / 3: ").strip()
        if choice == "1":
            print("已完成预览验证，不执行流水线。")
            return 0
        if choice == "2":
            return run_pipeline_subprocess(settings, dry_run=True)
        if choice == "3":
            return run_pipeline_subprocess(settings, dry_run=False)
        print("输入无效，请重新输入 1 / 2 / 3。")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    settings = load_settings(PROJECT_ROOT, account_key=args.account)
    settings.ensure_directories()
    logger = configure_logging(
        settings.logs_dir,
        level=settings.logging_level,
        logger_name="test_and_run",
    )

    logger.info("Starting test_and_run for account=%s", settings.account_key)
    try:
        layout_ok = run_layout_preview(settings, logger)
        if not layout_ok:
            logger.error("Layout preview was not approved by the operator.")
            return 1

        stealth_ok = run_stealth_verification(settings, logger, args.stealth_url)
        if not stealth_ok:
            logger.error("Stealth verification was not approved by the operator.")
            return 1

        wechat_ok = run_wechat_session_health_check(
            settings,
            logger,
            headless=args.session_headless,
        )
        if not wechat_ok:
            logger.error("WeChat session health check did not pass.")
            return 1

        return choose_next_action(settings)
    except KeyboardInterrupt:
        print("\n操作已中断。")
        return 130
    except Exception as exc:
        logger.exception("test_and_run failed: %s", exc)
        print(f"\n执行失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
