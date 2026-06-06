#!/usr/bin/env python3
"""本地发布+采集脚本 — 从 GitHub Actions 下载云端生成的内容，本地 headless 发布和采集。

用法:
    python scripts/local_publish.py                  # 下载+发布+采集
    python scripts/local_publish.py --toutiao-only   # 只发布头条
    python scripts/local_publish.py --wechat-only    # 只发布微信
    python scripts/local_publish.py --collect-only   # 只采集数据
    python scripts/local_publish.py --skip-collect   # 跳过采集
    python scripts/local_publish.py --dry-run        # 试运行模式

工作流程:
    1. 从 GitHub Actions 最新成功的运行下载 artifact
    2. 解压到本地 output 目录（合并，不覆盖本地已有文件）
    3. 用本地浏览器 profile（有效 cookie）headless 发布到头条/微信
    4. 采集头条/微信后台数据（阅读量、点赞等）

优势:
    - 云端负责内容生成（AI 调用等），不占用本地电脑
    - 本地只做发布和采集（1-3 分钟），headless 模式不弹窗
    - 使用本地 IP 和浏览器 profile，cookie 不会因 IP 变化失效
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
ENV_FILE = PROJECT_ROOT / ".env"

LOG_DIR = PROJECT_ROOT / "logs" / "scheduled"
LOG_DIR.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("local_publish")


def _setup_logging() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"local_publish_{timestamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )
    _logger.info("日志文件: %s", log_file)


def _load_dotenv() -> None:
    if not ENV_FILE.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=False)
    except ImportError:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


def _configure_pythonpath() -> None:
    src_str = str(SRC_DIR)
    existing = os.environ.get("PYTHONPATH", "")
    if src_str not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = f"{src_str}{os.pathsep}{existing}" if existing else src_str
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


def _download_latest_artifact(dest_dir: Path) -> bool:
    """从 GitHub Actions 下载最新成功的 daily-output artifact。"""
    _logger.info("正在从 GitHub Actions 下载最新 artifact...")

    # 获取最新成功的 workflow run
    result = subprocess.run(
        ["gh", "run", "list", "--workflow=daily-run.yml", "--status=success", "--limit=1", "--json", "databaseId"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        _logger.error("获取 workflow run 列表失败: %s", result.stderr)
        return False

    import json
    runs = json.loads(result.stdout)
    if not runs:
        _logger.error("没有找到成功的 workflow run")
        return False

    run_id = runs[0]["databaseId"]
    _logger.info("最新成功的 run ID: %s", run_id)

    # 下载 artifact
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["gh", "run", "download", str(run_id), "--dir", str(dest_dir)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        _logger.error("下载 artifact 失败: %s", result.stderr)
        return False

    _logger.info("Artifact 下载完成: %s", dest_dir)
    return True


def _merge_artifact_to_output(artifact_dir: Path, output_dir: Path) -> bool:
    """将 artifact 中的文件合并到本地 output 目录。"""
    if not artifact_dir.exists():
        _logger.error("Artifact 目录不存在: %s", artifact_dir)
        return False

    # artifact 下载后结构: dest_dir/daily-output-XX/output/ 和 dest_dir/daily-output-XX/logs/
    # 找到 output 子目录
    artifact_output = None
    for sub in artifact_dir.iterdir():
        candidate = sub / "output" if sub.is_dir() else None
        if candidate and candidate.exists():
            artifact_output = candidate
            break

    if not artifact_output:
        # 可能直接就是 output 目录
        if (artifact_dir / "runs").exists():
            artifact_output = artifact_dir
        else:
            _logger.error("Artifact 中未找到 output 目录")
            return False

    output_dir.mkdir(parents=True, exist_ok=True)
    _logger.info("合并 artifact 到本地 output: %s -> %s", artifact_output, output_dir)

    # 复制文件（不覆盖已有文件，保留本地状态）
    copied = 0
    for src_file in artifact_output.rglob("*"):
        if src_file.is_dir():
            continue
        rel = src_file.relative_to(artifact_output)
        dst_file = output_dir / rel
        if dst_file.exists():
            continue
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        copied += 1

    _logger.info("合并完成，复制了 %d 个新文件", copied)
    return True


def _run_publish(toutiao: bool, wechat: bool, dry_run: bool) -> int:
    """运行本地发布。"""
    if dry_run:
        # dry-run 模式用命令行参数
        cmd = [sys.executable, str(PROJECT_ROOT / "main.py")]
        if toutiao:
            cmd.append("--publish-latest-approved")
        if wechat:
            cmd.append("--wechat-publish-latest-approved")
        cmd.append("--dry-run")
        _logger.info("执行发布命令(dry-run): %s", " ".join(cmd))
        env = os.environ.copy()
        env["TOUTIAO_PUBLISH_HEADLESS"] = "1"
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, timeout=600)
        return result.returncode

    # 正常模式：直接调用 Python API，跳过 today_only 限制
    script = f"""import sys
