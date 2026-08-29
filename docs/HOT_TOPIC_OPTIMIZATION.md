# 🚀 热点二创 → 头条高浏览量 优化指南

> 本文档总结本次针对「对热点进行二创、发布到今日头条、获取高浏览量」目标的全部改动，
> 以及如何把新能力用起来。

---

## 一、核心改动总览

| 能力 | 改动 | 状态 |
|------|------|------|
| 🔥 头条热榜选题 | 新增 `toutiao_hot` 采集源（头条官方热榜 JSON API，含真实热度值） | ✅ 已上线 |
| 🔥 百度热搜选题 | 新增 `baidu_hot` 采集源（百度实时热搜） | ✅ 已上线 |
| 📊 热度优先排序 | 热榜话题热度值直接加权进选题总分，真实热点排最前 | ✅ 已上线 |
| ✍️ 热点快评体裁 | 热榜话题自动按「热点快评」写作：600-900字、无强制小标题、观点优先 | ✅ 已上线 |
| 🛡️ 敏感话题拦截 | 自杀/凶杀/性侵/毒品等高风险热榜话题自动拦截 | ✅ 已上线 |
| 📱 微头条发布 | 新增微头条（短内容）发布能力 + CLI 命令 | ✅ 已实现（需真机验证选择器） |
| 📈 浏览量反馈闭环 | 新增每周反馈脚本：采集真实阅读 → 演化学习 → 更新标题/文章 Prompt | ✅ 已上线 |
| ⏰ 采集频率 | 每日运行时段从 3 档增至 6 档（07:00/09:30/12:30/15:30/18:00/21:30） | ✅ 已调整 |
| 🔧 对标源修复 | 删除不可达的 Google News RSS（量子位对标源改用量子位/IT之家/solidot RSS） | ✅ 已修复 |

---

## 二、新增的选题源

### 头条热榜（`toutiao_hot`）

- 数据来源：`https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc`
- 返回头条站内实时热榜前 50 条，每条含 `Title`（标题）和 `HotValue`（热度值）
- **为什么重要**：这是头条平台自己的热榜——用户正在点击什么，一目了然。
  二创这类话题，天然踩在流量上。

### 百度热搜（`baidu_hot`）

- 数据来源：`https://top.baidu.com/board?tab=realtime`
- 返回百度实时热搜，含热搜指数。

### 选题排序逻辑（新增）

1. 热榜话题自动归属 `hot_topic`（实时热点）赛道；
2. 平台热度值按对数归一化（`log10(heat)*4`，上限 34 分）直接加进选题总分；
3. 真实热榜话题因此排在最前，优先被每日运行选中；
4. 各账号按赛道过滤热榜话题：
   - `default`（手蓝数据笔记）：接收全部热榜话题
   - `robot_notes`：只接收含机器人/AI/新能源关键词的热榜话题
   - `politics_notes`：只接收含国际政治关键词的热榜话题
   - `sports_notes`：只接收含体育关键词的热榜话题

### 敏感话题自动拦截

标题或摘要命中以下词时自动丢弃（避免账号被平台限流/封禁）：
自杀、自缢、跳楼、轻生、凶杀、奸杀、分尸、命案、枪击、爆炸事故、矿难、
坠机、性侵、猥亵、毒品、贩毒、涉黑、恐怖袭击、缅北、尸检、死刑、冤案、
医疗事故、医闹 等。

---

## 三、热点快评（Hot Topic Quick Commentary）写作模式

热榜话题被选中后，自动进入「热点快评」模式：

- **字数**：600-900 字（不再强制 1500 字长文）
- **结构**：不强制 3-4 个小标题（快评不需要研报结构）
- **开头**：前两句直接给判断/钩子，不复述标题
- **中段**：展开「为什么今天值得看、背后的原因/影响/争议、谁最关心」
- **结尾**：留一个具体的开放式互动问题，引导评论区
- **事实边界**：只能基于常识和公开背景做有限推断，明确区分事实与推测，
  禁止编造具体数字、人名、时间、公司或细节
- **体裁兜底**：即使 LLM 调用失败，本地兜底也按热点快评生成（不再注入产业链填充段）

> 说明：热点快评的质量门禁已放宽——热榜话题天然只有标题、没有完整原文/证据链，
> 这是正常形态，不应按「缺证据/缺公司」拦截。质量把关交给 Review Agent
> 和「敏感话题拦截」负责。

