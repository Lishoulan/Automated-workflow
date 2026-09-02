"""本地自动发布脚本 — 从云端下载最新内容并发布到头条和公众号。

设计原则：
- 云端负责生成内容（每天定时运行）
- 本地负责发布（IP固定，cookie不失效）
- 后台运行，不影响正常使用
- 电脑未开机则自动跳过
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 确保项目 src 在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def setup_logging(log_dir: Path) -> logging.Logger:
    """配置日志。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"local_publish_{datetime.now():%Y%m%d_%H%M%S}.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("local_publish")


def check_cloud_status(logger: logging.Logger) -> dict | None:
    """检查云端是否已完成内容生成。"""
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--limit", "1", "--json", "status,conclusion,databaseId,createdAt"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            encoding="utf-8",
        )
        if result.returncode != 0:
            logger.warning("无法获取云端运行状态: %s", result.stderr)
            return None
        
        runs = json.loads(result.stdout)
        if not runs:
            logger.info("没有找到云端运行记录")
            return None
        
        latest = runs[0]
        logger.info(
            "云端最新运行: id=%s status=%s conclusion=%s created=%s",
            latest["databaseId"],
            latest["status"],
            latest.get("conclusion"),
            latest["createdAt"],
        )
        
        # 检查是否在24小时内完成
        created = datetime.fromisoformat(latest["createdAt"].replace("Z", "+00:00"))
        now = datetime.now(created.tzinfo)
        if (now - created).total_seconds() > 86400:
            logger.info("云端运行超过24小时，跳过")
            return None
        
        if latest["status"] != "completed" or latest.get("conclusion") != "success":
            logger.info("云端运行未完成或失败，跳过")
            return None
        
        return latest
    
    except Exception as exc:
        logger.error("检查云端状态失败: %s", exc)
        return None


