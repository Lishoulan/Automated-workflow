# Article Generation Prompt v2

你正在为 `$platform` 生成一篇可直接发布的中文文章，服务品牌是 `$brand_name`。

## 系统级硬约束

1. 开篇必须在前 2 句内完成钩子。
- 只能使用以下三种方式之一：悬念、反差数据、反直觉判断。
- 禁止使用“今天我们来探讨”“不可否认”“值得注意的是”“综上所述”等套话。

2. 正文必须拆成 3 到 4 个小标题。
- 小标题必须直接写进 `article` 字段正文中。
- 每个小标题单独成行，统一使用 `### ` 开头。
- 小标题必须有对仗感，或带有明显的情绪/认知冲突，例如“热度在前，兑现在后”“看似提速，实则分化”。

3. 结尾必须留下开放式互动问题。
- 不能只写“你怎么看”。
- 必须围绕文中争议点、关键变量或利益分歧发问，方便读者评论区表达立场。

4. 全文必须是“真人作者在复盘”，不能写成提示词回显、流程说明或泛泛新闻播报。

## 强指令：语气与风格

$tone_directive

### Style Digest
$style_digest

### Brand Digest
$brand_digest

## 爆款模板机制

$viral_template_instruction

## 口语化表达补充

$kol_voice_instruction

## 输入素材

### 热点分析
$trend_json

### 数据分析
$data_json

### 写作要求
- 内容格式：$content_format_label
- 格式说明：$format_brief
- 段落要求：$paragraph_instruction
- 证据强度：$evidence_strength/100
- 话题质量：$topic_quality_score/100

### 账号与受众
- 品牌：$brand_name
- 行业方向：$industry_focus
- 目标读者：$audience
$cache_note

## 执行要求

- 全文目标字数：$article_word_target 字，允许上下浮动 10%，但不能明显偏短。
- 必须引用至少 2 个来自素材的具体数据点。
- 必须至少出现 1 处明确来源感表达，例如“根据公告”“数据里能看到”“报告显示”。
- 如果素材中有原话、表态或明确时间点，优先准确使用。
- 禁止虚构数据、人物、公司、时间、因果关系。
- 每段尽量控制在 1 到 3 句，优先短段落，适合移动端阅读。
- 段落之间要有推进关系，不要并列堆砌观点。
- 不要显式写“模板一”“第一部分”“总结一下”等机械提示。

## 输出格式

请只输出一个合法 JSON 对象，不要包裹 Markdown 代码块，字段如下：

{
  "opening_lines": ["开头第 1 句", "开头第 2 句", "开头第 3 句"],
  "article": "完整正文。正文内必须包含 3-4 个以 ### 开头的小标题，段落之间用两个换行分隔。",
  "summary": "100 字以内摘要",
  "comment_prompt": "结尾开放式互动问题",
  "tags": ["标签1", "标签2", "标签3"],
  "recommended_topics": ["推荐选题1", "推荐选题2"]
}

## 最终校验

- `opening_lines` 必须能单独构成钩子开场。
- `article` 必须包含 3 到 4 个 `###` 小标题。
- `article` 最后一句必须自然过渡到 `comment_prompt`。
- `comment_prompt` 必须具体、有争议点、有评论空间。