---

## 四、微头条发布

### 配置

```yaml
# config.yaml publishing 段
weitoutiao_enabled: true
weitoutiao_publish_url: "https://mp.toutiao.com/profile_v4/weitoutiao/publish"
weitoutiao_selectors:
  editor: [...]
  publish_button: [...]
  success_indicator: [...]
```

或环境变量：
```env
TOUTIAO_WEITOUTIAO_ENABLED=1
TOUTIAO_WEITOUTIAO_PUBLISH_URL=https://mp.toutiao.com/profile_v4/weitoutiao/publish
```

### 使用命令

```powershell
# 把指定 run 发布为微头条
python .\main.py --account robot_notes --publish-wtt-run --run-dir "output\runs\20260822-xxx"

# 把最新审核通过、尚未发布的 run 发布为微头条
python .\main.py --account robot_notes --publish-wtt-latest-approved

# 只填内容不点发布（存草稿，用于验证选择器）
python .\main.py --account robot_notes --publish-wtt-latest-approved --draft-only

# 独立微头条（高频化）：独立选题+写作，生成并发布 N 条
python .\main.py --account robot_notes --publish-wtt-standalone 2
```

### 微头条高频化（2026-08-27 新增）

头条涨粉研究表明微头条是涨粉最快入口，每日 2-3 条效果好于 1 条：

- 每日首时段：图文 1 篇 + 伴生微头条 1 条 + **独立微头条**补到目标（默认 3 条/日）。
- 独立微头条：从当日选题池**独立选题**（与图文/伴生去重），按人设轮换
  （`hot_comment` / `warm` / `sanhuo` / `shoulan`），独立写作短内容后复用同一
  浏览器登录会话发布，并写入 `publish_history.json`（platform=`toutiao_weitoutiao`）。
- 配置：`config.yaml` publishing 段 `weitoutiao_daily_target: 3`、
  `weitoutiao_standalone_enabled: true`、`weitoutiao_personas: [...]`；
  对应环境变量 `TOUTIAO_WEITOUTIAO_DAILY_TARGET` /
  `TOUTIAO_WEITOUTIAO_STANDALONE_ENABLED` / `TOUTIAO_WEITOUTIAO_PERSONAS`。
- 关闭：`TOUTIAO_WEITOUTIAO_STANDALONE_ENABLED=0` 恢复「仅伴生 1 条」。
- 实现：`src/toutiao_content_factory/services/weitoutiao_generator.py`（独立选题+写作），
  `publish_service.publish_weitoutiao(content_override=...)`（整条短内容直发，不再截取图文），
  `app_service._top_up_standalone_weitoutiao()`（按每日目标补齐，失败不阻塞主流程）。

### ⚠️ 使用前必须做一次真机验证

微头条发布页与图文发布页不同（无标题栏、仅正文区），选择器基于通用头条编辑器
模式编写。首次使用前请先以 `--draft-only` 跑一次，确认：

1. 浏览器能打开 `mp.toutiao.com/profile_v4/weitoutiao/publish`；
2. 正文能写入编辑器（内容区域出现文字）；
3. 截图/DOM 无异常。

如果编辑器选择器不匹配，只需更新 `config.yaml` 的 `publishing.weitoutiao_selectors`
（或 `config/selectors.yaml` 的 `toutiao.weitoutiao_page`），无需改代码。

---

## 五、浏览量反馈闭环（新增）

### 背景

之前系统有反馈循环代码，但真实浏览量数据没有被采集回来，导致「什么内容有浏览量」
无法学习。本次新增：

1. **每周反馈脚本** `scripts/run_feedback_loop.ps1`：
   - 采集创作者后台的真实阅读/点赞/评论/转发
   - 运行演化学习（EvolutionAgent 提炼规则）
   - 更新标题/文章 Prompt 的 Few-Shot 样本
   - 生成表现分析报告

2. **任务注册脚本** `scripts/register_feedback_task.ps1`：
   注册每周日 10:00 的 Windows 计划任务。

### 使用

```powershell
# 立即跑一次反馈闭环
powershell -ExecutionPolicy Bypass -File .\scripts\run_feedback_loop.ps1 -AccountKey robot_notes

# 注册每周自动反馈
powershell -ExecutionPolicy Bypass -File .\scripts\register_feedback_task.ps1 -AccountKey robot_notes
```