def download_latest_content(run_id: str, logger: logging.Logger) -> Path | None:
    """下载云端最新内容。"""
    output_dir = PROJECT_ROOT / ".runtime" / "local_publish"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 清理旧内容
    for item in output_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    
    try:
        logger.info("下载云端内容: run_id=%s", run_id)
        result = subprocess.run(
            ["gh", "run", "download", run_id, "--dir", str(output_dir)],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            encoding="utf-8",
        )
        
        if result.returncode != 0:
            logger.error("下载失败: %s", result.stderr)
            return None
        
        # 查找最新的 run 目录
        # artifact 结构: {artifact_name}/output/runs/
        runs_dir = None
        for candidate in [
            output_dir / "daily-output" / "output" / "runs",
            output_dir / "output" / "runs",
        ]:
            if candidate.exists():
                runs_dir = candidate
                break
        
        if not runs_dir:
            # 尝试查找任何包含 runs 目录的路径
            for runs_candidate in output_dir.rglob("runs"):
                if runs_candidate.is_dir():
                    runs_dir = runs_candidate
                    break
        
        if not runs_dir:
            logger.error("找不到 runs 目录")
            return None
        
        run_dirs = sorted(runs_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
        if not run_dirs:
            logger.error("runs 目录为空")
            return None
        
        latest_run = run_dirs[0]
        logger.info("找到最新内容: %s", latest_run.name)
        return latest_run
        
    except Exception as exc:
        logger.error("下载内容失败: %s", exc)
        return None


def publish_to_toutiao(run_dir: Path, logger: logging.Logger) -> dict:
    """发布到头条。"""
    from toutiao_content_factory.config import AppSettings
    from toutiao_content_factory.services.toutiao_publisher import ToutiaoPublisher
    
    result = {"platform": "toutiao", "status": "unknown", "message": ""}
    
    try:
        # 读取发布包
        publish_package = run_dir / "publish" / "toutiao_publish_package.md"
        if not publish_package.exists():
            result["status"] = "skipped"
            result["message"] = "发布包不存在"
            return result
        
        title_file = run_dir / "publish" / "title.txt"
        summary_file = run_dir / "publish" / "summary.txt"
        tags_file = run_dir / "publish" / "tags.txt"
        cover_file = run_dir / "images" / "cover.png"
        
        title = title_file.read_text(encoding="utf-8").strip() if title_file.exists() else ""
        summary = summary_file.read_text(encoding="utf-8").strip() if summary_file.exists() else ""
        tags = tags_file.read_text(encoding="utf-8").strip() if tags_file.exists() else ""
        content = publish_package.read_text(encoding="utf-8")
        cover = cover_file if cover_file.exists() else None
        
        logger.info("准备发布头条: title=%s", title)
        
        # 加载配置
        os.environ.setdefault("TOUTIAO_ACCOUNT", "default")
        settings = AppSettings()
        
        # 发布
        publisher = ToutiaoPublisher(settings, logger)
        publish_result = publisher.publish_article(
            title=title,
            content=content,
            summary=summary,
            tags=tags.split(",") if tags else [],
            cover_path=cover,
        )
        
        result["status"] = "success" if publish_result.get("success") else "failed"
        result["message"] = publish_result.get("message", "")
        result["url"] = publish_result.get("url", "")
        
        logger.info("头条发布结果: %s - %s", result["status"], result["message"])
        
    except Exception as exc:
        result["status"] = "error"
        result["message"] = str(exc)
        logger.error("头条发布异常: %s", exc)
    
    return result


def publish_to_wechat(run_dir: Path, logger: logging.Logger, account_key: str = "default") -> dict:
    """Publish to the WeChat Official Account through the supported backend/API paths."""
    from toutiao_content_factory.config import load_settings
    from toutiao_content_factory.services.wechat_service import WechatPublishService
    
    result = {"platform": "wechat", "status": "unknown", "message": ""}
    
    try:
        # 读取发布包
        article_md = run_dir / "publish" / "article.md"
        if not article_md.exists():
            result["status"] = "skipped"
            result["message"] = "文章不存在"
            return result
        
        title_file = run_dir / "publish" / "title.txt"
        summary_file = run_dir / "publish" / "summary.txt"
        cover_file = run_dir / "images" / "cover.png"
        
        title = title_file.read_text(encoding="utf-8").strip() if title_file.exists() else ""
        summary = summary_file.read_text(encoding="utf-8").strip() if summary_file.exists() else ""
        content = article_md.read_text(encoding="utf-8")
        cover = cover_file if cover_file.exists() else None
        
        logger.info("准备发布公众号: title=%s", title)
        
        # Use the official WeChat backend/API workflow. The legacy 135 route
        # is intentionally unavailable even when old environment values exist.
        os.environ.setdefault("TOUTIAO_ACCOUNT", account_key)
        os.environ["WECHAT_ENABLED"] = "1"
        os.environ["WECHAT_EDITOR_135_ENABLED"] = "0"
        settings = load_settings(PROJECT_ROOT, account_key=account_key)
        service = WechatPublishService(settings, logger)
        publish_exports = service.publish_run(run_dir, submit=True)

        result["status"] = "success"
        result["message"] = "WeChat publish completed through the official backend workflow."
        result["exports"] = {name: str(path) for name, path in publish_exports.items()}
        
        logger.info("公众号发布结果: %s - %s", result["status"], result["message"])
        
    except Exception as exc:
        result["status"] = "error"
        result["message"] = str(exc)
        logger.error("公众号发布异常: %s", exc)
    
    return result


def main():
    parser = argparse.ArgumentParser(description="本地自动发布")
    parser.add_argument("--account", default="default", help="账号名")
    parser.add_argument("--skip-cloud-check", action="store_true", help="跳过云端状态检查")
    parser.add_argument("--dry-run", action="store_true", help="只检查不发布")
    args = parser.parse_args()
    
    # 配置日志
    log_dir = PROJECT_ROOT / ".runtime" / "logs"
    logger = setup_logging(log_dir)
    
    logger.info("=" * 60)
    logger.info("本地发布开始")
    logger.info("=" * 60)
    
    # 检查云端状态
    if not args.skip_cloud_check:
        cloud_run = check_cloud_status(logger)
        if not cloud_run:
            logger.info("云端内容未就绪，退出")
            return 0
        run_id = str(cloud_run["databaseId"])
    else:
        logger.info("跳过云端状态检查")
        run_id = None
    
    # 下载最新内容
    if run_id:
        run_dir = download_latest_content(run_id, logger)
        if not run_dir:
            logger.error("下载内容失败，退出")
            return 1
    else:
        # 使用本地已有的内容
        run_dir = PROJECT_ROOT / ".runtime" / "local_publish" / "daily-output" / "output" / "runs"
        if run_dir.exists():
            run_dirs = sorted(run_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
            if run_dirs:
                run_dir = run_dirs[0]
            else:
                logger.error("本地没有可发布的内容")
                return 1
        else:
            logger.error("本地没有可发布的内容")
            return 1
    
    if args.dry_run:
        logger.info("Dry run 模式，只检查不发布")
        logger.info("发布目录: %s", run_dir)
        for item in run_dir.rglob("*"):
            if item.is_file():
                logger.info("  %s", item.relative_to(run_dir))
        return 0
    
    # 发布
    results = []
    
    logger.info("-" * 60)
    logger.info("开始发布到头条")
    logger.info("-" * 60)
    toutiao_result = publish_to_toutiao(run_dir, logger)
    results.append(toutiao_result)
    
    logger.info("-" * 60)
    logger.info("开始发布到公众号")
    logger.info("-" * 60)
    wechat_result = publish_to_wechat(run_dir, logger, args.account)
    results.append(wechat_result)
    
    # 汇总结果
    logger.info("=" * 60)
    logger.info("发布完成")
    logger.info("=" * 60)
    for r in results:
        logger.info("  %s: %s - %s", r["platform"], r["status"], r["message"])
    
    # 保存结果
    result_file = PROJECT_ROOT / ".runtime" / "local_publish" / "last_publish_result.json"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(
        json.dumps(
            {
                "timestamp": datetime.now().isoformat(),
                "results": results,
                "run_dir": str(run_dir),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    
    # 返回码
    all_success = all(r["status"] == "success" for r in results)
    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
