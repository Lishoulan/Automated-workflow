---
name: daily-run
description: 触发 AI 内容工厂的完整日常流水线（热点采集→选题→写作→审核→发布）。适用于用户说"开始今日发布"、"运行日常流水线"、"daily run"等场景。执行完成后展示发布结果摘要。
---

# Daily Run — 日常发布流水线

## 概述

执行 AI Content Factory 的全自动日常流水线：热点采集 → 智能选题 → AI 写作 → 质量审核 → 多平台发布（头条+微信135编辑器）。

## 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--account` | 账号 key（config.yaml 中定义） | `default` |
| `--fast-mode` | 跳过非必要步骤（图片生成等） | 关闭 |
| `--dry-run` | 试运行，不调用 API | 关闭 |
| `--draft-only` | 保存为草稿不直接发布 | 关闭 |

## 执行步骤

### Step 1: 确认参数

向用户确认以下信息（如果未指定则使用默认值）：
- 账号：`default`
- 是否快速模式：否
- 是否草稿模式：是（安全优先）

### Step 2: 执行流水线

在项目根目录 `f:\Automated-workflow-main` 执行：

```powershell
python main.py --daily-run --refresh-cache --account <account>
```

附加参数根据用户选择追加：
- `--fast-mode` 如果用户选择快速模式
- `--dry-run` 如果用户选择试运行
- `--draft-only` 如果用户选择草稿模式

**注意**：此命令可能运行 10-30 分钟，请使用 `is_background=false` 并设置合理的 `timeout`（建议 1800000ms）。

### Step 3: 读取结果

执行完成后，读取 `f:\Automated-workflow-main\output\automation\last_daily_run_result.json` 并展示：

```
今日发布结果：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  账号：{account_key}
  状态：{status} ✅/❌
  选题：{run_name}
  标题：{selected_title}
  审核：{review_status}
  头条：{publish_status}
  微信：{wechat_status}
  头条链接：{publish_url}
  微信链接：{wechat_publish_url}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 4: 处理失败

如果执行失败，读取 `logs/app.log` 最后 50 行，分析错误原因并给出建议：
- `PlatformAuthError` → 建议使用 `/login-check` 重新登录
- `no_topics` → 建议检查 RSS 源或手动指定选题
- `LowQualityAbortException` → 建议调整质量阈值或更换选题

## 常用组合

```powershell
# 最常用：快速草稿模式
python main.py --daily-run --refresh-cache --fast-mode --draft-only

# 指定账号
python main.py --daily-run --refresh-cache --account robot_notes --draft-only

# 试运行（不消耗 API 额度）
python main.py --daily-run --dry-run
```
