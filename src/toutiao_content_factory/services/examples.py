"""新服务集成示例

本文件展示如何在现有代码中集成调试快照、状态管理和通知服务。
"""

from dataclasses import asdict
from pathlib import Path
from toutiao_content_factory.services.debug_snapshot_service import DebugSnapshotService
from toutiao_content_factory.services.pipeline_state_manager import PipelineStateManager
from toutiao_content_factory.services.notification_service import NotificationService
from toutiao_content_factory.config import AppSettings


class EnhancedPublishService:
    """增强版发布服务 - 集成调试快照和通知"""

    def __init__(self, settings: AppSettings, output_dir: Path):
        self.settings = settings
        self.output_dir = Path(output_dir)

        # 初始化服务
        self.debug_service = DebugSnapshotService()
        self.state_manager = PipelineStateManager(
            output_dir=output_dir,
            notification_service=NotificationService(asdict(settings))
        )

    def publish_with_snapshot(self, page, run_name: str, **kwargs):
        """
        带调试快照的发布流程示例
        """
        try:
            # 保存开始状态
            self.state_manager.save_state(run_name, "PUBLISHING")

            # 执行发布逻辑
            result = self._do_publish(page, **kwargs)

            # 成功
            self.state_manager.mark_success(run_name, result.get("title", ""))
            return result

        except Exception as e:
            error_msg = str(e)

            # 捕获调试快照
            snapshot = self.debug_service.capture_snapshot(
                page=page,
                error_msg=error_msg,
                run_name=run_name,
                output_dir=self.output_dir
            )

            # 生成诊断上下文
            import traceback
            diagnostic = self.debug_service.generate_diagnostic_context(
                error_msg=error_msg,
                traceback_str=traceback.format_exc(),
                error_file=__file__,
                error_line=42,
                config_snippet={"publish_timeout": 30},
                output_dir=self.output_dir
            )

            # 保存失败状态
            self.state_manager.mark_failed(
                run_name=run_name,
                failed_stage="publish",
                error_message=error_msg,
                debug_snapshot=snapshot
            )

            # 发送失败通知
            print(f"诊断上下文已生成: {diagnostic}")

            raise

    def _do_publish(self, page, **kwargs):
        """实际的发布逻辑"""
        # 这里是你的发布代码
        pass


# ==========================================
# 使用示例：在pipeline.py中集成
# ==========================================

"""
在 pipeline.py 或 main.py 中使用：

```python
from pathlib import Path
from toutiao_content_factory.services.notification_service import NotificationService
from toutiao_content_factory.services.pipeline_state_manager import PipelineStateManager

def main():
    # 初始化
    settings = load_settings()
    output_dir = Path("output")

    # 初始化通知服务
    notification = NotificationService(asdict(settings))

    # 初始化状态管理器
    state_manager = PipelineStateManager(output_dir, notification)

    # 在适当的时机保存状态
    state_manager.save_state("run_2026-05-25", "DRAFTED", workflow_result)

    # 在发布成功后
    state_manager.mark_success("run_2026-05-25", "文章标题")

    # 在发布失败后
    state_manager.mark_failed(
        run_name="run_2026-05-25",
        failed_stage="publish",
        error_message="Timeout waiting for selector"
    )
```
"""


# ==========================================
# 使用示例：在daily_run.ps1中集成网络检测
# ==========================================

"""
在 daily_run.ps1 中使用网络检测功能：

```powershell
# 启动前检测网络
$networkOk = Test-NetworkConnectivity -TimeoutSeconds 10
if (-not $networkOk) {
    Write-Host "网络未连接，等待重试..."
    exit 1
}

# 发送成功通知
Send-Notification -Type "success" -Title "任务完成" -Message "文章已发布"

# 发送失败通知
Send-Notification -Type "failure" -Title "任务失败" -Message "错误信息：xxx"
```
"""
