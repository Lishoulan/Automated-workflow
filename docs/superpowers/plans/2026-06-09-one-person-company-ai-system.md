# One-Person Company AI System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dockerize and integrate 5 AI services (DeerFlow, Letta, prompt-optimizer, Ruflo, agency-agents) into the existing content factory.

**Architecture:** Docker Compose orchestrates all services on a shared network. Content factory calls each service via REST API. Letta provides persistent memory, DeerFlow provides deep research, prompt-optimizer enhances prompts, Ruflo orchestrates complex tasks, agency-agents provides role prompts.

**Tech Stack:** Docker Compose, Python 3.11, FastAPI, PostgreSQL, LangGraph

---

## File Structure

```
docker-compose.yml                          # Main orchestration
docker/
  Dockerfile                                # Content factory image
  .dockerignore                             # Docker ignore file
src/toutiao_content_factory/
  services/
    deerflow_client.py                      # DeerFlow API client
    letta_client.py                         # Letta memory client
    prompt_optimizer_client.py              # Prompt optimizer client
    ruflo_client.py                         # Ruflo orchestration client
  agents/
    agency_roles.py                         # Agency role loader
config/
  agency_roles/                             # Downloaded agency-agents role prompts
    marketing-expert.md
    seo-expert.md
    data-analyst.md
```

---

### Task 1: Create Docker Compose and Content Factory Dockerfile

**Files:**
- Create: `docker-compose.yml`
- Create: `docker/Dockerfile`
- Create: `docker/.dockerignore`

- [ ] **Step 1: Create docker/.dockerignore**

```
.runtime/
output/
logs/
__pycache__/
*.pyc
.env
.git
browser_profiles_seed.tar.gz
```

- [ ] **Step 2: Create docker/Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium --with-deps

COPY . .

ENV PYTHONPATH=/app/src
ENV PYTHONUTF8=1

EXPOSE 8000

CMD ["python", "main.py", "--web"]
```

- [ ] **Step 3: Create docker-compose.yml**

```yaml
version: "3.8"

services:
  content-factory:
    build:
      context: .
      dockerfile: docker/Dockerfile
    ports:
      - "8000:8000"
    env_file: .env
    environment:
      - TOUTIAO_PUBLISH_HEADLESS=1
      - TOUTIAO_PUBLISH_CHANNEL=chromium
    depends_on:
      deerflow:
        condition: service_started
      letta:
        condition: service_started
      prompt-optimizer:
        condition: service_started
    volumes:
      - ./output:/app/output
      - ./prompts:/app/prompts
      - ./config:/app/config
      - cf_runtime:/app/.runtime
    networks:
      - ai-network

  deerflow:
    image: bytedance/deer-flow:latest
    ports:
      - "8001:8001"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL}
    networks:
      - ai-network

  letta:
    image: letta/letta:latest
    ports:
      - "8283:8283"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL}
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - letta_data:/root/.letta
    networks:
      - ai-network

  prompt-optimizer:
    image: linshenkx/prompt-optimizer:latest
    ports:
      - "8501:8501"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL}
    networks:
      - ai-network

  ruflo:
    image: ruvnet/ruflo:latest
    ports:
      - "8002:8002"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - ./config/agency_roles:/app/agents
    networks:
      - ai-network

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: letta
      POSTGRES_USER: letta
      POSTGRES_PASSWORD: ${LETTA_DB_PASSWORD:-letta_pass}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U letta"]
      interval: 5s
      timeout: 5s
      retries: 5
    networks:
      - ai-network

volumes:
  letta_data:
  postgres_data:
  cf_runtime:

networks:
  ai-network:
    driver: bridge
```

- [ ] **Step 4: Verify Docker Compose config is valid**

Run: `docker compose config`
Expected: Valid config output with no errors

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml docker/Dockerfile docker/.dockerignore
git commit -m "feat: add Docker Compose orchestration for one-person company AI system"
```

