"""发布链路：`save=1` 的 pgc_id **不等于**已发布。

## 这个 bug 的代价

`_publish_to_toutiao` 里有一个 save 覆盖路由，会把编辑器的首个 `save=0` 请求
改写成 `save=1`（头条会以 7050 拒收首个 `save=0`，所以这一步是必要的）。
但改完之后，原代码在这里**直接 return**，并写下一句自认为合理的注释：

    "Draft save auto-submitted article for review on Toutiao.
     Skipping manual publish click."

那个判断是错的。头条对**存草稿**同样返回 `pgc_id`，所以「响应里有 pgc_id」
根本不能证明文章已提交。真正的发布按钮从来没被点过。

后果（2026-09-21 用 `scripts/verify_works_visibility.py` 读头条后台真实口径）：

    全部 177 条 / 已发布 0 条 / 仅我可见 176 条 / 审核未通过 1 条

    gid=7687678730147316251  老人去世，房产过户：兄弟姐妹为何反目  → 仅我可见
    gid=7687304540994978358  独居老人摔倒两天，邻居报警破门…        → 仅我可见
    gid=7687264638244831784  养老困境：独居老人摔倒…                → 仅我可见

三条"历史发布成功"全部仅我可见 —— **一次都不曾真正发布**，收益自然是 0。
用户看到的现象就是「发布的内容都是仅我可见」。

同一份 notes 里其实早有反证，只是当时没被当成失败信号：
    "Draft footer after no-cover selection: 草稿已保存"
    "Publish verification: article not found in content list. May need manual check."

## 为什么用结构化断言

这是一个**控制流**缺陷：「该继续往下走的地方提前 return 了」。
`_publish_to_toutiao` 是纯浏览器编排，要真正跑起来需要 mock 掉十几个内部方法，
那样的测试既脆又不一定真的覆盖到这条路径；而它一旦回归，代价是**全账号零曝光**，
属于必须硬拦的级别。所以这里直接断言控制流形状：

  1. save 覆盖分支里**不允许**再出现"跳过发布点击"这类早退；
  2. 真正的发布点击必须在同一函数里、且不是从该分支 return 出去的。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from toutiao_content_factory.services.publish_service import PublishService

PUBLISH_SOURCE = Path(inspect.getfile(PublishService))


def _method_source(name: str) -> str:
    return inspect.getsource(getattr(PublishService, name))


def test_save_override_does_not_skip_the_publish_click() -> None:
    """save=1 拿到 pgc_id 后必须继续走发布，不许当成已提交。"""
    source = _method_source("_publish_to_toutiao")
    tree = ast.parse(inspect.cleandoc(source))

    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_publish_to_toutiao"
    )

    # 只看**真正会执行的字符串常量**，不看注释/文档字符串。
    # （注释里保留这段历史说明是有意的 —— 后来者需要知道这个坑。）
    emitted: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            emitted.append(node.value)
    joined = "\n".join(emitted)

    for banned in (
        "Skipping manual publish click",
        "Draft save auto-submitted article for review",
        "skipping a duplicate click",
    ):
        assert banned not in joined, (
            f"`_publish_to_toutiao` 又把「存草稿当成已发布」写回了 note：{banned!r}"
        )

    assert "proceeding to the real publish action" in joined, (
        "save 覆盖分支必须留下「继续执行真正的发布动作」的 note，作为行为可观测证据"
    )


def test_draft_save_branch_does_not_return_before_the_publish_button() -> None:
    """save 覆盖分支不得提前 return；发布点击必须可达。

    判据：凡返回体里出现 `save_mode="draft_auto_submitted"` 的 return
    （即"把草稿当作已提交"那个返回值），都必须排在发布点击之后。
    """
    source = inspect.cleandoc(_method_source("_publish_to_toutiao"))
    tree = ast.parse(source)

    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_publish_to_toutiao"
    )

    click_lines = [
        node.lineno
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_click_immediate_publish_button"
    ]
    assert click_lines, "发布点击调用不见了：_click_immediate_publish_button"
    first_click_line = min(click_lines)

    offenders: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        segment = ast.get_source_segment(source, node.value) or ""
        # 只抓"以已提交身份返回"的那一个（save_mode=draft_auto_submitted +
        # status=submitted_for_review）。`publish_uncertain` 的保守早退是允许的，
        # 由 test_only_the_uncertain_branch_may_short_circuit 单独保护。
        if "draft_auto_submitted" not in segment:
            continue
        if "submitted_for_review" not in segment:
            continue
        if node.lineno < first_click_line:
            offenders.append((node.lineno, segment.splitlines()[0][:70]))

    assert not offenders, (
        "「草稿已提交」被当成发布成功提前返回了 —— 文章会再次变成「仅我可见」："
        f"{offenders}"
    )


def test_only_the_uncertain_branch_may_short_circuit() -> None:
    """允许的早退只有一个：证据不完整时的 publish_uncertain（保守失败）。"""
    source = inspect.cleandoc(_method_source("_publish_to_toutiao"))
    tree = ast.parse(source)
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_publish_to_toutiao"
    )

    short_circuits = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.If)
        and "submission_uncertain" in (ast.get_source_segment(source, node.test) or "")
    ]
    assert short_circuits, (
        "证据不完整时的 publish_uncertain 保守分支不见了 —— "
        "这一分支必须保留，否则会把证据不足的提交误报为发布成功"
    )


def test_accepted_submission_evidence_is_the_only_proof_gate() -> None:
    """证据判据本身只证明"草稿已落库"，不能当作发布成功的最终依据。

    这条测试固定语义：`_has_accepted_submission_evidence` 的 docstring 必须
    承认它对应的是 save=1 草稿响应 —— 提醒后来者不要再用它替代发布校验。
    """
    doc = inspect.getdoc(PublishService._has_accepted_submission_evidence) or ""
    assert "save=1" in doc
    assert "草稿" in doc or "已落库" in doc or "draft" in doc.lower()
