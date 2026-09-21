"""「头条首发」声明只由**正文实际字数**决定，且边界必须是 1500。

## 这条规则为什么值得单独锁住

`PublishService._publish_run_locked` 里的判定是：

```python
article_char_count = len(article 去空白)
if article_char_count < 1500:
    declare_first_release = False
    notes_warning = f"质量检查：字数不足1500字（当前{article_char_count}字），自动取消头条首发声明"
```

它有三个容易写错的地方，而写错任何一个的代价都是"首发永远开不起来"或"首发被违规声明"：

1. **计数口径**：必须去掉换行和空格再数。用 `len(article)` 会把排版空白算进去，
   1500 字的稿子可能实际只有 1400 多 → 系统以为达标、平台判定不达标。
2. **边界方向**：不足 1500 才取消，**正好 1500 应当保留**（`<` 而不是 `<=`）。
3. **不得被上游覆盖**：生成侧的目标/下限（1700/1550）只是"提示词里的数字"，
   不能反过来让发布侧跳过自己的实测 —— 模型可能少写。

这份测试直接对着源码断言，因为要跑通真实浏览器发布才能触发它，
而它一旦回归，损失是**每条内容的推荐量**（首发激励是初期最大的曝光来源）。
"""

from __future__ import annotations

import ast
import inspect
import re

from toutiao_content_factory.services.publish_service import PublishService

FIRST_RELEASE_LINE = 1500


def _publish_run_source() -> str:
    return inspect.getsource(PublishService._publish_run_locked)


def test_first_release_line_is_1500() -> None:
    source = _publish_run_source()
    assert "1500" in source, "首发资格线 1500 在发布判定里找不到"
    assert "declare_first_release = False" in source, (
        "「不足首发线就取消声明」这条逻辑不见了 —— 首发会在不达标时被错误声明"
    )


def test_char_count_ignores_whitespace() -> None:
    """计数口径必须是"去空白"。"""
    source = _publish_run_source()
    assert re.search(
        r'article_char_count\s*=\s*len\(\s*article\.replace\("\\n",\s*""\)\.replace\(" ",\s*""\)\s*\)',
        source,
    ), (
        "正文计数口径被改动：必须是 len(article.replace('\\n','').replace(' ',''))。"
        "直接用 len(article) 会把排版空白算进字数，导致首发在不达标时被声明"
    )


def test_comparison_is_strictly_less_than() -> None:
    """边界方向：`< 1500` 才取消，正好 1500 保留首发。"""
    # 用 inspect.signature 拿不到函数体，这里改用模块源码里的函数片段；
    # 注意不能 cleandoc —— 那会破坏方法体的相对缩进，导致 ast.parse 失败。
    source = inspect.getsource(PublishService)
    tree = ast.parse(source)

    comparisons = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "article_char_count"
    ]
    assert comparisons, "找不到以 article_char_count 为左值的比较 —— 判定被重写了？"

    ops = {type(op) for node in comparisons for op in node.ops}
    assert ast.Lt in ops, "边界不再是 `<`：`<=` 会把正好 1500 字的稿子也取消首发"
    assert ast.LtE not in ops, "出现了 `<=`，边界右边被写宽了"


def test_first_release_flag_is_passed_through_to_the_checkbox() -> None:
    """判定结果必须一路传到勾选动作，中间不能被写死。"""
    source = inspect.getsource(PublishService)
    assert "declare_first_release=declare_first_release" in source, (
        "declare_first_release 没有传到 `_apply_safe_publish_options`，勾选动作会拿到默认值"
    )
    assert "label.byte-checkbox:has-text('头条首发')" in source, (
        "「头条首发」勾选器的定位符不见了"
    )


def test_quality_warning_names_the_actual_count() -> None:
    """警告里必须带上实际字数，否则线上排查时无法区分"差一点"和"差很多"。"""
    source = _publish_run_source()
    assert "字数不足1500字（当前" in source, (
        "取消首发的警告没有带实际字数，排查时看不到差多少"
    )
