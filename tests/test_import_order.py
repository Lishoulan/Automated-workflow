"""回归：`agents` 与 `services` 之间的循环导入（2026-09-17 修）。

原来的失败路径：
    import toutiao_content_factory.agents.<任意>
      -> 先初始化父包 services/__init__.py（原来急切 import agent_workflow）
      -> agent_workflow 又 `from toutiao_content_factory.agents import (12 个 Agent)`
      -> agents 只初始化到一半
      -> ImportError: cannot import name 'DataAgent' from partially initialized module

因为几乎所有 agents 模块都在模块级 import 某个 services 子模块，**任何** agents 先导入
的顺序都会炸（只是报错的模块名不同）。包入口改成 PEP 562 惰性导出后，环断开。

⚠️ 这两个用例必须用**子进程**跑：导入顺序是进程级全局状态，同进程里测不出来。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _run(code: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=env,
    )


def test_agents_can_be_imported_before_services() -> None:
    """agents 先导入曾必炸 —— 这是最容易被手写脚本踩到的顺序。"""
    result = _run(
        "import logging; logging.disable(logging.INFO)\n"
        "from toutiao_content_factory.agents.base import BaseAgent\n"
        "from toutiao_content_factory.agents import DataAgent, TitleAgent\n"
        "from toutiao_content_factory.services import PerformanceService\n"
        "print('ok')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_services_can_be_imported_before_agents() -> None:
    """反向顺序不能因为惰性导出而坏掉。"""
    result = _run(
        "import logging; logging.disable(logging.INFO)\n"
        "from toutiao_content_factory.services import ContentFactoryAppService, GrowthOpsService\n"
        "from toutiao_content_factory.agents import DataAgent\n"
        "print('ok')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_services_package_entry_stays_lazy() -> None:
    """包入口一旦改回急切导入，循环导入就会复现 —— 这里把它钉住。"""
    result = _run(
        "import sys, logging; logging.disable(logging.INFO)\n"
        "import toutiao_content_factory.services\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('toutiao_content_factory.services.'))\n"
        "print('LOADED=' + ','.join(loaded))\n"
    )
    assert result.returncode == 0, result.stderr
    loaded = result.stdout.strip().split("LOADED=")[-1]
    assert "agent_workflow" not in loaded, f"services 包入口不应急切导入 agent_workflow，实际加载了：{loaded}"


def test_lazy_exports_resolve_every_name_in_all() -> None:
    """`__all__` 里每个名字都必须真的取得到（新增服务忘了登记就会在这里炸）。"""
    result = _run(
        "import logging; logging.disable(logging.INFO)\n"
        "import toutiao_content_factory.services as s\n"
        "missing = [name for name in s.__all__ if not hasattr(s, name)]\n"
        "print('MISSING=' + ','.join(missing))\n"
    )
    assert result.returncode == 0, result.stderr
    missing = result.stdout.strip().split("MISSING=")[-1]
    assert missing == "", f"这些导出名取不到：{missing}"