sys.path.insert(0, r"{SRC_DIR}")
from pathlib import Path
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.utils.logging_utils import configure_logging
import os
os.environ["TOUTIAO_PUBLISH_HEADLESS"] = "1"
PROJECT_ROOT = Path(r"{PROJECT_ROOT}")
settings = load_settings(PROJECT_ROOT, account_key="robot_notes")
logger = configure_logging(settings.logs_dir, settings.logging_level, logger_name="local_publish.publish")
app = ContentFactoryAppService(settings, logger)
results = []
if {str(toutiao).lower()}:
    try:
        r = app.publish_latest_approved(unpublished_only=True, today_only=False, draft_only=False)
        logger.info("头条发布完成: %%s", list(r.keys()))
        results.append("toutiao_ok")
    except FileNotFoundError as exc:
        logger.warning("没有找到可发布的头条run: %%s", exc)
        results.append("toutiao_skip")
    except Exception as exc:
        logger.error("头条发布失败: %%s", exc)
        results.append("toutiao_fail")
if {str(wechat).lower()}:
    try:
        r = app.publish_wechat_latest_approved(unpublished_only=False, submit=True)
        logger.info("微信发布完成: %%s", list(r.keys()))
        results.append("wechat_ok")
    except FileNotFoundError as exc:
        logger.warning("没有找到可发布的微信run: %%s", exc)
        results.append("wechat_skip")
    except Exception as exc:
        logger.error("微信发布失败: %%s", exc)
        results.append("wechat_fail")
print("RESULTS:", ",".join(results))
"""

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="local_publish_run_", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        script_path = f.name

    try:
        _logger.info("执行本地发布（today_only=False）...")
        env = os.environ.copy()
        env["TOUTIAO_PUBLISH_HEADLESS"] = "1"
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        _logger.info("发布输出: %s", result.stdout)
        if result.stderr:
            _logger.warning("发布stderr: %s", result.stderr[-500:])
        return result.returncode
    finally:
        Path(script_path).unlink(missing_ok=True)


def _run_collect_performance() -> int:
    """运行本地数据采集（头条/微信后台阅读量等）。"""
    _logger.info("执行数据采集...")

    script = f"""import sys
sys.path.insert(0, r"{SRC_DIR}")
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.performance_service import collect_performance_data
from toutiao_content_factory.utils.logging_utils import configure_logging
settings = load_settings(r"{PROJECT_ROOT}", account_key="robot_notes")
logger = configure_logging(settings.logs_dir, settings.logging_level, logger_name="local_publish.collect")
payload = collect_performance_data(settings, logger)
records = len(payload.get("records", []))
print(f"采集完成: {records} 条记录")
"""

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="collect_perf_", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        script_path = f.name

    try:
        env = os.environ.copy()
        env["TOUTIAO_PUBLISH_HEADLESS"] = "1"
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=str(PROJECT_ROOT),
            env=env,
            timeout=300,
        )
        return result.returncode
    finally:
        Path(script_path).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地发布+采集脚本：从云端下载内容，本地 headless 发布和采集")
    parser.add_argument("--toutiao-only", action="store_true", help="只发布头条")
    parser.add_argument("--wechat-only", action="store_true", help="只发布微信")
    parser.add_argument("--collect-only", action="store_true", help="只采集数据，不发布")
    parser.add_argument("--skip-collect", action="store_true", help="跳过数据采集")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式")
    parser.add_argument("--skip-download", action="store_true", help="跳过下载，使用本地已有的 output 目录")
    args = parser.parse_args()

    _setup_logging()
    _load_dotenv()
    _configure_pythonpath()

    _logger.info("=" * 60)
    _logger.info("本地发布+采集开始")
    _logger.info("  时间: %s", datetime.now().isoformat())
    _logger.info("  模式: %s", "仅采集" if args.collect_only else "发布" + ("+采集" if not args.skip_collect else ""))
    _logger.info("=" * 60)

    toutiao = not args.wechat_only
    wechat = not args.toutiao_only
    exit_code = 0

    # 1. 下载云端 artifact（采集模式不需要）
    if not args.collect_only and not args.skip_download:
        with tempfile.TemporaryDirectory(prefix="daily_artifact_") as tmp_dir:
            artifact_dir = Path(tmp_dir)
            if not _download_latest_artifact(artifact_dir):
                _logger.error("下载 artifact 失败，退出")
                return 1

            # 2. 合并到本地 output
            output_dir = PROJECT_ROOT / "output"
            if not _merge_artifact_to_output(artifact_dir, output_dir):
                _logger.error("合并 artifact 失败，退出")
                return 1

    # 3. 本地 headless 发布（采集模式跳过）
    if not args.collect_only:
        pub_code = _run_publish(toutiao=toutiao, wechat=wechat, dry_run=args.dry_run)
        if pub_code == 0:
            _logger.info("本地发布成功完成")
        else:
            _logger.error("本地发布失败 (exit_code=%d)", pub_code)
            exit_code = pub_code

    # 4. 数据采集
    if not args.skip_collect:
        collect_code = _run_collect_performance()
        if collect_code == 0:
            _logger.info("数据采集成功完成")
        else:
            _logger.error("数据采集失败 (exit_code=%d)", collect_code)
            if exit_code == 0:
                exit_code = collect_code

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