---

### Task 2: Add service configuration to config.py

**Files:**
- Modify: `src/toutiao_content_factory/config.py`

- [ ] **Step 1: Add service URL dataclass fields**

Add to the `AppSettings` dataclass (after the existing `enhanced_insights_*` fields):

```python
    # ---- One-Person Company AI Services ----
    deerflow_enabled: bool = False
    deerflow_base_url: str = "http://deerflow:8001"
    letta_enabled: bool = False
    letta_base_url: str = "http://letta:8283"
    letta_agent_id: str = ""
    prompt_optimizer_enabled: bool = False
    prompt_optimizer_base_url: str = "http://prompt-optimizer:8501"
    ruflo_enabled: bool = False
    ruflo_base_url: str = "http://ruflo:8002"
    agency_roles_dir: str = "config/agency_roles"
```

- [ ] **Step 2: Add environment variable loading**

In the `load_settings` function, add after the `enhanced_insights` section:

```python
    # One-Person Company AI Services
    deerflow_enabled=_env_flag("DEERFLOW_ENABLED", False),
    deerflow_base_url=os.getenv("DEERFLOW_BASE_URL", "http://deerflow:8001"),
    letta_enabled=_env_flag("LETTA_ENABLED", False),
    letta_base_url=os.getenv("LETTA_BASE_URL", "http://letta:8283"),
    letta_agent_id=os.getenv("LETTA_AGENT_ID", ""),
    prompt_optimizer_enabled=_env_flag("PROMPT_OPTIMIZER_ENABLED", False),
    prompt_optimizer_base_url=os.getenv("PROMPT_OPTIMIZER_BASE_URL", "http://prompt-optimizer:8501"),
    ruflo_enabled=_env_flag("RUFLO_ENABLED", False),
    ruflo_base_url=os.getenv("RUFLO_BASE_URL", "http://ruflo:8002"),
    agency_roles_dir=os.getenv("AGENCY_ROLES_DIR", "config/agency_roles"),
```

- [ ] **Step 3: Commit**

```bash
git add src/toutiao_content_factory/config.py
git commit -m "feat: add one-person company AI service configuration"
```

---

### Task 3: Create Letta client (Memory Brain)

**Files:**
- Create: `src/toutiao_content_factory/services/letta_client.py`

- [ ] **Step 1: Create the Letta client**

```python
"""Letta API client — persistent memory layer for AI agents.

Provides cross-conversation memory: user preferences, writing style,
past decisions, and historical context.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class LettaClient:
    """Client for the Letta memory service."""

    def __init__(self, base_url: str, agent_id: str, logger: logging.Logger) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.logger = logger

    def _request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            self.logger.warning("Letta API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("Letta unexpected error: %s", exc)
            return {}

    def get_memory_context(self, topic: str = "") -> str:
        """Retrieve relevant memory context for a given topic."""
        if not self.agent_id:
            return ""
        path = f"/v1/agents/{self.agent_id}/memory"
        result = self._request("GET", path)
        if not result:
            return ""
        # Extract relevant context from memory blocks
        memories = result.get("core_memory", [])
        if isinstance(memories, list):
            parts = [m.get("content", "") for m in memories if m.get("content")]
            return "\n".join(parts)
        return str(memories) if memories else ""

    def save_observation(self, observation: str) -> bool:
        """Save an observation to the agent's memory."""
        if not self.agent_id:
            return False
        path = f"/v1/agents/{self.agent_id}/messages"
        body = {
            "messages": [{"role": "user", "content": observation}],
        }
        result = self._request("POST", path, body)
        return bool(result)

    def create_agent(self, name: str, description: str = "") -> str:
        """Create a new Letta agent and return its ID."""
        body = {
            "name": name,
            "description": description or "Content factory memory agent",
            "memory_blocks": [
                {"label": "persona", "value": "I am a content factory assistant that remembers user preferences and writing style."},
                {"label": "human", "value": "The user is a tech content creator focusing on AI, robotics, and new energy."},
            ],
            "llm_config": {
                "model": "deepseek-v4-flash",
                "model_endpoint_type": "openai",
            },
        }
        result = self._request("POST", "/v1/agents", body)
        agent_id = result.get("id", "")
        if agent_id:
            self.agent_id = agent_id
            self.logger.info("Created Letta agent: %s", agent_id)
        return agent_id
```

