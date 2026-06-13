#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试脚本：将优化后的排版内容发送到微信公众号草稿箱
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import logging
from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.wechat_135editor_service import Editor135Service
from toutiao_content_factory.services.wechat_layout_engine import convert_markdown_to_wechat_html, THEME_PRESETS

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("wechat_test")

# 测试内容
test_markdown = """
微信公众号排版测试

这是我们优化后的新版微信公众号排版，包含：

## 精美的标题样式
- 支持渐变背景
- 增强的阴影效果
- 优化的字间距

### 更多丰富的组件
除了基础的标题、段落和列表，我们还新增了以下组件：

:::info
信息卡片组件，用于展示重要信息，背景使用渐变效果
:::

:::step
# 步骤卡片
用于展示操作步骤，左侧有主题色边框
:::

:::key
关键要点组件，突出显示重要内容
:::

:::stat
100%
用户满意度
:::

## 美观的引用样式
> 这是一个引用块，展示了优化后的引用样式
> - 左侧有主题色边框
> - 背景有渐变效果
> - 文字颜色柔和

## 列表示例
### 有序列表
1. 第一个项目（带渐变圆点装饰）
2. 第二个项目
3. 第三个项目

### 无序列表
- 列表项一
- 列表项二
- 列表项三

**加粗文字**和*普通文字*，还有[链接文本](https://example.com)和代码片段`inline code`，都经过了美化。

## 精美的分割线
---

排版引擎完全重写，采用纯内联样式方案，确保在微信公众号编辑器中完美显示，不依赖外部CSS文件。

所有主题都经过精心设计，包括科技蓝、活力橙、文艺绿、极简灰、商务金、皇家紫、樱花粉、海洋蓝等。
"""

def main():
    print("=" * 60)
    print("微信公众号草稿箱测试")
    print("=" * 60)
    
    # 加载配置
    settings = load_settings(Path(__file__).parent, account_key="default")
    
    # 选择主题（使用科技蓝作为默认主题）
    theme = THEME_PRESETS["tech_blue"]
    print(f"\n使用主题: {theme.name}")
    
    # 转换Markdown为微信公众号内联样式HTML
    print("\n正在转换排版...")
    article_html = convert_markdown_to_wechat_html(
        test_markdown,
        theme=theme,
        brand_name="测试号",
        add_closing=True
    )
    print(f"✓ HTML内容已生成，长度: {len(article_html)} 字符")
    
    # 保存预览文件
    preview_path = Path(__file__).parent / "test_wechat_preview.html"
    preview_path.write_text(article_html, encoding="utf-8")
    print(f"✓ 预览文件已保存: {preview_path}")
    
    # 标题
    title = "微信公众号排版测试 - 新版优化"
    
    print("\n正在通过135编辑器发送到微信公众号草稿箱...")
    print("注意：此操作需要已初始化135编辑器登录状态")
    print("如果登录过期，请先运行: python main.py --init-135editor-login")
    print()
    
    # 创建服务
    editor_service = Editor135Service(settings, logger)
    
    # 发布到草稿箱
    try:
        result = editor_service.publish_via_135editor(
            title=title,
            article_html=article_html
        )
        
        print()
        print("=" * 60)
        print("发布结果:")
        print("=" * 60)
        print(f"状态: {result.get('status')}")
        print(f"标题: {result.get('title')}")
        print(f"方法: {result.get('method')}")
        print("=" * 60)
        print()
        print("✓ 测试完成！请检查微信公众号草稿箱确认。")
        
    except Exception as e:
        print()
        print("!" * 60)
        print(f"发布失败: {e}")
        print("!" * 60)
        print()
        print("提示：")
        print("1. 首先运行: python main.py --init-135editor-login")
        print("2. 在打开的浏览器中登录135编辑器")
        print("3. 登录完成后，再次运行此脚本")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
