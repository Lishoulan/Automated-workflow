"""闭环执行器：把真实表现数据整理成下一轮生成可用的信号。

## 云端与本地是两条路径

- 默认（云端）：**只读**。计算闭环可用状态并落盘 `feedback_loop_status.json`，
  不改动 `prompts/` 下任何文件。原因是 GitHub Actions 跑完工作区即销毁，
  改 prompt 不会被持久化，改了等于没改。闭环真正的持久载体是
  `output/accounts/<slug>/automation/performance_metrics.json`，
  由 `title_agent` / `writer_agent` 在生成时通过 `performance_hints` 读取。
- `--full`（本地）：额外跑 `FeedbackLoopService.run_full_loop()`，会更新
  `prompts/title_prompt_v2.md`、`prompts/article_prompt_v2.md` 与
  `branding/self_benchmark_articles.yaml` 的 few-shot 段，仅供人工查看效果。

## 退出码

0 = 有可用信号；3 = 数据不足（不是失败，是还没到时候）；1 = 执行出错。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from toutiao_content_factory.config import load_settings  # noqa: E402
from toutiao_content_factory.services import performance_hints  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INSUFFICIENT = 3


def build_status(settings) -> dict:
    status = performance_hints.loop_status(settings)
    records = performance_hints.load_records(settings)
    status.update(
        {
            "account_key": settings.account_key,
            "observed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "metrics_path": str(Path(settings.automation_dir) / "performance_metrics.json"),
            "records_with_impressions": sum(
                1 for row in records if int(row.get("impression_count") or 0) > 0
            ),
            "records_with_reads": sum(
                1 for row in records if int(row.get("read_count") or 0) > 0
            ),
        }
    )
    if status["active"]:
        status["message"] = (
            f"闭环生效：{status['selected_count']} 条参考样本（模式 {status['mode']}）"
            f"将注入下一轮标题/正文生成。"
        )
    elif status["mode"] == "insufficient":
        status["message"] = (
            f"数据不足：现有 {status['record_count']} 条记录，"
            f"相对档至少需要 {status['min_records_for_relative']} 条。已跳过注入，不污染提示词。"
        )
    else:
        status["message"] = "无可注入样本。"
    return status


def write_status(settings, payload: dict) -> Path:
    target = Path(settings.automation_dir) / "feedback_loop_status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行内容闭环（真实数据 → 生成信号）")
    parser.add_argument("--account", default=None, help="账号 key，默认取配置里的默认账号")
    parser.add_argument("--full", action="store_true", help="额外执行 EvolutionAgent 并改写 prompt 模板（仅本地）")
    parser.add_argument("--json", action="store_true", help="把状态 JSON 打到标准输出")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    logger = logging.getLogger("feedback-loop")

    try:
        settings = load_settings(ROOT, args.account)
        status = build_status(settings)

        if args.full:
            from toutiao_content_factory.services.feedback_loop_service import FeedbackLoopService

            service = FeedbackLoopService(settings, logger)
            summary = service.run_full_loop()
            status["full_loop"] = summary

        path = write_status(settings, status)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            logger.info("闭环状态：%s", status["message"])
            logger.info("已写入 %s", path)

        return EXIT_OK if status["active"] else EXIT_INSUFFICIENT
    except Exception as exc:  # noqa: BLE001
        logger.exception("闭环执行失败：%s", exc)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
