#!/usr/bin/env python3
"""MCP Browser-Use 集成模块 — 当 Playwright 选择器失效时的智能回退方案。

本模块提供基于 MCP browser-use 的浏览器自动化辅助功能，
作为现有 Playwright 硬编码选择器方案的补充和回退。

使用场景：
1. Playwright 发布流程抛出 PlatformUIUpdateException 时
2. 需要智能识别页面元素（而非依赖固定 CSS 选择器）时
3. 登录检查 / session 验证时

注意：本模块需要在 Qoder Agent 环境中运行（通过 MCP 协议调用 browser-use）。
独立运行时仅提供接口文档和示例。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 发布回退策略
# ---------------------------------------------------------------------------

@dataclass
class PublishFallbackResult:
    """发布回退结果。"""
    success: bool = False
    method: str = ""  # "playwright" | "mcp_browser_use" | "manual"
    message: str = ""
    screenshot_path: str = ""
    debug_info: dict = field(default_factory=dict)


@dataclass
class PlatformPublishConfig:
    """平台发布配置。"""
    name: str
    editor_url: str
    login_url: str
    login_check_keywords: list[str] = field(default_factory=list)
    title_input_hint: str = ""
    content_input_hint: str = ""
    publish_button_hint: str = ""
    draft_button_hint: str = ""
    success_keywords: list[str] = field(default_factory=list)


# 各平台配置
TOUTIAO_CONFIG = PlatformPublishConfig(
    name="toutiao",
    editor_url="https://mp.toutiao.com/profile_v4/graphic/publish",
    login_url="https://mp.toutiao.com/profile_v4/graphic/publish",
    login_check_keywords=["login", "sso", "登录"],
    title_input_hint="标题输入框（placeholder 含 '标题'）",
    content_input_hint="文章编辑器（富文本区域，含 '正文' placeholder）",
    publish_button_hint="发布按钮（文本为 '发布'）",
    draft_button_hint="存草稿按钮（文本含 '草稿' 或 '保存'）",
    success_keywords=["发布成功", "已保存", "success"],
)

EDITOR_135_CONFIG = PlatformPublishConfig(
    name="135editor",
    editor_url="https://www.135editor.com/beautify_editor.html",
    login_url="https://www.135editor.com/beautify_editor.html",
    login_check_keywords=["登录", "login", "微信"],
    title_input_hint="标题输入区域",
    content_input_hint="HTML 编辑模式区域",
    publish_button_hint="保存同步按钮",
    success_keywords=["同步成功", "已保存"],
)

PLATFORM_CONFIGS = {
    "toutiao": TOUTIAO_CONFIG,
    "135editor": EDITOR_135_CONFIG,
}


# ---------------------------------------------------------------------------
# MCP Browser-Use 操作步骤（供 Qoder Agent 调用）
# ---------------------------------------------------------------------------

def generate_mcp_publish_steps(platform: str, *, title: str, content_html: str,
                                draft_only: bool = True) -> list[dict[str, Any]]:
    """生成 MCP browser-use 的发布操作步骤序列。

    这些步骤描述了如何通过 MCP browser-use 工具完成发布，
    供 Qoder Agent 按顺序调用 CallMcpTool 执行。

    Args:
        platform: 平台名称 (toutiao / 135editor)
        title: 文章标题
        content_html: 排版好的 HTML 内容
        draft_only: 是否仅保存为草稿

    Returns:
        操作步骤列表，每个步骤包含 MCP tool_name 和 arguments
    """
    if platform == "135editor":
        return [{"error": "135 Editor integration is disabled; use the WeChat backend workflow."}]

    config = PLATFORM_CONFIGS.get(platform)
    if not config:
        return [{"error": f"Unknown platform: {platform}"}]

    steps = []

    # Step 1: 导航到编辑器页面
    steps.append({
        "step": 1,
        "description": f"打开 {config.name} 编辑器",
        "tool_name": "navigate_page",
        "server_name": "browser-use",
        "arguments": {"type": "url", "url": config.editor_url},
    })

    # Step 2: 获取页面快照，检查登录状态
    steps.append({
        "step": 2,
        "description": "获取页面快照，检查是否需要登录",
        "tool_name": "take_snapshot",
        "server_name": "browser-use",
        "arguments": {"verbose": True},
        "check": {
            "condition": "snapshot_contains_login_keywords",
            "keywords": config.login_check_keywords,
            "on_fail": "请用户手动登录后重试",
        },
    })

    # Step 3: 截图确认页面状态
    steps.append({
        "step": 3,
        "description": "截图确认当前页面",
        "tool_name": "take_screenshot",
        "server_name": "browser-use",
        "arguments": {"format": "png"},
    })

    # Step 4: 在快照中查找标题输入框并填入标题
    steps.append({
        "step": 4,
        "description": f"填入标题: {title[:20]}...",
        "instruction": (
            f"从 Step 2 的快照中找到 {config.title_input_hint} 对应的元素 uid，"
            f"然后使用 fill 工具填入标题"
        ),
        "tool_name": "fill",
        "server_name": "browser-use",
        "arguments": {"uid": "<从快照中获取>", "value": title},
    })

    # Step 5: 填入文章内容
    if platform == "135editor":
        steps.append({
            "step": 5,
            "description": "切换到 HTML 模式并粘贴内容",
            "instruction": (
                "在 135 编辑器中找到 HTML 源码切换按钮，点击切换到 HTML 模式，"
                "然后将内容粘贴到 HTML 编辑区域"
            ),
            "tool_name": "fill",
            "server_name": "browser-use",
            "arguments": {"uid": "<html_editor_uid>", "value": content_html},
        })
    else:
        steps.append({
            "step": 5,
            "description": "填入文章内容",
            "instruction": (
                f"从快照中找到 {config.content_input_hint}，"
                f"使用 fill 或 click + press_key 工具输入内容"
            ),
            "tool_name": "fill",
            "server_name": "browser-use",
            "arguments": {"uid": "<editor_uid>", "value": content_html[:500]},
        })

    # Step 6: 点击发布/草稿按钮
    if draft_only:
        steps.append({
            "step": 6,
            "description": "保存为草稿",
            "instruction": f"从快照中找到 {config.draft_button_hint}，点击保存",
            "tool_name": "click",
            "server_name": "browser-use",
            "arguments": {"uid": "<draft_button_uid>"},
        })
    else:
        steps.append({
            "step": 6,
            "description": "发布文章",
            "instruction": f"从快照中找到 {config.publish_button_hint}，点击发布",
            "tool_name": "click",
            "server_name": "browser-use",
            "arguments": {"uid": "<publish_button_uid>"},
        })

    # Step 7: 验证发布结果
    steps.append({
        "step": 7,
        "description": "验证发布结果",
        "tool_name": "take_snapshot",
        "server_name": "browser-use",
        "arguments": {"verbose": False},
        "check": {
            "condition": "snapshot_contains_success_keywords",
            "keywords": config.success_keywords,
        },
    })

    # Step 8: 最终截图
    steps.append({
        "step": 8,
        "description": "截图保存发布结果",
        "tool_name": "take_screenshot",
        "server_name": "browser-use",
        "arguments": {"format": "png"},
    })

    return steps


# ---------------------------------------------------------------------------
# 登录检查步骤
# ---------------------------------------------------------------------------

def generate_login_check_steps(platform: str) -> list[dict[str, Any]]:
    """生成 MCP browser-use 的登录检查步骤。"""
    if platform == "135editor":
        return [{"error": "135 Editor integration is disabled; use the WeChat backend workflow."}]

    config = PLATFORM_CONFIGS.get(platform)
    if not config:
        return [{"error": f"Unknown platform: {platform}"}]

    return [
        {
            "step": 1,
            "description": f"打开 {config.name} 页面",
            "tool_name": "navigate_page",
            "server_name": "browser-use",
            "arguments": {"type": "url", "url": config.login_url},
        },
        {
            "step": 2,
            "description": "等待页面加载",
            "tool_name": "wait_for",
            "server_name": "browser-use",
            "arguments": {"time": 3},
        },
        {
            "step": 3,
            "description": "获取页面快照检查登录状态",
            "tool_name": "take_snapshot",
            "server_name": "browser-use",
            "arguments": {"verbose": True},
            "check": {
                "condition": "check_login_status",
                "login_keywords": config.login_check_keywords,
                "logged_in_message": f"{config.name} 登录状态正常",
                "not_logged_in_message": f"{config.name} 需要重新登录",
            },
        },
        {
            "step": 4,
            "description": "截图确认",
            "tool_name": "take_screenshot",
            "server_name": "browser-use",
            "arguments": {"format": "png"},
        },
    ]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def analyze_snapshot_for_login(snapshot_text: str, login_keywords: list[str]) -> dict[str, Any]:
    """分析页面快照判断登录状态。"""
    text_lower = snapshot_text.lower()
    found_keywords = [kw for kw in login_keywords if kw.lower() in text_lower]

    return {
        "is_logged_in": len(found_keywords) == 0,
        "found_login_keywords": found_keywords,
        "message": "登录正常" if not found_keywords else f"检测到登录相关关键词: {found_keywords}",
    }


def analyze_snapshot_for_success(snapshot_text: str, success_keywords: list[str]) -> dict[str, Any]:
    """分析页面快照判断发布是否成功。"""
    found = [kw for kw in success_keywords if kw in snapshot_text]

    return {
        "is_success": len(found) > 0,
        "found_success_keywords": found,
        "message": "发布成功" if found else "未检测到成功标识",
    }


def save_steps_to_file(steps: list[dict], output_path: Path) -> None:
    """将步骤序列保存到文件，供调试使用。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(steps, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI 入口（用于生成步骤文档）
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="MCP Browser-Use 集成 — 生成发布步骤")
    parser.add_argument("--platform", default="toutiao", help="平台名称 (toutiao)")
    parser.add_argument("--title", default="测试标题", help="文章标题")
    parser.add_argument("--draft-only", action="store_true", default=True, help="仅保存草稿")
    parser.add_argument("--login-check", action="store_true", help="生成登录检查步骤")
    parser.add_argument("--output", default="", help="输出文件路径")
    args = parser.parse_args()

    if args.login_check:
        steps = generate_login_check_steps(args.platform)
    else:
        steps = generate_mcp_publish_steps(
            args.platform,
            title=args.title,
            content_html="<p>示例内容</p>",
            draft_only=args.draft_only,
        )

    output = json.dumps(steps, ensure_ascii=False, indent=2)

    if args.output:
        save_steps_to_file(steps, Path(args.output))
        print(f"步骤已保存到: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
