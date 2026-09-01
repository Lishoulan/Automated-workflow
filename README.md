<div align="center">

# 🏭 AI Content Factory

**AI 驱动的全平台自媒体内容工厂 — 一键从热点到发布**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Playwright](https://img.shields.io/badge/Playwright-1.52%2B-2EAD33.svg)](https://playwright.dev)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](#english) · [中文](#中文)

</div>

---

<a id="中文"></a>

## ✨ 这是什么？

**AI Content Factory** 是一套面向中文科技自媒体的全自动内容生产流水线。从热点采集、数据分析、文章撰写、标题优化、合规审核到多平台发布，全部由 AI Agent 协作完成，无需人工干预。

> 🎯 **一句话概括**：给一个 API Key，还你一个 7×24 小时不停更的自媒体矩阵。

### 🖥️ 内置 Web 控制台

实时查看选题池、内容生成进度、审核结果、发布状态，一切尽在掌握。

### 🤖 多 Agent 协作架构

| Agent | 职责 |
|-------|------|
| 🔥 Trend Agent | 热点采集与趋势分析 |
| 📊 Data Agent | 数据提取与行业信号解读 |
| ✍️ Writer Agent | 深度长文生成（1200+ 字） |
| 🏷️ Title Agent | 20 选 3 标题优化 |
| 💎 Polish Agent | 去 AI 味润色 |
| 🔍 Review Agent | 质量评分与 AI 风险检测 |
| 📋 Publish Review Agent | 发布前终审 |
| 📐 WeChat Layout Agent | 公众号排版适配 |

---

## 🚀 核心特性

- **全流程自动化** — 热点 → 选题 → 写作 → 审核 → 发布，零人工介入
- **多平台支持** — 今日头条、微信公众号、抖音，一个流水线全覆盖
- **多账号矩阵** — 隔离运行目录、浏览器 Profile、发布历史，互不干扰
- **合规先行** — AIGC 声明、AI 风险评分、内容安全守卫，符合 2026 平台规则
- **智能选题** — 对标账号分析 + 自有爆款学习，选题越来越准
- **定时调度** — Windows 任务计划程序，每天 4 个时段自动运行
- **Web 控制台** — FastAPI 驱动的本地 Dashboard，实时监控一切
- **OpenAI 兼容** — 支持 DeepSeek、通义千问等任何 OpenAI 兼容 API

---

## 📦 快速开始

### 环境要求

- Windows 10/11
- Python 3.10+
- Microsoft Edge

### 安装

```powershell
git clone https://github.com/Lishoulan/Automated-workflow.git
cd Automated-workflow
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

### 配置

编辑 `.env`，至少填写：

```ini
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

### 启动

```powershell
# 启动 Web 控制台
powershell -ExecutionPolicy Bypass -File .\scripts\start_webapp.ps1

# 或命令行生成一篇文章
python .\main.py
```

打开 http://127.0.0.1:8000 即可看到控制台。

---

## 🎮 常用命令

```powershell
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

# 注册每日定时任务（内容时段按脚本默认配置；22:00 统一发送通知日报）
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish
```

---

## 🏗️ 项目结构

```
├── src/toutiao_content_factory/
│   ├── agents/           # AI Agent 集合
│   ├── services/         # 核心业务服务
│   ├── loaders/          # 文档加载器
│   ├── renderers/        # 渲染器
│   ├── utils/            # 工具函数
│   └── web/              # Web 控制台
├── prompts/              # Prompt 模板
├── scripts/              # 启动/调度/登录脚本
├── templates/web/        # HTML 模板
├── static/               # CSS/图标
├── branding/             # 品牌与对标配置
├── config/               # 运行时配置
├── main.py               # CLI 入口
├── webapp.py             # Web 入口
└── config.yaml           # 应用配置
```

---

## 🔧 技术栈

| 层级 | 技术 |
|------|------|
| AI 引擎 | OpenAI-compatible API (DeepSeek / GPT / Qwen) |
| Web 框架 | FastAPI + Uvicorn |
| 浏览器自动化 | Playwright (Edge) |
| 图像生成 | OpenAI Image API / Pillow / Matplotlib |
| 模板引擎 | Jinja2 |
| 任务调度 | Windows Task Scheduler + APScheduler |
| 数据格式 | YAML / JSON / Markdown |

---

## 🛡️ 安全与合规

- `.env` 和 API Key 永远不会被提交到 Git
- 浏览器 Profile 目录独立隔离，账号互不干扰
- AIGC 声明自动添加，符合平台反自动化创作规范
- 内容安全守卫（Content Guard）自动检测敏感词
- AI 风险评分超过阈值自动拦截，不发布低质量内容
- 默认"草稿 + 人工审核"模式，自动发布需手动开启

---

## 📊 增长运营

```powershell
# 生成对标账号分析报告
python .\main.py --build-benchmark-report

# 记录文章表现数据
python .\main.py --record-performance --run-dir "..." --impression-count 10000 --read-count 860

# 生成表现分析报告
python .\main.py --build-performance-report
```

系统会从对标账号和自有爆款中持续学习，让选题和写作策略越来越精准。

---

## 🌐 多平台发布

| 平台 | 状态 | 方式 |
|------|------|------|
| 今日头条 | ✅ 完整支持 | Playwright 浏览器自动化 |
| 微信公众号 | ✅ 完整支持 | 官方 API + 浏览器提交 |
| 抖音 | 🧪 实验性 | 浏览器自动化（需校准选择器） |

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 提交 Pull Request

---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

<a id="english"></a>

## ✨ What is this?

**AI Content Factory** is a fully automated content production pipeline for Chinese tech media. From trending topic discovery, data analysis, article writing, title optimization, compliance review to multi-platform publishing — all handled by collaborative AI Agents with zero human intervention.

> 🎯 **TL;DR**: Give it an API Key, get a 24/7 self-updating media matrix.

### Key Features

- **Full Pipeline Automation** — Trending → Topic → Writing → Review → Publish
- **Multi-Platform** — Toutiao, WeChat Official Account, Douyin
- **Multi-Account Matrix** — Isolated profiles, histories, and directories
- **Compliance First** — AIGC declarations, AI risk scoring, content guard
- **Smart Topic Selection** — Benchmark analysis + self-learning from top performers
- **Scheduled Runs** — 4 daily time slots via Windows Task Scheduler
- **Web Dashboard** — FastAPI-powered local console for real-time monitoring
- **OpenAI Compatible** — Works with DeepSeek, Qwen, or any OpenAI-compatible API

### Quick Start

```powershell
git clone https://github.com/Lishoulan/Automated-workflow.git
cd Automated-workflow
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
# Edit .env with your API key, then:
python .\main.py --daily-run --refresh-cache
```

### Tech Stack

Python · FastAPI · Playwright · OpenAI API · Pillow · Jinja2 · APScheduler

---

<div align="center">

**如果这个项目对你有帮助，请给个 ⭐ Star！**

</div>
