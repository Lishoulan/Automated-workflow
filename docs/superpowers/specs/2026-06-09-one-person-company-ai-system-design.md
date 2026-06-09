# One-Person Company AI System Integration Design

> **Goal:** Integrate 5 open-source AI projects (DeerFlow, prompt-optimizer, agency-agents, Ruflo, Letta) into the existing content factory as Dockerized services, creating a full one-person company AI system.

---

## 1. System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   Docker Compose Network                   │
│                                                            │
│  ┌───────────┐  ┌───────────┐  ┌───────────────────┐    │
│  │ DeerFlow  │  │   Letta   │  │ prompt-optimizer  │    │
│  │ :8001     │  │  :8283    │  │     :8501         │    │
│  │ (调研员)   │  │ (进化大脑) │  │   (翻译师)        │    │
│  └─────┬─────┘  └─────┬─────┘  └────────┬──────────┘    │
│        │              │                  │                │
│        └──────────────┼──────────────────┘                │
│                       │                                    │
│  ┌────────────────────┴────────────────────┐             │
│  │        Content Factory (existing)        │             │
│  │        FastAPI + Playwright              │             │
│  │        :8000                             │             │
│  └────────────────────┬────────────────────┘             │
│                       │                                    │
│  ┌────────────┐  ┌────┴──────┐                           │
│  │   Ruflo    │  │  Agency   │                           │
│  │  :8002     │  │  Agents   │                           │
│  │ (总经理)    │  │ (员工库)  │                           │
│  └────────────┘  └───────────┘                           │
└──────────────────────────────────────────────────────────┘
```

## 2. Service Specifications

### 2.1 DeerFlow (AI Researcher)
- **Repo:** https://github.com/bytedance/deer-flow
- **Stars:** ~57k
- **Port:** 8001
- **Role:** Deep research agent — auto-search, summarize, compare, generate 30-page reports
- **Integration:**
  - Content factory calls `POST http://deerflow:8001/research` with a topic
  - Returns structured research report (markdown + JSON)
  - Replaces/enhances existing TopicAgent for deep research tasks
- **Trigger:** Daily topic research phase + manual deep-dive requests
- **Dependencies:** Python 3.11+, LangGraph, search APIs

### 2.2 Letta (AI Memory Brain)
- **Repo:** https://github.com/letta-ai/letta
- **Stars:** ~23k
- **Port:** 8283
- **Role:** Persistent memory layer — remembers user preferences, writing style, past decisions across conversations
- **Integration:**
  - Before each Agent call, query Letta for user context: `GET http://letta:8283/v1/agents/{agent_id}/memory`
  - After each run, write back observations: `POST http://letta:8283/v1/agents/{agent_id}/messages`
  - All existing Agents (InsightAgent, WriterAgent, etc.) get memory context injected
- **Trigger:** Every content generation run
- **Dependencies:** Python 3.10+, PostgreSQL (for persistence)

### 2.3 prompt-optimizer (AI Translator)
- **Repo:** https://github.com/linshenkx/prompt-optimizer
- **Stars:** ~10k
- **Port:** 8501
- **Role:** Translate casual requirements into professional AI prompts; optimize existing prompts
- **Integration:**
  - Offline mode: batch-optimize all prompts in `prompts/` directory
  - Online mode: `POST http://prompt-optimizer:8501/optimize` with raw prompt, returns optimized version
  - User input (casual requirements) gets auto-translated before feeding to Agents
- **Trigger:** Prompt template updates + user custom requirement input
- **Dependencies:** Node.js or Python, OpenAI-compatible API

### 2.4 Ruflo (AI Project Manager)
- **Repo:** https://github.com/ruvnet/ruflo
- **Stars:** ~48.5k
- **Port:** 8002
- **Role:** Multi-agent orchestration — decompose projects into subtasks, assign to AI employees, auto-verify
- **Integration:**
  - Content factory exposes Agent capabilities as tools Ruflo can call
  - Ruflo decomposes complex requests (e.g., "launch a new account") into subtasks
  - Each subtask calls back to content factory's API
- **Limitation:** Requires Anthropic API Key (Claude ecosystem)
- **Dependencies:** Node.js, Anthropic API Key

