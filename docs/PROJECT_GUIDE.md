# AI 头条内容工厂 - 完整项目指南

> 🎯 目标：每日自动生产高阅读量、零违规的文章，赚取阅读收益

## 📋 项目概述

本项目是一个基于 AI 的自动化内容生产系统，专门为头条号创作者设计。系统可以：

- ✅ 自动采集热点话题
- ✅ 分析行业数据信号
- ✅ 生成高质量原创文章
- ✅ 生成吸引人的标题
- ✅ 自动生成配图
- ✅ 智能审核把关
- ✅ 自动发布到头条

---

## 🏗️ 系统架构

```
d:\Solo\今日头条/
│
├── src/toutiao_content_factory/          # 核心应用代码
│   ├── agents/                          # AI Agent 模块
│   │   ├── writer_agent.py              # 文章生成Agent (已优化：减少模板化)
│   │   ├── polish_agent.py              # 文章润色Agent (已优化：添加模板化检测)
│   │   ├── title_agent.py               # 标题生成Agent
│   │   ├── trend_agent.py               # 热点分析Agent
│   │   ├── data_agent.py                # 数据分析Agent
│   │   └── review_agent.py              # 审核Agent
│   │
│   ├── services/                        # 服务模块
│   │   ├── pipeline.py                  # 主流水线
│   │   ├── publish_service.py           # 发布服务
│   │   ├── content_guard_service.py     # 内容安全检测
│   │   ├── notification_service.py      # 通知服务 (新增)
│   │   ├── pipeline_state_manager.py    # 状态管理 (新增)
│   │   ├── debug_snapshot_service.py     # 调试快照 (新增)
│   │   └── topic_pool_service.py         # 话题池管理
│   │
│   └── web/                             # Web 后台
│       └── app.py                       # FastAPI 应用
│
├── scripts/                             # 脚本工具
│   ├── daily_run_enhanced.ps1          # 增强版每日运行 (新增)
│   ├── register_daily_task.ps1         # 任务注册 (已更新)
│   └── quick_config.ps1                # 快速配置 (新增)
│
├── prompts/                             # Prompt 模板
│   ├── article_prompt_v2.md            # 文章生成模板
│   ├── title_prompt_v2.md             # 标题生成模板
│   └── polish_prompt.md                # 润色模板
│
└── branding/                           # 品牌配置
    └── self_benchmark_articles.yaml     # 自有高绩效文章
```

---

## 🎯 核心优化：符合头条首发激励计划

### ✅ 已完成的优化

#### 1. **减少模板化表达** (writer_agent.py)
- 新增 `_varied_opening_first_line()` 方法，提供 8 种不同开头方式
- 新增 `_varied_angle_line()` 方法，提供多样化的角度结尾
- 优化多个内容格式的开头表达

#### 2. **优化结尾互动** (writer_agent.py)
- 新增 6 个多样化的互动引导方法：
  - `_varied_sports_comment_prompt()` - 5 种方式
  - `_varied_international_comment_prompt()` - 5 种方式
  - `_varied_data_comment_prompt()` - 5 种方式
  - `_varied_project_comment_prompt()` - 5 种方式
  - `_varied_quick_comment_prompt()` - 5 种方式
  - `_varied_default_comment_prompt()` - 8 种方式

#### 3. **添加模板化检测** (polish_agent.py)
- 新增 `_count_template_patterns()` 方法
- 检测8种常见模板化句式
- 验证评分中加入模板化评估项

### 📊 五大质量标准对照

| 标准 | 系统能力 | 状态 |
|-----|---------|------|
| 首发时效 | 支持快速响应模式 | ✅ 已实现 |
| 原创深度 | 多样化表达减少模板化 | ✅ 已优化 |
| 信息增量 | 数据分析和洞察 | ✅ 已实现 |
| 创作规范 | 证据强度评估 | ✅ 已实现 |
| 用户价值 | 自然互动引导 | ✅ 已优化 |

---

## 🚀 快速启动

### 步骤 1：环境配置

```powershell
# 进入项目目录
cd d:\Solo\今日头条

# 初始化环境
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

### 步骤 2：配置 .env

编辑 `.env` 文件，配置必要的 API 密钥：

```env
# DeepSeek 配置（推荐）
OPENAI_API_KEY=你的DeepSeek密钥
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat

