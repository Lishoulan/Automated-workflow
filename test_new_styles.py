#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试新的排版引擎样式
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / 'src'))

from toutiao_content_factory.services.wechat_layout_engine import (
    convert_markdown_to_wechat_html,
    THEME_PRESETS,
)

# 测试内容
test_markdown = """
微信公众号排版测试

## 测试标题

这是一段测试文本，用来看看新的排版效果。这里会更加丰富和美观。

### 子标题测试

这是另一段内容，包含了一些 **加粗文本** 和 [链接文本](https://example.com)。

:::info
这是一个信息卡片
支持多行文本
:::

:::step
# 步骤一
这是步骤内容演示
:::

:::key
这是关键要点内容
:::

:::stat
100%
用户满意度
:::

---

> 这是一段引用文本，展示了引用块的效果
> 支持多行内容显示

1.  有序列表第一项
2.  有序列表第二项
3.  有序列表第三项

- 无序列表第一项
- 无序列表第二项
- 无序列表第三项
"""

# 测试所有主题
for theme_name, theme in THEME_PRESETS.items():
    print(f"\n{'='*60}")
    print(f"生成 {theme_name} 主题")
    print(f"{'='*60}\n")
    html = convert_markdown_to_wechat_html(
        test_markdown,
        theme=theme,
        brand_name="测试品牌",
        add_closing=True
    )
    output_file = Path(__file__).parent / f"test_preview_{theme_name}.html"
    output_file.write_text(html, encoding='utf-8')
    print(f"✓ 已生成: {output_file}")

print(f"\n✅ 所有主题预览文件已生成完成!")
print(f"请在浏览器中打开 test_preview_*.html 查看效果")
