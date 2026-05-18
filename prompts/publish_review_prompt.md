请审查下面这篇准备发布到 $platform 的科技行业文章，并严格输出 JSON。

要求：
1. 只从“是否适合直接发布”角度审查。
2. 重点看：标题长度、信息可信度、数据出处提示、AI 痕迹、是否容易引发误解、是否需要人工再核。
3. 不要重写全文，只给出发布层面的判断。

风格上下文：
$style_digest

候选标题：
$recommended_titles_json

标签：
$tags_json

质量评分：
$score_json

证据备注：
$evidence_notes_json

风险提示：
$risk_flags_json

摘要：
$summary

正文：
$article

请输出 JSON，字段如下：
{
  "publish_readiness_score": 0-100 的整数,
  "publish_risk_flags": ["最多 5 条"],
  "publish_recommendation": "ready_to_publish | manual_review | revise_before_publish",
  "publish_notes": ["最多 5 条"]
}
