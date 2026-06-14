---
name: write-article
description: 触发 AI 内容工厂的文章生成流程，支持自动选题或手动指定主题，生成高质量科技文章。适用于用户说"写一篇文章"、"生成内容"、"write article"等场景。
---

# Write Article — AI 文章创作

## 概述

触发 AI 内容工厂的文章生成流程：智能选题 → AI 写作 → 质量审核 → 标题优化。

## 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--account` | 账号 key | `default` |
| `--topic-title` | 指定选题关键词 | 自动选择 |
| `--topic-rank` | 使用第 N 个选题 | 1 |
| `--style` | 写作风格 | `deep_industry_analysis` |

## 可用写作风格

| 风格 key | 中文名 | 适用场景 |
|----------|--------|----------|
| `deep_industry_analysis` | 深度行业分析风 | 产业链分析、公司竞争格局 |
| `tech_media` | 科技媒体风 | 快讯、事件报道 |
| `finance_report` | 财经研报风 | 投资分析、财务数据 |
| `viral_science` | 爆款科普风 | 大众科普、轻松有趣 |

## 执行步骤

### Step 1: 选择模式

询问用户：
- **自动选题**（推荐）：系统从热点池中自动选择最优选题
- **指定选题**：用户提供关键词，从匹配结果中选择
- **粘贴素材**：用户粘贴原始素材文本

### Step 2: 执行生成

**自动选题模式：**
```powershell
python main.py --auto-run --account <account> --topic-rank 1
```

**指定选题模式：**
```powershell
python main.py --auto-run --account <account> --topic-title "<关键词>"
```

**粘贴素材模式：**
```powershell
python main.py --paste "<素材文本>"
```

设置 `timeout=1800000`（30分钟），因为生成过程较长。

### Step 3: 展示结果

执行完成后，读取 `output/` 目录中的产出文件：

1. **文章正文**：`output/article.md`
2. **标题候选**：`output/titles.txt`（展示前 3 个推荐标题）
3. **质量评分**：`output/content_score.json`
4. **选题信息**：`output/selected_topic.md`

展示格式：
```
✍️ 文章生成完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 选题：{topic_title}
📝 标题：{top_3_titles}
📊 质量分：{overall_score}/100
🤖 AI风险：{ai_risk_score}/100
📏 字数：约 {word_count} 字

[文章预览前 200 字...]

下一步：
- 使用 /review-article 审核文章
- 使用 /daily-run 直接发布
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 4: 后续操作建议

- 如果质量分 ≥ 72 且 AI 风险 ≤ 45 → 建议直接发布
- 如果质量分偏低 → 建议更换选题或调整风格
- 如果 AI 风险偏高 → 建议执行审核流程