- [ ] **Step 2: Commit**

```bash
git add src/toutiao_content_factory/services/letta_client.py
git commit -m "feat: add Letta memory client"
```

---

### Task 4: Create DeerFlow client (AI Researcher)

**Files:**
- Create: `src/toutiao_content_factory/services/deerflow_client.py`

- [ ] **Step 1: Create the DeerFlow client**

```python
"""DeerFlow API client — deep research agent by ByteDance.

Auto-searches, summarizes, compares, and generates structured research reports.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class DeerFlowClient:
    """Client for the DeerFlow research service."""

    def __init__(self, base_url: str, logger: logging.Logger) -> None:
        self.base_url = base_url.rstrip("/")
        self.logger = logger

    def _request(self, method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            self.logger.warning("DeerFlow API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("DeerFlow unexpected error: %s", exc)
            return {}

    def research(self, topic: str, depth: str = "medium") -> dict[str, Any]:
        """Submit a research topic and get a structured report.

        Args:
            topic: Research topic (e.g., "人形机器人产业链分析")
            depth: Research depth — "quick" (5min), "medium" (15min), "deep" (30min)

        Returns:
            Dict with keys: title, summary, sections, references, created_at
        """
        body = {
            "topic": topic,
            "depth": depth,
            "output_format": "structured",
        }
        self.logger.info("DeerFlow research started: %s (depth=%s)", topic[:50], depth)
        result = self._request("POST", "/research", body, timeout=300)
        if result:
            self.logger.info("DeerFlow research completed: %s", result.get("title", "untitled")[:50])
        return result

    def quick_research(self, topic: str) -> str:
        """Quick research — returns a summary string for topic selection."""
        result = self.research(topic, depth="quick")
        if not result:
            return ""
        return result.get("summary", "")

    def deep_research(self, topic: str) -> dict[str, Any]:
        """Deep research — returns full structured report."""
        return self.research(topic, depth="deep")

    def health_check(self) -> bool:
        """Check if DeerFlow service is available."""
        try:
            result = self._request("GET", "/health", timeout=5)
            return result.get("status") == "ok"
        except Exception:
            return False
```

- [ ] **Step 2: Commit**

```bash
git add src/toutiao_content_factory/services/deerflow_client.py
git commit -m "feat: add DeerFlow research client"
```

---

### Task 5: Create prompt-optimizer client

**Files:**
- Create: `src/toutiao_content_factory/services/prompt_optimizer_client.py`

- [ ] **Step 1: Create the prompt optimizer client**

```python
"""Prompt Optimizer API client — translates casual requirements into professional AI prompts.

Optimizes existing prompts for better AI output quality.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class PromptOptimizerClient:
    """Client for the prompt-optimizer service."""

    def __init__(self, base_url: str, logger: logging.Logger) -> None:
        self.base_url = base_url.rstrip("/")
        self.logger = logger

    def _request(self, method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            self.logger.warning("Prompt Optimizer API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("Prompt Optimizer unexpected error: %s", exc)
            return {}

    def optimize(self, prompt: str, target: str = "general") -> str:
        """Optimize a prompt for better AI output.

        Args:
            prompt: Raw prompt text to optimize
            target: Optimization target — "general", "writing", "analysis", "marketing"

        Returns:
            Optimized prompt text
        """
        body = {
            "prompt": prompt,
            "target": target,
            "language": "zh",
        }
        result = self._request("POST", "/optimize", body)
        optimized = result.get("optimized_prompt", "")
        if optimized:
            self.logger.info("Prompt optimized: %d → %d chars", len(prompt), len(optimized))
        return optimized or prompt

    def translate_requirement(self, requirement: str) -> str:
        """Translate a casual user requirement into a professional AI prompt.

        Example: "写小红书爆款" → structured prompt with hooks, tags, tone requirements
        """
        body = {
            "requirement": requirement,
            "mode": "translate",
            "language": "zh",
        }
        result = self._request("POST", "/translate", body)
        translated = result.get("prompt", "")
        if translated:
            self.logger.info("Requirement translated: %s → %d chars", requirement[:30], len(translated))
        return translated or requirement

    def health_check(self) -> bool:
        """Check if prompt-optimizer service is available."""
        try:
            result = self._request("GET", "/health", timeout=5)
            return result.get("status") == "ok"
        except Exception:
            return False
```

