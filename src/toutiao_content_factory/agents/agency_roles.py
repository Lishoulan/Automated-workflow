"""Agency Roles loader — loads professional AI role prompts from agency-agents.

Provides 82+ specialized role prompts that can be injected into any Agent.
"""
from __future__ import annotations

import logging
from pathlib import Path


# Built-in role snippets for the most commonly needed roles
_BUILTIN_ROLES: dict[str, str] = {
    "marketing-expert": (
        "你是一位资深营销专家。你擅长：\n"
        "- 分析目标受众画像和消费心理\n"
        "- 设计高转化率的内容钩子和CTA\n"
        "- 制定多渠道分发策略\n"
        "- 优化标题和摘要的点击率\n"
        "- 识别内容的热点窗口和传播路径\n"
        "输出要求：给出具体可执行的营销建议，包含数据支撑。"
    ),
    "seo-expert": (
        "你是一位SEO优化专家。你擅长：\n"
        "- 关键词研究和竞争分析\n"
        "- 标题和描述的SEO优化\n"
        "- 内容结构和内链策略\n"
        "- 搜索意图匹配和长尾词布局\n"
        "- 平台算法适配（头条、微信、抖音）\n"
        "输出要求：给出具体的SEO优化建议，包含关键词列表和布局方案。"
    ),
    "data-analyst": (
        "你是一位数据分析专家。你擅长：\n"
        "- 从行业数据中提取关键指标和趋势\n"
        "- 识别数据异常和拐点\n"
        "- 构建数据驱动的论证链\n"
        "- 可视化数据叙事\n"
        "- 竞品数据对比和市场份额分析\n"
        "输出要求：用数据说话，给出具体数字、百分比和趋势判断。"
    ),
    "content-strategist": (
        "你是一位内容策略专家。你擅长：\n"
        "- 选题规划和内容日历制定\n"
        "- 内容矩阵设计（深度+短平快+互动）\n"
        "- 跨平台内容适配和差异化\n"
        "- 爆款内容模式识别和复制\n"
        "- 内容生命周期管理\n"
        "输出要求：给出可执行的内容策略，包含选题方向和发布节奏。"
    ),
}


class AgencyRoleLoader:
    """Load and manage AI role prompts from agency-agents or built-in library."""

    def __init__(self, roles_dir: str, logger: logging.Logger) -> None:
        self.roles_dir = Path(roles_dir)
        self.logger = logger
        self._cache: dict[str, str] = {}

    def get_role(self, role_name: str) -> str:
        """Get a role prompt by name.

        Lookup order:
        1. File in agency_roles_dir (e.g., config/agency_roles/marketing-expert.md)
        2. Built-in role library
        3. Empty string if not found
        """
        if role_name in self._cache:
            return self._cache[role_name]

        # Try file-based role
        role_file = self.roles_dir / f"{role_name}.md"
        if role_file.exists():
            content = role_file.read_text(encoding="utf-8").strip()
            if content:
                self._cache[role_name] = content
                return content

        # Try built-in role
        if role_name in _BUILTIN_ROLES:
            self._cache[role_name] = _BUILTIN_ROLES[role_name]
            return _BUILTIN_ROLES[role_name]

        self.logger.warning("Agency role not found: %s", role_name)
        return ""

    def list_roles(self) -> list[str]:
        """List all available role names (file-based + built-in)."""
        roles = set(_BUILTIN_ROLES.keys())
        if self.roles_dir.exists():
            for f in self.roles_dir.glob("*.md"):
                roles.add(f.stem)
        return sorted(roles)

    def get_role_context(self, role_names: list[str]) -> str:
        """Get combined context from multiple roles for Agent injection."""
        parts = []
        for name in role_names:
            prompt = self.get_role(name)
            if prompt:
                parts.append(f"【{name} 视角】\n{prompt}")
        return "\n\n".join(parts)
