# story_notes 第二账号接入清单

> 状态：**代码侧已全部就绪，等账号信息**。
> 更新：2026-09-18
> 目标账号：`story_notes`（手蓝人间故事），定位社会故事类（大妈文 / 家庭伦理 / 职场人情）。

这份清单是「拿到账号之后要做的全部事情」。代码、配置、工作流都已经在仓库里，
只差**登录态**和**账号本身**，而这两样只能由你来提供。

---

## 一、我需要你提供什么

### 必需

| # | 东西 | 用途 |
|---|---|---|
| 1 | 头条账号名 + 主页链接 | 确认账号现状、领域权重、历史内容 |
| 2 | **作品管理 / 数据页截图** | 算它的真实 CTR、展现量分布、收益结构（robot_notes 就是这么诊断的） |
| 3 | 一次本地登录 | 我用 `scripts/export_cookies.py` 导出登录态，云端才能自动发布 |

### 如果拿不到截图

只给账号名也行，我可以用现有的浏览器自动化去采（同样需要你本地登录一次）。

### ⚠️ 一个必须先确认的冲突

你说这个号**已经发过一些内容**。如果它现在做的是科技/财经/其他垂直领域，
改成社会故事等于**换赛道**——头条的领域权重会重置，短期流量可能比现在更差。
这跟「尽快出收益」是冲突的。

所以我会先诊断，再给结论：**继续用这个号转定位，还是用它注册一个新号做社会故事**。
不要在我看到数据之前就开始发。

---

## 二、代码侧已经做完的事

### 云端免费模型（要求 1）

| 项 | 状态 |
|---|---|
| 标题模型 | `glm-4.6`（付费、余额不足→静默退回模板）→ **已改 `glm-4-flash`** |
| 正文模型 | 本来就是 `glm-4-flash`（免费） |
| 实测 | 免费模型生成 19 条标题、14 条过闸（74%） |
| 其余候选模型 | `glm-4.5-air`/`glm-5.3-flash`/`glm-4.6` 全部「余额不足」，只剩 `glm-4-flash` 可用 |

### 云端零成本（要求 3）

| 改动 | 省下什么 |
|---|---|
| 删掉 DeerFlow / Letta / Prompt-Optimizer 三个 Docker 服务 | 每次运行 1~2 分钟（经核实这三个服务**从不参与内容生成**，只被健康检查调用） |
| 修掉互动 Agent 的 `DeepSeek API 调用失败: 404` | 一次运行 11 次失败调用，白烧时间 |
| 选题池加入缓存 | 云端不再每次都是空池、不再只发长青稿 |

Actions 额度参考：2026-09 用了 720 分钟 / 免费 2000 分钟（私有仓库）。
加一个账号预计 +700~900 分钟，仍在上限内但偏紧；把仓库转 public 则完全免费。

### 账号与内容策略（要求 2 的代码部分）

- **账号配置**：`config.yaml` 的 `story_notes`（品牌名「手蓝人间故事」、中老年读者定位、3 个采集源）
- **写作风格**：新增 `family_story`（人间故事观察风）
- **选题过滤**：新增 `social_story` 判据 + 关键词表
- **实测**：采集到 3 条全部正对题的选题（高空作业绳被剪 / 车祸身亡获赔）

### 双账号云端（要求 3 的架构部分）

| 改动 | 为什么必须 |
|---|---|
| 新增 `daily-run-story.yml`，cron 与 robot_notes **错开 1 小时** | 独立故障域；且两个账号不抢同一个发布窗口 |
| cookie 按账号解析（`scripts/resolve_cookie_asset.py`） | 原来两个账号会共用同一个 cookie 文件 = 拿 A 的身份发 B 的内容 |
| 跨通道互斥状态加账号维度 | 原来只按「日期+平台」索引，A 发过会让 B **整天静默不发** |

### 写作领域与微头条人设

| 改动 | 为什么必须 |
|---|---|
| 新增 `StyleProfile.content_domain`（默认 `industry`） | 原来写作机制写死为「中文科技产业文章」「产业链拆解」「必须引用至少 3 个数据点」，故事号只能编数字 |
| 新增 `story_comment` 微头条人设 | 原来 4 个人设全是科技评论/正能量鸡汤腔；中老年流量的主入口是微头条 |
| 微头条人设改为按账号解析 | `weitoutiao_personas` 原来只读全局，账号级配置被忽略 |

> **合规约束是跟着领域一起走的**：换领域只换写作机制，不换安全约束。
> 「绝不虚构」「改写距离原创红线」「证据边界」三段在两个领域下都生效，
> 有测试专门断言这一点 —— 防止以后有人换领域时把红线一起换掉。