# 发布配置
TOUTIAO_PUBLISH_ENABLED=1
TOUTIAO_AUTO_PUBLISH=1
```

### 步骤 3：初始化登录

```powershell
# 初始化头条登录
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1
```

### 步骤 4：注册定时任务

```powershell
# 使用增强版脚本（推荐）
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish -UseEnhancedScript
```

---

## 📝 日常使用命令

### 生成一篇手动测试文章

```powershell
python .\main.py
```

### 查看所有可用命令

```powershell
python .\main.py --help
```

### 采集话题（不生成文章）

```powershell
python .\main.py --topics-only
```

### 发布已审核通过的文章

```powershell
python .\main.py --publish-latest-approved
```

---

## ⚙️ 配置说明

### 核心配置项 (.env)

#### 发布设置
```env
TOUTIAO_PUBLISH_ENABLED=1          # 启用发布
TOUTIAO_AUTO_PUBLISH=1            # 自动发布已审核文章
TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS=3  # 每天最多尝试3个话题
```

#### 内容质量
```env
TOUTIAO_FAST_MODE=1                # 快速模式
TOUTIAO_SKIP_IMAGE_GENERATION=1    # 跳过图片生成
TOUTIAO_SKIP_REVIEW_AGENT=1        # 跳过AI审核
TOUTIAO_SKIP_PUBLISH_REVIEW=1      # 跳过发布前审核
```

#### 发布激励
```env
TOUTIAO_AUTO_PUBLISH_MIN_OVERALL_SCORE=80    # 自动发布最低总分
TOUTIAO_AUTO_PUBLISH_MAX_AI_RISK=40          # 最高AI风险限制
```

### 通知配置（可选）

```env
# 企业微信通知
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的密钥

# 飞书通知
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/你的HOOK

