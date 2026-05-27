你要为一篇微信公众号文章设计“组件排版方案”，不是重写正文。

目标：
1. 提升公众号文章的精致度、品牌感、可读性、收藏感
2. 使用成熟公众号常见组件思路：品牌头图块、导语卡、编号小标题、数据提示框、风险提示、小图标、结尾互动卡
3. 风格必须克制、专业，不要廉价营销感，不要过度花哨

账号信息：
- 账号名：$account_name
- 账号定位：$account_focus

文章标题：
$title

文章摘要：
$summary

正文节选：
$article_excerpt

请输出 JSON，字段如下：
{
  "layout_style": "版式主风格，例如 insight_brief / data_feature / narrative_analysis",
  "header_variant": "顶部样式，例如 brand_summary / editorial_banner",
  "section_heading_style": "小标题样式，例如 badge_number / left_bar / chip_heading",
  "divider_style": "分隔样式，例如 double_dot / thin_line / star_break",
  "lead_variant": "导语区样式，例如 smart_take / lead_quote",
  "closing_variant": "结尾互动区样式，例如 comment_card / keyword_reply_card",
  "image_frame_style": "配图样式，例如 editorial_card / clean_full_bleed",
  "component_plan": [
    {"type": "brand_header", "enabled": true},
    {"type": "summary_strip", "enabled": true},
    {"type": "lead_card", "enabled": true},
    {"type": "section_heading", "enabled": true},
    {"type": "data_callout", "enabled": true},
    {"type": "risk_callout", "enabled": true},
    {"type": "inline_visual", "enabled": true},
    {"type": "conclusion_card", "enabled": true},
    {"type": "closing_card", "enabled": true},
    {"type": "footer_badge", "enabled": true}
  ],
  "icon_tokens": {
    "header": "单个符号",
    "insight": "单个符号",
    "data": "单个符号",
    "risk": "单个符号",
    "closing": "单个符号"
  },
  "tone_keywords": ["4到6个短词"],
  "data_callout_title": "适合这篇文的数据提示框标题",
  "risk_callout_title": "适合这篇文的风险提示框标题",
  "conclusion_title": "适合这篇文的结论卡标题",
  "closing_title": "适合这篇文的结尾互动标题"
}

要求：
1. 只输出一个 JSON 对象
2. 不要输出解释
3. 不要建议夸张颜色
4. 要符合成熟公众号常见审美：简洁、信息型、轻品牌化、便于长期复用
