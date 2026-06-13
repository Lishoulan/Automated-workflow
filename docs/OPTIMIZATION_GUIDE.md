# 自动化系统优化方案 - 使用指南

## 概述

本优化方案针对"每天自动定时运转"系统，在系统调度、容错自愈、断点重试以及排错辅助四个维度进行了针对性优化。

## 已实现的功能

### 1. 调试快照与诊断上下文生成

创建了 `debug_snapshot_service.py` 服务，提供以下功能：

- **截图捕获**：在出错时自动保存浏览器截图和HTML源码
- **诊断上下文生成**：生成可发送给Codex/ChatGPT的诊断报告
- **状态持久化**：保存流水线状态到JSON文件

### 2. 流水线状态管理器

创建了 `pipeline_state_manager.py`，实现状态标记系统：

- `DRAFTED` → `APPROVED` → `PUBLISHING` → `PUBLISHED` / `FAILED`
- 支持断点续传
- 支持基于已有草稿重新发布

### 3. 通知服务

创建了 `notification_service.py`，支持多种推送渠道：

- **Bark** (iOS免费推送)
- **飞书群机器人**
- **钉钉群机器人**
- **企业微信群机器人**

### 4. 增强的每日运行脚本

创建了 `daily_run_enhanced.ps1`，包含：

- 网络拨测：启动前检测网络连接
- 启动延迟：等待网络就绪后再执行
- 自动重试：失败时自动重试（默认3次，间隔15分钟）
- 主动通知：成功/失败时自动发送推送通知

## 部署步骤

### 第一阶段：状态持久化和调试快照

新服务已自动集成到项目中：

```
src/toutiao_content_factory/services/
├── debug_snapshot_service.py      # 调试快照服务
├── pipeline_state_manager.py     # 流水线状态管理器
└── notification_service.py       # 通知服务
```

**现有功能**：

publish_service.py 中已有的截图功能会自动保存调试快照到：
```
output/debug_snapshots/error_YYYYMMDD_HHMMSS.png
output/debug_snapshots/error_YYYYMMDD_HHMMSS.html
```

### 第二阶段：使用增强的每日运行脚本

替换原来的 `daily_run.ps1` 为 `daily_run_enhanced.ps1`：

```powershell
# 注册任务时使用增强版
powershell -ExecutionPolicy Bypass -File .\scripts\daily_run_enhanced.ps1 -Account default -EnableAutoPublish
```

**配置环境变量**（在 `.env` 中添加）：

```env
# 企业微信通知
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY

# 飞书通知
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_HOOK

# 钉钉通知
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN

# Bark通知 (iOS)
BARK_URL=https://api.day.app/YOUR_BARK_KEY
```

### 第三阶段：Windows任务计划优化

在 Windows 任务计划程序中进行以下优化：

1. **允许唤醒计算机**：在任务属性的"条件"选项卡中勾选"允许唤醒计算机运行此任务"

2. **配置失败重试**：在"设置"选项卡中：
   - 勾选"如果任务失败，重新启动"
   - 设置"每隔 15 分钟重新启动一次"
   - 设置"最多尝试 3 次"

3. **配置运行设置**：
   - 选择"无论用户是否登录均运行"
   - 选择"使用最高权限运行"

## 使用场景

### 场景1：文章发布失败

1. 系统自动保存调试截图到 `output/debug_snapshots/`
2. 状态标记为 `FAILED`
3. 自动发送失败通知到配置的渠道
4. 登录后台查看失败原因和截图

### 场景2：断点续传

如果任务在"写作"阶段失败，重新运行时会：

1. 检测到上次运行状态为 `FAILED`
2. 检查是否有可用的草稿
3. 如果有，直接从"写作"阶段继续，跳过"采集"和"分析"阶段
4. 节省API调用，避免重复消耗Token

### 场景3：快速定位问题

打开后台的运行详情页，可以：

- 查看错误截图，一眼看出卡在了哪个弹窗或验证码上
- 点击"复制诊断上下文"按钮，生成可发送给Codex的诊断报告
- 根据诊断报告快速修复问题

## 文件位置

所有新增的服务文件位于：

```
src/toutiao_content_factory/services/
├── debug_snapshot_service.py      # 调试快照与诊断上下文生成
├── pipeline_state_manager.py     # 流水线状态管理
└── notification_service.py       # 通知服务
```

增强的运行脚本位于：

```
scripts/
├── daily_run_enhanced.ps1       # 增强版每日运行脚本（带网络拨测和重试）
```

## 注意事项

1. **首次使用需要配置通知渠道**：在 `.env` 中添加相应的 WebHook URL
2. **截图会占用磁盘空间**：定期清理 `output/debug_snapshots/` 目录
3. **状态文件会累积**：定期检查 `output/runs/` 目录，清理不需要的历史记录
4. **网络延迟参数可调**：在 `daily_run_enhanced.ps1` 中可以调整 `NetworkTimeoutSeconds` 和 `StartupDelaySeconds`

## 故障排除

### 通知没有发送

1. 检查 `.env` 中是否配置了正确的 WebHook URL
2. 检查网络连接是否正常
3. 查看日志文件确认是否有错误信息

### 截图没有保存

1. 确认 `output/debug_snapshots/` 目录存在且有写入权限
2. 检查 Playwright 浏览器是否正常运行

### 断点续传不生效

1. 确认上次运行的 JSON 状态文件存在
2. 检查状态是否正确标记为 `FAILED`
3. 确认有可用的 WorkflowResult 数据