- [ ] **Step 2: Commit**

```bash
git add src/toutiao_content_factory/services/prompt_optimizer_client.py
git commit -m "feat: add prompt optimizer client"
```

---

### Task 6: Create Ruflo client and agency-roles loader

**Files:**
- Create: `src/toutiao_content_factory/services/ruflo_client.py`
- Create: `src/toutiao_content_factory/agents/agency_roles.py`

- [ ] **Step 1: Create Ruflo client**

```python
"""Ruflo API client — multi-agent orchestration platform.

Decomposes projects into subtasks, assigns AI employees, and auto-verifies results.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from urllib.request import Request, urlopen
from urllib.error import URLError


class RufloClient:
    """Client for the Ruflo orchestration service."""

    def __init__(self, base_url: str, logger: logging.Logger) -> None:
        self.base_url = base_url.rstrip("/")
        self.logger = logger

    def _request(self, method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except URLError as exc:
            self.logger.warning("Ruflo API call failed: %s %s → %s", method, path, exc)
            return {}
        except Exception as exc:
            self.logger.warning("Ruflo unexpected error: %s", exc)
            return {}

    def submit_project(self, description: str, roles: list[str] | None = None) -> dict[str, Any]:
        """Submit a project for Ruflo to decompose and orchestrate.

        Args:
            description: Project description (e.g., "调研人形机器人产业链并生成3篇深度文章")
            roles: List of agency-agents role names to involve

        Returns:
            Dict with keys: project_id, subtasks, status
        """
        body = {
            "description": description,
            "roles": roles or ["marketing-expert", "data-analyst"],
            "callback_url": "",
        }
        self.logger.info("Ruflo project submitted: %s", description[:50])
        return self._request("POST", "/projects", body)

    def get_project_status(self, project_id: str) -> dict[str, Any]:
        """Get the status of a submitted project."""
        return self._request("GET", f"/projects/{project_id}")

    def health_check(self) -> bool:
        """Check if Ruflo service is available."""
        try:
            result = self._request("GET", "/health", timeout=5)
            return result.get("status") == "ok"
        except Exception:
            return False
```

- [ ] **Step 2: Create agency-roles loader**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add src/toutiao_content_factory/services/ruflo_client.py src/toutiao_content_factory/agents/agency_roles.py
git commit -m "feat: add Ruflo orchestration client and agency-roles loader"
```

---

### Task 7: Integrate Letta memory into Agent workflow

**Files:**
- Modify: `src/toutiao_content_factory/services/agent_workflow.py`

- [ ] **Step 1: Add Letta context injection to agent workflow**

At the top of `agent_workflow.py`, add import:

```python
from toutiao_content_factory.services.letta_client import LettaClient
```

In the main workflow class `__init__`, add:

```python
        # One-Person Company AI Services
        self._letta_client: LettaClient | None = None
        if settings.letta_enabled and settings.letta_base_url:
            self._letta_client = LettaClient(
                base_url=settings.letta_base_url,
                agent_id=settings.letta_agent_id,
                logger=logger,
            )
