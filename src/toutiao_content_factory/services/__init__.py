"""服务层包入口（**惰性导出**，见下）。

为什么要惰性（2026-09-17 修的一个真实循环导入）：

    agents/base.py        ──> services.growth_ops_service
    agents/benchmark_*    ──> services.benchmark_hotspot_service
    ...（几乎所有 agents 模块都在模块级 import 某个 services）

只要 `import toutiao_content_factory.agents.<任意>`，而 `services` 还没被导入过，
Python 就会先初始化父包 `services/__init__.py`；它原来**急切**地 `from .agent_workflow import ...`，
而 `agent_workflow` 又 `from toutiao_content_factory.agents import (12 个 Agent)` ——
此时 `agents` 正处于"初始化到一半"的状态，于是报
`cannot import name 'DataAgent' from partially initialized module ...`。

实测依赖图（2026-09-17）：
- agents → services 的边：**几乎每个 agents 模块都有**；
- services → agents 的边：**只有 4 个**（`agent_workflow` / `app_service` /
  `feedback_loop_service` / `review_service`），且**被 agents 引用的那些 service
  （growth_ops / openai / prompt_repository / company_entities / article_sanitizer /
  benchmark_hotspot）一个都不反向引用 agents**。

所以只要让包入口不再"导入一个子模块就顺带把所有服务拉起来"，环就断了。
PEP 562 的模块级 `__getattr__` 正好：`from toutiao_content_factory.services import X` 照旧可用，
但只在真正取用 X 时才导入对应子模块。

⚠️ 注意事项：
- 不要改回急切导入（会复现上面的 ImportError）；
- 新增服务时，**记得往 `_LAZY_EXPORTS` 里加一条**（忘了的话 `from ... import X` 会报
  AttributeError 而不是静默出错，属于可接受的失败方式）。
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

# 导出名 -> 子模块名
_LAZY_EXPORTS: dict[str, str] = {
    "AgentWorkflowService": "agent_workflow",
    "ContentFactoryAppService": "app_service",
    "TopicAutomationService": "automation",
    "CacheService": "cache_service",
    "CompanyOSService": "company_os_service",
    "ExportService": "exporter",
    "GrowthOpsService": "growth_ops_service",
    "LeadOpsService": "lead_ops_service",
    "MonetizationService": "monetization_service",
    "SignalExtractor": "extractor",
    "OpenAIContentService": "openai_service",
    "ContentFactoryPipeline": "pipeline",
    "PerformanceService": "performance_service",
    "PromptRepository": "prompt_repository",
    "RevenueEngineService": "revenue_engine_service",
    "ReviewWorkflowService": "review_service",
    "TopicCollectionService": "topic_collection",
    "TopicExportService": "topic_exporter",
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", __name__), name)
    # 缓存到包命名空间，后续访问不再走 __getattr__
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
