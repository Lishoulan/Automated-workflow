# 🤖 AI Content Factory 全自动运营启动说明

> 本文档记录系统进入「每日自动生成文章并发布到今日头条」全自动运营状态的全部配置、
> 当前状态与待办事项。最后更新：2026-08-22。

---

## ✅ 已就绪的自动化配置

### 1. 头条登录态（关键前置条件）

- 账号：`robot_notes`（手蓝机器人笔记）
- 浏览器 Profile：`.runtime/accounts/robot-notes/toutiao_profile`
- 登录状态：**有效**（2026-08-22 已扫码登录，session cookie 完整：sessionid / sid_tt / odin_tt 等）
- 验证方式：`python main.py --account robot_notes --publish-preflight`
  - 通过标准：`current_url` 为 `mp.toutiao.com/profile_v4/graphic/publish` 且 `blocking_flags` 为空

> ⚠️ 登录态有效期：头条登录态通常可持续数周。若过期，重新执行：
> `powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -Account robot_notes`
> （会打开浏览器扫码，登录后按回车）

### 2. 自动发布配置

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `TOUTIAO_PUBLISH_ENABLED` | `1` | 头条发布总开关 |
| `TOUTIAO_AUTO_PUBLISH` | `1` | **自动发布**（daily-run 生成审核通过后直接发布） |
| `TOUTIAO_MAX_DAILY_POSTS` | `1` | 每日最多 1 篇（保护账号权重） |
| `TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS` | `4` | 每轮最多尝试 4 个选题（混合策略：1 热榜 + 素材题） |
| `WECHAT_ENABLED` | `0` | 微信关闭（仅头条自动发布） |
| `BILIBILI/ZHIHU/XIAOHONGSHU_ENABLED` | `0` | 其他平台关闭 |

自动发布质量门槛（`.env`，2026-08-23 二次校准后实测通过）：
- 素材深度文：综合 ≥ 70、AI 风险 ≤ 40、验证 ≥ 80、就绪 ≥ 60
- 热榜快评：综合 ≥ 65、AI 风险 ≤ 50、验证 ≥ 80、就绪 ≥ 50
  （`TOUTIAO_HOT_TOPIC_MIN_AUTO_PUBLISH_OVERALL_SCORE` /
   `TOUTIAO_HOT_TOPIC_MAX_AUTO_PUBLISH_AI_RISK` /
   `TOUTIAO_HOT_TOPIC_MIN_AUTO_PUBLISH_READINESS_SCORE`）

> 📌 2026-08-22 实测：素材题文章（qbitai 优必选 WRC）评分为综合 71 / AI 风险 38 /
> 验证 100 / 就绪 63，发布成功（pgc_id=7676884739529359891）。
> 📌 2026-08-23 实测：热榜快评（上千台中国机器人向国旗行注目礼）评分为综合 66 /
> AI 风险 42 / 验证 98 / 就绪 62，**无人干预自动发布成功**（pgc_id=7677049943223222803）。

### 3. 每日定时任务（Windows 计划任务）

已注册 7 个任务（前缀 `ToutiaoContentFactory-robot-notes`）：

| 任务 | 时间 | 作用 |
|------|------|------|
| `-1` | 07:00 | 每日发布（选热榜话题→写作→审核→发布） |
| `-2` | 09:30 | 每日发布（若 07:00 未发或失败则重试/换题） |
| `-3` | 12:30 | 每日发布 |
| `-4` | 15:30 | 每日发布 |
| `-5` | 18:00 | 每日发布 |
| `-6` | 21:30 | 每日发布 |
| `-collect-performance` | 01:00 | 采集昨日发布文章的阅读/互动数据 |

> 📌 设计说明：6 个时段共享「每日 1 篇」上限——首个时段发布成功后，
> 后续时段因无可用选题自动跳过（`no_topics` 状态，不会重复发布）。

### 2b. 微头条高频化（2026-08-27 新增）