### ⚠️ story_notes **不做对标**（这是设计，不是缺陷）

`branding/benchmark_accounts_toutiao.yaml` 里目前只有 4 个赛道
（科技商业 / AI机器人 / 国际热点 / 体育赛事），**没有社会故事**。
所以 story_notes 解析出的对标赛道就是 `None`，整个对标采集环节被跳过。

**不要"修"这个行为。** 之前的兜底逻辑会让它落到默认值「科技商业」，
于是去抓 36氪/虎嗅/量子位/智东西 的科技热点，实测把
「人形机器人融资」「钙钛矿光伏测试标准」「Anthropic 算力规划」
三条科技选题混进了社会故事号的选题池。

若日后确实要给社会故事加对标账号：在 yaml 里加 `track: 社会故事` 的账号，
并把 `tests/test_benchmark_track_scoping.py::test_benchmark_yaml_has_no_social_story_track_yet`
这个断言改成「story_notes 解析出社会故事赛道」。

> ⛔ **`daily-run-story.yml` 的定时目前是停用状态**（只有 `workflow_dispatch`）。
> 原因：账号还没建好时若 cron 跑起来，会回落到 legacy cookie（robot_notes 的登录态）
> 去发布。账号就绪后取消那 5 行注释即可。

---

## 三、拿到账号后的执行步骤

### 第 1 步：诊断账号（为换赛道决策提供依据）

```powershell
# 本地登录一次（会打开浏览器让你扫码）
.venv\Scripts\python.exe main.py --account story_notes --collect-performance
```

我会据此产出：CTR、展现量分布、收益结构、领域权重判断、**转定位 vs 换新号**的结论。

### 第 2 步：导出并上传账号专属登录态

```powershell
# 导出 cookie（注意：资产名必须带 story-notes 后缀，否则云端会拿到 robot_notes 的）
.venv\Scripts\python.exe scripts\export_cookies.py --account story_notes

# 确认解析出的资产名（应为 exported_cookies_story-notes.json）
.venv\Scripts\python.exe scripts\resolve_cookie_asset.py --account story_notes --format primary

# 上传到 Release
gh release upload browser-profiles-v1 exported_cookies_story-notes.json --clobber --repo Lishoulan/Automated-workflow
```

同时把浏览器 profile 打包上传，云端才有可用的登录环境：

```powershell
.venv\Scripts\python.exe scripts\pack_browser_profiles.py --account story_notes
gh release upload browser-profiles-v1 browser_profiles_seed.tar.gz --clobber --repo Lishoulan/Automated-workflow
```

### 第 3 步：手动 dry-run 联调（不发布）

```powershell
gh workflow run daily-run-story.yml -f account=story_notes -f dry_run=true -f publish_toutiao=false
gh run watch
```

### 第 4 步：手动真发一次，肉眼验收

确认标题不是兜底模板形态（「XX如何实测」）、正文是对题的、封面正常、没有违规风险。

```powershell
gh workflow run daily-run-story.yml -f account=story_notes -f publish_toutiao=true
```

### 第 5 步：启用定时

取消 `.github/workflows/daily-run-story.yml` 里 `schedule:` 那 5 行注释并推送。

---

## 四、启用后要盯什么（判断「有没有人看」）

**不要看收益。** 收益是滞后指标，30 日 0.17 元这种量级看不出任何变化。

看这两个：

| 指标 | 现状（robot_notes） | 目标 |
|---|---|---|
| **阅读率（阅读/展现）** | 中位数 0.09%，47% 文章零阅读 | 先做到**单篇稳定 > 2%** |
| 单篇阅读量 | 中位数 1，近期 20~81 | 先做到**稳定 > 100** |

前 2~4 周的正常预期是：**阅读率在动、阅读量不稳定**。这段时间的作用是
用真实数据找出「哪种标题 + 哪种题材在这个号上能出量」，而不是赚钱。

---

## 五、已知的开放风险

1. **换赛道会让领域权重重置** —— 需第 1 步数据才能判断是否值得。
2. **虚构故事是违规红线** —— 头条《内容创作规范》明确把「编造与事实不符的故事」
   「以讲述自身经历的形式吸引点击」列为违规，且平台正在专项治理 AI 低质内容。
   所以 `family_story` 风格强制「真实事件 + 观察解读」，并写进了代码约束 + 测试。
3. **免费模型的上限** —— `glm-4-flash` 能写出合规、能过闸的内容，
   但写出爆款叙事的把握不大。如果 4 周后阅读率仍不动，
   下一步应该考虑的不是换模型，而是**换内容形态**（例如缩短到微头条为主）。
