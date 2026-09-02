# 本地自动发布配置指南

## 架构说明

```
云端（GitHub Actions）          本地（你的电脑）
┌─────────────────┐           ┌─────────────────┐
│ 每天定时运行      │           │ 每天定时检查      │
│ ↓               │           │ ↓               │
│ 生成文章内容     │  上传到    │ 下载最新内容      │
│ 生成配图        │  Artifact │ ↓               │
│ 互动Agent       │ ────────> │ 发布到头条       │
│ 变现数据同步     │           │ 发布到公众号      │
│                 │           │ ↓               │
│ IP变化 → cookie │           │ IP固定 → cookie │
│ 经常失效        │           │ 长期有效         │
└─────────────────┘           └─────────────────┘
```

## 快速开始

### 1. 确保环境准备就绪

```powershell
# 确保已登录 GitHub CLI
gh auth status

# 确保项目依赖已安装
cd f:\Automated-workflow-main
pip install -r requirements.txt
playwright install chromium
```

### 2. 确保浏览器 profile 已登录

```powershell
# 登录头条（如果还没登录）
py main.py --init-publish-login --account default

# 登录微信公众号后台（如果还没登录）
py main.py --init-wechat-login --account default
```

### 3. 测试本地发布

```powershell
# 先检查云端状态（不实际发布）
py scripts/local_publish.py --dry-run

# 跳过云端检查，直接发布（测试用）
py scripts/local_publish.py --skip-cloud-check
```

### 4. 配置定时任务

```powershell
# 安装定时任务（每天 09:00 运行）
.\scripts\setup_scheduled_task.ps1 -Action install -RunTime "09:00"

# 查看任务状态
.\scripts\setup_scheduled_task.ps1 -Action status

# 卸载定时任务
.\scripts\setup_scheduled_task.ps1 -Action uninstall
```

## 运行逻辑

1. **检查云端状态** — 查看 GitHub Actions 最新一次运行是否成功
2. **下载内容** — 从云端 artifact 下载最新文章、配图等
3. **发布到头条** — 使用本地浏览器 profile 发布（IP固定，cookie不失效）
4. **发布到公众号** — 通过官方 API 或微信公众号后台浏览器流程发布
5. **记录结果** — 日志保存在 `.runtime/logs/`

## 注意事项

- 电脑未开机时任务会自动跳过，下次开机后如果还在当天，会自动补发
- 发布使用 headless 模式，后台运行，不影响正常使用
- 整个过程不到 2 分钟
- 日志位置：`.runtime/logs/local_publish_YYYYMMDD_HHMMSS.log`
- 发布结果：`.runtime/local_publish/last_publish_result.json`

## 常见问题

### Q: 电脑没开机怎么办？
A: 任务会自动跳过。如果开机后还在当天，任务会在下次触发时自动补发。

### Q: 会影响我正常使用电脑吗？
A: 不会。发布使用 headless 浏览器，后台运行，你看不到任何窗口。

### Q: 怎么查看发布结果？
A: 查看日志：`.runtime/logs/local_publish_*.log`
   或查看结果：`.runtime/local_publish/last_publish_result.json`

### Q: 怎么修改运行时间？
A: 先卸载旧任务，再安装新任务：
```powershell
.\scripts\setup_scheduled_task.ps1 -Action uninstall
.\scripts\setup_scheduled_task.ps1 -Action install -RunTime "10:00"
```

### Q: 云端内容生成失败了怎么办？
A: 本地脚本会检测到云端状态不是 success，自动跳过发布，不会发布错误内容。
