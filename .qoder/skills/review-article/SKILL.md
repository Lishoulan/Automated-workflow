---
name: review-article
description: 审核最新生成的文章，展示质量评分、AI 风险分数和合规检查结果，支持批准或拒绝操作。适用于用户说"审核文章"、"review article"、"看看文章质量"等场景。
---

# Review Article — 文章审核

## 概述

展示最新待审核文章的详细评分，支持批准（触发发布）或拒绝操作。

## 执行步骤

### Step 1: 查找待审核文章

扫描 `output/runs/` 目录，找到最新的待审核 run 目录。

判断依据：`run_dir/review_status.json` 不存在或 status 不是 `approved`。

### Step 2: 准备审核包

```powershell
python main.py --prepare-review --run-dir "<run_dir_path>"
```

### Step 3: 展示审核详情

读取以下文件并展示：

1. **文章内容**：`<run_dir>/article.md`
2. **质量评分**：`<run_dir>/content_score.json`
3. **审核包**：`<run_dir>/review_packet.md`

展示格式：
```
📋 文章审核
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 选题：{topic_title}
📝 推荐标题：{selected_title}

📊 质量评分
  总分：{overall_score}/100 {pass/fail}
  原创性：{originality_score}/100
  可读性：{readability_score}/100
  信息密度：{info_density_score}/100
  逻辑性：{logic_score}/100

🤖 AI 风险评分：{ai_risk_score}/100 {pass/fail}
  （阈值：≤ 45 通过，≤ 70 可自动发布）

🔍 合规检查
  AIGC 声明：{aigc_declaration}
  敏感词检测：{content_guard_status}
  标题长度：{title_length}/30

📄 文章预览：
{article_preview_300_chars}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 4: 用户决策

提供三个选项：

**批准并发布：**
```powershell
python main.py --approve-latest --auto-publish
```

**批准为草稿（不直接发布）：**
```powershell
python main.py --approve-latest
```

**拒绝并附注原因：**
```powershell
python main.py --reject-run --run-dir "<run_dir>" --review-notes "<原因>"
```

### Step 5: 指定标题

用户可以选择推荐标题中的某一个：

```powershell
python main.py --approve-latest --selected-title-index 2
```

或自定义标题：

```powershell
python main.py --approve-latest --selected-title "自定义标题"
```

## 评分标准参考

| 指标 | 最低要求 | 自动发布要求 |
|------|----------|--------------|
| 总分 | ≥ 72 | ≥ 80 |
| AI 风险 | ≤ 45 | ≤ 40 |
| 验证分 | - | ≥ 80 |
| 就绪分 | - | ≥ 70 |

## 注意事项

- 批准后可通过 `/daily-run --publish-latest-approved` 发布
- 草稿模式下文章保存在头条草稿箱，需手动确认后发布
- 微信草稿通过 135 编辑器同步，不直接调用微信 API