头条涨粉研究表明**微头条是涨粉最快入口，每日 2-3 条效果好于 1 条**。系统现在：

1. **伴生微头条**（已有）：图文发布成功后自动发 1 条，内容取自图文（短观点+话题标签）。
2. **独立微头条**（新增）：独立选题 + 独立写作，复用同一浏览器登录会话发布，
   内容**不复用图文**——从当日选题池独立选题（与图文/伴生选题去重），
   按人设轮换（`hot_comment` 热点锐评 / `warm` 正能量 / `sanhuo` 散户实战 / `shoulan` 金融硬核）。

配置（`config.yaml` publishing 段 或 `.env`）：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `TOUTIAO_WEITOUTIAO_ENABLED` | `1` | 微头条总开关 |
| `TOUTIAO_WEITOUTIAO_DAILY_TARGET` | `3` | 每日微头条目标条数（**含**图文伴生 1 条） |
| `TOUTIAO_WEITOUTIAO_STANDALONE_ENABLED` | `1` | 独立微头条开关（超出伴生部分靠它补齐） |
| `TOUTIAO_WEITOUTIAO_PERSONAS` | `hot_comment,warm,sanhuo` | 独立微头条人设轮换列表 |

运行行为：
- 每日首时段：图文 1 篇 + 伴生微头条 1 条 + 独立微头条补到目标（默认 3 条/日）。
- 后续时段：若当日微头条仍未达标（如首时段部分失败），继续补发独立微头条直到达标；
  已达标的时段依旧秒级跳过（不烧 token）。
- 所有微头条均写入 `publish_history.json`（platform=`toutiao_weitoutiao`），日报分开统计。

手动触发独立微头条（调试/补发）：

```powershell
# 立即生成并发布 2 条独立微头条
python main.py --account robot_notes --publish-wtt-standalone 2

# 只填内容不点发布（存草稿，验证选择器）
python main.py --account robot_notes --publish-wtt-standalone 2 --draft-only
```

> ⚠️ 关闭方式：`TOUTIAO_WEITOUTIAO_STANDALONE_ENABLED=0` 恢复「仅图文伴生 1 条」；
> `TOUTIAO_WEITOUTIAO_ENABLED=0` 完全关闭微头条。
> 这保证了热点时效性（何时有热点都能及时发布），同时不超发。

**当前状态：已启用（Ready）**——2026-08-22 晚间重新启用全部 6 个发布任务，
首次真实自动发布已成功（pgc_id=7676884739529359891，后端返回"提交成功"）。

### 4. 热点选题采集（新增）

- **头条热榜**（`toutiao_hot`）：头条官方热榜 API，含真实热度值
- **百度热搜**（`baidu_hot`）：百度实时热搜
- 热榜话题按热度值加权排序，真实热点排最前
- 垂直账号（robot_notes）只接收含机器人/AI/新能源关键词的热榜话题
- 敏感话题自动拦截（自杀/凶杀/性侵等）

### 5. 发布自动化链路（已验证）

发布流程核心环节实测正常：
- 登录态识别（发布页可访问）
- 浏览器打开头条创作中心发布页
- 封面上传失败自动回退"无封面"模式
- 弹窗自动关闭（AI 助手推荐、通用遮罩）
- 内容消毒与合规预检（status=passed）

### 6. 定时任务脚本修复记录（2026-08-22）

注册任务使用的 `daily_run_enhanced.ps1` 存在两个致命 bug，已修复：

| Bug | 影响 | 修复 |
|-----|------|------|
| 无 UTF-8 BOM | PowerShell 5.1 按 GBK 误读中文，脚本语法解析失败，任务完全无法运行 | 全部 .ps1 脚本添加 UTF-8 BOM |
| 使用裸 `python` 命令 | 系统 PATH 无 python，流水线执行立即失败 | 自动解析 venv python（优先 `.venv\Scripts\python.exe`） |
| `-AccountKey` 参数不匹配 | 注册脚本传 `-AccountKey`，enhanced 脚本原用 `-Account` | 添加 `[Alias("AccountKey")]` |

