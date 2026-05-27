# AI 今日头条内容工厂 — Code Wiki

> 版本：1.4.0 | 生成日期：2026-05-26

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈与依赖](#2-技术栈与依赖)
3. [项目目录结构](#3-项目目录结构)
4. [整体架构](#4-整体架构)
5. [核心模块详解](#5-核心模块详解)
   - 5.1 [配置模块 (config)](#51-配置模块-config)
   - 5.2 [数据模型 (models)](#52-数据模型-models)
   - 5.3 [异常体系 (exceptions)](#53-异常体系-exceptions)
   - 5.4 [Agent 智能体层](#54-agent-智能体层)
   - 5.5 [服务层 (services)](#55-服务层-services)
   - 5.6 [加载器层 (loaders)](#56-加载器层-loaders)
   - 5.7 [Web 控制台层 (web)](#57-web-控制台层-web)
   - 5.8 [工具层 (utils)](#58-工具层-utils)
6. [核心工作流程](#6-核心工作流程)
7. [多账号体系](#7-多账号体系)
8. [发布平台集成](#8-发布平台集成)
9. [Prompt 模板体系](#9-prompt-模板体系)
10. [项目运行方式](#10-项目运行方式)
11. [依赖关系图](#11-依赖关系图)

---

## 1. 项目概述

**AI 今日头条内容工厂**（Toutiao Content Factory）是一个面向中文科技行业媒体账号的自动化内容工作流系统。它能够：

- 从配置的多个新闻源采集热门话题
- 分析数据与行业信号
- 生成标题、文章正文、标签、摘要与图片提示词
- 生成封面图与信息图素材
- 审核与批准内容运行
- 通过 Playwright 浏览器自动化发布到今日头条创作者中心
- 支持微信公众号草稿/发布
- 支持抖音创作者中心发布（实验性）
- 管理定时日更任务与本地 Web 控制台

---

## 2. 技术栈与依赖

| 类别 | 技术 | 版本要求 |
|------|------|----------|
| 语言 | Python | 3.11+ |
| Web 框架 | FastAPI + Uvicorn | ≥0.115.0 / ≥0.30.0 |
| 浏览器自动化 | Playwright | ≥1.52.0 |
| AI 接口 | OpenAI-compatible API (DeepSeek等) | openai ≥1.30.0 |
| 图像处理 | Pillow / Matplotlib | ≥10.0.0 / ≥3.8.0 |
| 模板引擎 | Jinja2 | ≥3.1.0 |
| 配置管理 | PyYAML + python-dotenv | ≥6.0 / ≥1.0.1 |
| PDF 解析 | pypdf | ≥4.2.0 |
| 文件上传 | python-multipart | ≥0.0.9 |
| 操作系统 | Windows 10/11 | Microsoft Edge |

---

## 3. 项目目录结构

```
.
├── src/                              # 应用源码
│   └── toutiao_content_factory/
│       ├── __init__.py               # 包入口
│       ├── config.py                 # 配置加载与 AppSettings
│       ├── models.py                 # 数据模型定义
│       ├── exceptions.py             # 自定义异常
│       ├── agents/                   # AI Agent 智能体
│       │   ├── base.py               # Agent 基类
│       │   ├── trend_agent.py        # 趋势分析 Agent
│       │   ├── data_agent.py         # 数据提取 Agent
│       │   ├── writer_agent.py       # 文章写作 Agent
│       │   ├── polish_agent.py       # 文章润色 Agent
│       │   ├── title_agent.py        # 标题生成 Agent
│       │   ├── review_agent.py       # 内容审核 Agent
│       │   ├── publish_review_agent.py # 发布前审核 Agent
│       │   ├── benchmark_monitor_agent.py # 标杆监控 Agent
│       │   ├── sample_collector_agent.py  # 样本采集 Agent
│       │   └── wechat_layout_agent.py     # 微信排版 Agent
│       ├── loaders/
│       │   └── document_loader.py    # 文档加载器
│       ├── renderers/
│       │   └── __init__.py           # 渲染器（预留）
│       ├── services/                 # 业务服务层
│       │   ├── pipeline.py           # 核心管线
│       │   ├── agent_workflow.py     # Agent 工作流编排
│       │   ├── app_service.py        # 应用服务（业务入口）
│       │   ├── openai_service.py     # OpenAI API 封装
│       │   ├── content_generator.py  # 内容生成器
│       │   ├── cache_service.py      # 缓存管理
│       │   ├── exporter.py           # 内容导出
│       │   ├── extractor.py          # 信号提取器
│       │   ├── publish_service.py    # 今日头条发布
│       │   ├── wechat_service.py     # 微信公众号服务
│       │   ├── wechat_launch_service.py # 微信首发服务
│       │   ├── douyin_service.py     # 抖音发布服务
│       │   ├── review_service.py     # 审核服务
│       │   ├── prompt_repository.py  # Prompt 模板仓库
│       │   ├── topic_collection.py   # 话题采集
│       │   ├── topic_pool_service.py # 话题池管理
│       │   ├── topic_exporter.py     # 话题导出
│       │   ├── image_generation_service.py # 图片生成
│       │   ├── growth_ops_service.py # 增长运营
│       │   ├── benchmark_hotspot_service.py # 标杆热点
│       │   ├── notification_service.py # 通知服务
│       │   ├── automation.py         # 自动化调度
│       │   ├── pipeline_state_manager.py # 管线状态管理
│       │   ├── article_quality_checker.py # 文章质量检查
│       │   ├── article_sanitizer.py  # 文章清洗
│       │   ├── content_guard_service.py # 内容守卫
│       │   ├── schema_guard_service.py # Schema 守卫
│       │   ├── debug_snapshot_service.py # 调试快照
│       │   ├── feedback_service.py   # 反馈服务
│       │   ├── md2wechat_service.py  # MD转微信排版
│       │   ├── platform_health_service.py # 平台健康检查
│       │   ├── search_service.py     # 搜索服务
│       │   └── examples.py           # 示例数据
│       ├── utils/
│       │   ├── file_utils.py         # 文件工具
│       │   ├── logging_utils.py      # 日志工具
│       │   └── runtime_status.py     # 运行时状态
│       └── web/
│           ├── app.py                # FastAPI 应用
│           └── view_service.py       # 视图服务
├── scripts/                          # 运维脚本
│   ├── bootstrap.ps1                 # 环境初始化
│   ├── daily_run.ps1                 # 日更执行
│   ├── daily_run_enhanced.ps1        # 增强日更
│   ├── daily_run_wrapper.cmd/vbs     # 日更包装器
│   ├── init_publish_login.ps1        # 今日头条登录初始化
│   ├── init_wechat_publish_login.ps1 # 微信登录初始化
│   ├── quick_config.ps1              # 快速配置
│   ├── register_daily_task.ps1       # 注册定时任务
│   ├── register_all_accounts.ps1     # 注册所有账号任务
│   ├── register_wechat_daily_task.ps1 # 注册微信定时任务
│   ├── start_webapp.ps1              # 启动 Web 控制台
│   ├── run_now.ps1/bat               # 立即执行
│   └── unregister_daily_task.ps1     # 注销定时任务
├── prompts/                          # Prompt 模板目录
├── templates/web/                    # HTML 模板
├── static/                           # Web 静态资源
├── branding/                         # 品牌与标杆账号配置
├── input/                            # 手动输入文件
├── main.py                           # CLI 入口
├── webapp.py                         # Web 控制台入口
├── run.py                            # 独立微头条生成器
├── config.yaml                       # 应用配置
├── requirements.txt                  # Python 依赖
└── .env.example                      # 环境变量模板
```

运行时目录（Git 排除）：

| 目录 | 用途 |
|------|------|
| `output/` | 内容输出、运行归档 |
| `logs/` | 运行日志 |
| `cache/` | 缓存数据 |
| `.runtime/` | 浏览器 Profile、自动化状态 |
| `.venv/` | Python 虚拟环境 |

---

## 4. 整体架构

系统采用 **分层架构 + Agent 编排** 模式：

```
┌──────────────────────────────────────────────────────────┐
│                    Web 控制台 (FastAPI)                     │
│  dashboard / topics / runs / published / accounts         │
├──────────────────────────────────────────────────────────┤
│                    应用服务层 (AppService)                  │
│  manual_run / auto_run / daily_run / publish / review     │
├──────────────────────────────────────────────────────────┤
│                    核心管线 (Pipeline)                      │
│  load → extract → cache_check → workflow → export         │
├──────────────────────────────────────────────────────────┤
│                Agent 工作流 (AgentWorkflowService)          │
│  Trend → Data → Writer → Polish → Title → Review → PubReview │
├──────────────────────────────────────────────────────────┤
│                    基础服务层                               │
│  OpenAI / Cache / Export / Prompt / Notification / ...    │
├──────────────────────────────────────────────────────────┤
│                    平台发布层                               │
│  ToutiaoPublish / WechatService / DouyinService           │
├──────────────────────────────────────────────────────────┤
│                    配置与模型层                             │
│  AppSettings / WorkflowResult / ContentScore / ...        │
└──────────────────────────────────────────────────────────┘
```

---

## 5. 核心模块详解

### 5.1 配置模块 (config)

**文件**: [config.py](file:///d:/Solo/今日头条/src/toutiao_content_factory/config.py)

| 类/函数 | 职责 |
|---------|------|
| `AppSettings` | 核心配置数据类，整合所有运行时参数 |
| `StyleProfile` | 写作风格配置（key、label、tone、各类 instruction） |
| `load_settings()` | 从 config.yaml + .env 加载配置，支持多账号 |
| `normalize_account_key()` | 规范化账号标识 |
| `DEFAULT_ACCOUNT_KEY` | 默认账号键值常量 `"default"` |

**配置来源优先级**: `.env` 环境变量 > `config.yaml` > 代码默认值

**关键配置分组**:

| 分组 | 说明 |
|------|------|
| `model` | AI 模型配置（name, temperature） |
| `writing` | 写作参数（字数目标、风格、标题数量、缓存阈值） |
| `industry` | 行业定位（品牌名、焦点、受众） |
| `topic_collection` | 话题采集（来源、关键词映射、超时） |
| `automation` | 自动化参数（默认选题排名、归档设置） |
| `review` | 审核阈值（最低分、AI 风险上限、自动发布阈值） |
| `styles` | 四种写作风格定义 |
| `accounts` | 多账号配置（每个账号独立的行业/风格/来源） |
| `douyin` | 抖音发布配置 |

### 5.2 数据模型 (models)

**文件**: [models.py](file:///d:/Solo/今日头条/src/toutiao_content_factory/models.py)

| 数据类 | 职责 |
|--------|------|
| `SourceDocument` | 输入文档（类型、内容、路径、元数据） |
| `ExtractionResult` | 信号提取结果（核心数据、趋势、公司、关键词、选题信息、质量评分） |
| `TrendReport` | 趋势分析报告（热门趋势、平台话题、受众钩子、推荐角度） |
| `DataReport` | 数据分析报告（核心数据、增长数字、风险标记、写作简报） |
| `ContentScore` | 内容评分（总分、标题吸引力、信息密度、AI 风险、发布就绪度） |
| `GeneratedContent` | 生成内容（文章、标题、标签、摘要、图片提示词、开头语） |
| `WorkflowResult` | 完整工作流结果（聚合上述所有结果 + 风格/缓存/模型信息） |
| `PublishRecord` | 发布记录（平台、标题、状态、外部链接） |
| `CacheContext` | 缓存上下文（命中状态、相似度、需避开的标题/角度） |
| `TopicCandidate` | 话题候选（标题、URL、来源、匹配行业、评分、排名） |
| `DailyTopicsReport` | 日报话题报告（话题列表、来源统计、过滤统计） |
| `VisualMetric` | 可视化指标（标签、值、单位） |
| `VisualBrief` | 视觉简报（封面/信息图提示、关键指标、趋势标签） |
| `ImageArtifacts` | 图片产物（封面 PNG、信息图 PNG/SVG/HTML） |

所有数据类均提供 `from_dict()` 类方法用于从 JSON 反序列化，`WorkflowResult` 和 `DailyTopicsReport` 还提供 `to_dict()` 方法。

### 5.3 异常体系 (exceptions)

**文件**: [exceptions.py](file:///d:/Solo/今日头条/src/toutiao_content_factory/exceptions.py)

```
ContentFactoryError (基类)
├── InputNotFoundError       # 未找到输入源
├── UnsupportedFileTypeError # 不支持的文件类型
├── AIRequestError           # 模型请求失败
└── AIResponseFormatError    # 模型响应解析失败
```

### 5.4 Agent 智能体层

**目录**: [agents/](file:///d:/Solo/今日头条/src/toutiao_content_factory/agents/)

所有 Agent 继承自 `BaseAgent`，遵循统一接口模式。

#### BaseAgent（基类）

**文件**: [base.py](file:///d:/Solo/今日头条/src/toutiao_content_factory/agents/base.py)

| 方法 | 职责 |
|------|------|
| `render_prompt()` | 渲染 Prompt 模板 |
| `request_json()` | 调用 OpenAI 获取 JSON 响应 |
| `style_digest()` | 生成风格摘要文本 |
| `brand_digest()` | 生成品牌写法摘要 |
| `infer_benchmark_track()` | 推断标杆赛道 |
| `benchmark_strategy_digest()` | 生成标杆策略摘要 |
| `cache_note()` | 生成缓存提示文本 |
| `clean_publish_text()` | 清洗发布文本（去除品牌名、内部标签） |
| `strip_prompt_leakage()` | 剥离 Prompt 泄露内容 |
| `normalize_title()` | 标题标准化 |

#### Agent 清单

| Agent | 文件 | 职责 |
|-------|------|------|
| `TrendAgent` | `trend_agent.py` | 分析热点趋势，输出趋势报告 |
| `DataAgent` | `data_agent.py` | 提取数据信号，输出数据报告 |
| `WriterAgent` | `writer_agent.py` | 根据趋势+数据生成文章 |
| `PolishAgent` | `polish_agent.py` | 润色文章，提升质量与可读性 |
| `TitleAgent` | `title_agent.py` | 生成候选标题与推荐标题 |
| `ReviewAgent` | `review_agent.py` | 内容质量审核与评分 |
| `PublishReviewAgent` | `publish_review_agent.py` | 发布前预检与就绪度评估 |
| `BenchmarkMonitorAgent` | `benchmark_monitor_agent.py` | 标杆账号监控 |
| `SampleCollectorAgent` | `sample_collector_agent.py` | 优质样本采集 |
| `WechatLayoutAgent` | `wechat_layout_agent.py` | 微信公众号排版适配 |

### 5.5 服务层 (services)

**目录**: [services/](file:///d:/Solo/今日头条/src/toutiao_content_factory/services/)

#### 核心管线服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `ContentFactoryPipeline` | `pipeline.py` | 核心管线：加载→提取→缓存→工作流→导出 |
| `AgentWorkflowService` | `agent_workflow.py` | Agent 编排：串联 7 个 Agent 的执行顺序 |
| `ContentFactoryAppService` | `app_service.py` | 应用层入口：封装所有业务操作 |
| `OpenAIContentService` | `openai_service.py` | OpenAI API 封装：文本生成、JSON 补全 |
| `ContentGenerator` | `content_generator.py` | 内容生成核心逻辑 |

#### 数据与缓存服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `SignalExtractor` | `extractor.py` | 从文档中提取关键信号 |
| `CacheService` | `cache_service.py` | 缓存管理（精确/相似命中检测） |
| `ExportService` | `exporter.py` | 将 WorkflowResult 导出为文件 |
| `PromptRepository` | `prompt_repository.py` | Prompt 模板加载与渲染 |
| `DocumentLoader` | `document_loader.py` (loaders/) | 加载输入文档（文本/PDF/粘贴） |

#### 话题与增长服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `TopicCollectionService` | `topic_collection.py` | 多源话题采集（RSS/HTML/Google News） |
| `TopicPoolService` | `topic_pool_service.py` | 话题池管理 |
| `TopicExporter` | `topic_exporter.py` | 话题导出 |
| `GrowthOpsService` | `growth_ops_service.py` | 增长运营（标杆分析、标题策略） |
| `BenchmarkHotspotService` | `benchmark_hotspot_service.py` | 标杆热点服务 |

#### 发布平台服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `PublishService` | `publish_service.py` | 今日头条 Playwright 自动发布 |
| `WechatService` | `wechat_service.py` | 微信公众号 API + 浏览器发布 |
| `WechatLaunchService` | `wechat_launch_service.py` | 微信首发周文章包 |
| `DouyinService` | `douyin_service.py` | 抖音创作者中心发布（实验性） |
| `Md2wechatService` | `md2wechat_service.py` | Markdown 转微信排版 |

#### 审核与质量服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `ReviewService` | `review_service.py` | 审核流程管理 |
| `ArticleQualityChecker` | `article_quality_checker.py` | 文章质量检查 |
| `ArticleSanitizer` | `article_sanitizer.py` | 文章内容清洗 |
| `ContentGuardService` | `content_guard_service.py` | 内容安全守卫 |
| `SchemaGuardService` | `schema_guard_service.py` | Schema 合规守卫 |

#### 运维与辅助服务

| 服务 | 文件 | 职责 |
|------|------|------|
| `AutomationService` | `automation.py` | 自动化调度与日更流程 |
| `PipelineStateManager` | `pipeline_state_manager.py` | 管线状态持久化 |
| `NotificationService` | `notification_service.py` | 通知广播 |
| `DebugSnapshotService` | `debug_snapshot_service.py` | 调试快照 |
| `FeedbackService` | `feedback_service.py` | 反馈收集 |
| `PlatformHealthService` | `platform_health_service.py` | 平台健康检查 |
| `ImageGenerationService` | `image_generation_service.py` | AI 图片生成 |
| `SearchService` | `search_service.py` | 搜索服务 |

### 5.6 加载器层 (loaders)

**文件**: [document_loader.py](file:///d:/Solo/今日头条/src/toutiao_content_factory/loaders/document_loader.py)

| 类 | 职责 |
|----|------|
| `DocumentLoader` | 从目录和粘贴文本加载输入文档，支持 .txt / .md / .pdf 格式 |

### 5.7 Web 控制台层 (web)

**文件**: [app.py](file:///d:/Solo/今日头条/src/toutiao_content_factory/web/app.py) | [view_service.py](file:///d:/Solo/今日头条/src/toutiao_content_factory/web/view_service.py)

| 组件 | 职责 |
|------|------|
| `create_app()` | 创建 FastAPI 应用，注册所有路由 |
| `AccountContext` | 账号上下文（settings + app_service + view_service） |
| `WebViewService` | 视图数据加载（Dashboard、Runs、Topics、Published） |

**主要路由**:

| 路由 | 方法 | 功能 |
|------|------|------|
| `/` | GET | 账号管理总览 |
| `/dashboard` | GET | 单账号控制台 |
| `/topics` | GET | 话题列表 |
| `/runs` | GET | 运行列表 |
| `/runs/{run_name}` | GET | 运行详情 |
| `/published` | GET | 已发布文章 |
| `/actions/collect-topics` | POST | 触发话题采集 |
| `/actions/manual-run` | POST | 手动生成 |
| `/actions/auto-run` | POST | 自动选题生成 |
| `/actions/daily-run` | POST | 完整日更流程 |
| `/runs/{run_name}/approve` | POST | 审核通过 |
| `/runs/{run_name}/reject` | POST | 审核驳回 |
| `/runs/{run_name}/publish` | POST | 触发发布 |
| `/api/logs/stream` | GET | SSE 日志流 |
| `/healthz` | GET | 健康检查 |

### 5.8 工具层 (utils)

| 文件 | 职责 |
|------|------|
| `file_utils.py` | 文件操作工具 |
| `logging_utils.py` | 日志配置工具 |
| `runtime_status.py` | 运行时状态查询 |

---

## 6. 核心工作流程

### 6.1 内容生成管线

```
输入文档/粘贴文本
       ↓
  DocumentLoader.load()
       ↓
  SignalExtractor.extract()  →  ExtractionResult
       ↓
  CacheService.inspect()     →  CacheContext
       ↓ (cache miss)
  AgentWorkflowService.run()
       ↓
  ┌─────────────────────────────────────────┐
  │  TrendAgent.run()      → TrendReport    │
  │       ↓                                  │
  │  DataAgent.run()       → DataReport      │
  │       ↓                                  │
  │  WriterAgent.run()     → GeneratedContent│
  │       ↓                                  │
  │  PolishAgent.run()     → 润色结果         │
  │       ↓                                  │
  │  TitleAgent.run()      → 标题列表         │
  │       ↓                                  │
  │  ReviewAgent.run()     → ContentScore    │
  │       ↓                                  │
  │  [条件] 二次润色（质量不达标时）           │
  │       ↓                                  │
  │  PublishReviewAgent.run() → 发布预检      │
  └─────────────────────────────────────────┘
       ↓
  CacheService.save()
       ↓
  ExportService.export()     →  文件输出
```

### 6.2 二次润色触发条件

当以下任一条件满足时，系统会自动执行二次润色：

- `content_score.pass_status == "needs_revision"`
- `content_score.ai_generated_risk >= 32`
- `content_score.completion_rate_potential <= 84`
- `content.validation_score <= 84`
- 文章缺少来源锚点（"据"/"数据显示"/"财报"等）
- 存在超长段落（≥180字）

### 6.3 日更自动流程

```
daily_run 启动
    ↓
采集新鲜话题 (collect_topics)
    ↓
尝试最多 N 个候选话题 (TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS)
    ↓
自动审核通过高质量运行
    ↓
[条件] 自动发布今日头条 (TOUTIAO_AUTO_PUBLISH=1)
    ↓
[条件] 创建微信草稿并提交 (WECHAT_AUTO_PUBLISH=1)
```

---

## 7. 多账号体系

系统支持多账号并行运营，每个账号拥有独立的配置和运行时环境。

### 账号隔离规则

| 资源 | 默认账号 | 子账号 |
|------|---------|--------|
| 输入目录 | `input/` | `input/accounts/<slug>/` |
| 输出目录 | `output/` | `output/accounts/<slug>/` |
| 日志目录 | `logs/` | `logs/accounts/<slug>/` |
| 缓存目录 | `cache/` | `cache/accounts/<slug>/` |
| 浏览器 Profile | `.runtime/toutiao_profile/` | `.runtime/accounts/<key>/toutiao_profile/` |

### 当前配置账号

| 账号 Key | 名称 | 定位 | 风格 | 状态 |
|----------|------|------|------|------|
| `default` | 手蓝科技笔记 | AI/机器人/新能源/储能/智能制造 | deep_industry_analysis | 启用 |
| `robot_notes` | 手蓝政治笔记 | 国际政治/地缘博弈/关税/外交 | tech_media | 启用 |
| `energy_notes` | 手蓝体育笔记 | 体育赛事/足球/篮球 | viral_science | 禁用 |
| `hardware_notes` | 手蓝硬件笔记 | AI硬件/芯片/边缘算力 | deep_industry_analysis | 禁用 |
| `automation_notes` | 手蓝智造笔记 | 工业自动化/机器视觉/伺服 | tech_media | 禁用 |

---

## 8. 发布平台集成

### 8.1 今日头条

- **方式**: Playwright 浏览器自动化（非官方 API）
- **浏览器**: Microsoft Edge（持久化 Profile）
- **流程**: 登录初始化 → 内容填充 → 图片上传 → 发布
- **图片策略**: `cover.png` 作为封面 + `infographic_1.png` 作为文中插图
- **标题限制**: 最长 30 字
- **注意**: 依赖页面选择器，头条页面改版可能需要维护

### 8.2 微信公众号

- **草稿创建**: 官方 API（需 APP_ID / APP_SECRET）
- **发布提交**: 浏览器自动化（从草稿箱提交）
- **排版**: 支持 md2wechat CLI / 托管 API / 内置渲染器三级回退
- **标题限制**: 最长 64 字
- **摘要限制**: 最长 120 字

### 8.3 抖音（实验性）

- **方式**: 浏览器自动化
- **状态**: 基础框架已就绪，选择器可能需要校准
- **配置**: `DOUYIN_NICHE` / `DOUYIN_PUBLISH_MODE=browser`

---

## 9. Prompt 模板体系

**目录**: `prompts/`

| 模板文件 | 用途 |
|----------|------|
| `trend_prompt.md` / `trend_prompt_v2.md` | 趋势分析 Prompt |
| `data_prompt.md` / `data_prompt_v2.md` | 数据提取 Prompt |
| `article_prompt.md` / `article_prompt_v2.md` | 文章写作 Prompt |
| `title_prompt.md` / `title_prompt_v2.md` | 标题生成 Prompt |
| `polish_prompt.md` | 文章润色 Prompt |
| `review_prompt.md` | 内容审核 Prompt |
| `publish_review_prompt.md` | 发布前审核 Prompt |
| `generation_prompt.md` | 通用生成 Prompt |
| `image_prompt.md` | 图片生成 Prompt |
| `analysis_prompt.md` | 分析 Prompt |
| `wechat_layout_prompt.md` | 微信排版 Prompt |

模板通过 `PromptRepository.render(template_name, **kwargs)` 渲染，支持变量替换。

---

## 10. 项目运行方式

### 10.1 环境初始化

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

创建 `.venv`、安装依赖、安装 Playwright Chromium、从 `.env.example` 生成 `.env`。

### 10.2 CLI 模式

| 命令 | 功能 |
|------|------|
| `python main.py` | 生成一次手动运行 |
| `python main.py --account robot_notes` | 指定账号运行 |
| `python main.py --topics-only` | 仅采集话题 |
| `python main.py --daily-run --refresh-cache` | 完整日更流程 |
| `python main.py --publish-latest-approved` | 发布最新审核通过的文章 |
| `python main.py --wechat-draft-latest-approved` | 微信草稿 |
| `python main.py --wechat-publish-latest-approved` | 微信发布 |
| `python main.py --init-wechat-login` | 微信登录初始化 |
| `python main.py --build-benchmark-report` | 构建标杆报告 |
| `python main.py --record-performance ...` | 记录绩效数据 |

### 10.3 Web 控制台

```powershell
python .\webapp.py --port 8001
```

访问 `http://127.0.0.1:8001`，支持多账号切换、话题采集、手动/自动生成、审核、发布等全流程操作。

### 10.4 定时任务

```powershell
# 注册 4 个时段的日更任务 (08:00, 12:00, 18:30, 22:00)
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish

# 注册所有账号
powershell -ExecutionPolicy Bypass -File .\scripts\register_all_accounts.ps1 -EnableAutoPublish

# 微信独立任务 (07:00)
powershell -ExecutionPolicy Bypass -File .\scripts\register_wechat_daily_task.ps1 -AccountKey default -EnableAutoPublish
```

### 10.5 独立微头条生成器

```powershell
python run.py s   # 手蓝·金融硬核思维
python run.py x   # 散户派·实战向
python run.py w   # 正能量情感创作者
```

使用 DeepSeek API，独立于主管线运行。

---

## 11. 依赖关系图

### 模块间核心依赖

```
main.py / webapp.py
    ├── config.load_settings()
    ├── pipeline.ContentFactoryPipeline
    │   ├── loaders.DocumentLoader
    │   ├── services.extractor.SignalExtractor
    │   ├── services.prompt_repository.PromptRepository
    │   ├── services.openai_service.OpenAIContentService
    │   ├── services.agent_workflow.AgentWorkflowService
    │   │   ├── agents.TrendAgent
    │   │   ├── agents.DataAgent
    │   │   ├── agents.WriterAgent
    │   │   ├── agents.PolishAgent
    │   │   ├── agents.TitleAgent
    │   │   ├── agents.ReviewAgent
    │   │   └── agents.PublishReviewAgent
    │   ├── services.cache_service.CacheService
    │   ├── services.exporter.ExportService
    │   ├── services.pipeline_state_manager.PipelineStateManager
    │   └── services.notification_service.NotificationService
    ├── services.app_service.ContentFactoryAppService
    │   ├── services.topic_collection.TopicCollectionService
    │   ├── services.publish_service.PublishService
    │   ├── services.wechat_service.WechatService
    │   ├── services.image_generation_service.ImageGenerationService
    │   ├── services.growth_ops_service.GrowthOpsService
    │   └── services.automation.AutomationService
    └── web.app.create_app()
        └── web.view_service.WebViewService
```

### 外部依赖关系

```
项目
 ├── OpenAI-compatible API (DeepSeek / 其他)
 │    ├── 文本生成 (文章/标题/审核)
 │    └── 图片生成 (封面/信息图)
 ├── 今日头条创作者中心
 │    └── Playwright 浏览器自动化
 ├── 微信公众平台
 │    ├── 官方 API (草稿创建)
 │    └── Playwright 浏览器自动化 (发布提交)
 ├── 抖音创作者中心
 │    └── Playwright 浏览器自动化 (实验性)
 ├── 新闻源
 │    ├── Google News RSS
 │    ├── 36kr RSS
 │    ├── 智东西 HTML
 │    ├── OFweek HTML
 │    ├── 高工机器人 HTML
 │    └── 储能/新能源 in-en.com HTML
 └── md2wechat (可选)
      ├── 本地 CLI
      └── 托管 API
```

---

> 本文档基于项目源码自动分析生成，涵盖项目整体架构、模块职责、关键类与函数、依赖关系及运行方式。如需更新，请重新运行分析。
