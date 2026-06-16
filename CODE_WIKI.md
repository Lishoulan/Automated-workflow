# Code Wiki — 手蓝AI内容工厂 (Automated-workflow)

> 版本：1.0.0 | Python ≥ 3.10 | 最后更新：2026-06-16

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [目录结构](#3-目录结构)
4. [核心模块职责](#4-核心模块职责)
   - 4.1 [入口层](#41-入口层)
   - 4.2 [配置层 (config)](#42-配置层-config)
   - 4.3 [数据模型层 (models)](#43-数据模型层-models)
   - 4.4 [Agent 层](#44-agent-层)
   - 4.5 [Service 层](#45-service-层)
   - 4.6 [数据库层 (database)](#46-数据库层-database)
   - 4.7 [Web 层](#47-web-层)
   - 4.8 [工具层 (utils)](#48-工具层-utils)
5. [关键类与函数说明](#5-关键类与函数说明)
6. [核心工作流](#6-核心工作流)
7. [多平台发布体系](#7-多平台发布体系)
8. [商业化与变现系统](#8-商业化与变现系统)
9. [依赖关系](#9-依赖关系)
10. [项目运行方式](#10-项目运行方式)
11. [异常体系](#11-异常体系)
12. [配置体系](#12-配置体系)

---

## 1. 项目概述

**手蓝AI内容工厂** 是一个全自动化的 AI 内容生产与多平台发布系统。核心能力包括：

- **智能选题**：从 36kr、智东西、OFweek 等行业媒体源自动采集热点话题，通过关键词匹配与评分排序筛选优质选题
- **AI 内容生成**：基于 OpenAI 兼容 API（DeepSeek 等）驱动多 Agent 协作流水线，完成数据提取、趋势分析、文章撰写、标题生成、润色优化、质量审核等全流程
- **多平台发布**：支持今日头条、微信公众号、B站、知乎、小红书等平台的自动化发布（RPA 浏览器自动化 + API）
- **多账号矩阵运营**：支持多个内容账号独立配置与并行运营，账号间隔离数据与浏览器 Profile
- **商业化闭环**：集成线索管理、收入引擎、增长运营、赞助商管理、订阅者管理等一人公司 AI 系统
- **定时调度**：内置 APScheduler 定时调度 + Windows 任务计划 + GitHub Actions 云端运行

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        入口层 (Entry Points)                     │
│  main.py (CLI)  │  webapp.py (Web)  │  scheduler.py (定时)       │
│  run.py (简单模式) │  run_web.py (Web) │  GitHub Actions (云端)   │
└──────────────┬──────────────────────────────┬───────────────────┘
               │                              │
┌──────────────▼──────────────────────────────▼───────────────────┐
│                   AppService (应用编排层)                         │
│            ContentFactoryAppService                              │
│  daily_run / auto_run / manual_run / collect_topics / publish   │
└──────────────┬──────────────────────────────┬───────────────────┘
               │                              │
┌──────────────▼──────────┐  ┌────────────────▼──────────────────┐
│   Pipeline (流水线)      │  │   Agent Workflow (代理工作流)       │
│ ContentFactoryPipeline   │  │   AgentWorkflowService            │
│  run → resume → close    │  │  Trend → Data → Writer → Review  │
└──────────────┬──────────┘  │  → Polish → Title → Insight       │
               │              └────────────────┬──────────────────┘
┌──────────────▼───────────────────────────────▼──────────────────┐
│                     Service 层 (业务服务)                         │
│  OpenAI / Extractor / Review / Export / Cache / ContentGuard    │
│  Compliance / AntiAiFlavor / Image / Topic / Publish / WeChat   │
│  Engagement / Monetization / Lead / Revenue / Company          │
│  Matrix / PlatformHealth / Performance / Notification / ...     │
└──────────────┬──────────────────────────────┬───────────────────┘
               │                              │
┌──────────────▼──────────┐  ┌────────────────▼──────────────────┐
│   Database (数据持久化)   │  │   External AI Services (外部AI)    │
│  SQLModel + SQLite       │  │  DeerFlow / Letta / Ruflo         │
│  Topic / Article / Run   │  │  PromptOptimizer / OpenAI API     │
└─────────────────────────┘  └───────────────────────────────────┘
```

---

## 3. 目录结构

```
Automated-workflow-main/
├── main.py                    # 主 CLI 入口（完整功能）
├── run.py                     # 简单模式入口（DeepSeek 直接生成）
├── webapp.py                  # Web 控制台入口（FastAPI + Uvicorn）
├── run_web.py                 # Web 入口（简化版）
├── scheduler.py               # 纯 Python 定时调度器
├── config.yaml                # 全局配置文件
├── .env.example               # 环境变量模板
├── pyproject.toml             # 项目元数据与依赖
├── requirements.txt           # Python 依赖列表
├── docker-compose.yml         # Docker 编排（含 DeerFlow/Letta 等）
├── Dockerfile                 # 容器构建文件
│
├── src/toutiao_content_factory/   # 核心代码包
│   ├── __init__.py
│   ├── config.py              # 配置加载与模型定义
│   ├── models.py              # 数据模型（ExtractionResult, WorkflowResult 等）
│   ├── exceptions.py          # 自定义异常层级
│   │
│   ├── agents/                # AI Agent 模块
│   │   ├── base.py            # Agent 基类
│   │   ├── agency_roles.py    # Agent 角色定义
│   │   ├── trend_agent.py     # 趋势分析 Agent
│   │   ├── data_agent.py      # 数据分析 Agent
│   │   ├── writer_agent.py    # 文章撰写 Agent
│   │   ├── review_agent.py    # 质量审核 Agent
│   │   ├── polish_agent.py    # 润色优化 Agent
│   │   ├── title_agent.py     # 标题生成 Agent
│   │   ├── insight_agent.py   # 深度洞察 Agent
│   │   ├── data_mining_agent.py   # 数据挖掘 Agent
│   │   ├── evolution_agent.py     # 内容进化 Agent
│   │   ├── publish_review_agent.py # 发布审核 Agent
│   │   ├── wechat_layout_agent.py  # 微信排版 Agent
│   │   ├── benchmark_monitor_agent.py # 基准监控 Agent
│   │   └── sample_collector_agent.py  # 样本采集 Agent
│   │
│   ├── services/              # 业务服务模块
│   │   ├── pipeline.py        # 核心流水线
│   │   ├── app_service.py     # 应用编排服务
│   │   ├── agent_workflow.py  # Agent 工作流编排
│   │   ├── openai_service.py  # OpenAI API 封装
│   │   ├── content_generator.py   # 内容生成器
│   │   ├── extractor.py       # 信号提取器
│   │   ├── review_service.py  # 审核工作流
│   │   ├── exporter.py        # 内容导出
│   │   ├── automation.py      # 自动化调度
│   │   ├── cache_service.py   # 缓存服务
│   │   ├── publish_service.py # 头条发布服务
│   │   ├── wechat_service.py  # 微信发布服务
│   │   ├── multi_platform_publisher.py  # 多平台发布器
│   │   ├── bilibili_publisher.py   # B站发布
│   │   ├── zhihu_publisher.py      # 知乎发布
│   │   ├── xiaohongshu_publisher.py # 小红书发布
│   │   ├── content_guard_service.py    # 内容合规守卫
│   │   ├── compliance_service.py       # 合规服务
│   │   ├── anti_ai_flavor.py           # 去 AI 味服务
│   │   ├── article_quality_checker.py  # 文章质量检查
│   │   ├── article_sanitizer.py        # 文章净化器
│   │   ├── image_generation_service.py # 图片生成
│   │   ├── frontend_image_renderer.py  # 前端图片渲染
│   │   ├── wechat_layout_engine.py     # 微信排版引擎
│   │   ├── wechat_135editor_service.py # 135 编辑器集成
│   │   ├── md2wechat_service.py        # MD 转 WeChat
│   │   ├── html_anything_service.py    # HTML 渲染服务
│   │   ├── wechat_launch_service.py    # 微信启动服务
│   │   ├── wechat_pages.py             # 微信页面处理
│   │   ├── topic_collection.py         # 话题采集
│   │   ├── topic_pool_service.py       # 话题池管理
│   │   ├── topic_exporter.py           # 话题导出
│   │   ├── benchmark_hotspot_service.py # 基准热点服务
│   │   ├── search_service.py           # 搜索服务
│   │   ├── engagement_agent.py         # 互动 Agent
│   │   ├── monetization_engine.py      # 变现引擎
│   │   ├── monetization_service.py     # 变现服务
│   │   ├── company_os_service.py       # 公司 OS 服务
│   │   ├── lead_ops_service.py         # 线索运营服务
│   │   ├── revenue_engine_service.py   # 收入引擎服务
│   │   ├── growth_ops_service.py       # 增长运营服务
│   │   ├── matrix_ops_manager.py       # 矩阵运营管理
│   │   ├── feedback_service.py         # 反馈服务
│   │   ├── feedback_loop_service.py    # 反馈闭环服务
│   │   ├── notification_service.py     # 通知服务
│   │   ├── circuit_breaker.py          # 熔断器
│   │   ├── browser_launch_guard.py     # 浏览器启动守卫
│   │   ├── platform_health_service.py  # 平台健康服务
│   │   ├── performance_service.py      # 性能服务
│   │   ├── pipeline_state_manager.py   # 流水线状态管理
│   │   ├── workflow_executor.py        # 工作流执行器
│   │   ├── workflow_dag.py             # 工作流 DAG
│   │   ├── prompt_repository.py        # Prompt 仓库
│   │   ├── prompt_optimizer_client.py  # Prompt 优化客户端
│   │   ├── deerflow_client.py          # DeerFlow 客户端
│   │   ├── letta_client.py             # Letta 客户端
│   │   ├── ruflo_client.py             # Ruflo 客户端
│   │   ├── schema_guard_service.py     # Schema 守卫
│   │   ├── debug_snapshot_service.py   # 调试快照
│   │   ├── first_release_adapter.py    # 首发适配器
│   │   ├── rpa_utils.py               # RPA 工具
│   │   └── examples.py                # 示例
│   │
│   ├── database/              # 数据库模块
│   │   ├── models.py          # SQLModel 表模型
│   │   └── service.py         # 数据库 CRUD 服务
│   │
│   ├── web/                   # Web 层
│   │   ├── app.py             # FastAPI 应用
│   │   └── view_service.py    # 视图数据服务
│   │
│   ├── utils/                 # 工具模块
│   │   ├── browser_utils.py   # 浏览器工具
│   │   ├── file_utils.py      # 文件工具
│   │   ├── logging_utils.py   # 日志工具
│   │   └── runtime_status.py  # 运行时状态
│   │
│   ├── loaders/               # 加载器
│   │   └── document_loader.py # 文档加载器
│   │
│   ├── renderers/             # 渲染器
│   │   └── __init__.py
│   │
│   └── constants/             # 常量
│       └── selectors.py       # CSS 选择器常量
│
├── prompts/                   # Prompt 模板目录
│   ├── analysis_prompt.md     # 分析 Prompt
│   ├── article_prompt.md      # 文章 Prompt
│   ├── trend_prompt.md        # 趋势 Prompt
│   ├── data_prompt.md         # 数据 Prompt
│   ├── title_prompt.md        # 标题 Prompt
│   ├── review_prompt.md       # 审核 Prompt
│   ├── polish_prompt.md       # 润色 Prompt
│   ├── insight_prompt.md      # 洞察 Prompt
│   ├── image_prompt.md        # 图片 Prompt
│   ├── publish_review_prompt.md # 发布审核 Prompt
│   ├── wechat_layout_prompt.md  # 微信排版 Prompt
│   └── ...                    # 其他 Prompt
│
├── scripts/                   # 运维与辅助脚本
│   ├── daily_run.ps1          # 每日运行 PowerShell 脚本
│   ├── cloud_daily_run.py     # 云端每日运行脚本
│   ├── local_publish.py       # 本地发布脚本
│   ├── keep_alive.py          # 浏览器会话保活
│   ├── inject_cookies.py      # Cookie 注入
│   ├── export_cookies.py      # Cookie 导出
│   ├── login_platforms.py     # 平台登录
│   └── ...                    # 其他脚本
│
├── templates/                 # Jinja2 模板
│   ├── web/                   # Web 页面模板
│   │   ├── base.html          # 基础布局
│   │   ├── dashboard.html     # 仪表盘
│   │   ├── dashboard_console.html # 控制台
│   │   ├── topics.html        # 话题页
│   │   ├── runs.html          # 运行记录页
│   │   ├── run_detail.html    # 运行详情页
│   │   ├── published.html     # 已发布页
│   │   ├── compare.html       # 对比页
│   │   └── accounts.html      # 账号管理页
│   └── images/                # 图片模板
│
├── static/                    # 静态资源
├── config/                    # 配置子目录
│   ├── selectors.yaml         # CSS 选择器配置
│   ├── bad_words.txt          # 敏感词库
│   ├── ai_cliche_words.txt    # AI 套路词库
│   ├── company_*.yaml         # 公司运营配置
│   └── deerflow/config.yaml   # DeerFlow 配置
│
├── branding/                  # 品牌与账号矩阵
│   ├── benchmark_accounts*.yaml  # 基准账号配置
│   └── self_benchmark_articles.yaml
│
├── input/                     # 输入文件目录
├── aitoearn_src/              # AiToEarn 集成源码 (TypeScript)
├── tests/                     # 测试目录
└── docs/                      # 文档目录
```

---

## 4. 核心模块职责

### 4.1 入口层

| 文件 | 职责 | 说明 |
|------|------|------|
| `main.py` | 主 CLI 入口 | 完整功能入口，支持 `--daily-run`、`--auto-run`、`--publish-run`、`--engagement` 等数十个命令行参数 |
| `run.py` | 简单模式入口 | 使用 DeepSeek API 直接生成微头条，支持手蓝金融风/散户派/正能量三种模式 |
| `webapp.py` | Web 控制台入口 | FastAPI + Uvicorn，提供仪表盘、话题管理、运行管理、发布操作等 Web 界面 |
| `run_web.py` | Web 简化入口 | 零配置启动 Web 服务 |
| `scheduler.py` | 纯 Python 调度器 | 独立进程，按配置的时间点循环触发 `main.py --daily-run` |

### 4.2 配置层 (config)

**文件**: `src/toutiao_content_factory/config.py`

核心类与数据类：

| 类名 | 职责 |
|------|------|
| `AppSettings` | 应用全局配置，聚合所有子配置 |
| `TopicCollectionSettings` | 话题采集配置（来源、关键词、过滤规则） |
| `ImageGenerationSettings` | 图片生成配置（尺寸、后端、主题色） |
| `AutomationSettings` | 自动化配置（定时时间、归档策略） |
| `ReviewSettings` | 审核配置（评分阈值、自动发布条件） |
| `PublishSettings` | 头条发布配置（浏览器、选择器、超时） |
| `WechatSettings` | 微信发布配置（API、排版、135编辑器） |
| `AccountProfile` | 账号配置模型（品牌、行业、写作风格覆盖） |
| `StyleProfile` | 写作风格配置（语调、指令、图片风格） |

关键函数：

| 函数 | 说明 |
|------|------|
| `load_settings(project_root, account_key)` | 加载配置，合并 config.yaml + 环境变量 + 账号覆盖 |
| `_deep_merge(base, override)` | 深度合并字典，用于账号配置覆盖全局配置 |
| `_env_flag(name, default)` | 解析布尔环境变量 |
| `normalize_account_key(raw_value)` | 规范化账号键名 |
| `sanitize_account_slug(raw_value)` | 生成账号 URL 安全的 slug |

配置优先级（高→低）：账号级环境变量 > 全局环境变量 > 账号覆盖(config.yaml) > 全局 config.yaml > 代码默认值

### 4.3 数据模型层 (models)

**文件**: `src/toutiao_content_factory/models.py`

| 数据类 | 职责 |
|--------|------|
| `SourceDocument` | 源文档（类型、内容、路径、元数据） |
| `ExtractionResult` | 提取结果（核心数据、行业趋势、增长数字、公司、供应链关键词、写作简报等） |
| `TrendReport` | 趋势报告（热点、平台话题、受众钩子、推荐角度） |
| `DataReport` | 数据报告（核心数据、趋势、风险标记、证据强度） |
| `ContentScore` | 内容评分（总分、标题吸引力、信息密度、AI风险、发布就绪度） |
| `GeneratedContent` | 生成内容（文章、标题、标签、封面提示词、开头行） |
| `WorkflowResult` | 工作流结果（聚合上述所有结果） |
| `TopicCandidate` | 话题候选（标题、URL、来源、匹配行业/关键词、评分、排名） |
| `DailyTopicsReport` | 每日话题报告（聚合所有候选话题） |
| `CacheContext` | 缓存上下文（命中状态、相似度、避免重复） |
| `VisualBrief` / `VisualMetric` | 视觉简报（封面标题、指标、趋势标签） |
| `ImageArtifacts` | 图片产物（封面 PNG/SVG、信息图） |
| `InsightReport` | 深度洞察报告 |
| `DataInsightReport` | 数据洞察报告 |
| `MultiSourceReport` | 多源信息聚合报告 |
| `HistoricalComparison` | 历史对比报告 |
| `ArticlePerformance` | 文章表现数据 |
| `PublishRecord` | 发布记录 |

### 4.4 Agent 层

**目录**: `src/toutiao_content_factory/agents/`

所有 Agent 继承自 `BaseAgent`（`base.py`），通过 `AgentWorkflowService` 编排执行。

| Agent | 文件 | 职责 |
|-------|------|------|
| `BaseAgent` | `base.py` | 基类，提供文本清理、品牌名处理、Prompt 构建、LLM 调用等通用能力 |
| `TrendAgent` | `trend_agent.py` | 分析行业趋势，识别热点话题，生成趋势报告 |
| `DataAgent` | `data_agent.py` | 收集和处理数据，提取关键信息与结构化数据 |
| `WriterAgent` | `writer_agent.py` | 撰写文章，根据风格和简报生成完整文章 |
| `ReviewAgent` | `review_agent.py` | 质量审核，评分并给出修改建议 |
| `PolishAgent` | `polish_agent.py` | 润色优化，提升可读性和吸引力 |
| `TitleAgent` | `title_agent.py` | 生成候选标题，结合热点和内容亮点 |
| `InsightAgent` | `insight_agent.py` | 深度洞察，提供独特观点和分析 |
| `DataMiningAgent` | `data_mining_agent.py` | 数据挖掘，从大量数据中提取价值信息 |
| `EvolutionAgent` | `evolution_agent.py` | 内容进化，跟踪内容演变趋势 |
| `PublishReviewAgent` | `publish_review_agent.py` | 发布前审查，最终校对 |
| `WechatLayoutAgent` | `wechat_layout_agent.py` | 微信排版，适配公众号阅读体验 |
| `BenchmarkMonitorAgent` | `benchmark_monitor_agent.py` | 基准监控，对比行业基准提供优化建议 |
| `SampleCollectorAgent` | `sample_collector_agent.py` | 样本采集，收集参考样本 |

`agency_roles.py` 定义了各 Agent 的角色描述和协作关系。

### 4.5 Service 层

**目录**: `src/toutiao_content_factory/services/`

#### 4.5.1 核心流水线

| 服务 | 文件 | 职责 |
|------|------|------|
| `ContentFactoryPipeline` | `pipeline.py` | 核心流水线，协调文档加载→信号提取→内容生成→审核→导出的完整流程 |
| `ContentFactoryAppService` | `app_service.py` | 应用编排服务，提供 `daily_run()`、`auto_run()`、`manual_run()`、`collect_topics()`、`publish_run()` 等高层接口 |
| `AgentWorkflowService` | `agent_workflow.py` | Agent 工作流编排，按顺序调度各 Agent 执行 |
| `WorkflowExecutor` | `workflow_executor.py` | 工作流执行器，按 DAG 拓扑顺序运行任务 |
| `WorkflowStep` / `validate_workflow` / `topological_sort` | `workflow_dag.py` | 工作流 DAG 定义与验证 |
| `PipelineStateManager` | `pipeline_state_manager.py` | 流水线状态持久化（SQLite + JSON 回退），支持断点续跑 |

#### 4.5.2 内容生成与处理

| 服务 | 文件 | 职责 |
|------|------|------|
| `OpenAIContentService` | `openai_service.py` | OpenAI API 封装，含重试机制与费用跟踪 |
| `ContentGenerator` | `content_generator.py` | 内容生成器，整合 Prompt 仓库与 AI 服务 |
| `SignalExtractor` | `extractor.py` | 信号提取器，从输入文档中提取结构化数据 |
| `ExportService` | `exporter.py` | 内容导出器，输出 Markdown/JSON/编辑包 |
| `PromptRepository` | `prompt_repository.py` | Prompt 模板管理与检索 |
| `AntiAiFlavorService` | `anti_ai_flavor.py` | 去 AI 味，调整生成内容使其更自然 |
| `ArticleQualityChecker` | `article_quality_checker.py` | 文章质量检测与评估 |
| `ArticleSanitizer` | `article_sanitizer.py` | 文章净化，清理 HTML 标签与转义字符 |
| `SchemaGuardService` | `schema_guard_service.py` | 数据 Schema 检查 |

#### 4.5.3 话题管理

| 服务 | 文件 | 职责 |
|------|------|------|
| `TopicCollectionService` | `topic_collection.py` | 从 RSS/HTML 源采集话题 |
| `TopicPoolService` | `topic_pool_service.py` | 话题池增删查改 |
| `TopicExporter` | `topic_exporter.py` | 话题数据导出 |
| `BenchmarkHotspotService` | `benchmark_hotspot_service.py` | 基准热点识别与分析 |

#### 4.5.4 发布服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `PublishService` | `publish_service.py` | 头条发布（Playwright RPA） |
| `WechatPublishService` | `wechat_service.py` | 微信公众号发布（API + 浏览器自动化） |
| `MultiPlatformPublisher` | `multi_platform_publisher.py` | 多平台发布统一管理 |
| `BilibiliPublisher` | `bilibili_publisher.py` | B站发布 |
| `ZhihuPublisher` | `zhihu_publisher.py` | 知乎发布 |
| `XiaohongshuPublisher` | `xiaohongshu_publisher.py` | 小红书发布 |

#### 4.5.5 微信排版

| 服务 | 文件 | 职责 |
|------|------|------|
| `WechatLayoutEngine` | `wechat_layout_engine.py` | 微信排版引擎，内置多主题 |
| `Wechat135EditorService` | `wechat_135editor_service.py` | 135 编辑器集成 |
| `Md2WechatService` | `md2wechat_service.py` | Markdown 转 WeChat HTML |
| `HtmlAnythingService` | `html_anything_service.py` | 通用 HTML 渲染 |
| `WechatLaunchService` | `wechat_launch_service.py` | 微信账号启动包 |
| `WechatPages` | `wechat_pages.py` | 微信页面处理 |

#### 4.5.6 图片生成

| 服务 | 文件 | 职责 |
|------|------|------|
| `ImageGenerationService` | `image_generation_service.py` | 图片生成（OpenAI Images API + 本地渲染） |
| `FrontendImageRenderer` | `frontend_image_renderer.py` | 前端图片渲染（HTML + Playwright 截图） |

#### 4.5.7 审核与合规

| 服务 | 文件 | 职责 |
|------|------|------|
| `ReviewWorkflowService` | `review_service.py` | 审核工作流（预审、批准、拒绝、发布） |
| `ContentGuardService` | `content_guard_service.py` | 内容合规守卫 |
| `ComplianceService` | `compliance_service.py` | 合规服务（AIGC 声明、日发限制） |

#### 4.5.8 商业化

| 服务 | 文件 | 职责 |
|------|------|------|
| `MonetizationEngine` | `monetization_engine.py` | 变现引擎（收入报告） |
| `MonetizationService` | `monetization_service.py` | 变现服务 |
| `CompanyOSService` | `company_os_service.py` | 一人公司操作系统蓝图 |
| `LeadOpsService` | `lead_ops_service.py` | 线索运营（记录、跟进、方案生成、转化） |
| `RevenueEngineService` | `revenue_engine_service.py` | 收入引擎（赞助商、订阅者管理） |
| `GrowthOpsService` | `growth_ops_service.py` | 增长运营 |

#### 4.5.9 运营与监控

| 服务 | 文件 | 职责 |
|------|------|------|
| `EngagementAgent` | `engagement_agent.py` | 互动 Agent（点赞、评论、关注） |
| `MatrixOpsManager` | `matrix_ops_manager.py` | 矩阵运营管理 |
| `PlatformHealthService` | `platform_health_service.py` | 平台健康监控 |
| `PerformanceService` | `performance_service.py` | 性能监控与分析 |
| `NotificationService` | `notification_service.py` | 通知服务 |
| `CircuitBreaker` | `circuit_breaker.py` | 熔断器模式 |
| `BrowserLaunchGuard` | `browser_launch_guard.py` | 浏览器启动保护 |
| `FeedbackService` | `feedback_service.py` | 反馈收集 |
| `FeedbackLoopService` | `feedback_loop_service.py` | 反馈闭环 |
| `DebugSnapshotService` | `debug_snapshot_service.py` | 调试快照 |
| `FirstReleaseAdapter` | `first_release_adapter.py` | 首发适配 |

#### 4.5.10 外部 AI 服务客户端

| 客户端 | 文件 | 职责 |
|--------|------|------|
| `DeerFlowClient` | `deerflow_client.py` | DeerFlow 深度研究 |
| `LettaClient` | `letta_client.py` | Letta 记忆 Agent |
| `RufloClient` | `ruflo_client.py` | Ruflo 工作流引擎 |
| `PromptOptimizerClient` | `prompt_optimizer_client.py` | Prompt 优化 |

#### 4.5.11 其他

| 服务 | 文件 | 职责 |
|------|------|------|
| `CacheService` | `cache_service.py` | 缓存服务（防重复计算） |
| `AutomationService` | `automation.py` | 自动化调度（话题选择、归档） |
| `SearchService` | `search_service.py` | 内容搜索与检索 |
| `RpaUtils` | `rpa_utils.py` | RPA 浏览器自动化工具 |

### 4.6 数据库层 (database)

**目录**: `src/toutiao_content_factory/database/`

| 组件 | 文件 | 职责 |
|------|------|------|
| `Topic` | `models.py` | 话题持久化模型（标题、URL、来源、评分、匹配行业/关键词） |
| `Article` | `models.py` | 文章持久化模型（标题、正文、流水线阶段、审核状态、评分） |
| `RunRecord` | `models.py` | 运行记录模型（流水线阶段、各平台发布状态、调试快照） |
| `PipelineStage` | `models.py` | 流水线阶段枚举（INIT → DOCUMENTS_LOADED → SIGNALS_EXTRACTED → CONTENT_GENERATED → REVIEWED → APPROVED → PUBLISHING → PUBLISHED / FAILED） |
| `PublishPlatform` | `models.py` | 发布平台枚举（TOUTIAO / WECHAT） |
| `DatabaseService` | `service.py` | 数据库 CRUD 服务，封装 SQLModel + SQLite，支持 Article/Topic/RunRecord 的增删查改，含去重逻辑（URL + 标题相似度） |

### 4.7 Web 层

**目录**: `src/toutiao_content_factory/web/`

| 组件 | 文件 | 职责 |
|------|------|------|
| `create_app()` | `app.py` | 创建 FastAPI 应用，注册所有路由与调度器 |
| `AccountContext` | `app.py` | 账号上下文（settings + app_service + view_service） |
| `WebViewService` | `view_service.py` | 视图数据服务，为模板提供数据 |

Web 路由：

| 路由 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 账号管理首页 |
| `/dashboard` | GET | 控制台仪表盘 |
| `/topics` | GET | 话题列表 |
| `/runs` | GET | 运行记录列表 |
| `/runs/{run_name}` | GET | 运行详情 |
| `/runs/{run_name}/compare` | GET | 原文与生成内容对比 |
| `/published` | GET | 已发布文章列表 |
| `/actions/collect-topics` | POST | 采集话题 |
| `/actions/manual-run` | POST | 手动生成 |
| `/actions/auto-run` | POST | 自动选题生成 |
| `/actions/daily-run` | POST | 完整日更流程 |
| `/runs/{run_name}/approve` | POST | 审核通过 |
| `/runs/{run_name}/reject` | POST | 审核驳回 |
| `/runs/{run_name}/publish` | POST | 发布 |
| `/api/scheduler/start` | POST | 启动调度器 |
| `/api/scheduler/pause` | POST | 暂停调度器 |
| `/api/logs/stream` | GET | SSE 日志流 |
| `/healthz` | GET | 健康检查 |

### 4.8 工具层 (utils)

| 模块 | 文件 | 职责 |
|------|------|------|
| `browser_utils` | `browser_utils.py` | 浏览器启动、页面交互等 Playwright 工具 |
| `file_utils` | `file_utils.py` | 文件读写、路径处理工具 |
| `logging_utils` | `logging_utils.py` | 日志配置工具 |
| `runtime_status` | `runtime_status.py` | 运行时心跳状态管理 |

---

## 5. 关键类与函数说明

### 5.1 ContentFactoryAppService（应用编排核心）

```python
class ContentFactoryAppService:
    def __init__(self, settings: AppSettings, logger: logging.Logger)

    # 话题采集
    def collect_topics(self) -> dict[str, str]

    # 运行模式
    def daily_run(self, style_override, dry_run, refresh_cache, topic_rank, topic_title, no_archive, fast_mode, skip_wait) -> dict[str, str]
    def auto_run(self, style_override, dry_run, refresh_cache, topic_rank, topic_title, collect_if_missing, force_collect, no_archive, fast_mode) -> dict[str, str]
    def manual_run(self, paste_text, style_override, dry_run, refresh_cache, include_input_dir, fast_mode) -> dict[str, str]

    # 审核与发布
    def prepare_review(self, run_dir) -> dict[str, str]
    def approve_run(self, run_dir, selected_title, selected_title_index, review_notes, auto_publish) -> dict[str, str]
    def reject_run(self, run_dir, review_notes) -> dict[str, str]
    def publish_run(self, run_dir, draft_only) -> dict[str, str]
    def publish_latest_approved(self, unpublished_only, draft_only) -> dict[str, str]

    # 微信发布
    def publish_wechat_run(self, run_dir, submit) -> dict[str, str]
    def publish_wechat_latest_approved(self, unpublished_only, submit) -> dict[str, str]

    # 平台健康与登录
    def collect_publish_preflight(self) -> dict[str, str]
    def collect_platform_health(self) -> dict[str, str]
    def initialize_publish_login(self) -> dict[str, str]
    def initialize_wechat_publish_login(self) -> dict[str, str]

    # 商业化
    def build_company_os(self) -> dict[str, str]
    def build_revenue_engine(self) -> dict[str, str]
    def run_profit_loop(self) -> dict[str, str]
    def record_lead(...) -> dict[str, str]
    def update_lead(...) -> dict[str, str]
    def generate_lead_proposal(...) -> dict[str, str]
    # ... 更多线索操作方法

    # 基准与性能
    def build_benchmark_report(self) -> dict[str, str]
    def collect_benchmark_hot_topics(...) -> dict[str, str]
    def record_performance(...) -> dict[str, str]
    def build_performance_report(self) -> dict[str, str]

    # 图片
    def generate_images_for_run(self, run_dir) -> dict[str, str]
```

### 5.2 ContentFactoryPipeline（流水线核心）

```python
class ContentFactoryPipeline:
    def __init__(self, settings, logger, task_id, fast_mode)

    def run(self, paste_text, style_override, dry_run, refresh_cache, topic_rank, topic_title, collect_if_missing, force_collect, no_archive, skip_wait) -> dict[str, str]
    def resume(self, task_id, paste_text) -> dict[str, str]
    def close(self) -> None
```

### 5.3 DatabaseService（数据库服务）

```python
class DatabaseService:
    def __init__(self, db_path, echo, auto_create_tables)

    # Article CRUD
    def save_article(self, article: Article) -> Optional[Article]
    def get_article(self, run_name: str) -> Optional[Article]
    def list_articles(self, account_key, limit, offset, status) -> list[Article]
    def update_article_stage(self, run_name, stage, extra) -> None

    # Run Record CRUD
    def save_run_record(self, record: RunRecord) -> Optional[RunRecord]
    def get_run_record(self, run_name, account_key) -> Optional[RunRecord]
    def list_run_records(self, account_key, limit, offset) -> list[RunRecord]
    def mark_run_completed(self, run_name, account_key, status) -> None

    # Topic CRUD
    def save_topic(self, topic: Topic) -> Optional[Topic]  # 含 URL 去重 + 标题相似度去重
    def list_topics(self, account_key, unused_only, limit) -> list[Topic]

    # Stats
    def get_stats(self, account_key) -> dict[str, Any]
```

---

## 6. 核心工作流

### 6.1 Daily Run 完整流程

```
话题采集 → 话题筛选与排序 → 自动选题 → 数据提取 → 趋势分析
    → 文章撰写 → 质量审核 → 润色优化 → 标题生成 → 深度洞察
    → 内容评分 → 审核决策 → 导出发布包 → 多平台发布
```

详细阶段：

1. **话题采集** (`TopicCollectionService`)：从配置的 RSS/HTML 源抓取最新文章，通过关键词匹配行业，评分排序
2. **自动选题** (`TopicAutomationService`)：按排名选择最佳话题，检查缓存避免重复
3. **信号提取** (`SignalExtractor`)：从原始材料中提取核心数据、趋势、公司、供应链关键词
4. **Agent 工作流** (`AgentWorkflowService`)：依次执行 Trend → Data → Writer → Review → Polish → Title → Insight Agent
5. **内容评分** (`ContentScore`)：综合评分（标题吸引力、信息密度、收藏潜力、AI 风险）
6. **审核决策**：根据评分决定自动发布或人工审核
7. **导出发布包** (`ExportService`)：生成 Markdown/JSON/编辑包
8. **多平台发布**：头条（RPA）、微信（API+RPA）等

### 6.2 Pipeline 阶段流转

```
INIT → DOCUMENTS_LOADED → SIGNALS_EXTRACTED → CONTENT_GENERATED
    → REVIEWED → APPROVED → PUBLISHING → PUBLISHED
    ↘ FAILED
    ↘ REVIEW_REQUIRED
```

支持通过 `--resume` 参数从断点续跑。

### 6.3 审核与发布流程

```
生成内容 → 自动评分 → [评分达标?] → 自动发布
                      → [评分不达标?] → 人工审核
                                    → approve_run → publish_run
                                    → reject_run
```

自动发布条件（`ReviewSettings.qualifies_for_auto_publish()`）：
- `overall_score >= min_auto_publish_overall_score`（默认 65）
- `ai_risk <= max_auto_publish_ai_risk`（默认 70）
- `validation_score >= min_auto_publish_validation_score`（默认 70）
- `publish_readiness_score >= min_auto_publish_readiness_score`（默认 70）

---

## 7. 多平台发布体系

### 7.1 发布架构

```
                    ┌─ PublishService (头条 RPA)
                    ├─ WechatPublishService (微信 API + RPA)
ContentFactoryAppService ├─ BilibiliPublisher (B站)
                    ├─ ZhihuPublisher (知乎)
                    └─ XiaohongshuPublisher (小红书)
```

### 7.2 发布方式

| 平台 | 发布方式 | 说明 |
|------|----------|------|
| 今日头条 | Playwright RPA | 浏览器自动化填写表单、上传封面、点击发布 |
| 微信公众号 | API + 浏览器 RPA | API 上传素材 + 浏览器创建草稿/提交发布 |
| B站/知乎/小红书 | 预留接口 | 通过 `MultiPlatformPublisher` 统一注册 |

### 7.3 浏览器会话管理

- 每个账号独立的浏览器 Profile 目录（`.runtime/accounts/{slug}/toutiao_profile`）
- `BrowserLaunchGuard` 防止多实例冲突
- `keep_alive.py` 定期刷新 Cookie 防止会话过期
- `inject_cookies.py` / `export_cookies.py` 支持跨环境 Cookie 迁移

---

## 8. 商业化与变现系统

### 8.1 一人公司 AI 系统

| 组件 | 服务 | 功能 |
|------|------|------|
| 公司 OS | `CompanyOSService` | 生成运营蓝图、待办清单、评分卡 |
| 收入引擎 | `RevenueEngineService` | 赞助商拓展、订阅者管理、收入追踪 |
| 线索运营 | `LeadOpsService` | 线索记录、跟进、方案生成、转化追踪 |
| 增长运营 | `GrowthOpsService` | 用户增长策略 |
| 变现引擎 | `MonetizationEngine` | 收入报告与数据同步 |

### 8.2 线索生命周期

```
record_lead → mark_lead_outreach_sent → triage_lead_reply
    → generate_lead_proposal → mark_lead_proposal_sent
    → mark_lead_call_booked → mark_lead_payment_received
    → mark_lead_delivery_started → mark_lead_delivery_completed
    → request_lead_testimonial → mark_lead_expansion
    → convert_expansion_to_lead (闭环)
```

### 8.3 外部 AI 服务集成

| 服务 | 用途 | Docker 镜像 |
|------|------|-------------|
| DeerFlow | 深度研究 | `ghcr.io/bytedance/deer-flow-backend` |
| Letta | 记忆 Agent | `letta/letta` |
| PromptOptimizer | Prompt 优化 | `linshen/prompt-optimizer` |
| Ruflo | 工作流引擎 | `ruvnet/ruflo` |

---

## 9. 依赖关系

### 9.1 Python 核心依赖

| 包 | 版本 | 用途 |
|----|------|------|
| `openai` | ≥1.30.0 | OpenAI 兼容 API 调用 |
| `fastapi` | ≥0.115.0 | Web 框架 |
| `uvicorn` | ≥0.30.0 | ASGI 服务器 |
| `playwright` | ==1.53.0 | 浏览器自动化 |
| `sqlmodel` | ≥0.0.22 | ORM + 数据库 |
| `pydantic` | ≥2.0.0 | 数据验证 |
| `Jinja2` | ≥3.1.0 | 模板引擎 |
| `PyYAML` | ≥6.0 | YAML 配置解析 |
| `Pillow` | ≥10.0.0 | 图片处理 |
| `matplotlib` | ≥3.8.0 | 图表生成 |
| `python-dotenv` | ≥1.0.1 | 环境变量加载 |
| `tenacity` | ≥8.2.0 | 重试机制 |
| `psutil` | ≥5.9.8 | 进程管理 |
| `httpx` | ≥0.27.0 | HTTP 客户端 |
| `APScheduler` | ≥3.10.0 | 定时调度 |

### 9.2 模块间依赖关系

```
main.py / webapp.py
    └── ContentFactoryAppService
        ├── ContentFactoryPipeline
        │   ├── AgentWorkflowService
        │   │   └── BaseAgent → TrendAgent / DataAgent / WriterAgent / ...
        │   ├── SignalExtractor
        │   ├── ContentGenerator
        │   │   └── OpenAIContentService
        │   ├── ExportService
        │   ├── CacheService
        │   └── PipelineStateManager
        │       └── DatabaseService
        ├── TopicCollectionService
        ├── ReviewWorkflowService
        ├── PublishService (头条)
        ├── WechatPublishService
        │   ├── WechatLayoutEngine
        │   ├── Wechat135EditorService
        │   └── Md2WechatService
        ├── MultiPlatformPublisher
        ├── ImageGenerationService
        │   └── FrontendImageRenderer
        ├── ContentGuardService
        ├── ComplianceService
        ├── AntiAiFlavorService
        ├── MonetizationEngine
        ├── LeadOpsService
        ├── RevenueEngineService
        ├── CompanyOSService
        ├── EngagementAgent
        ├── PlatformHealthService
        ├── NotificationService
        └── CircuitBreaker
```

---

## 10. 项目运行方式

### 10.1 环境准备

```bash
# 1. 克隆项目
git clone <repo-url>
cd Automated-workflow-main

# 2. 创建虚拟环境
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
playwright install chromium
playwright install-deps chromium

# 5. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key 等配置
```

### 10.2 CLI 运行

```bash
# 完整日更流程（采集→生成→审核→发布）
python main.py --daily-run --account default

# 自动选题生成（不发布）
python main.py --auto-run --account robot_notes

# 手动生成（指定文本）
python main.py --paste "一段文字" --style deep_industry_analysis

# 仅采集话题
python main.py --collect-topics

# 审核与发布
python main.py --approve-latest
python main.py --publish-latest-approved

# 微信发布
python main.py --wechat-publish-latest-approved

# 深度研究
python main.py --deep-research "人形机器人产业链"

# 互动 Agent
python main.py --engagement --account default

# 断点续跑
python main.py --resume <task_id>

# 简单模式（DeepSeek 直接生成）
python run.py s    # 手蓝金融风
python run.py x    # 散户派实战向
python run.py w    # 正能量情感风
```

### 10.3 Web 控制台

```bash
# 启动 Web 服务
python webapp.py --port 8001

# 访问
http://127.0.0.1:8001/
```

### 10.4 定时调度

**方式一：Web 内置调度器**（推荐）

Web 服务启动后自动注册 APScheduler 定时任务，按 `config.yaml` 中 `automation.daily_run_times` 配置的时间点执行。

**方式二：Windows 任务计划**

```powershell
# 注册定时任务
powershell -ExecutionPolicy Bypass -File scripts\register_daily_task.ps1
```

**方式三：纯 Python 调度器**

```bash
python scheduler.py
```

**方式四：GitHub Actions 云端运行**

配置 GitHub Secrets 后，`.github/workflows/daily-run.yml` 会按 cron 时间表自动运行。

### 10.5 Docker 部署

```bash
# 启动所有服务（含 DeerFlow、Letta 等）
docker compose up -d

# 仅启动内容工厂
docker compose up -d content-factory
```

### 10.6 浏览器登录初始化

```bash
# 头条登录
python main.py --init-publish-login --account default

# 微信登录
python main.py --init-wechat-login

# 135 编辑器登录
python main.py --init-135editor-login
```

---

## 11. 异常体系

**文件**: `src/toutiao_content_factory/exceptions.py`

```
ContentFactoryError (基类)
├── ContentQualityError          # 内容质量不达标
│   └── LowQualityAbortException # 话题质量过低，中止工作流
├── ContentSafetyError           # 内容安全违规
│   └── SensitiveWordError       # 敏感词检测
├── InputNotFoundError           # 无输入源
├── UnsupportedFileTypeError     # 不支持的文件类型
├── AIRequestError               # AI 请求失败
│   └── AIResponseFormatError    # AI 响应格式错误
├── PublishError                 # 发布失败
│   ├── RPAError                 # RPA 自动化失败
│   │   └── RPAElementNotFoundError # 页面元素未找到
│   ├── PlatformAuthError        # 平台认证过期
│   ├── WechatAuthError          # 微信 API 认证失败
│   └── SessionExpiredException  # 微信浏览器会话过期
├── ConfigError                  # 配置错误
├── DatabaseError                # 数据库错误
└── NetworkError                 # 网络错误
    ├── NetworkTimeoutError      # 请求超时
    ├── NetworkConnectionError   # 连接失败
    └── SourceUnavailableError   # 内容源不可用
```

---

## 12. 配置体系

### 12.1 配置来源

| 来源 | 优先级 | 说明 |
|------|--------|------|
| 账号级环境变量 | 最高 | `BRAND_NAME__ROBOT_NOTES=xxx`，仅对指定账号生效 |
| 全局环境变量 | 高 | `OPENAI_API_KEY`、`TOUTIAO_AUTO_PUBLISH` 等 |
| `.env` 文件 | 高 | 通过 python-dotenv 加载到环境变量 |
| 账号覆盖 (config.yaml) | 中 | `accounts.{key}` 下的非 meta 字段深度合并 |
| 全局 config.yaml | 低 | 项目根目录配置文件 |
| 代码默认值 | 最低 | `_default_config_payload()` 等函数 |

### 12.2 多账号配置

`config.yaml` 中的 `accounts` 节定义多个账号，每个账号可覆盖：

- `industry`：品牌名、行业焦点、目标受众
- `writing`：默认写作风格
- `topic_collection`：话题源、关键词映射
- `browser_profile_dir`：独立浏览器 Profile

当前预配置账号：

| 账号 Key | 标签 | 行业焦点 | 状态 |
|----------|------|----------|------|
| `default` | 手蓝科技笔记 | AI、机器人、新能源、储能 | 启用 |
| `robot_notes` | 手蓝机器人笔记 | 人形机器人、具身智能、AI硬件 | 启用 |
| `politics_notes` | 手蓝政治笔记 | 国际政治、地缘博弈 | 启用 |
| `sports_notes` | 手蓝体育笔记 | 体育赛事、足球篮球 | 启用 |
| `hardware_notes` | 手蓝硬件笔记 | AI硬件、芯片、CPO | 禁用 |
| `automation_notes` | 手蓝智造笔记 | 工业自动化、智能装备 | 禁用 |

### 12.3 写作风格

| 风格 Key | 标签 | 特点 |
|----------|------|------|
| `deep_industry_analysis` | 深度行业分析风 | 冷静专业，强调产业链兑现和环节分化 |
| `tech_media` | 科技媒体风 | 节奏快、信息密度高、兼顾可读性 |
| `finance_report` | 财经研报风 | 结论先行，强调数据和盈利模型 |
| `viral_science` | 爆款科普风 | 易懂、反差强、适合非专业读者 |

### 12.4 话题采集源

| 源 Key | 类型 | URL |
|--------|------|-----|
| `36kr` | RSS | 36kr.com/feed-article, /feed-newsflash |
| `zhidx` | HTML | zhidx.com |
| `ofweek` | HTML | robot.ofweek.com, chuneng.ofweek.com |
| `chuneng_ofweek` | HTML | chuneng.ofweek.com (同域限制) |
| `newenergy_ofweek` | HTML | nev.ofweek.com (同域限制) |
| `qbitai` | RSS | qbitai.com/feed |
| `jiqizhixin` | RSS | jiqizhixin.com/rss |
| `leiphone` | RSS | leiphone.com/feed |
| `gg_robot` | HTML | gg-robot.com |
