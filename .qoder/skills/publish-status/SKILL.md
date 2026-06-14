---
name: publish-status
description: 查看 AI 内容工厂的发布状态仪表盘，包括最近运行结果、平台健康状态、发布历史和浏览器 session 状态。适用于用户问"发布状态"、"今天发了什么"、"publish status"等场景。
---

# Publish Status — 发布状态看板

## 概述

汇总展示 AI Content Factory 的运营状态，包括最近运行结果、平台健康、发布历史等。

## 执行步骤

### Step 1: 读取运行结果

读取以下文件并汇总：

1. **最近运行结果**：`output/automation/last_daily_run_result.json`
2. **平台健康状态**：`output/automation/platform_health.json`
3. **定时任务状态**：`output/automation/scheduled_status.json`
4. **发布历史**：`output/publish_history.json`（头条）和 `output/wechat_publish_history.json`（微信）

### Step 2: 展示状态报告

```
📊 AI 内容工厂状态报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🕐 最近运行
  时间：{updated_at}
  状态：{status}
  选题：{run_name}
  标题：{selected_title}

📋 审核状态：{review_status}
📰 头条发布：{publish_status} {publish_url}
💬 微信发布：{wechat_status} {wechat_publish_url}

🏥 平台健康
  头条：{toutiao_status} (上次检查：{last_check})
  微信：{wechat_status}
  抖音：{douyin_status}

⏰ 定时调度
  状态：{scheduler_status}
  上次心跳：{heartbeat}

📚 发布历史（最近 5 条）
  {recent_publishes}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 3: 检查异常

如果发现以下异常，主动提示：
- 平台 session 过期 → 建议执行 `/login-check`
- 连续多次发布失败 → 建议检查账号状态
- 定时任务长时间无心跳 → 建议检查调度器

## 文件路径参考

| 文件 | 路径 |
|------|------|
| 运行结果 | `output/automation/last_daily_run_result.json` |
| 平台健康 | `output/automation/platform_health.json` |
| 调度状态 | `output/automation/scheduled_status.json` |
| 头条历史 | `output/publish_history.json` |
| 微信历史 | `output/wechat_publish_history.json` |
| 选题池 | `output/automation/topic_pool/topic_pool_summary.json` |