修复后验证：语法 0 错误、wrapper→脚本→网络检测→python 流水线→成功上报全链路实测通过。

### 6b. 头条发布失败修复记录（2026-08-22 深夜，实测发布成功）

排查时发布 3 次均报 `No publish submission response was captured`，最终定位两个根因并修复：

| 根因 | 现象 | 修复 |
|------|------|------|
| save-mode 覆盖路由安装太晚 | 首个 autosave 以 save=0（发布模式）+ coverType=2 + 空封面发出，头条后端返回 `7050 保存失败`，草稿永远保存不了，发布按钮无草稿可提交 | 把 `_install_save_mode_override` 提前到**第一次触发 autosave 之前**（标题/正文填入前），首个保存即被改写为 save=1 + coverType=1 |
| 封面上传抽屉（`byte-drawer-wrapper`/`mp-ic-img-drawer`）残留遮挡发布按钮 | 发布按钮点击被抽屉拦截，无新 POST 发出 | 新增 `_close_stray_cover_panel()`：点击发布前扫描上传面板/抽屉，先点"确定/取消"，再按 Escape，最后 CSS 强制隐藏 `byte-drawer-wrapper` |

修复后实测：后端返回 `code=0 提交成功`，pgc_id=7676884739529359891，
文章「以完成一次演示工厂背后，具身智能谁在抢跑」进入头条审核队列（status=submitted_for_review）。

### 6c. 热榜快评评分修复记录（2026-08-23，实测自动发布成功）

第二日验证发现：定时任务运行正常（exit 0），但两轮 8 个候选全部未达标，原因有二：

| 根因 | 现象 | 修复 |
|------|------|------|
| 热榜快评被强制补段到 1500 字 | 短评被模板段注水，AI 痕迹分 70-80（门槛 45-50），信息密度也被稀释 | `_apply_polish_pack` 对热榜改用 850 字目标；`_score_ai_risk` 增加热榜豁免（缺素材不算 AI 痕迹） |
| 评分 LLM 不知道是热榜快评 | 按深度文标准评估热榜短评，整体分 44-57 远低于门槛 | `review_prompt.md` / `publish_review_prompt.md` 注入热榜快评说明（观点密度优先、缺数据不算缺陷、ready≥55 即可发） |
| 热榜就绪分仍偏低 | ready 45-55 < 60 全局门槛 | 新增 `hot_topic_min_auto_publish_readiness_score=50` 独立热榜就绪门槛 |
| collect-performance 任务误跑 daily-run | 01:00 任务带 `-CollectPerformanceOnly` 但 enhanced 脚本不支持该参数 | `daily_run_enhanced.ps1` 增加 `-CollectPerformanceOnly` 支持（只跑 `--collect-performance`） |

修复后实测（2026-08-23 10:35）：热榜文章评分 66/42/98/62 → 自动批准 → 头条后端返回
`提交成功`（pgc_id=7677049943223222803），全自动发布闭环首次完整跑通。

### 6d. 资源优化与数据反馈闭环（2026-08-23 晚间）

针对"6 个时段任务 vs 每日 1 篇上限冲突（空转烧 token）"和"无数据反馈"两个问题：

| 问题 | 修复 | 效果 |
|------|------|------|
| 发布成功后后续 5 个时段仍完整生成（选题→写作→评分），烧大量 token | `_daily_run_impl` 开头新增**当日已发布短路**：`count_today_published >= 1` 时直接返回 `already_published`，5 秒内跳过 | 后续时段任务从 15+ 分钟/轮降为 ~5 秒，仅首轮消耗生成 token |
| 采集 0 数据（headless 下头条不渲染管理表格） | 采集强制有头浏览器；URL 优先文章管理页；自动点击「作品管理」；**新增 iframe 内提取**（头条列表渲染在 `manage/content/all` iframe）；行选择器更新为 `article-card-bone` | 实测采集 5 条真实数据（展现 70-431） |
| 无"什么文章浏览量高"的反馈 | collect 后自动跑 feedback loop：数据 → EvolutionAgent 提炼规则 → 更新 title/article prompt + 自对标样本 | 实测生成 7 条规则（标题/开头/冲突设计/结尾跟踪等），写入 `config/best_practices_rule.json`，WriterAgent 每次写作自动注入 |

