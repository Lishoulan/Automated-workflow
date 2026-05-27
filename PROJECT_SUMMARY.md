# 📋 AI头条内容工厂 - 项目整理总结

## 🎯 项目目标
> 每日自动生产高阅读量、零违规的文章，赚取阅读收益

---

## 🏗️ 系统架构

```
d:\Solo\今日头条/
│
├── src/toutiao_content_factory/          # 核心应用代码
│   ├── agents/                          # AI Agent 模块
│   │   ├── writer_agent.py              # 文章生成 (已优化)
│   │   ├── polish_agent.py              # 文章润色 (已优化)
│   │   ├── title_agent.py               # 标题生成
│   │   ├── trend_agent.py               # 热点分析
│   │   ├── data_agent.py                # 数据分析
│   │   └── review_agent.py              # 审核
│   │
│   ├── services/                        # 服务模块
│   │   ├── pipeline.py                  # 主流水线
│   │   ├── publish_service.py           # 发布服务
│   │   ├── content_guard_service.py     # 内容安全检测
│   │   ├── notification_service.py      # 通知服务 (新增)
│   │   ├── pipeline_state_manager.py    # 状态管理 (新增)
│   │   ├── debug_snapshot_service.py    # 调试快照 (新增)
│   │   └── article_quality_checker.py   # 文章质量检查 (新增)
│   │
│   └── web/                             # Web 后台
│       └── app.py                       # FastAPI 应用
│
├── scripts/                             # 脚本工具
│   ├── daily_run_enhanced.ps1          # 增强版每日运行 (新增)
│   ├── register_daily_task.ps1         # 任务注册 (已更新)
│   ├── quick_config.ps1                # 快速配置 (新增)
│   ├── run_now.ps1/bat                 # 快捷启动脚本 (新增)
│   └── init_publish_login.ps1          # 登录初始化
│
├── prompts/                             # Prompt 模板
│   ├── article_prompt_v2.md            # 文章生成模板
│   ├── title_prompt_v2.md             # 标题生成模板
│   └── polish_prompt.md                # 润色模板
│
└── output/                              # 输出目录
    ├── runs/                           # 运行记录
    ├── logs/                           # 日志文件
    └── debug_snapshots/                # 调试快照
```

---

## ✅ 已完成的优化

### 1. 文章质量优化（首发激励合规）