### 2.5 agency-agents (AI Employee Library)
- **Repo:** https://github.com/sitarzewski/agency-agents
- **Stars:** ~90k
- **Role:** 82 professional AI role prompts (frontend engineer, data analyst, marketing expert, SEO expert, etc.)
- **Integration:**
  - Role prompt files mounted as volume into Ruflo
  - Selected roles (marketing-expert, seo-expert, data-analyst) also embedded directly into content factory Agents
  - No separate service needed — it's a collection of prompt files

## 3. Data Flow

### Daily Run Flow (Enhanced)
```
1. Cron trigger → cloud_daily_run.py
2. Letta: load user preferences & historical context
3. DeerFlow: deep research on trending topics → structured report
4. prompt-optimizer: optimize topic selection prompt with user context
5. TopicAgent: select best topic using DeerFlow research + Letta context
6. InsightAgent: generate insights (with Letta memory of past insights)
7. WriterAgent: write article (with Letta style preferences)
8. ReviewAgent: review article (with Letta quality standards)
9. Letta: save this run's observations to memory
10. Publish: Toutiao + WeChat auto-publish
```

### Manual Deep Research Flow
```
1. User requests: "帮我调研人形机器人产业链"
2. Ruflo: decompose into subtasks
   - Task 1: DeerFlow research on humanoid robot supply chain
   - Task 2: Data analyst role analyze market data
   - Task 3: Marketing expert role identify content angles
3. Each subtask executes via content factory API
4. Ruflo: auto-verify results, compile final report
5. Letta: save research findings to memory
```

## 4. Docker Compose Structure

```yaml
# docker-compose.yml
services:
  content-factory:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    depends_on: [deerflow, letta, prompt-optimizer]
    volumes:
      - ./output:/app/output
      - ./prompts:/app/prompts

  deerflow:
    image: bytedance/deer-flow:latest
    ports: ["8001:8001"]
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL}

  letta:
    image: letta/letta:latest
    ports: ["8283:8283"]
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL}
    volumes:
      - letta_data:/root/.letta

  prompt-optimizer:
    image: linshenkx/prompt-optimizer:latest
    ports: ["8501:8501"]
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL}

  ruflo:
    image: ruvnet/ruflo:latest
    ports: ["8002:8002"]
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - ./agency-agents:/app/agents

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: letta
      POSTGRES_USER: letta
      POSTGRES_PASSWORD: ${LETTA_DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  letta_data:
  postgres_data:
```

## 5. Integration Points in Existing Code

### 5.1 New Files
- `src/toutiao_content_factory/services/deerflow_client.py` — DeerFlow API client
- `src/toutiao_content_factory/services/letta_client.py` — Letta memory client
- `src/toutiao_content_factory/services/prompt_optimizer_client.py` — Prompt optimizer client
- `src/toutiao_content_factory/services/ruflo_client.py` — Ruflo orchestration client
- `src/toutiao_content_factory/agents/agency_roles.py` — Agency role loader
- `docker-compose.yml` — Docker Compose orchestration
- `docker/Dockerfile` — Content factory Docker image

### 5.2 Modified Files
- `src/toutiao_content_factory/config.py` — Add service URLs and feature flags
- `src/toutiao_content_factory/services/agent_workflow.py` — Inject Letta context into Agent calls
- `src/toutiao_content_factory/agents/base.py` — Add memory context parameter
- `src/toutiao_content_factory/services/app_service.py` — Add deep research endpoint
- `main.py` — Add --deep-research CLI flag
- `.github/workflows/daily-run.yml` — Add Docker service containers

## 6. Phased Rollout

### Phase 1: Core Infrastructure (Week 1)
- Docker Compose setup with all 5 services
- Letta client integration (memory layer)
- DeerFlow client integration (research)

### Phase 2: Prompt & Role Enhancement (Week 2)
- prompt-optimizer integration
- agency-agents role embedding
- Prompt optimization pipeline

### Phase 3: Orchestration (Week 3)
- Ruflo integration
- Multi-agent task decomposition
- Auto-verification pipeline

### Phase 4: Cloud Deployment (Week 4)
- GitHub Actions integration with Docker services
- Cloud VPS deployment option
- Monitoring and health checks

## 7. Prerequisites

- Docker Desktop installed on Windows
- DeepSeek API Key with sufficient balance (current: insufficient)
- Anthropic API Key (for Ruflo)
- PostgreSQL for Letta persistence
- Minimum 8GB RAM for all Docker services