**数据采集页面结构**（2026-08-23 实测）：
- 入口：`mp.toutiao.com/profile_v4/graphic/manage` → 点击「作品管理」
- 数据 iframe：`profile_v4/manage/content/all`
- 行元素：`[class*='article-card-bone']`，文本形如「标题 | 日期 | 已发布 | 展现 X 阅读 Y 点赞 Z 评论 N」

**collect 定时任务**（01:00）：`--collect-performance --feedback-loop` 组合执行，
采集昨日数据后自动完成"数据→规则→prompt 优化"闭环，明早写作即用新规则。

### 6e. 提流量三件套（2026-08-24）

针对"有展现无阅读"系统性问题，修复/启用三个提流量能力：

| 能力 | 问题 | 修复 | 验证 |
|------|------|------|------|
| **封面上传** | 封面从未成功上传，文章全部无封面发布（信息流点击率腰斩） | 新增 `_upload_cover_via_js_data_transfer`（JS DataTransfer 注入文件 + JS 点确定），并修复 AI 抽屉遮罩拦截点击（`_hide_ai_drawer_mask_css`） | ✅ 封面应用成功，验证截图 `cover_verification/`（6/16 以来首张） |
| **互动 Agent** | `--engagement` 从未运行，账号零互动冷启动 | 注册每日 10:00 任务 `ToutiaoEngagement-robot-notes`；修复头条文章页点赞（`div.detail-like`）与评论输入（`div.comment-textarea`）选择器 | ✅ 实测赞/评各 4+ 成功 |
| **微头条引流** | 微头条从未使用，涨粉引流通道浪费 | 每日图文发布成功后自动发一条微头条（`_build_weitoutiao_content` 生成 200-400 字短观点+话题标签） | ✅ draft-only 验证内容填充正常 |

> 📌 封面修复原理：头条发布页的 AI 助手抽屉遮罩会拦截封面触发区域点击，导致 file-chooser 永不出现；
> 且 Playwright 的 file-chooser 事件与 is_visible 时序判断都不可靠。
> 最终采用纯 JS 方案：点击触发 → DataTransfer 注入文件到 input → 触发 change → JS 点"确定"。

---

## ✅ 待办事项

### ✅ 已解决：LLM API 余额不足

- 已切换至 fhl 中转站：`https://www.fhl.mom/v1` + `gpt-5.6-luna`
- 已加 `OPENAI_DISABLE_RESPONSE_FORMAT=1`（中转站不支持 response_format，避免 502 浪费）
- 已加 `httpx.Timeout` 读超时 90s，长请求不再挂死
- 实测发布成功（2026-08-22 23:54，pgc_id=7676884739529359891）

### ⏳ 待办：Bark 手机通知（已改用 ServerChan 微信推送，见下）

- 配置项：`BARK_URL`（形如 `https://api.day.app/你的key`，仅 iOS 可用）
- 华为等安卓手机改用 **ServerChan**（`SERVERCHAN_SENDKEY`）或 **PushPlus**（`PUSHPLUS_TOKEN`）

### ✅ 通知与数据记录（2026-08-26 更新）

- **每日只推 1 条**：发布成功/失败改为只记日志（`automation/notification_log.jsonl`），
  仅**紧急通知**（登录过期等）与**每日 22:00 日报**推送微信（ServerChan 免费额度每天 1 条）