# 钉钉通知
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=你的TOKEN
```

---

## 🔍 质量保障体系

### 1. 多层审核机制

```
话题采集 → 数据分析 → 文章生成 → 润色优化 → 审核评分 → 发布
```

### 2. 评分维度

- **总分** (满分100)
  - 证据锚点 (10分)
  - 主体清晰度 (12分)
  - 产业链覆盖 (10分)
  - 切入角度 (8分)
  - 来源证据 (10分)
  - 数字边界 (8分)
  - 段落长度 (10分)
  - 格式规范 (8分)
  - 段落结构 (6分)
  - **模板化程度 (10分)** ← 新增

### 3. 自动发布阈值

- 总分 ≥ 80
- AI风险 ≤ 40
- 验证分 ≥ 80

---

## 📁 输出文件说明

每次运行会在 `output/runs/` 下生成：

```
output/runs/YYYY-MM-DD_HHMMSS/
├── workflow.json              # 完整工作流结果
├── article.md                # 文章正文
├── title.txt                 # 推荐标题
├── summary.txt               # 文章摘要
├── cover.png                 # 封面图
├── infographic_1.png        # 信息图
├── benchmark_strategy.json    # 基准策略
├── review_result.json        # 审核结果
└── publish_result.json       # 发布结果
```

---

## 🛠️ 故障排查

### 发布失败

1. **检查网络**
   ```powershell
   # 在 daily_run_enhanced.ps1 中已包含网络检测
   ```

2. **查看调试截图**
   
   系统会自动保存失败时的浏览器截图：
   ```
   output/debug_snapshots/error_YYYYMMDD_HHMMSS.png
   output/debug_snapshots/error_YYYYMMDD_HHMMSS.html
   ```

3. **复制诊断上下文**
   
   在后台页面找到失败的运行，点击"复制诊断上下文"，粘贴给 AI 修复

### 文章质量差

1. **检查话题选择**
   - 查看 `output/runs/*/workflow.json` 中的话题质量评分
   - 优先选择评分高的话题

2. **调整参数**
   - 提高 `TOUTIAO_AUTO_PUBLISH_MIN_OVERALL_SCORE`
   - 降低 `TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS`

3. **优化 Prompt**
   
   编辑 `prompts/article_prompt_v2.md` 中的指导原则

---

## 📈 提升阅读量的技巧

### 1. 标题优化

- 使用数字（如"3大趋势"、"5个关键"）
- 使用疑问句引发好奇
- 避免过于绝对的表述
- 参考同类爆款标题结构

### 2. 内容优化

- **开头3秒法则**：前3段要抓住读者
- **信息密度**：每个段落要有实质内容
- **情感共鸣**：结尾要有互动引导
- **配图质量**：封面图要吸引眼球

### 3. 发布时机

- 早间：07:00-09:00（上班路上）
- 午间：12:00-13:30（休息时间）
- 晚间：18:00-20:30（下班后）
- 深夜：22:00-23:00（睡前浏览）

### 4. 首发激励

勾选"声明首发"可获得：
- 基础分成
- 额外首发补贴（最高5倍）
- 全网维权服务

---

## ⚠️ 避免违规

### 常见违规类型

| 违规类型 | 风险 | 规避方法 |
|---------|-----|---------|
| 抄袭搬运 | 高 | 100%原创，保证独特视角 |
| 虚假信息 | 高 | 数据要有来源，避免绝对化表述 |
| 标题党 | 中 | 标题要与内容相符，避免夸张 |
| 低质内容 | 中 | 减少模板化，增加原创观点 |
| 重复发布 | 中 | 每次选择新话题，保持内容新鲜 |

### 已实施的规避措施

1. **模板化检测** (polish_agent.py)
   - 检测并降低模板化内容的评分

2. **证据边界控制** (polish_agent.py)
   - 避免出现素材中没有的数据

3. **风险提示优化** (writer_agent.py)
   - 使用更谨慎的表述方式

4. **多样化表达** (writer_agent.py)
   - 避免固定句式，增加表达多样性

---

## 📚 相关文档

| 文档 | 说明 |
|-----|------|
| `README.md` | 官方项目文档 |
| `FIRST_PUBLISH_OPTIMIZATION.md` | 首发激励计划优化指南 |
| `OPTIMIZATION_GUIDE.md` | 自动化系统优化指南 |

---

## 🎓 最佳实践

### 每日流程

1. **早上** - 检查昨晚的运行结果
   - 查看发布状态
   - 检查阅读量数据

2. **白天** - 监控任务执行
   - 接收失败通知
   - 及时处理问题

3. **晚上** - 优化策略
   - 分析高绩效文章
   - 调整配置参数

### 持续优化

1. **积累优质话题**
   - 将表现好的话题记录到话题池
   - 建立自己的话题库

2. **学习爆款规律**
   - 分析高阅读量文章的共同点
   - 调整内容和标题策略

3. **定期维护**
   - 清理 `output/debug_snapshots/` 释放空间
   - 检查 `output/runs/` 保留重要结果

---

## 💡 常见问题

### Q1: 如何提高文章质量？

**A**: 从以下几个方面入手：
1. 提高话题选择质量（选择有数据支撑的话题）
2. 调整润色参数（提高验证分阈值）
3. 优化 Prompt（编辑 prompts/ 下的模板文件）

### Q2: 为什么文章没有通过审核？

**A**: 可能的原因：
1. 总分低于阈值（检查 review_result.json）
2. AI风险过高（检查AI风险分）
3. 模板化表达过多（系统会自动扣分）

### Q3: 如何关闭自动发布？

**A**: 设置：
```env
TOUTIAO_AUTO_PUBLISH=0
```

### Q4: 通知没有收到？

**A**: 检查：
1. `.env` 中是否配置了 WebHook URL
2. WebHook 是否有效
3. 网络连接是否正常

---

## 🚀 下一步

想要进一步优化？尝试：

1. **接入更多数据源** - 增加话题来源
2. **优化图片生成** - 提高配图质量
3. **建立自己的基准库** - 编辑 `branding/self_benchmark_articles.yaml`
4. **A/B测试标题** - 尝试不同的标题策略

---

## 📞 技术支持

遇到问题？
1. 查看 `output/logs/` 下的日志文件
2. 使用"复制诊断上下文"功能获取 AI 修复建议
3. 检查 `.env` 配置是否正确

---

**祝您创作愉快，收益节节高升！** 🎉