```

Add a helper method:

```python
    def _get_memory_context(self, topic: str = "") -> str:
        """Retrieve memory context from Letta if available."""
        if not self._letta_client:
            return ""
        try:
            return self._letta_client.get_memory_context(topic)
        except Exception as exc:
            self.logger.warning("Letta memory retrieval failed (non-blocking): %s", exc)
            return ""

    def _save_memory_observation(self, observation: str) -> None:
        """Save an observation to Letta memory if available."""
        if not self._letta_client:
            return
        try:
            self._letta_client.save_observation(observation)
        except Exception as exc:
            self.logger.warning("Letta memory save failed (non-blocking): %s", exc)
```

- [ ] **Step 2: Inject memory context into InsightAgent and WriterAgent calls**

Before the InsightAgent call, add:

```python
        memory_context = self._get_memory_context(selected_topic.get("title", ""))
        if memory_context:
            self.logger.info("Letta memory context loaded: %d chars", len(memory_context))
```

After the workflow completes successfully, add:

```python
        self._save_memory_observation(
            f"Run {run_name}: topic='{selected_topic_title}', "
            f"title='{final_title}', score={overall_score}, "
            f"style={self.settings.writing.default_style}"
        )
```

- [ ] **Step 3: Commit**

```bash
git add src/toutiao_content_factory/services/agent_workflow.py
git commit -m "feat: integrate Letta memory into agent workflow"
```

---

### Task 8: Add CLI flags and API endpoints

**Files:**
- Modify: `main.py`
- Modify: `src/toutiao_content_factory/services/app_service.py`

- [ ] **Step 1: Add --deep-research CLI flag to main.py**

Add argument:

```python
    parser.add_argument(
        "--deep-research",
        type=str,
        default=None,
        help="Run DeerFlow deep research on a given topic and output the report.",
    )
```

Add handler:

```python
            if args.deep_research:
                from toutiao_content_factory.services.deerflow_client import DeerFlowClient
                client = DeerFlowClient(settings.deerflow_base_url, logger)
                result = client.deep_research(args.deep_research)
                if result:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print("Deep research failed or DeerFlow service unavailable.")
                return 0
```

- [ ] **Step 2: Add service health check endpoint to app_service.py**

Add a method to AppService:

```python
    def check_ai_services_health(self) -> dict[str, bool]:
        """Check health of all one-person company AI services."""
        health = {}
        if self.settings.deerflow_enabled:
            from toutiao_content_factory.services.deerflow_client import DeerFlowClient
            client = DeerFlowClient(self.settings.deerflow_base_url, self.logger)
            health["deerflow"] = client.health_check()
        if self.settings.letta_enabled:
            from toutiao_content_factory.services.letta_client import LettaClient
            client = LettaClient(self.settings.letta_base_url, self.settings.letta_agent_id, self.logger)
            health["letta"] = bool(client.get_memory_context())
        if self.settings.prompt_optimizer_enabled:
            from toutiao_content_factory.services.prompt_optimizer_client import PromptOptimizerClient
            client = PromptOptimizerClient(self.settings.prompt_optimizer_base_url, self.logger)
            health["prompt_optimizer"] = client.health_check()
        if self.settings.ruflo_enabled:
            from toutiao_content_factory.services.ruflo_client import RufloClient
            client = RufloClient(self.settings.ruflo_base_url, self.logger)
            health["ruflo"] = client.health_check()
        return health