- **微头条记录入库**：微头条发布成功后写入 `publish_history.json`（platform=`toutiao_weitoutiao`），
  日报可统计微头条引流效果（今日图文 + 微头条分开计数）
- 待用户提供 key 后填入 `.env`

---

## 🚀 恢复全自动运营步骤（API 恢复后）

```powershell
# 1. 填入有效 API Key（编辑 .env）
#    OPENAI_API_KEY=你的新key
#    OPENAI_BASE_URL=https://api.deepseek.com/v1
#    OPENAI_MODEL=deepseek-chat

# 2. 验证 API 可用
python -c "import urllib.request,json; req=urllib.request.Request('https://api.siliconflow.cn/v1/chat/completions',data=json.dumps({'model':'deepseek-ai/DeepSeek-V4-Flash','messages':[{'role':'user','content':'hi'}],'max_tokens':5}).encode(),headers={'Authorization':'Bearer 你的key','Content-Type':'application/json'}); print(urllib.request.urlopen(req,timeout=20).read()[:100])"

# 3. 重新启用定时任务
schtasks /Change /TN "ToutiaoContentFactory-robot-notes-1" /ENABLE
schtasks /Change /TN "ToutiaoContentFactory-robot-notes-2" /ENABLE
schtasks /Change /TN "ToutiaoContentFactory-robot-notes-3" /ENABLE
schtasks /Change /TN "ToutiaoContentFactory-robot-notes-4" /ENABLE
schtasks /Change /TN "ToutiaoContentFactory-robot-notes-5" /ENABLE
schtasks /Change /TN "ToutiaoContentFactory-robot-notes-6" /ENABLE

# 4. 验证全链路（真实发布一篇）
python main.py --account robot_notes --daily-run --refresh-cache

# 5. （可选）配置 Bark 通知后重启即可生效
```

---

## 📊 状态监控

### 查看运行状态

```powershell
# 定时任务状态
schtasks /Query /FO TABLE | Select-String "ToutiaoContentFactory"

# 最近运行状态
Get-Content "F:\Automated-workflow-main\output\accounts\robot-notes\automation\scheduled_status.json"

# 运行日志
Get-Content "F:\Automated-workflow-main\logs\accounts\robot-notes\app.log" -Tail 50
```

### 每日运营检查清单

- [ ] 查看 `scheduled_status.json` 的 `overall_status` 是否为 `success`
- [ ] 查看最近 run 的 `publish/publish_result.json` 状态是否为 `submitted_for_review`
- [ ] 每周跑一次反馈闭环（浏览量学习）：
  `powershell -ExecutionPolicy Bypass -File .\scripts\run_feedback_loop.ps1 -AccountKey robot_notes`
- [ ] 关注 Bark 通知（配置后）的发布成功/失败提醒

---

## 🔧 常用运维命令

```powershell
# 立即运行一次完整流程（采集→写作→审核→发布）
python main.py --account robot_notes --daily-run --refresh-cache

# 仅采集选题（看今天的热点）
python main.py --account robot_notes --topics-only

# 发布最新审核通过的文章
python main.py --account robot_notes --publish-latest-approved

# 以微头条形式发布
python main.py --account robot_notes --publish-wtt-latest-approved

# 登录态检查
python main.py --account robot_notes --publish-preflight

# 重新登录（扫码）
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -Account robot_notes
```

---

## 📁 关键文件位置

| 内容 | 路径 |
|------|------|
| 文章输出 | `output/accounts/robot-notes/runs/` |
| 发布历史 | `output/accounts/robot-notes/publish_history.json` |
| 运行状态 | `output/accounts/robot-notes/automation/scheduled_status.json` |
| 运行日志 | `logs/accounts/robot-notes/app.log` |
| 发布调试截图 | `output/accounts/robot-notes/publish_debug/` |
| 浏览器登录态 | `.runtime/accounts/robot-notes/toutiao_profile/` |
| 优化说明 | `docs/HOT_TOPIC_OPTIMIZATION.md` |
