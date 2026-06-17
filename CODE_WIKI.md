# Code Wiki — 手蓝 AI 内容工厂 (Automated-workflow)

> 版本：2.0.0 | Python ≥ 3.10 | 最后更新：2026-06-17
>
> 本文档基于对仓库源码的逐层通读分析生成，覆盖项目整体架构、主要模块职责、关键类与函数说明、依赖关系以及项目运行方式。

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈与依赖](#2-技术栈与依赖)
3. [项目目录结构](#3-项目目录结构)
4. [整体架构](#4-整体架构)
5. [核心模块详解](#5-核心模块详解)
   - 5.1 [入口层](#51-入口层)
   - 5.2 [配置层 (config)](#52-配置层-config)
   - 5.3 [数据模型层 (models)](#53-数据模型层-models)
   - 5.4 [异常体系 (exceptions)](#54-异常体系-exceptions)
   - 5.5 [Agent 智能体层](#55-agent-智能体层)
   - 5.6 [服务层 (services)](#56-服务层-services)
   - 5.7 [数据库层 (database)](#57-数据库层-database)
   - 5.8 [加载器层 (loaders)](#58-加载器层-loaders)
   - 5.9 [Web 控制台层 (web)](#59-web-控制台层-web)
   - 5.10 [工具层 (utils)](#510-工具层-utils)
   - 5.11 [常量层 (constants)](#511-常量层-constants)
6. [关键类与函数说明](#6-关键类与函数说明)
7. [核心工作流](#7-核心工作流)
8. [多账号矩阵体系](#8-多账号矩阵体系)
9. [多平台发布体系](#9-多平台发布体系)
10. [商业化与变现系统](#10-商业化与变现系统)
11. [Prompt 模板体系](#11-prompt-模板体系)
12. [依赖关系图](#12-依赖关系图)
13. [项目运行方式](#13-项目运行方式)
14. [部署与调度](#14-部署与调度)

---

## 1. 项目概述

**手蓝 AI 内容工厂**（AI Content Factory / Toutiao Content Factory）是一套面向中文科技自媒体的**全自动内容生产流水线**。从热点采集、数据分析、文章撰写、标题优化、合规审核到多平台发布，全部由 AI Agent 协作完成，无需人工干预。

### 核心能力

- **智能选题**：从 36kr、智东西、OFweek、量子位、机器之心、雷锋网等行业媒体源自动采集热点话题，通过关键词匹配与评分排序筛选优质选题
- **AI 内容生成**：基于 OpenAI 兼容 API（DeepSeek / GPT / Qwen）驱动多 Agent 协作流水线，完成数据提取、趋势分析、文章撰写、标题生成、润色优化、质量审核等全流程
- **多平台发布**：支持今日头条、微信公众号、B 站、知乎、小红书等平台的自动化发布（RPA 浏览器自动化 + API）
- **多账号矩阵运营**：支持多个内容账号独立配置与并行运营，账号间隔离数据、输出目录与浏览器 Profile
- **合规先行**：AIGC 声明、AI 风险评分、敏感词守卫（AC 自动机）、内容查重，符合 2026 平台规则
- **商业化闭环**：集成线索管理、收入引擎、增长运营、赞助商管理、订阅者管理等"一人公司"AI 系统
- **定时调度**：内置 APScheduler 定时调度 + Windows 任务计划程序 + GitHub Actions 云端运行
- **Web 控制台**：FastAPI 驱动的本地 Dashboard，实时监控选题池、生成进度、审核结果、发布状态

### 一句话定位

> 给一个 API Key，还你一个 7×24 小时不停更的自媒体矩阵。

---

## 2. 技术栈与依赖

| 类别 | 技术 | 版本要求 | 用途 |
|------|------|----------|------|
| 语言 | Python | ≥ 3.10 | 主开发语言 |
| Web 框架 | FastAPI + Uvicorn | ≥0.115.0 / ≥0.30.0 | Web 控制台与 API |
| 浏览器自动化 | Playwright | 1.53.0（pyproject）/ ≥1.52.0（requirements） | 多平台 RPA 发布（Edge/Chromium） |
| AI 接口 | OpenAI-compatible API | openai ≥1.30.0 | DeepSeek / GPT / Qwen 等模型调用 |
| 图像处理 | Pillow / Matplotlib | ≥10.0.0 / ≥3.8.0 | 封面图与信息图渲染 |
| 模板引擎 | Jinja2 | ≥3.1.0 | HTML 模板渲染 |
| 配置管理 | PyYAML + python-dotenv | ≥6.0 / ≥1.0.1 | YAML 配置 + .env 环境变量 |
| 数据库 | SQLModel + SQLite | ≥0.0.22 | 持久化 Topic/Article/RunRecord |
| PDF 解析 | pypdf | ≥4.2.0 | PDF 源文档加载 |
| 文件上传 | python-multipart | ≥0.0.9 | Web 文件上传 |
| 任务调度 | APScheduler | ≥3.10.0 | 后台定时任务 |
| 系统监控 | psutil | ≥5.9.8 | 进程与心跳检测 |
| 重试机制 | tenacity | ≥8.2.0 | 网络请求重试 |
| HTTP 客户端 | httpx | ≥0.27.0 | 异步 HTTP 调用 |
| 数据校验 | pydantic | ≥2.0.0 | 模型校验 |
| 操作系统 | Windows 10/11 | Microsoft Edge | 主要运行环境（RPA 依赖） |

### 可选 AI 服务（docker-compose 编排）

| 服务 | 端口 | 用途 |
|------|------|------|
| deerflow | 8001 | 字节跳动 DeerFlow 深度研究 |
| letta | 8283 | Letta 记忆系统（依赖 postgres） |
| prompt-optimizer | 8501 | Prompt 优化服务 |
| ruflo | 8002 | 工作流编排服务 |
| postgres | 5432 | Letta 后端数据库 |

---

## 3. 项目目录结构

```
Automated-workflow-main/
├── src/toutiao_content_factory/        # 应用核心源码包
│   ├── __init__.py
│   ├── config.py                       # 配置加载与 AppSettings
│   ├── models.py                       # 数据模型 (DTO)
│   ├── exceptions.py                   # 自定义异常体系
│   ├── agents/                         # AI Agent 智能体集合
│   │   ├── base.py                     # Agent 基类
│   │   ├── agency_roles.py             # Agent 角色定义加载器
│   │   ├── trend_agent.py              # 热点采集 Agent
│   │   ├── data_agent.py               # 数据分析 Agent
│   │   ├── data_mining_agent.py        # 数据挖掘 Agent
│   │   ├── insight_agent.py            # 洞察 Agent
│   │   ├── writer_agent.py             # 文章写作 Agent
│   │   ├── title_agent.py              # 标题优化 Agent
│   │   ├── polish_agent.py             # 润色 Agent
│   │   ├── review_agent.py             # 审核 Agent
│   │   ├── publish_review_agent.py     # 发布前审核 Agent
│   │   ├── wechat_layout_agent.py      # 微信排版 Agent
│   │   ├── evolution_agent.py          # 演进 Agent
│   │   ├── sample_collector_agent.py   # 样本采集 Agent
│   │   └── benchmark_monitor_agent.py  # 对标监控 Agent
│   ├── services/                       # 核心业务服务（60+ 文件）
│   │   ├── app_service.py              # 应用编排门面
│   │   ├── pipeline.py                 # 内容生产流水线
│   │   ├── agent_workflow.py           # Agent 工作流编排
│   │   ├── openai_service.py           # OpenAI 兼容 API 封装
│   │   ├── content_generator.py        # 内容生成器（兼容/兜底）
│   │   ├── extractor.py                # 信号提取器
│   │   ├── cache_service.py            # 缓存服务
│   │   ├── exporter.py                 # 导出服务
│   │   ├── pipeline_state_manager.py   # 流水线状态管理
│   │   ├── topic_pool_service.py       # 选题池服务
│   │   ├── topic_collection.py         # 选题采集
│   │   ├── review_service.py           # 审核工作流
│   │   ├── publish_service.py          # 今日头条发布
│   │   ├── wechat_service.py           # 微信公众号发布
│   │   ├── multi_platform_publisher.py # 多平台发布器
│   │   ├── bilibili_publisher.py       # B 站发布
│   │   ├── zhihu_publisher.py          # 知乎发布
│   │   ├── xiaohongshu_publisher.py    # 小红书发布
│   │   ├── compliance_service.py       # 平台合规约束
│   │   ├── content_guard_service.py    # 内容红线守卫
│   │   ├── article_quality_checker.py  # 文章质量检查
│   │   ├── article_sanitizer.py        # 文章清洗
│   │   ├── anti_ai_flavor.py           # 去 AI 味处理
│   │   ├── growth_ops_service.py       # 增长运营服务
│   │   ├── company_os_service.py       # 一人公司 OS
│   │   ├── lead_ops_service.py         # 线索运营
│   │   ├── monetization_service.py     # 变现服务
│   │   ├── monetization_engine.py      # 变现引擎
│   │   ├── revenue_engine_service.py   # 收入引擎
│   │   ├── benchmark_hotspot_service.py# 对标热点服务
│   │   ├── feedback_service.py         # 反馈服务
│   │   ├── feedback_loop_service.py    # 反馈闭环
│   │   ├── performance_service.py      # 表现追踪
│   │   ├── platform_health_service.py  # 平台健康检查
│   │   ├── notification_service.py     # 通知服务
│   │   ├── circuit_breaker.py          # 平台熔断器
│   │   ├── browser_launch_guard.py     # 浏览器启动守卫
│   │   ├── image_generation_service.py # 图片生成
│   │   ├── frontend_image_renderer.py  # 前端图片渲染
│   │   ├── wechat_layout_engine.py     # 微信排版引擎
│   │   ├── wechat_135editor_service.py # 135 编辑器服务
│   │   ├── md2wechat_service.py        # Markdown 转微信
│   │   ├── wechat_launch_service.py    # 微信启动周服务
│   │   ├── wechat_pages.py             # 微信页面定义
│   │   ├── deerflow_client.py          # DeerFlow 客户端
│   │   ├── letta_client.py             # Letta 记忆客户端
│   │   ├── ruflo_client.py             # Ruflo 客户端
│   │   ├── prompt_optimizer_client.py  # Prompt 优化客户端
│   │   ├── prompt_repository.py        # Prompt 仓库
│   │   ├── schema_guard_service.py     # Schema 守卫
│   │   ├── search_service.py           # 搜索服务
│   │   ├── matrix_ops_manager.py       # 矩阵运营管理
│   │   ├── engagement_agent.py         # 互动 Agent
│   │   ├── debug_snapshot_service.py   # 调试快照
│   │   ├── html_anything_service.py    # HTML 转换服务
│   │   ├── first_release_adapter.py    # 首发适配器
│   │   ├── topic_exporter.py           # 选题导出
│   │   ├── examples.py                 # 示例
│   │   ├── rpa_utils.py                # RPA 工具
│   │   ├── workflow_dag.py             # 工作流 DAG
│   │   └── workflow_executor.py        # 工作流执行器
│   ├── database/                       # 数据库层
│   │   ├── models.py                   # SQLModel 表定义
│   │   └── service.py                  # 数据库 CRUD 服务
│   ├── loaders/                        # 文档加载器
│   │   └── document_loader.py
│   ├── renderers/                      # 渲染器
│   ├── constants/                      # 常量
│   │   └── selectors.py                # 平台选择器常量
│   ├── utils/                          # 工具函数
│   │   ├── browser_utils.py            # 浏览器自动化工具
│   │   ├── file_utils.py               # 文件读写工具
│   │   ├── logging_utils.py            # 日志与指标
│   │   └── runtime_status.py           # 运行时状态/心跳
│   └── web/                            # Web 控制台
│       ├── app.py                      # FastAPI 应用主体
│       └── view_service.py             # 视图聚合服务
├── prompts/                            # Prompt 模板（20+ 文件）
├── scripts/                            # 启动/调度/登录脚本（70+ 文件）
├── templates/                          # HTML 模板
│   ├── web/                            # Web 页面模板
│   └── images/                         # 图片模板
├── static/                             # CSS/图标
├── branding/                           # 品牌与对标配置
├── config/                             # 运行时配置
│   ├── deerflow/config.yaml
│   ├── selectors.yaml                  # 平台选择器
│   ├── ai_cliche_words.txt             # AI 套话词表
│   ├── bad_words.txt                   # 敏感词词表
│   └── company_*.yaml                  # 公司运营配置
├── aitoearn_src/                       # AIToEarn 集成源码（TypeScript）
├── docs/                               # 文档
├── tests/                              # 测试
├── input/                              # 输入素材目录
├── main.py                             # CLI 入口
├── webapp.py                           # Web 入口
├── scheduler.py                        # 独立调度器
├── run.py / run_web.py                 # 辅助运行脚本
├── config.yaml                         # 应用主配置
├── .env.example                        # 环境变量示例
├── requirements.txt / pyproject.toml   # 依赖声明
├── Dockerfile / docker-compose.yml     # 容器化
└── .github/workflows/daily-run.yml     # GitHub Actions 云端调度
```

---

## 4. 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        入口层 (Entry Points)                         │
│   main.py (CLI)   │   webapp.py (Web)   │   scheduler.py (定时)      │
└──────────┬──────────────────┬───────────────────┬────────────────────┘
           │                  │                   │
           ▼                  ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│              编排层 (Orchestration Layer)                            │
│            ContentFactoryAppService (门面)                           │
│   collect_topics │ manual_run │ auto_run │ daily_run │ approve/publish│
└──────────┬──────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              流水线层 (Pipeline Layer)                               │
│            ContentFactoryPipeline                                    │
│  加载 → 提取 → 缓存检查 → AgentWorkflow → 导出                       │
└──────────┬──────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Agent 编排层 (AgentWorkflowService)                     │
│  Evolution → Trend → Data → [Insight] → Writer → Polish →           │
│  Title → Review → [二次Polish] → PublishReview → BadWordsGate       │
└──────────┬──────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              基础设施层 (Infrastructure)                              │
│  OpenAIContentService │ PromptRepository │ ContentGuardService      │
│  CacheService │ ExportService │ PipelineStateManager                │
│  GrowthOpsService │ DatabaseService                                    │
└──────────┬──────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              发布层 (Publishing Layer)                               │
│  ReviewWorkflowService (审核)                                        │
│   ├── PublishService (头条, Playwright)                              │
│   ├── WechatPublishService (微信, API+浏览器)                        │
│   └── MultiPlatformPublisher (B站/知乎/小红书)                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 架构特点

1. **门面模式**：`ContentFactoryAppService` 统一持有 12+ 子服务，对外暴露高层 API
2. **流水线模式**：`ContentFactoryPipeline` 串联"加载→提取→缓存→生成→导出"
3. **多 Agent 协作**：10 个专职 Agent 按既定顺序生成结构化内容
4. **统一回退策略**：每个 Agent 在模型调用失败时都有本地 `_fallback` 实现，保证流水线不中断
5. **多账号隔离**：通过 `account_key` 切换 settings/output/logs/profile 目录
6. **文件优先 + 数据库可选**：`DatabaseService` 在 sqlmodel 缺失时降级为 no-op
7. **YAML 优先 + 硬编码兜底**：selectors、config 均支持 YAML 覆盖

---

## 5. 核心模块详解

### 5.1 入口层

#### `main.py` — CLI 入口

- **职责**：命令行入口，解析 100+ 参数，分发到 `ContentFactoryAppService` 的对应方法
- **关键函数**：
  - `build_parser() -> argparse.ArgumentParser`：构建参数解析器（涵盖选题/生成/审核/发布/线索/对标/表现/变现等全量命令）
  - `main() -> int`：主入口，加载 settings → 初始化 logger → 按 args 分发到 app_service 各方法
  - `_is_review_or_publish_action(args) -> bool`：判断是否为审核/发布类操作
  - `_handle_daily_run_exception(...)`：处理 daily_run 的已知异常（无选题/登录过期）
  - `_write_daily_run_result(...)`：将运行结果写入 `last_daily_run_result.json`
- **依赖**：`ContentFactoryAppService`、`load_settings`、`prepare_browser_profile`

#### `webapp.py` — Web 入口

- **职责**：Web 控制台命令行入口，参数解析、端口探测与 uvicorn 启动
- **关键内容**：
  - `--account` / `--host` / `--port` 参数（默认 `TOUTIAO_WEB_HOST=127.0.0.1`、`TOUTIAO_WEB_PORT=8001`）
  - `is_port_available()`：socket bind 检测端口占用
  - 调用 `create_app(PROJECT_ROOT, account_key=...)` 构造 FastAPI 实例

#### `scheduler.py` — 独立调度器

- **职责**：轮询式定时调度器，按固定时间点触发 `main.py --daily-run`
- **关键内容**：
  - `RUN_TIMES = ["07:00", "11:30", "18:00", "22:00"]`
  - `run_daily_workflow()`：通过 `subprocess.run` 调用 `python main.py --account ... --daily-run`
  - `wait_until(target_time)`：每分钟检查一次，避免长 sleep
  - 日志写入 `.runtime/scheduler.log`

---

### 5.2 配置层 (config)

#### `src/toutiao_content_factory/config.py`

- **职责**：分层配置加载器，按"账号级环境变量 > 全局环境变量 > 账号覆盖 > config.yaml > 代码默认值"优先级合并配置
- **关键类/函数**：
  - `AppSettings`：核心配置类，包含 60+ 字段（openai_api_key、style_profiles、topic_collection、image_generation、automation、review、publishing、wechat、deerflow/letta/ruflo 等）
  - `load_settings(project_root, account_key) -> AppSettings`：主入口，加载 .env → config.yaml → 账号 overrides → 子配置
  - `AccountProfile` + `account_env_name()`：实现 `{BASE}__{ACCOUNT_SLUG}` 形式的账号级环境变量覆盖
  - `TopicCollectionSettings` / `ImageGenerationSettings` / `AutomationSettings` / `ReviewSettings` / `PublishSettings` / `WechatSettings`：子配置 dataclass
  - `StyleProfile`：写作风格画像（label/tone/article_instruction/title_instruction 等）
  - `normalize_account_key()` / `sanitize_account_slug()`：账号键规范化
  - `_deep_merge()` / `_env_flag()` / `_env_flag_scoped()`：配置合并与环境变量标志解析
  - `resolve_default_font_path()`：按 Windows/macOS/Linux 返回中文字体路径

#### `config.yaml` — 应用主配置

包含以下顶层配置块：

| 配置块 | 说明 |
|--------|------|
| `model` | 模型名称与温度 |
| `writing` | 写作参数（字数目标、风格、标题数、缓存阈值、KOL 口吻） |
| `industry` | 品牌定位（品牌名、聚焦行业、平台、受众） |
| `topic_collection` | 选题采集（回看天数、最大时效、启用源、关键词映射、RSS/HTML 源 URL） |
| `automation` | 自动化（默认选题排名、每日运行时间、归档设置） |
| `review` | 审核（最低分、AI 风险阈值、自动发布阈值、发布目录名） |
| `compliance` | 合规（AIGC 声明、每日上限、去 AI 味词表与风格规则、代理、登录间隔） |
| `image_generation` | 图片生成（尺寸、后端、模型、主题色、导出格式） |
| `publishing` | 头条发布（启用、自动发布、平台、user_data_dir、超时、历史文件） |
| `wechat` | 微信发布（启用、API 凭证、排版后端、135 编辑器、Serv00 代理、主题） |
| `styles` | 4 种写作风格定义（深度行业分析/科技媒体/财经研报/爆款科普） |
| `accounts` | 多账号矩阵配置（default/robot_notes/politics_notes/sports_notes 等） |
| `enhanced_insights` | 增强洞察开关 |
| `one_person_company` | 一人公司 AI 服务（deerflow/letta/ruflo/prompt_optimizer） |

#### `.env.example` — 环境变量模板

覆盖 API、图片、发布、微信、Serv00 代理、合规与多账号防关联配置。关键变量：

- API：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`
- 头条发布：`TOUTIAO_PUBLISH_ENABLED`、`TOUTIAO_AUTO_PUBLISH`（默认 0 草稿模式）、`TOUTIAO_BROWSER_EXECUTABLE`
- 微信：`WECHAT_ENABLED`、`WECHAT_APP_ID/SECRET`、`WECHAT_SERV00_PROXY_*`
- 图片：`IMAGE_BACKEND`、`IMAGE_MODEL`、`IMAGE_FALLBACK_TO_RENDERER`
- 防关联：`TOUTIAO_PROXY_SERVER`、`ACCOUNT_LOGIN_INTERVAL_SECONDS`、`ACCOUNT_SWITCH_INTERVAL_SECONDS`

---

### 5.3 数据模型层 (models)

#### `src/toutiao_content_factory/models.py`

- **职责**：定义全部业务数据传输对象（DTO），均为 `@dataclass`，提供 `from_dict()` / `to_dict()` 序列化
- **关键模型**：

| 模型 | 说明 |
|------|------|
| `SourceDocument` | 源文档（内容、来源、元数据） |
| `ExtractionResult` | 信号提取结果（关键词、公司、数据句、趋势、证据、增长数字） |
| `TrendReport` | 趋势报告（热点趋势、平台话题、读者钩子、推荐角度） |
| `DataReport` | 数据报告（核心数据、风险标记、内容体裁、字数目标） |
| `InsightReport` | 洞察报告（主洞察、支撑数据、替代角度、反方观点、预测） |
| `DataInsightReport` | 数据挖掘报告（指标、趋势、异常、相关性） |
| `GeneratedContent` | 生成内容（article、titles、recommended_titles、cover_prompt、infographic_prompts） |
| `ContentScore` | 内容评分（overall_score、ai_generated_risk、publish_readiness_score、publish_recommendation） |
| `WorkflowResult` | 工作流聚合结果（extraction/trend/data/content/score，流水线核心产物） |
| `CacheContext` | 缓存上下文（status、cache_key、similarity、avoid_titles） |
| `TopicCandidate` | 选题候选（heat/read/save/discussion 评分、quality_flags） |
| `DailyTopicsReport` | 每日选题报告 |
| `ArticlePerformance` | 文章表现指标 |
| `PublishRecord` | 发布记录 |
| `VisualBrief` / `ImageArtifacts` | 视觉简报与图片产物 |
| `MultiSourceReport` / `HistoricalComparison` | 多源与历史对比 |

---

### 5.4 异常体系 (exceptions)

#### `src/toutiao_content_factory/exceptions.py`

- **职责**：定义分层异常体系，按"内容质量/内容安全/输入 IO/AI 模型/发布/配置/数据库/网络/微信"分类
- **异常树**：

```
ContentFactoryError (基类)
├── ContentQualityError
│   └── LowQualityAbortException       # 质量分<60 或缺核心数据时抛出
├── InputError
│   ├── InputNotFoundError
│   └── UnsupportedFileTypeError
├── AIRequestError
│   └── AIResponseFormatError
├── PublishError
│   ├── RPAError
│   │   └── RPAElementNotFoundError
│   ├── PlatformAuthError              # 带 blocking_platforms
│   ├── WechatAuthError
│   └── SessionExpiredException
├── ConfigError
├── DatabaseError
├── NetworkError
│   ├── NetworkTimeoutError
│   ├── NetworkConnectionError
│   └── SourceUnavailableError
└── SensitiveWordError
```

---

### 5.5 Agent 智能体层

所有 Agent（除 `BenchmarkMonitorAgent`）均继承 `BaseAgent`，复用 OpenAI 调用、Prompt 渲染、文本清洗、对标摘要、self-benchmark 等能力。

#### `agents/base.py` — Agent 基类

- **类名**：`BaseAgent`
- **职责**：为所有 Agent 提供共享的配置注入、Prompt 渲染、OpenAI 调用、文本清洗、公司/数字锚点识别、段落均衡等通用能力
- **关键方法**：
  - `__init__(settings, prompt_repository, openai_service, logger)`：注入 AppSettings、PromptRepository、OpenAIContentService，并初始化 GrowthOpsService
  - `request_json(system_prompt, user_prompt)`：统一调用 OpenAI 返回 JSON，失败时回退本地策略
  - `render_prompt(template_name, **kwargs)`：通过 PromptRepository 渲染模板
  - `clean_publish_text / strip_prompt_leakage / normalize_title`：清洗发布文本、剥离 Prompt 泄漏与品牌自称
  - `benchmark_strategy_digest / infer_benchmark_track`：构建对标打法摘要与赛道推断
  - `_company_candidates / _is_valid_company_name / _build_company_or_order_anchor`：公司主体识别与订单锚点生成
  - `_extract_numeric_tokens / _unsupported_numeric_tokens`：数字证据边界校验
  - `_rebalance_paragraphs / _split_long_paragraph`：段落长度均衡与切分

#### Agent 一览表

| Agent | 文件 | 职责 | 关键方法 |
|-------|------|------|----------|
| `TrendAgent` | `trend_agent.py` | 基于抽取结果与缓存上下文，输出热点趋势、平台话题、读者钩子、推荐切入角度与竞争优势 | `run(extraction, style, cache_context) -> TrendReport`、`_fallback`、`_choose_angle` |
| `DataAgent` | `data_agent.py` | 清洗 Prompt 注入、归一化字段、推断内容体裁与格式、产出可指导写作的 DataReport | `run(extraction, trend_report, style) -> DataReport`、`_resolve_content_route`、`_normalize_mode_fields` |
| `WriterAgent` | `writer_agent.py` | 基于 Trend/Data/风格画像生成长文正文、开头、摘要、标签，保证字数与小标题结构合规 | `run(trend_report, data_report, style, cache_context) -> GeneratedContent`、`run_with_insights`、`_finalize_article`、`_expand_article_via_llm` |
| `TitleAgent` | `title_agent.py` | 生成多组标题，按点击/收藏/讨论/品牌契合度打分排序，选出好奇型与理性型双标题 | `run(content, trend_report, data_report, style, cache_context) -> dict`、`_rank_titles`、`_pick_dual_titles` |
| `PolishAgent` | `polish_agent.py` | 反 AI 味润色、删除套话、补强证据与主体锚点，产出验证评分与修改建议 | `run(content, trend_report, data_report, style, cache_context, revision_focus) -> dict`、`_finalize_article`、`_validate_article` |
| `ReviewAgent` | `review_agent.py` | 多维度评分（标题吸引力、信息密度、收藏潜力、完读率、AI 风险），输出优化开头与 pass_status | `run(content, data_report, style, cache_context) -> dict`、`_score_title`、`_score_ai_risk` |
| `PublishReviewAgent` | `publish_review_agent.py` | 给出发布就绪度评分、风险标记、发布建议（ready/manual_review/revise_before_publish） | `run(content, data_report, style, content_score, preflight_summary) -> dict`、`_fallback` |
| `WechatLayoutAgent` | `wechat_layout_agent.py` | 为微信公众号文章规划排版组件方案（页头、摘要条、章节标题、数据/风险卡片）与图标 token | `plan_layout(title, summary, article, account_name, account_focus) -> dict`、`_normalize_layout_payload` |
| `InsightAgent` | `insight_agent.py` | 生成深度观点（主洞察、支撑数据、替代角度、反方观点、预测与置信度） | `run(extraction, trend_report, data_report, style, cache_context) -> InsightReport`、`_fallback` |
| `DataMiningAgent` | `data_mining_agent.py` | 从抽取文本与核心数据中挖掘指标、趋势、异常、相关性与数据结论 | `run(extraction, style) -> DataInsightReport`、`_fallback` |
| `EvolutionAgent` | `evolution_agent.py` | 读取历史表现指标，对比 Top/Bottom 文章，提炼可执行写作规则写入 `best_practices_rule.txt` | `run() -> dict`、`_load_records`、`_summarize_rules` |
| `SampleCollectorAgent` | `sample_collector_agent.py` | 收集账号自身高表现样本并构建对标报告，作为 self-benchmark 上下文来源 | `run(account_key, track, top_n) -> dict` |
| `BenchmarkMonitorAgent` | `benchmark_monitor_agent.py` | 采集对标平台/赛道的爆款热点内容（**未继承 BaseAgent**，独立实现） | `__init__(settings, logger)`、`run(platform, track, limit) -> dict` |

#### `agents/agency_roles.py` — Agent 角色定义

- **类名**：`AgencyRoleLoader`
- **职责**：从文件目录或内置库加载专业 AI 角色 Prompt（营销专家、SEO 专家、数据分析师、内容策略专家等 82+ 角色），供任意 Agent 注入多视角上下文
- **关键方法**：
  - `get_role(role_name) -> str`：按"文件优先 → 内置库 → 空串"顺序查找角色 Prompt
  - `list_roles() -> list[str]`：列出所有可用角色名
  - `get_role_context(role_names) -> str`：将多个角色 Prompt 合并为带视角标签的上下文文本

#### Agent 流水线顺序

```
EvolutionAgent（失败容忍，注入历史规则）
    ↓
TrendAgent → DataAgent（失败走 _fallback）
    ↓
[质量门控: _abort_if_low_quality]
    ↓
[InsightAgent（可选，集成 Letta 记忆）]
    ↓
WriterAgent.run / run_with_insights
    ↓
PolishAgent.run → _apply_polish_pack（含 _ensure_article_min_chars）
    ↓
TitleAgent.run → _apply_title_pack
    ↓
ReviewAgent.run → _apply_review_pack
    ↓
[若 _should_run_second_polish: 二次润色 + 重审]
    ↓
PublishReviewAgent.run → 写入 publish_readiness_score
    ↓
[BadWordsGate: _apply_bad_words_gate 敏感词红线]
```

---

### 5.6 服务层 (services)

服务层是项目最庞大的层，包含 60+ 个服务文件。以下按职能分组说明核心服务。

#### 5.6.1 应用编排

##### `services/app_service.py` — `ContentFactoryAppService`

- **职责**：整个系统的**门面/编排层**，统一持有并调度所有子服务，对外暴露高层 API 供 CLI / 定时任务调用
- **构造时注入的子服务**：TopicAutomationService、ReviewWorkflowService、WechatPublishService、GrowthOpsService、TopicPoolService、ContentGuardService、PlatformHealthService、NotificationService、PlatformCircuitBreaker、CompanyOSService、LeadOpsService、MonetizationService、RevenueEngineService 等 12+ 个
- **关键方法**：
  - `collect_topics() -> dict[str, str]`：收集每日选题，合并 benchmark 热点，过滤后导出
  - `manual_run(paste_text, style_override, dry_run, refresh_cache, include_input_dir, fast_mode) -> dict`：单次手动运行，构造 `ContentFactoryPipeline` 并执行 `pipeline.run()`
  - `auto_run(style_override, dry_run, refresh_cache, topic_rank, topic_title, collect_if_missing, force_collect, no_archive, fast_mode) -> dict`：自动化运行：健康检查 → 选题 → 内容守卫预检 → 调用 manual_run → 归档 → 自动审核准备
  - `daily_run(style_override, dry_run, refresh_cache, topic_rank, topic_title, no_archive, fast_mode, skip_wait) -> dict`：定时任务入口，带文件锁/心跳，多候选尝试，自动审批并触发多平台发布
  - `approve_run / reject_run / publish_run / publish_wechat_run / retry_platform_publish`：审核与发布操作
  - `_run_daily_platform_automation(run_dir, *, dry_run, skip_wait)`：编排微信→头条→知乎/小红书的发布顺序，含熔断器、IP 白名单降级、会话过期通知
- **编排方式**：
  - 横向编排：构造时一次性注入所有子服务实例
  - 纵向流水线：`daily_run` → `auto_run` → `manual_run` → `ContentFactoryPipeline.run`
  - 平台编排：`_run_daily_platform_automation` 按"微信优先 → 等待同步延迟 → 头条首发声明 → 多平台扩展"顺序执行

#### 5.6.2 内容生产流水线

##### `services/pipeline.py` — `ContentFactoryPipeline`

- **职责**：单次内容生成的**核心流水线**，串联"加载→提取→缓存→Agent 工作流→导出"全流程
- **流水线阶段**（`run` 方法）：
  1. 加载阶段：`DocumentLoader.load(paste_text, include_input_dir)`
  2. 提取阶段：`SignalExtractor.extract(documents)`
  3. 缓存检查：`CacheService.inspect(merged_text, style.key, refresh_cache)`
  4. 生成阶段：缓存未命中时调用 `AgentWorkflowService.run(...)`，并 `CacheService.save(result)`
  5. 导出阶段：`ExportService.export(result)`
- **关键方法**：
  - `run(paste_text, include_input_dir) -> dict[str, str]`
  - `save_as_draft(run_dir) -> dict`：写入 `draft_status.json` 并通过 `PipelineStateManager` 落盘状态
  - `publish_draft(run_dir) -> dict`：校验 `check_daily_post_limit` 后置为 published 并发通知
  - `publish_with_human_approval(paste_text, include_input_dir) -> dict`：生成 + 落草稿 + 等待人工确认
  - `close()`：关闭 `openai_service`

##### `services/agent_workflow.py` — `AgentWorkflowService`

- **职责**：编排 10 个专职 Agent 按既定顺序生成结构化内容，并执行质量门控与二次润色
- **关键方法**：
  - `__init__(settings, prompt_repository, openai_service, logger)`：实例化全部 Agent + `ContentGuardService` + 可选 `LettaClient`
  - `run(extraction, style_key, cache_context, fast_mode, skip_review_agent, skip_publish_review) -> WorkflowResult`：主编排入口
  - `_abort_if_low_quality(extraction, data_report)`：质量分 < 60 或缺核心数据/证据/主体锚点时抛 `LowQualityAbortException`
  - `_ensure_article_min_chars(article, data_report, min_chars)`：字数不足 1500 时模板补段 + LLM 扩写
  - `_should_run_second_polish(content, content_score)`：触发二次润色条件
  - `_apply_bad_words_gate(content, content_score)`：敏感词红线，命中强制 `publish_readiness_score=0`

##### `services/extractor.py` — `SignalExtractor`

- **职责**：从源文档中抽取核心数据/趋势/公司/关键词/证据/增长数字
- **关键方法**：`extract(documents) -> ExtractionResult`
- **常量**：`MAX_COMPANIES_COUNT=4`、`MAX_SENTENCES_COUNT=20`、`MAX_DATA_SENTENCES_COUNT=6`、`INDUSTRY_KEYWORDS` 等

##### `services/cache_service.py` — `CacheService`

- **职责**：基于文本指纹与相似度的缓存服务，避免重复生成
- **关键方法**：
  - `inspect(source_text, style_key, refresh_cache) -> CacheContext`：判定 exact_hit / partial / miss
  - `save(result)`：保存工作流结果
- **常量**：`CACHE_TTL_SECONDS = 6 * 3600`（6 小时）、`_MAX_FINGERPRINT_LENGTH=2400`

##### `services/exporter.py` — `ExportService`

- **职责**：将 `WorkflowResult` 导出为文件（article.md、editorial_packet.md、titles.txt、content_score.json、workflow_result.json、benchmark_strategy.json）
- **关键方法**：`export(result: WorkflowResult) -> dict[str, Path]`

##### `services/pipeline_state_manager.py` — `PipelineStateManager`

- **职责**：流水线状态持久化管理，SQLite 优先 + JSON 文件兜底
- **状态机**：`INIT → DOCUMENTS_LOADED → SIGNALS_EXTRACTED → CONTENT_GENERATED → REVIEWED → APPROVED → PUBLISHING → PUBLISHED / FAILED`
- **依赖**：`DatabaseService`、`NotificationService`

#### 5.6.3 AI 与 Prompt

##### `services/openai_service.py` — `OpenAIContentService`

- **职责**：封装 OpenAI 兼容 API，提供结构化 JSON 补全、指数退避重试、Token 用量与成本估算
- **关键方法**：
  - `__init__(settings, logger, force_mock=False)`：根据 api_key 与 base_url 构造 OpenAI 客户端；预判模型类型选择费率
  - `create_json_completion(system_prompt, user_prompt) -> dict`：先尝试 `response_format={"type":"json_object"}`，失败回退 prompt-only JSON
  - `_call_with_retry(**kwargs)`：最多 4 次重试，指数退避 2→30s
  - `_is_retryable_error(exc) -> bool`：429/5xx/RateLimit/Timeout 可重试，Authentication/BadRequest 不可重试
  - `_accumulate_usage(response)`：累计 prompt/completion/total tokens 与估算成本
- **费率策略**：DeepSeek-Reasoner 0.004/0.016，其他默认 0.001/0.002（每 1K tokens），可通过环境变量覆盖
- **属性**：`available`、`estimated_cost`、`get_usage_snapshot()`

##### `services/content_generator.py` — `ContentGenerator`

- **职责**：基于 `OpenAIContentService` 与 `PromptRepository`，通过两步生成（分析→写作）产出 `GeneratedContent`；API 不可用时回退本地模板草稿
- **备注**：较早期的简化生成器，当前主流程已由 `AgentWorkflowService` 接管，保留作为兼容/兜底路径

##### `services/prompt_repository.py` — `PromptRepository`

- **职责**：从 `prompts/` 目录加载并渲染 Prompt 模板（Jinja2）

#### 5.6.4 选题与采集

##### `services/topic_pool_service.py` — `TopicPoolService`

- **职责**：维护本地 JSONL 选题池（`automation_dir/topic_pool/topic_pool.jsonl`），支持入库、去重、状态流转（pending/selected/discarded）、过期判定、常青选题兜底
- **关键方法**：
  - `ingest_report(report, source_label) -> dict`：将每日报告选题写入池，按 `identity_key` 去重
  - `build_fallback_report() -> DailyTopicsReport`：池中有 pending 则用 pending，否则加载常青选题兜底
  - `list_pending_topics(limit=50) -> list[TopicCandidate]`
  - `mark_selected(topic, run_dir)` / `mark_discarded(topic, reason)`
- **常青选题分类**：AI 机器人、新能源储能、国际热点、体育赛事、科技商业（按 account_key 匹配）

##### `services/topic_collection.py` / `services/topic_exporter.py`

- **职责**：选题采集（RSS/HTML 抓取）与选题导出

#### 5.6.5 审核与发布

##### `services/review_service.py` — `ReviewWorkflowService`

- **职责**：管理运行目录的**审核生命周期**（pending → approved/rejected/skipped），生成审核包，导出发布物料，桥接 `PublishService` 完成发布
- **关键方法**：
  - `resolve_run_dir(run_dir) -> Path`：解析运行目录，未指定则取最新
  - `resolve_latest_approved_run(unpublished_only, today_only) -> Path` / `resolve_latest_pending_run() -> Path`
  - `prepare_review(run_dir) -> dict`：生成 `review_packet.md` + `review_status.json`
  - `approve_review(run_dir, selected_title, title_index, reviewer_notes, auto_publish) -> dict`：选标题 → 清洗文章 → `monetization_service.append_cta` → 写 publish 目录 → `SchemaGuardService.validate_publish_meta` 校验 → 更新 status=approved；`auto_publish=True` 时直接调用 `publish_service.publish_run`
  - `reject_review(run_dir, reviewer_notes)` / `mark_skipped_review(run_dir, reviewer_notes)`
  - `publish_run(run_dir, declare_first_release) -> dict`：透传 `PublishService.publish_run`
- **依赖**：`PublishService`、`GrowthOpsService`、`MonetizationService`、`SchemaGuardService`、`article_sanitizer`

##### `services/publish_service.py` — `PublishService`（今日头条发布）

- **职责**：通过 Playwright 浏览器自动化将审核通过的文章发布到**今日头条**创作者后台
- **关键方法**：
  - `publish_run(run_dir, declare_first_release=True) -> dict`：入口，通过 `_publish_session_lock`（msvcrt 文件锁）串行化
  - `_publish_run_locked(run_dir, declare_first_release)`：校验审核状态/重复选题/字数 → 收集 preflight → 合并 content_guard 报告 → 重试 2 次调用 `_publish_to_toutiao` → 写 `PublishRecord` 历史
  - `initialize_login_session() -> dict`：启动有头浏览器，轮询 10 分钟直到 preflight 无 blocking_flags
  - `collect_preflight_summary() -> dict` / `export_preflight_summary() -> dict`：发布前健康检查
  - `_publish_to_toutiao(title, article, draft_only, run_dir, declare_first_release)`：浏览器自动化核心（编辑器填充、AI 助手屏蔽、封面/配图、首发声明、发布事件捕获、状态验证）
- **辅助能力**：`_apply_cover_image`、`_try_apply_article_image`、`_confirm_publish_modal`、`_verify_toutiao_publish_success`、`_install_publish_event_capture`（监听 `/mp/agw/article/publish` 接口）

##### `services/wechat_service.py` — `WechatPublishService`（微信公众号发布）

- **职责**：将文章发布到**微信公众号**，采用"API + 浏览器"混合模式：优先调用微信开放平台 API，失败时降级到 135 编辑器或纯浏览器自动化；并负责 Markdown→微信 HTML 排版
- **关键方法**：
  - `publish_run(run_dir, submit=False) -> dict`：主入口：AntiAIFlavor 合规检查 → 解析图片 → 获取 access_token → 上传封面/内联图 → 渲染 HTML → 创建草稿 → 按需 submit
  - `initialize_login_session() -> dict`：Playwright 启动并轮询 10 分钟等待登录就绪
  - `_get_access_token() -> str`：支持 Serv00 代理 + 40164 IP 白名单自动重试 5 次
  - `_render_article_html_via_backend(...)`：四级降级：md2wechat → html-anything → 内置 `MarkdownToWechatConverter` → 基础渲染
  - `_publish_via_browser_only / _publish_via_135editor / _submit_draft_via_browser`：三种发布通道
- **特色**：`_build_article_blocks` 智能识别 heading/lead/paragraph/closing；`_format_inline_rich_text` 自动高亮数字与加粗；`_ensure_original_declaration` 勾选原创声明

##### `services/multi_platform_publisher.py` — `MultiPlatformPublisher`

- **职责**：多平台发布器，通过 `register_publisher(name, publisher)` 注册各平台发布器，统一调度
- **支持平台**：bilibili（`BilibiliPublisher`）、zhihu（`ZhihuPublisher`）、xiaohongshu（`XiaohongshuPublisher`）

#### 5.6.6 合规与内容守卫

##### `services/compliance_service.py`

- **职责**：提供**平台级合规约束**——每日发布上限、AIGC 声明勾选、账号登录/切换间隔限流、代理配置
- **关键函数/类**：
  - `check_daily_post_limit(history_path, platform="toutiao") -> bool`：统计今日已发布数，超过 `TOUTIAO_MAX_DAILY_POSTS`/`WECHAT_MAX_DAILY_POSTS`（默认 2）则返回 False
  - `is_aigc_declaration_enabled() / is_wechat_aigc_declaration_enabled()`：读取环境变量（默认 "1"）
  - `async click_aigc_declaration_toutiao(page) / click_aigc_declaration_wechat(page)`：通用 AIGC 声明勾选
  - `class AccountLoginGuard`：类方法 `wait_before_login(account_key)` / `wait_before_switch()`，基于间隔限流
  - `apply_proxy_to_browser(launch_options)`：注入代理

##### `services/content_guard_service.py` — `ContentGuardService`

- **职责**：**内容红线守卫**——基于 AC 自动机（Aho-Corasick）的敏感词扫描、与近 15 天已发布内容的余弦相似度查重、AI 痕迹风险评估、高风险类目（医疗/荐股/命理）拦截、数字出处校验
- **关键方法**：
  - `evaluate_candidate_topic(topic_title, topic_url) -> dict`：选题阶段查重（阈值 0.70）+ 敏感词 + 高风险类目
  - `evaluate_run(run_dir) -> dict`：运行阶段查重（阈值 0.76）+ AI 风险 + 数字出处校验，写 `content_guard_report.json`
  - `build_publish_guard_report(run_dir) -> dict`：合并 `ArticleQualityChecker` 结果，输出 `blocking_flags` 与 `review_status_override`
  - `scan_text(text) -> dict`：AC 自动机敏感词扫描，命中即 `blocked=True`
  - `build_review_rejection_payload(...)` / `apply_review_rejection(run_dir, payload)`：命中红线时强制写 `review_status.json` 为 rejected
  - `_estimate_ai_giness(title, article) -> float`：综合段落开头重复率、营销短语密度、感叹号比例等输出 0-1 风险分
- **阈值常量**：`TOPIC_SIMILARITY_THRESHOLD=0.70`、`RUN_SIMILARITY_THRESHOLD=0.76`、`AI_RISK_THRESHOLD=0.62`、`HUMAN_REVIEW_THRESHOLD=0.45`
- **辅助类**：`SensitiveWordTrie`、`TrieNode`（AC 自动机实现）

##### `services/article_quality_checker.py` / `services/article_sanitizer.py` / `services/anti_ai_flavor.py`

- `ArticleQualityChecker`：文章质量检查
- `clean_article_text(article)`：文章清洗（剥离 Prompt 泄漏、品牌自称等）
- `AntiAIFlavorPipeline`：去 AI 味处理流水线

#### 5.6.7 增长运营与商业化

##### `services/growth_ops_service.py` — `GrowthOpsService`

- **职责**：增长运营服务，管理对标账号库（`branding/benchmark_accounts_*.yaml`）与自有爆款样本库（`branding/self_benchmark_articles.yaml`），提供对标打法摘要、标题 playbook、self-benchmark 上下文
- **关键方法**：`benchmark_strategy_digest`、`get_title_playbook_track_context`、`collect_self_benchmark_samples`、`build_benchmark_report`
- **常量**：`BENCHMARK_PLATFORMS = ("toutiao", "wechat")`

##### `services/company_os_service.py` — `CompanyOSService`

- **职责**：构建"一人公司"运营系统蓝图、backlog 与 scorecard

##### `services/lead_ops_service.py` — `LeadOpsService`

- **职责**：线索运营，管理本地线索账本（记录、更新、提案生成、外联标记、回复分类、成交跟进、推荐转化）

##### `services/monetization_service.py` — `MonetizationService`

- **职责**：变现服务，`append_cta` 为文章追加行动号召

##### `services/monetization_engine.py` — `MonetizationEngine`

- **职责**：变现引擎，`get_revenue_report(period)` 生成收入报告

##### `services/revenue_engine_service.py` — `RevenueEngineService`

- **职责**：收入引擎，构建客户开发、赞助商开发、外联与订阅变现资产

##### `services/matrix_ops_manager.py` — `MatrixOpsManager`

- **职责**：矩阵运营管理，`get_dashboard_data()` 返回矩阵仪表盘数据

##### `services/engagement_agent.py` — `EngagementAgent`

- **职责**：互动 Agent，`run_daily_engagement(platform, account)` 执行点赞/评论/关注，`mine_comments_for_topics` 从评论挖掘选题

##### `services/performance_service.py` / `services/feedback_service.py` / `services/feedback_loop_service.py`

- **职责**：表现追踪、反馈服务、反馈闭环

#### 5.6.8 平台健康与基础设施

##### `services/platform_health_service.py` — `PlatformHealthService`

- **职责**：平台会话健康检查（头条/微信登录状态、IP 白名单、Cookie 有效性）

##### `services/notification_service.py` — `NotificationService`

- **职责**：通知服务，写入 `notifications.json`

##### `services/circuit_breaker.py` — `PlatformCircuitBreaker`

- **职责**：平台熔断器，防止发布雪崩

##### `services/browser_launch_guard.py` — `prepare_browser_profile`

- **职责**：浏览器启动守卫，准备 Profile 目录、检测锁文件

##### `services/image_generation_service.py` / `services/frontend_image_renderer.py`

- **职责**：图片生成（OpenAI Image API）与前端图片渲染（Pillow/Matplotlib）

##### `services/wechat_layout_engine.py` / `services/wechat_135editor_service.py` / `services/md2wechat_service.py` / `services/wechat_launch_service.py`

- **职责**：微信排版引擎、135 编辑器服务、Markdown 转微信、微信启动周服务

##### `services/deerflow_client.py` / `services/letta_client.py` / `services/ruflo_client.py` / `services/prompt_optimizer_client.py`

- **职责**：外部 AI 服务客户端（DeerFlow 深度研究、Letta 记忆、Ruflo 工作流、Prompt 优化）

##### `services/schema_guard_service.py` / `services/debug_snapshot_service.py` / `services/html_anything_service.py` / `services/first_release_adapter.py` / `services/rpa_utils.py` / `services/workflow_dag.py` / `services/workflow_executor.py`

- **职责**：Schema 校验守卫、调试快照、HTML 转换、首发适配、RPA 工具、工作流 DAG 与执行器

---

### 5.7 数据库层 (database)

#### `database/models.py`

- **职责**：基于 SQLModel 定义 SQLite 持久化表结构
- **关键表/枚举**：

| 模型 | 类型 | 关键字段 |
|------|------|----------|
| `PipelineStage` | Enum | INIT/DOCUMENTS_LOADED/SIGNALS_EXTRACTED/CONTENT_GENERATED/REVIEWED/APPROVED/PUBLISHING/PUBLISHED/FAILED/REVIEW_REQUIRED |
| `PublishPlatform` | Enum | toutiao / wechat |
| `Topic` | table | title、url、source、heat_score、total_score、quality_score、account_key、used |
| `Article` | table | run_name(unique)、account_key、article_text、polished_article、pipeline_stage、review_status、overall_score、ai_risk_score、publish_result |
| `RunRecord` | table | run_name、account_key、pipeline_stage、status、toutiao_status、wechat_status、state_data(JSON) |

#### `database/service.py` — `DatabaseService`

- **职责**：线程安全的 SQLModel/SQLite CRUD 服务，sqlmodel 缺失时优雅降级为 no-op
- **关键方法**：
  - `_session()`：上下文管理器，自动 commit/rollback，`expire_on_commit=False`
  - `save_article() / get_article() / list_articles() / update_article_stage()`：文章 CRUD
  - `save_run_record() / mark_run_completed()`：批次记录管理
  - `save_topic()`：含 URL 去重 + 标题相似度（`SequenceMatcher` 阈值 0.7）去重
  - `get_stats()`：返回 articles/topics/run_records 计数

---

### 5.8 加载器层 (loaders)

#### `loaders/document_loader.py` — `DocumentLoader`

- **职责**：从粘贴文本与 input 目录加载源文档（.txt/.md/.markdown/.pdf），清洗 markdown 前缀并截断到 max_input_chars
- **关键方法**：
  - `load(paste_text, include_input_dir)`：聚合 paste + 目录文件，无文档时抛 `InputNotFoundError`
  - `_read_pdf()`：依赖 pypdf，缺失时抛 `UnsupportedFileTypeError`
  - `_read_text()`：依次尝试 utf-8 / utf-8-sig / gb18030 编码
  - `_trim_text()`：去除空行、剥离 `#/-/*/\d.` 前缀，截断到 max_input_chars
  - `_extract_inline_metadata()`：用 15 个正则从文本提取 `标题：` `入选理由：` `主赛道：` 等元数据

---

### 5.9 Web 控制台层 (web)

#### `web/app.py` — FastAPI 应用主体

- **职责**：注册全部路由、APScheduler 后台调度、多账号上下文管理与后台任务执行池
- **关键类**：`create_app()`、`create_app_factory()`、`AccountContext`（dataclass）
- **关键路由**：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/`、`/dashboard` | 仪表盘 |
| GET | `/topics` | 选题池 |
| GET | `/runs`、`/runs/{run_name}`、`/runs/{run_name}/compare` | 运行列表/详情/对比 |
| GET | `/published` | 已发布 |
| GET | `/accounts` | 账号管理 |
| POST | `/actions/collect-topics` | 采集选题 |
| POST | `/actions/manual-run` | 手动运行 |
| POST | `/actions/auto-run` | 自动运行 |
| POST | `/actions/daily-run` | 日常流水线 |
| POST | `/actions/init-login` | 初始化登录 |
| POST | `/actions/record-lead` | 记录线索 |
| POST | `/runs/{run_name}/approve\|publish\|reject\|retry` | 审核操作 |
| POST | `/api/scheduler/start\|pause` | 调度控制 |
| GET | `/api/logs/list\|view\|stream` | 日志查看 |
| GET | `/artifacts/{account}/...` | 静态产物 |

- **调度**：`register_scheduler_jobs()` 按账号注册 daily_run / keep_alive / collect_performance_data 三类 CronTrigger 任务
- **后台任务**：`run_in_background()` 通过 `ThreadPoolExecutor(max_workers=4)` 提交并写入 notifications.json

#### `web/view_service.py` — `WebViewService`

- **职责**：从 output/logs/automation 目录聚合 JSON/YAML 数据，构建仪表盘、批次详情、调度状态、健康面板等渲染上下文
- **关键方法**：
  - `load_dashboard()`：聚合 30+ 数据源返回完整仪表盘 payload
  - `build_account_snapshot()`：构建单账号概览（头条/公众号平台快照、attention_items）
  - `load_run_detail(run_name)`：加载批次详情
  - `load_scheduled_task_catalog()`：通过 PowerShell `Get-ScheduledTask` 查询 Windows 计划任务（20s TTL 缓存）
  - `_build_pipeline_timeline()` / `_build_actionable_alerts()`：构建管线时间线与可操作告警
- **辅助 dataclass**：`RunSummary`、`ScheduledTaskSummary`、`PublishStatusSummary`、`WechatPublishStatusSummary`、`ScheduledWindowSummary`、`LogSummary`

---

### 5.10 工具层 (utils)

#### `utils/browser_utils.py`

- **职责**：Playwright 浏览器自动化辅助工具
- **关键方法**：
  - `BrowserTimeout(seconds)`：基于线程 Event 的超时上下文，超时抛 `TimeoutError`
  - `apply_stealth()`：可选应用 `playwright_stealth`，缺失时静默跳过
  - `safe_click_with_fallbacks(selectors)` / `safe_fill_with_fallbacks(selectors, value)`：按顺序尝试多个选择器
  - `human_delay()` / `random_scroll()`：模拟人类反应延迟与滚动行为

#### `utils/file_utils.py`

- **职责**：极简文件读写工具，统一编码（文本 utf-8，JSON utf-8-sig 兼容 BOM）
- **函数**：`read_text()`、`write_text()`、`read_json()`、`write_json()`（自动 `mkdir parents=True`）

#### `utils/logging_utils.py`

- **职责**：日志配置与轻量运行时指标采集
- **关键内容**：
  - `configure_logging(logs_dir, level, logger_name, structured)`：清空 handlers，同时输出到 `app.log` 与控制台
  - `StructuredFormatter`：JSON 结构化日志（timestamp/level/module/message + extra_data + exception）
  - `MetricsCollector`：`record/increment/start_timer/stop_timer/to_dict/reset` 流水线运行指标采集

#### `utils/runtime_status.py`

- **职责**：调度器运行时状态文件（`scheduled_status.json`）的读写、心跳维护与僵尸状态自动恢复
- **关键方法/常量**：
  - `HEARTBEAT_TIMEOUT_SECONDS = 30 * 60`：心跳超时阈值
  - `refresh_heartbeat(path, pid)`：更新 pid + last_heartbeat 并持久化
  - `recover_stale_running_status(path)`：状态为 running 但进程已死或心跳超时，自动标记为 `aborted`
  - `process_exists(pid)`：跨平台进程检测（Windows 用 tasklist，POSIX 用 os.kill(pid,0)）
  - `normalize_runtime_status()`：补全 updated_at/last_run_time/overall_status/platforms 字段
  - `utcnow_iso()` / `load_runtime_status()` / `save_runtime_status()` / `mark_status_aborted()` / `is_stale_heartbeat()`

---

### 5.11 常量层 (constants)

#### `constants/selectors.py`

- **职责**：集中注册所有平台 RPA 选择器（CSS/XPath/text），YAML 优先（`config/selectors.yaml`）、硬编码兜底
- **关键类/函数**：
  - `PageSelectors`（frozen dataclass）：`get(element_name)` / `first(element_name)` 取选择器列表/首个
  - `get_wechat_page()` / `get_toutiao_page()` / `get_editor_135_page()`：获取各平台页面选择器
- **页面覆盖**：
  - WeChat：login_page、home_page、draft_list_page、editor_page、cover_dialog、publish_dialog、dismiss_dialog、original_declaration
  - Toutiao：home_page、editor_page（含 ai_drawer_mask/close）
  - editor_135：login_page、editor_page、sync_dialog
- **常量元组**：`WECHAT_SESSION_READY_MARKERS`、`WECHAT_PUBLISH_BUTTON_TEXTS`、`WECHAT_SUCCESS_MARKERS` 等

---

## 6. 关键类与函数说明

### 6.1 核心类索引

| 类名 | 文件 | 职责 |
|------|------|------|
| `ContentFactoryAppService` | `services/app_service.py` | 应用编排门面，统一调度所有子服务 |
| `ContentFactoryPipeline` | `services/pipeline.py` | 单次内容生成核心流水线 |
| `AgentWorkflowService` | `services/agent_workflow.py` | 10 个 Agent 的工作流编排 |
| `OpenAIContentService` | `services/openai_service.py` | OpenAI 兼容 API 封装与成本估算 |
| `SignalExtractor` | `services/extractor.py` | 源文档信号提取 |
| `CacheService` | `services/cache_service.py` | 文本指纹缓存 |
| `ExportService` | `services/exporter.py` | 工作流结果导出 |
| `PipelineStateManager` | `services/pipeline_state_manager.py` | 流水线状态持久化（SQLite+JSON） |
| `TopicPoolService` | `services/topic_pool_service.py` | 选题池管理 |
| `ReviewWorkflowService` | `services/review_service.py` | 审核生命周期管理 |
| `PublishService` | `services/publish_service.py` | 今日头条 Playwright 发布 |
| `WechatPublishService` | `services/wechat_service.py` | 微信公众号 API+浏览器发布 |
| `MultiPlatformPublisher` | `services/multi_platform_publisher.py` | 多平台发布调度 |
| `ContentGuardService` | `services/content_guard_service.py` | AC 自动机敏感词+查重+AI 风险守卫 |
| `GrowthOpsService` | `services/growth_ops_service.py` | 对标账号与 self-benchmark 管理 |
| `DatabaseService` | `database/service.py` | 线程安全 SQLite CRUD |
| `BaseAgent` | `agents/base.py` | Agent 基类 |
| `AppSettings` | `config.py` | 分层配置加载产物 |
| `WebViewService` | `web/view_service.py` | Web 视图聚合 |
| `DocumentLoader` | `loaders/document_loader.py` | 源文档加载 |

### 6.2 核心函数索引

| 函数 | 文件 | 说明 |
|------|------|------|
| `load_settings(project_root, account_key)` | `config.py` | 配置加载主入口 |
| `check_daily_post_limit(history_path, platform)` | `compliance_service.py` | 每日发布上限检查 |
| `clean_article_text(article)` | `article_sanitizer.py` | 文章清洗 |
| `prepare_browser_profile(...)` | `browser_launch_guard.py` | 浏览器 Profile 准备 |
| `run_sync_playwright_safe(...)` | `rpa_utils.py` | 安全的同步 Playwright 执行 |
| `create_app(project_root, account_key)` | `web/app.py` | FastAPI 应用工厂 |
| `refresh_heartbeat(path, pid)` | `runtime_status.py` | 心跳刷新 |
| `configure_logging(logs_dir, level)` | `logging_utils.py` | 日志配置 |

---

## 7. 核心工作流

### 7.1 日常流水线（daily_run）

```
ContentFactoryAppService.daily_run()
    │
    ├─ 1. 健康检查 (PlatformHealthService)
    ├─ 2. 选题采集 (TopicPoolService + benchmark 热点合并)
    │     └─ 无选题时跳过（status=no_topics）
    ├─ 3. 内容守卫预检 (ContentGuardService.evaluate_candidate_topic)
    ├─ 4. 调用 auto_run → manual_run
    │     └─ ContentFactoryPipeline.run()
    │         ├─ DocumentLoader.load()           # 加载源文档
    │         ├─ SignalExtractor.extract()        # 信号提取
    │         ├─ CacheService.inspect()           # 缓存检查
    │         ├─ AgentWorkflowService.run()       # Agent 工作流
    │         │   ├─ EvolutionAgent.run()         # 注入历史规则
    │         │   ├─ TrendAgent.run()             # 趋势分析
    │         │   ├─ DataAgent.run()              # 数据分析
    │         │   ├─ [质量门控]
    │         │   ├─ [InsightAgent.run()]         # 增强洞察
    │         │   ├─ WriterAgent.run()            # 文章写作
    │         │   ├─ PolishAgent.run()            # 润色
    │         │   ├─ TitleAgent.run()             # 标题生成
    │         │   ├─ ReviewAgent.run()            # 审核
    │         │   ├─ [二次润色 + 重审]
    │         │   ├─ PublishReviewAgent.run()     # 发布前审核
    │         │   └─ [BadWordsGate]               # 敏感词红线
    │         ├─ CacheService.save()              # 缓存保存
    │         └─ ExportService.export()           # 导出文件
    ├─ 5. 归档 (archive_outputs)
    ├─ 6. 自动审核准备 (ReviewWorkflowService.prepare_review)
    ├─ 7. 自动审批 (若达到阈值: approve_review + auto_publish)
    └─ 8. 多平台发布 (_run_daily_platform_automation)
          ├─ 微信优先 (WechatPublishService)
          ├─ 等待同步延迟 (toutiao_sync_delay_seconds)
          ├─ 头条首发声明 (PublishService)
          └─ 多平台扩展 (MultiPlatformPublisher: B站/知乎/小红书)
```

### 7.2 审核工作流

```
prepare_review(run_dir)          # 生成 review_packet.md + review_status.json
    │
    ├─ approve_review(...)        # 状态 → approved
    │   ├─ 选标题 (selected_title / title_index)
    │   ├─ 清洗文章 (clean_article_text)
    │   ├─ 追加 CTA (monetization_service.append_cta)
    │   ├─ 写 publish 目录 (article/title/summary/comment/tags/meta)
    │   ├─ SchemaGuardService.validate_publish_meta 校验
    │   └─ auto_publish=True → publish_service.publish_run()
    │
    ├─ reject_review(...)         # 状态 → rejected
    └─ mark_skipped_review(...)   # 状态 → skipped
```

### 7.3 状态机

```
INIT → DOCUMENTS_LOADED → SIGNALS_EXTRACTED → CONTENT_GENERATED
  → REVIEWED → APPROVED → PUBLISHING → PUBLISHED
  → FAILED / REVIEW_REQUIRED
```

---

## 8. 多账号矩阵体系

项目通过 `account_key` 实现多账号隔离运营：

- **配置隔离**：`config.yaml` 的 `accounts` 块为每个账号定义独立的品牌定位、写作风格、选题源、关键词映射
- **目录隔离**：每个账号有独立的 output/logs/automation/browser_profile 目录
- **环境变量隔离**：支持 `{BASE}__{ACCOUNT_SLUG}` 形式的账号级环境变量覆盖（如 `OPENAI_API_KEY__ROBOT_NOTES`）
- **浏览器 Profile 隔离**：`browser_profile_dir` 配置独立 Profile，账号互不干扰
- **防关联**：`AccountLoginGuard` 控制登录间隔（默认 300s）与切换间隔（默认 60s），支持代理配置

### 内置账号

| account_key | 标签 | 定位 | 启用 |
|-------------|------|------|------|
| `default` | 手蓝科技笔记 | AI、机器人、芯片、新能源、储能、智能制造 | ✅ |
| `robot_notes` | 机器人/地缘科技笔记 | 人形机器人、具身智能、AI 硬件、工业自动化 | ✅ |
| `politics_notes` | 手蓝政治笔记 | 国际政治、地缘博弈、关税、外交 | ✅ |
| `sports_notes` | 体育笔记 | 足球、篮球、国足、国际大赛 | ✅ |
| `hardware_notes` | 手蓝硬件笔记 | AI 硬件、机器人芯片、边缘算力 | ❌ |
| `automation_notes` | 手蓝智造笔记 | 工业自动化、智能装备、机器视觉 | ❌ |

---

## 9. 多平台发布体系

| 平台 | 服务类 | 方式 | 状态 |
|------|--------|------|------|
| 今日头条 | `PublishService` | Playwright 浏览器自动化 | ✅ 完整支持 |
| 微信公众号 | `WechatPublishService` | 官方 API + 浏览器 + 135 编辑器（四级降级） | ✅ 完整支持 |
| B 站 | `BilibiliPublisher` | 浏览器自动化 | 🧪 实验性 |
| 知乎 | `ZhihuPublisher` | 浏览器自动化 | 🧪 实验性 |
| 小红书 | `XiaohongshuPublisher` | 浏览器自动化 | 🧪 实验性 |
| 抖音 | — | 浏览器自动化（需校准选择器） | 🧪 实验性 |

### 发布顺序策略

`_run_daily_platform_automation` 按"**微信优先 → 等待同步延迟 → 头条首发声明 → 多平台扩展**"顺序执行，使用 `PlatformCircuitBreaker` 防雪崩，支持 IP 白名单降级与会话过期通知。

### 微信发布四级降级

```
md2wechat (外部 API) → html-anything (外部服务) → 内置 MarkdownToWechatConverter → 基础 _render_article_html
```

---

## 10. 商业化与变现系统

项目集成了"一人公司"AI 系统，包含完整的商业化闭环：

| 模块 | 服务 | 职责 |
|------|------|------|
| 公司 OS | `CompanyOSService` | 构建运营蓝图、backlog、scorecard |
| 线索运营 | `LeadOpsService` | 线索记录/更新/提案/外联/成交/推荐 |
| 变现服务 | `MonetizationService` | 文章追加 CTA |
| 变现引擎 | `MonetizationEngine` | 收入报告生成 |
| 收入引擎 | `RevenueEngineService` | 客户开发、赞助商开发、订阅变现 |
| 矩阵运营 | `MatrixOpsManager` | 矩阵仪表盘 |
| 互动运营 | `EngagementAgent` | 点赞/评论/关注、评论挖掘选题 |
| 增长运营 | `GrowthOpsService` | 对标账号与 self-benchmark |
| 表现追踪 | `PerformanceService` | 文章表现记录与报告 |

### 线索生命周期

```
record_lead → update_lead → generate_lead_proposal → mark_lead_outreach_sent
  → triage_lead_reply → mark_lead_proposal_sent → mark_lead_call_booked
  → mark_lead_payment_received → mark_lead_delivery_started → mark_lead_delivery_completed
  → request_lead_testimonial → mark_lead_expansion → convert_expansion_to_lead
```

---

## 11. Prompt 模板体系

`prompts/` 目录包含 20+ Prompt 模板，由 `PromptRepository` 加载并通过 Jinja2 渲染：

| 模板 | 用途 | 调用方 |
|------|------|--------|
| `trend_prompt_v2.md` | 趋势分析 | TrendAgent |
| `data_prompt_v2.md` | 数据分析 | DataAgent |
| `article_prompt_v2.md` | 文章写作 | WriterAgent |
| `writer_with_insights_prompt.md` | 带洞察写作 | WriterAgent.run_with_insights |
| `title_prompt_v2.md` | 标题生成 | TitleAgent |
| `polish_prompt.md` | 润色 | PolishAgent |
| `review_prompt.md` | 审核 | ReviewAgent |
| `publish_review_prompt.md` | 发布前审核 | PublishReviewAgent |
| `wechat_layout_prompt.md` | 微信排版 | WechatLayoutAgent |
| `insight_prompt.md` | 洞察 | InsightAgent |
| `data_mining_prompt.md` | 数据挖掘 | DataMiningAgent |
| `analysis_prompt.md` | 分析 | ContentGenerator |
| `generation_prompt.md` | 生成 | ContentGenerator |
| `anti_ai_flavor_prompt.md` | 去 AI 味 | AntiAIFlavorPipeline |
| `image_prompt.md` | 图片提示 | ImageGenerationService |
| `title_prompt.md` / `trend_prompt.md` / `data_prompt.md` | v1 旧版 | 兼容 |
| `wechat_layout_prompt.md` | 微信排版 | WechatLayoutAgent |

### self-benchmark 演进闭环

```
SampleCollectorAgent 采集高表现样本
    ↓
EvolutionAgent 提炼写作规则 → best_practices_rule.txt
    ↓
WriterAgent / ReviewAgent / PublishReviewAgent 注入规则并命中调整
    ↓
形成自我演进闭环
```

---

## 12. 依赖关系图

### 12.1 模块依赖总览

```
ContentFactoryAppService (门面)
├── ContentFactoryPipeline (流水线)
│   ├── DocumentLoader / SignalExtractor
│   ├── AgentWorkflowService (Agent 编排)
│   │   ├── 10 个 Agent (Trend/Data/Evolution/Writer/Polish/Title/Review/PublishReview/Insight/DataMining)
│   │   ├── ContentGuardService (内容守卫)
│   │   ├── LettaClient (记忆，可选)
│   │   └── OpenAIContentService (LLM 调用)
│   ├── CacheService / ExportService / PipelineStateManager
│   └── compliance_service.check_daily_post_limit
├── ReviewWorkflowService (审核)
│   ├── PublishService (头条发布，Playwright)
│   ├── GrowthOpsService / MonetizationService
│   └── SchemaGuardService
├── WechatPublishService (微信发布)
│   ├── Md2WechatService / HtmlAnythingService / Editor135Service
│   ├── wechat_layout_engine / anti_ai_flavor
│   └── GrowthOpsService
├── TopicPoolService (选题池，纯本地)
├── ContentGuardService (敏感词/查重/AI 风险)
├── PlatformHealthService / NotificationService / PlatformCircuitBreaker
├── GrowthOpsService / CompanyOSService / LeadOpsService
├── MonetizationService / RevenueEngineService
└── ImageGenerationService / WechatLaunchService
```

### 12.2 核心数据流

```
选题（TopicPool/Collection）
  → 流水线（Pipeline + AgentWorkflow + OpenAI）
  → 守卫（ContentGuard）
  → 审核（ReviewWorkflow）
  → 发布（PublishService 头条 / WechatPublishService 微信 / Zhihu/Xiaohongshu/Bilibili）
```

### 12.3 Agent 依赖链

```
BaseAgent
  ├── OpenAIContentService
  ├── PromptRepository
  ├── GrowthOpsService (benchmark_strategy_digest / self-benchmark)
  ├── CacheContext / StyleProfile
  └── article_sanitizer.clean_article_text

TrendAgent → ExtractionResult, CacheContext → TrendReport
DataAgent → ExtractionResult, TrendReport → DataReport
WriterAgent → TrendReport, DataReport, CacheContext → GeneratedContent
TitleAgent → GeneratedContent, TrendReport, DataReport → titles
PolishAgent → GeneratedContent, DataReport → polished_article
ReviewAgent → GeneratedContent, DataReport → ContentScore
PublishReviewAgent → GeneratedContent, ContentScore, preflight → readiness
```

### 12.4 外部依赖

| 依赖 | 用途 | 必需 |
|------|------|------|
| OpenAI-compatible API | LLM 调用 | ✅ |
| Playwright + Edge/Chromium | 浏览器自动化 | ✅（发布功能） |
| SQLite (内置) | 数据持久化 | ✅ |
| DeerFlow (可选) | 深度研究 | ❌ |
| Letta (可选) | 记忆系统 | ❌ |
| Ruflo (可选) | 工作流编排 | ❌ |
| Prompt Optimizer (可选) | Prompt 优化 | ❌ |
| Serv00 代理 (可选) | 微信 IP 白名单代理 | ❌ |
| md2wechat (可选) | Markdown 转微信 | ❌ |
| html-anything (可选) | HTML 转换 | ❌ |

---

## 13. 项目运行方式

### 13.1 环境要求

- Windows 10/11（RPA 发布功能依赖）
- Python 3.10+
- Microsoft Edge（浏览器自动化）

### 13.2 安装

```powershell
git clone https://github.com/Lishoulan/Automated-workflow.git
cd Automated-workflow
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

### 13.3 配置

编辑 `.env`，至少填写：

```ini
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

### 13.4 常用命令

```powershell
# 启动 Web 控制台
powershell -ExecutionPolicy Bypass -File .\scripts\start_webapp.ps1
# 或
python .\webapp.py
# 打开 http://127.0.0.1:8000

# 生成一篇文章
python .\main.py

# 仅采集选题
python .\main.py --topics-only

# 完整日常流水线（采集 → 写作 → 审核 → 发布）
python .\main.py --daily-run --refresh-cache

# 指定账号运行
python .\main.py --account robot_notes --daily-run

# 发布最新审核通过的文章
python .\main.py --publish-latest-approved

# 注册每日定时任务（08:00 / 12:00 / 18:30 / 22:00）
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish

# 增长运营
python .\main.py --build-benchmark-report
python .\main.py --record-performance --run-dir "..." --impression-count 10000 --read-count 860
python .\main.py --build-performance-report

# 深度研究
python .\main.py --deep-research "话题"

# 互动运营
python .\main.py --engagement
python .\main.py --engagement-mine-topics

# 多平台发布
python .\main.py --multi-publish --platforms bilibili,zhihu

# 矩阵仪表盘
python .\main.py --matrix-dashboard

# 变现同步
python .\main.py --monetization-sync

# 线索管理
python .\main.py --record-lead --lead-name "..." --lead-contact "..." --lead-intent "咨询"
python .\main.py --generate-lead-proposal --lead-id "..." --proposal-offer-key "..."
```

### 13.5 CLI 参数分类

`main.py` 的 `build_parser()` 提供 100+ 参数，按职能分类：

| 类别 | 关键参数 |
|------|----------|
| 基础 | `--account`、`--input-dir`、`--output-dir`、`--style`、`--dry-run`、`--refresh-cache` |
| 选题 | `--collect-topics`、`--topics-only`、`--auto-run`、`--topic-rank`、`--topic-title` |
| 流水线 | `--daily-run`、`--fast-mode`、`--skip-wait`、`--resume`、`--no-archive` |
| 审核 | `--prepare-review`、`--approve-run`、`--approve-latest`、`--reject-run`、`--selected-title`、`--selected-title-index` |
| 发布 | `--publish-run`、`--publish-latest-approved`、`--draft-only`、`--publish-preflight`、`--health-preflight` |
| 微信 | `--wechat-draft-latest-approved`、`--wechat-publish-latest-approved`、`--wechat-draft-run`、`--wechat-publish-run` |
| 登录 | `--init-publish-login`、`--init-wechat-login`、`--init-135editor-login` |
| 账号管理 | `--reset-account`、`--audit-account-artifacts`、`--cleanup-stale-account-artifacts` |
| 对标 | `--build-benchmark-report`、`--collect-benchmark-hot-topics`、`--collect-self-benchmark-samples`、`--benchmark-platform`、`--benchmark-track`、`--benchmark-limit` |
| 表现 | `--record-performance`、`--build-performance-report`、`--backfill-performance-ledger`、`--performance-platform`、`--impression-count`、`--read-count` |
| 商业化 | `--build-company-os`、`--build-revenue-engine`、`--run-profit-loop`、`--execute-profit-actions` |
| 线索 | `--record-lead`、`--update-lead`、`--generate-lead-proposal`、`--mark-lead-outreach`、`--triage-lead-reply`、`--mark-lead-call-booked`、`--mark-lead-payment-received`、`--mark-lead-delivery-*`、`--request-lead-testimonial`、`--mark-lead-expansion`、`--convert-expansion-to-lead` |
| 赞助/订阅 | `--update-sponsor-outreach`、`--update-newsletter-subscriber` |
| 微信启动周 | `--prepare-wechat-launch-day`、`--prepare-wechat-launch-week`、`--generate-images-run` |
| 多平台 | `--multi-publish`、`--platforms`、`--matrix-dashboard`、`--monetization-sync` |
| 互动 | `--engagement`、`--engagement-mine-topics` |
| 深度研究 | `--deep-research` |

---

## 14. 部署与调度

### 14.1 本地部署

- **Web 控制台**：`python webapp.py` 或 `scripts/start_webapp.ps1`，默认 `http://127.0.0.1:8000`
- **CLI 运行**：`python main.py --daily-run`
- **独立调度器**：`python scheduler.py`（轮询式，按 `RUN_TIMES` 触发）

### 14.2 Windows 任务计划程序

```powershell
# 注册每日定时任务
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish

# 多账号注册
powershell -ExecutionPolicy Bypass -File .\scripts\multi_account_daily_run.ps1

# 注销定时任务
powershell -ExecutionPolicy Bypass -File .\scripts\unregister_daily_task.ps1
```

### 14.3 Docker 部署

`docker-compose.yml` 编排 6 个服务：

| 服务 | 端口 | 说明 |
|------|------|------|
| `content-factory` | 8000 | Web UI + 内容生成（**不支持 RPA/Playwright**） |
| `deerflow` | 8001 | 字节跳动 DeerFlow 深度研究 |
| `letta` | 8283 | Letta 记忆系统（依赖 postgres） |
| `prompt-optimizer` | 8501 | Prompt 优化服务 |
| `ruflo` | 8002 | 工作流编排服务 |
| `postgres` | 5432 | Letta 后端数据库 |

**注意**：Dockerfile 注释明确说明 RPA 浏览器自动化不支持，发布功能需在本地 Windows 环境执行。Docker 部署仅用于 Web UI + 内容生成 + AI 服务。

卷映射：`./output`、`./prompts`、`./config`、`cf_runtime`（命名卷）。环境变量 `TZ=Asia/Shanghai`、`PYTHONPATH=/app/src`。

### 14.4 GitHub Actions 云端调度

`.github/workflows/daily-run.yml` 每天北京时间 2/6/10/14/18/22 点触发完整 daily pipeline：

1. checkout → setup-python 3.11 → cache pip → install deps
2. `playwright install chromium`
3. `docker compose up -d deerflow letta prompt-optimizer postgres` + 健康检查
4. 创建 Letta agent
5. 恢复/种子化 browser profile（从 GitHub Release 下载 `browser_profiles_seed.tar.gz`）
6. 注入 cookies
7. `keep_alive.py` 保活
8. `scripts/cloud_daily_run.py` 执行云端日常运行
9. 可选 engagement/monetization/matrix
10. 上传 output/logs artifact
11. 更新 browser profile seed

支持 `workflow_dispatch` 手动触发，可输入 account/dry_run/fast_mode 参数。`concurrency.group: daily-run-${{ github.ref }}` + `cancel-in-progress: true` 防止并发。

### 14.5 双层调度体系

| 调度方式 | 机制 | 适用场景 |
|----------|------|----------|
| APScheduler | Web 应用内后台 CronTrigger | 本地 Web 控制台运行时 |
| Windows 任务计划程序 | `register_daily_task.ps1` | 本地无人值守定时 |
| 独立调度器 | `scheduler.py` 轮询 | 本地轻量定时 |
| GitHub Actions | cron + workflow_dispatch | 云端定时（含 RPA 种子恢复） |

### 14.6 安全与合规

- `.env` 和 API Key 永远不会被提交到 Git
- 浏览器 Profile 目录独立隔离，账号互不干扰
- AIGC 声明自动添加，符合平台反自动化创作规范
- 内容安全守卫（ContentGuardService）自动检测敏感词（AC 自动机）
- AI 风险评分超过阈值自动拦截，不发布低质量内容
- 默认"草稿 + 人工审核"模式（`TOUTIAO_AUTO_PUBLISH=0`），自动发布需手动开启
- 每日发布上限检查（默认 2 篇/平台/天）
- 账号登录/切换间隔限流，防关联

---

## 附录：项目元数据

| 项 | 值 |
|----|-----|
| 项目名 | toutiao-content-factory |
| 版本 | 1.0.0 (pyproject) |
| Python 要求 | ≥ 3.10 |
| 许可证 | MIT |
| 主分支入口 | `main.py` (CLI) / `webapp.py` (Web) |
| 包根 | `src/toutiao_content_factory/` |
| 测试 | `tests/` (pytest) |
| 文档 | `docs/` |

---

> 本文档由对仓库源码的逐层通读分析生成，覆盖入口层、配置层、数据模型层、异常体系、Agent 层、服务层、数据库层、加载器层、Web 层、工具层、常量层的全部核心模块，并梳理了核心工作流、多账号体系、多平台发布、商业化系统、Prompt 体系、依赖关系与运行方式。