| 优化项 | 修改文件 | 效果 |
|-------|---------|------|
| 减少模板化表达 | [`writer_agent.py`](file:///d:/Solo/今日头条/src/toutiao_content_factory/agents/writer_agent.py) | 8种不同开头方式，避免被识别为低质内容 |
| 多样化互动引导 | [`writer_agent.py`](file:///d:/Solo/今日头条/src/toutiao_content_factory/agents/writer_agent.py) | 6类共36种互动结尾方式 |
| 模板化检测 | [`polish_agent.py`](file:///d:/Solo/今日头条/src/toutiao_content_factory/agents/polish_agent.py) | 检测8种模板化句式，加入评分 |
| 文章质量预检 | [`article_quality_checker.py`](file:///d:/Solo/今日头条/src/toutiao_content_factory/services/article_quality_checker.py) | 首发激励合规性检查 |

### 2. 系统稳定性优化

| 优化项 | 修改文件 | 效果 |
|-------|---------|------|
| 增强版运行脚本 | [`daily_run_enhanced.ps1`](file:///d:/Solo/今日头条/scripts/daily_run_enhanced.ps1) | 网络检测、自动重试、状态管理 |
| 断点续传 | [`pipeline_state_manager.py`](file:///d:/Solo/今日头条/src/toutiao_content_factory/services/pipeline_state_manager.py) | 支持基于已有草稿重新发布 |
| 调试快照 | [`debug_snapshot_service.py`](file:///d:/Solo/今日头条/src/toutiao_content_factory/services/debug_snapshot_service.py) | 失败时自动保存截图和HTML |
| 通知服务 | [`notification_service.py`](file:///d:/Solo/今日头条/src/toutiao_content_factory/services/notification_service.py) | 支持企业微信/飞书/钉钉推送 |

---

## ⚙️ 配置说明

### API配置（DeepSeek）

`.env` 文件中配置：
```env
OPENAI_API_KEY=你的DeepSeek密钥
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

### 发布配置
```env
TOUTIAO_PUBLISH_ENABLED=1      # 启用发布
TOUTIAO_AUTO_PUBLISH=1         # 自动发布已审核文章
```

### 通知配置（可选）
```env
WECOM_WEBHOOK_URL=企业微信WebHook
FEISHU_WEBHOOK_URL=飞书WebHook
DINGTALK_WEBHOOK_URL=钉钉WebHook
```

---

## 🚀 使用指南

### 快速启动

```powershell
# 1. 进入项目目录
cd d:\Solo\今日头条

# 2. 填写 API 密钥
notepad .env

# 3. 初始化登录
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1

# 4. 测试运行
python .\main.py
```

### 定时任务配置

1. 打开"任务计划程序"
2. 创建任务，设置触发器（每天 08:00）
3. 设置操作：
   - 程序：`powershell.exe`
   - 参数：`-ExecutionPolicy Bypass -File "D:\Solo\今日头条\scripts\daily_run_enhanced.ps1" -EnableAutoPublish`
4. 设置条件：勾选"唤醒计算机运行此任务"
5. 设置：失败时每15分钟重试，最多3次

---

## 📊 输出文件

每次运行生成：
```
output/runs/YYYY-MM-DD_HHMMSS/
├── workflow.json              # 完整工作流结果
├── article.md                # 文章正文
├── title.txt                 # 推荐标题
├── summary.txt               # 文章摘要
├── cover.png                 # 封面图
├── benchmark_strategy.json   # 基准策略
├── review_result.json        # 审核结果
└── publish_result.json       # 发布结果
```

---

## 🎯 头条首发激励计划优化

### 五大质量标准对照

| 标准 | 系统能力 | 状态 |
|-----|---------|------|
| 首发时效 | 支持快速响应模式 | ✅ |
| 原创深度 | 多样化表达减少模板化 | ✅ |
| 信息增量 | 数据分析和洞察 | ✅ |
| 创作规范 | 证据强度评估 | ✅ |
| 用户价值 | 自然互动引导 | ✅ |

### 违规规避措施

| 违规类型 | 规避方法 |
|---------|---------|
| 抄袭搬运 | 100%原创AI生成 |
| 标题党 | 标题质量检测 |
| 模板化 | 模板化表达检测和扣分 |
| 绝对化表述 | 风险词汇检测 |
| 低质内容 | 多维度质量评分 |

---

## 📚 文档索引

| 文档 | 用途 |
|-----|------|
| [QUICK_START.md](file:///d:/Solo/今日头条/QUICK_START.md) | 快速上手 |
| [PROJECT_GUIDE.md](file:///d:/Solo/今日头条/PROJECT_GUIDE.md) | 完整指南 |
| [FIRST_PUBLISH_OPTIMIZATION.md](file:///d:/Solo/今日头条/FIRST_PUBLISH_OPTIMIZATION.md) | 首发激励优化 |
| [COMPLETE_CONFIG_GUIDE.md](file:///d:/Solo/今日头条/COMPLETE_CONFIG_GUIDE.md) | 配置指南 |
| [SETUP_CHECKLIST.md](file:///d:/Solo/今日头条/SETUP_CHECKLIST.md) | 检查清单 |

---

## 🔧 故障排查

### 常见问题

| 问题 | 解决方案 |
|-----|---------|
| 文章没发布 | 检查 `TOUTIAO_AUTO_PUBLISH=1` |
| API错误 | 检查 `OPENAI_API_KEY` |
| 发布失败 | 查看 `output/debug_snapshots/` |
| 需要调试 | 使用后台"复制诊断上下文"功能 |

### 日志位置
- 运行日志：`output/logs/`
- 调试快照：`output/debug_snapshots/`
- 运行记录：`output/runs/`

---

## 💡 提高收益的技巧

1. **发布时机**：07:00-09:00、12:00-13:30、18:00-20:30
2. **标题优化**：使用数字、疑问句式
3. **首发激励**：勾选"声明首发"，获得额外补贴（最高5倍）
4. **互动引导**：结尾自然引导互动，提高完读率

---

## ✅ 配置检查清单

- [ ] 已填写 `OPENAI_API_KEY`
- [ ] 已运行 `init_publish_login.ps1` 登录
- [ ] 已测试运行一次
- [ ] 已配置 Windows 任务计划
- [ ] 已配置通知（可选）

---

**🎉 项目整理完成！**

如需进一步帮助，请查看上述文档或告诉我具体需求。