> 提示：浏览量数据通常发布 24-48 小时后才有参考价值，建议每周跑 1-2 次。
> 采集需要创作者后台登录态有效（与发布共用同一浏览器 Profile）。

---

## 六、运营建议（提升浏览量）

1. **保持每天 1 篇的发布频率**（`max_daily_posts: 1`）——这是之前账号权重被稀释后
   的止血决定。优先把 1 篇的质量和时效做到位，而不是加量。

2. **时效就是流量**：热榜话题 12 小时内价值最大。每日 6 档采集（07:00/09:30/
   12:30/15:30/18:00/21:30）保证选题新鲜；审核通过后尽快发布。

3. **头条首发激励**：发布时会自动勾选「声明首发」复选框，满足头条首发激励计划的
   基础条件（时效 + 原创深度 + 信息增量）。热点快评模式下若字数不足 1500，
   系统会自动取消首发声明以合规——如需首发补贴，热点快评可适当写到 1200+ 字。

4. **标题是点击率的关键**：热榜二创的标题应突出「判断 + 反差 + 具体主体」，
   复用 `output/growth_ops/title_playbook_*.md` 中已验证的高表现模式，
   但不要照抄模板（系统已内置模板去重）。

5. **每周看反馈报告**：`output/growth_ops/performance/performance_report.md`
   会告诉你哪些标题/选题/体裁真正带来了阅读，据此持续迭代。

---

## 七、回滚方式

所有改动均向后兼容，若某账号不想用热榜源：

```yaml
# config.yaml 中对应账号的 topic_collection
enabled_sources:   # 移除 toutiao_hot / baidu_hot
focus_industries:  # 移除 hot_topic
```

微头条发布不受影响，`weitoutiao_enabled: false` 即可关闭。

---

## 八、本次改动文件清单

| 文件 | 改动 |
|------|------|
| `src/toutiao_content_factory/services/topic_collection.py` | 新增头条热榜/百度热搜采集器、热度加权、敏感拦截、热榜质量门禁放宽 |
| `src/toutiao_content_factory/models.py` | TopicCandidate 增加 `heat_value`；ExtractionResult/DataReport 增加 `is_hot_topic` |
| `src/toutiao_content_factory/services/automation.py` | 账号级热榜话题边界过滤、hot_topic 赛道、热点快评写作指令 |
| `src/toutiao_content_factory/services/extractor.py` | 解析平台热度值 → `is_hot_topic` |
| `src/toutiao_content_factory/loaders/document_loader.py` | 解析「平台热度值」元数据 |
| `src/toutiao_content_factory/agents/base.py` | 新增 hot_topic 内容模式识别 |
| `src/toutiao_content_factory/agents/writer_agent.py` | 热点快评：短字数、无强制小标题、兜底段落、禁用产业链填充 |
| `src/toutiao_content_factory/agents/data_agent.py` | 热榜话题路由到快评、质量标记放宽 |
| `src/toutiao_content_factory/agents/review_agent.py` | 热榜二创信息密度加分 |
| `src/toutiao_content_factory/services/agent_workflow.py` | 热榜话题跳过证据型质量门禁 |
| `src/toutiao_content_factory/services/article_sanitizer.py` | 支持最小字数参数（热榜快评不补产业链段） |
| `src/toutiao_content_factory/config.py` | 热榜源默认配置、微头条发布配置 |
| `src/toutiao_content_factory/constants/selectors.py` | 微头条页面选择器 |
| `config/selectors.yaml` | 微头条页面选择器 |
| `src/toutiao_content_factory/services/publish_service.py` | 新增 `publish_weitoutiao` 方法 |
| `src/toutiao_content_factory/services/review_service.py` | 转发微头条发布 |
| `src/toutiao_content_factory/services/app_service.py` | 微头条发布编排 |
| `main.py` | `--publish-wtt-run` / `--publish-wtt-latest-approved` CLI |
| `config.yaml` | 热榜源接入全部账号、每日 6 档时段、微头条配置 |
| `.env` | `TOUTIAO_WEITOUTIAO_ENABLED=1` 等 |
| `branding/benchmark_accounts_toutiao.yaml` | 修复量子位对标源（替换 Google News RSS） |
| `scripts/run_feedback_loop.ps1` | 新增：每周浏览量反馈闭环 |
| `scripts/register_feedback_task.ps1` | 新增：注册每周反馈任务 |
