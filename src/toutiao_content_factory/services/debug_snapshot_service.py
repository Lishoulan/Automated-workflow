"""调试快照与诊断上下文生成服务"""
import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class DebugSnapshotService:
    """负责在出错时捕获调试快照并生成诊断上下文"""

    @staticmethod
    def capture_snapshot(
        page,
        error_msg: str,
        run_name: str,
        output_dir: Path
    ) -> dict[str, Any]:
        """
        捕获当前页面的截图和HTML源码
        :param page: Playwright Page对象
        :param error_msg: 错误信息
        :param run_name: 运行名称
        :param output_dir: 输出目录
        :return: 包含截图和HTML路径的字典
        """
        snapshot_dir = output_dir / "debug_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result = {
            "timestamp": timestamp,
            "error_message": error_msg,
            "screenshot_path": None,
            "html_path": None
        }

        try:
            screenshot_path = snapshot_dir / f"error_{timestamp}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            result["screenshot_path"] = str(screenshot_path)
        except Exception as e:
            result["screenshot_error"] = str(e)

        try:
            html_path = snapshot_dir / f"error_{timestamp}.html"
            content = page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(content)
            result["html_path"] = str(html_path)
        except Exception as e:
            result["html_error"] = str(e)

        return result

    @staticmethod
    def generate_diagnostic_context(
        error_msg: str,
        traceback_str: str,
        error_file: str,
        error_line: int,
        config_snippet: dict[str, Any],
        output_dir: Path
    ) -> str:
        """
        生成可发送给Codex/ChatGPT的诊断上下文
        :return: Markdown格式的诊断报告
        """
        snapshot_dir = output_dir / "debug_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        env_info = {
            "os": platform.system(),
            "os_version": platform.version(),
            "python_version": sys.version,
            "timestamp": timestamp
        }

        markdown = f"""# 诊断上下文

## 运行环境
```json
{json.dumps(env_info, indent=2, ensure_ascii=False)}
```

## 错误信息
```
{error_msg}
```

## 异常堆栈
```
{traceback_str}
```

## 出错位置
- 文件: `{error_file}`
- 行号: `{error_line}`

## 相关配置片段
```yaml
{json.dumps(config_snippet, indent=2, ensure_ascii=False)}
```

## 建议
请根据以上信息分析可能的错误原因，并给出修复代码。

"""

        context_path = snapshot_dir / f"diagnostic_context_{timestamp}.md"
        with open(context_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        return markdown

    @staticmethod
    def save_pipeline_state(
        state: str,
        run_name: str,
        workflow_data: dict[str, Any],
        output_dir: Path
    ) -> Path:
        """
        保存流水线状态到JSON文件
        :param state: 状态标记（DRAFTED/APPROVED/PUBLISHING/FAILED/PUBLISHED）
        :param run_name: 运行名称
        :param workflow_data: 工作流数据
        :param output_dir: 输出目录
        :return: 保存的文件路径
        """
        state_dir = output_dir / "runs"
        state_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        state_file = state_dir / f"{run_name}_state.json"

        state_record = {
            "run_name": run_name,
            "state": state,
            "timestamp": timestamp,
            "workflow_data": workflow_data
        }

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state_record, f, indent=2, ensure_ascii=False)

        return state_file

    @staticmethod
    def load_pipeline_state(run_name: str, output_dir: Path) -> dict[str, Any] | None:
        """
        加载流水线状态
        :return: 状态记录，如果不存在则返回None
        """
        state_file = output_dir / "runs" / f"{run_name}_state.json"
        if not state_file.exists():
            return None

        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
