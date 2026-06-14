---
name: pick-topic
description: 采集今日热点并展示选题列表，让用户选择要写的文章主题。适用于用户说"看看今日热点"、"采集选题"、"pick topic"、"有什么好写的"等场景。
---

# Pick Topic — 智能选题

## 概述

从多个来源（36kr、智东西、OFweek 等）采集今日热点，展示选题列表供用户选择。

## 执行步骤

### Step 1: 采集热点

```powershell
python main.py --topics-only --refresh-cache --account <account>
```

设置 `timeout=300000`（5分钟）。

### Step 2: 读取选题列表

读取 `output/daily_topics.json`，展示选题列表：

```
🔥 今日热点选题（共 {count} 条）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{for each topic}
  {rank}. [{source}] {title}
     关键词：{matched_keywords}
     赛道：{industry_track}
     时间：{published_at}
     链接：{url}
{endfor}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 输入序号选择选题，或告诉我你想写什么主题
```

### Step 3: 展示选题池摘要

同时读取 `output/automation/topic_pool/topic_pool_summary.json`，展示选题池概况：

```
📦 选题池状态
  总量：{total_topics}
  今日新增：{today_count}
  可用：{available_count}
  已发布：{published_count}
```

### Step 4: 用户选择后

用户选择选题后，可以：
- 直接触发写作：使用 `/write-article --topic-title "<选题>"`
- 或继续浏览其他选题

## 来源说明

| 来源 | 类型 | 覆盖领域 |
|------|------|----------|
| 36kr | RSS | 科技商业综合 |
| 智东西 | HTML | AI、机器人 |
| OFweek | HTML | 新能源、储能、机器人 |
| 量子位 | RSS | AI 前沿 |
| 机器之心 | RSS | AI 学术/产业 |
| 雷锋网 | RSS | AI 应用 |

## 注意事项

- 选题会去重，已发布的主题不会重复推荐
- 选题池持久化，跨天可用
- 每次采集最多 20 条（可配置）
