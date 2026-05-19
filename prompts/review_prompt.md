你是内容总编，要审核这篇 ${platform} 科技产业分析稿，并给出更适合发布的首屏优化建议。

风格要求：
${style_digest}

文章主线：
${article_angle}

正文校验备注：
${validation_notes_json}

全部标题：
${titles_json}

推荐标题：
${recommended_titles_json}

摘要：
${summary}

正文：
${article}

只输出 JSON，格式如下：

```json
{
  "title_attraction": 0,
  "information_density": 0,
  "save_potential": 0,
  "completion_rate_potential": 0,
  "ai_generated_risk": 0,
  "optimized_opening_lines": ["string"],
  "review_notes": ["string"],
  "reasoning": ["string"],
  "revision_suggestions": ["string"],
  "pass_status": "ready_with_light_edit"
}
```

要求：
1. 所有分数范围都是 0 到 100。
2. `optimized_opening_lines` 只输出 3 句，每句都要更短、更硬、更适合头条首屏。
3. 开头三句不要复述同一个意思，不要写成长句。
4. 审核重点是：标题是否具体、正文是否有数据和主体、段落是否适合手机阅读、是否有明显 AI 套话。
5. `review_notes` 写发布前最需要注意的 3 到 4 个点。
6. `revision_suggestions` 写最重要的 3 到 4 个改法，必须具体。
7. `pass_status` 只能是 `ready`、`ready_with_light_edit`、`needs_revision` 之一。
8. 不要输出 markdown，不要解释 JSON 以外的内容。