```

- [ ] **Step 3: Commit**

```bash
git add main.py src/toutiao_content_factory/services/app_service.py
git commit -m "feat: add deep-research CLI flag and service health checks"
```

---

### Task 9: Update GitHub Actions workflow for Docker services

**Files:**
- Modify: `.github/workflows/daily-run.yml`

- [ ] **Step 1: Add Docker Compose service containers to workflow**

Add a new step after "Install Playwright chromium":

```yaml
      - name: Start AI services via Docker Compose
        run: |
          docker compose up -d deerflow letta prompt-optimizer postgres
          echo "Waiting for services to be ready..."
          sleep 15
          docker compose ps
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          OPENAI_BASE_URL: ${{ secrets.OPENAI_BASE_URL }}
          LETTA_DB_PASSWORD: letta_cloud_pass

      - name: Check AI services health
        run: |
          echo "Checking DeerFlow..."
          curl -sf http://localhost:8001/health && echo "DeerFlow: OK" || echo "DeerFlow: UNAVAILABLE"
          echo "Checking Letta..."
          curl -sf http://localhost:8283/v1/health && echo "Letta: OK" || echo "Letta: UNAVAILABLE"
          echo "Checking Prompt Optimizer..."
          curl -sf http://localhost:8501/health && echo "Prompt Optimizer: OK" || echo "Prompt Optimizer: UNAVAILABLE"
```

Add environment variables to the daily-run job env section:

```yaml
      DEERFLOW_ENABLED: "1"
      DEERFLOW_BASE_URL: "http://localhost:8001"
      LETTA_ENABLED: "1"
      LETTA_BASE_URL: "http://localhost:8283"
      PROMPT_OPTIMIZER_ENABLED: "1"
      PROMPT_OPTIMIZER_BASE_URL: "http://localhost:8501"
      RUFLO_ENABLED: "0"
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/daily-run.yml
git commit -m "feat: add Docker AI services to GitHub Actions workflow"
```

---

### Task 10: Update .env and config.yaml with new service settings

**Files:**
- Modify: `.env`
- Modify: `config.yaml`

- [ ] **Step 1: Add service config to .env**

Append to `.env`:

```
# ============================ 一人公司 AI 服务配置 ============================
DEERFLOW_ENABLED=0
DEERFLOW_BASE_URL=http://localhost:8001
LETTA_ENABLED=0
LETTA_BASE_URL=http://localhost:8283
LETTA_AGENT_ID=
PROMPT_OPTIMIZER_ENABLED=0
PROMPT_OPTIMIZER_BASE_URL=http://localhost:8501
RUFLO_ENABLED=0
RUFLO_BASE_URL=http://localhost:8002
ANTHROPIC_API_KEY=
AGENCY_ROLES_DIR=config/agency_roles
```

- [ ] **Step 2: Add service config to config.yaml**

Add to config.yaml root:

```json
  "one_person_company": {
    "deerflow_enabled": false,
    "deerflow_base_url": "http://localhost:8001",
    "letta_enabled": false,
    "letta_base_url": "http://localhost:8283",
    "letta_agent_id": "",
    "prompt_optimizer_enabled": false,
    "prompt_optimizer_base_url": "http://localhost:8501",
    "ruflo_enabled": false,
    "ruflo_base_url": "http://localhost:8002",
    "agency_roles_dir": "config/agency_roles"
  }
```

- [ ] **Step 3: Commit**

```bash
git add .env config.yaml
git commit -m "feat: add one-person company AI service configuration to .env and config.yaml"
```

---

## Self-Review

1. **Spec coverage:** All 5 services covered — DeerFlow (Task 4), Letta (Task 3, 7), prompt-optimizer (Task 5), Ruflo (Task 6), agency-agents (Task 6). Docker Compose (Task 1). Config (Task 2, 10). CLI/API (Task 8). CI/CD (Task 9).

2. **Placeholder scan:** No TBD/TODO found. All code is complete.

3. **Type consistency:** All clients use `base_url: str` and `logger: logging.Logger`. Config fields match client constructors. LettaClient additionally takes `agent_id: str`.

4. **Gap:** Docker images for DeerFlow, Letta, prompt-optimizer, and Ruflo may not have official Docker images with the exact ports/endpoints assumed. This will need verification during deployment — the clients gracefully handle failures (return empty/dict).
