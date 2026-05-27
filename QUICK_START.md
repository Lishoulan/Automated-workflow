# 🎯 AI头条内容工厂 - 快速上手指南

> **目标：每日自动生成高阅读量、零违规的文章，赚取阅读收益**

---

## 📦 第一步：一键配置（5分钟）

### 1.1 打开 PowerShell（管理员）

按 `Win + X`，选择"Windows PowerShell (管理员)"

### 1.2 运行快速配置

```powershell
cd d:\Solo\今日头条
powershell -ExecutionPolicy Bypass -File .\scripts\quick_config.ps1
```

这会自动：
- ✅ 创建 `.env` 文件
- ✅ 删除旧的定时任务
- ✅ 注册新的定时任务（增强版）

---

## ⚙️ 第二步：配置密钥（3分钟）

### 2.1 编辑 .env 文件

```powershell
notepad d:\Solo\今日头条\.env
```

### 2.2 填写必要配置

```env
# 必须填写的 - DeepSeek 配置
OPENAI_API_KEY=你的DeepSeek密钥
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat

# 让一个定时任务同时处理头条和公众号
TARGET_PLATFORM=all

# 今日头条自动发布
TOUTIAO_PUBLISH_ENABLED=1
TOUTIAO_AUTO_PUBLISH=1

# 微信公众号自动发布
WECHAT_ENABLED=1
WECHAT_AUTO_PUBLISH=1
WECHAT_APP_ID=你的公众号AppID
WECHAT_APP_SECRET=你的公众号AppSecret
```

---

## 🔐 第三步：初始化头条和公众号登录（3分钟）

```powershell
cd d:\Solo\今日头条
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1
```

按提示完成登录。这个脚本会同时打开头条创作者后台和微信公众号后台。

---

## 📝 第四步：手动测试（可选）

测试一次完整的文章生成流程：

```powershell
cd d:\Solo\今日头条
python .\main.py
```

查看生成的输出：
```
output/runs/YYYY-MM-DD_HHMMSS/
├── article.md          # 生成的文章
├── title.txt          # 推荐标题
└── workflow.json      # 完整工作流
```

---

## ⏰ 第五步：设置定时任务

### 5.1 注册每日任务

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish -UseEnhancedScript
```

这套主任务在 `TARGET_PLATFORM=all` 时会在同一次日常流程里同时处理：
- 今日头条自动发布
- 微信公众号草稿/自动提交

### 5.2 配置任务计划属性

1. 打开"任务计划程序"（搜索框输入"任务计划"）
2. 找到任务 `ToutiaoContentFactory-...`
3. 右键 → 属性：
   - ✅ **条件** → 勾选"允许唤醒计算机运行此任务"
   - ✅ **设置** → 勾选"如果任务失败，重新启动"
     - 间隔：15分钟
     - 最多尝试：3次

---

## 🎓 日常使用

### 📊 查看运行状态

访问后台（默认）：
```
http://127.0.0.1:8000
```

查看：
- 📋 运行历史
- ❌ 失败原因和截图
- 📈 发布结果

### 📱 接收通知

配置通知后，任务完成或失败会推送通知：

**企业微信**：
1. 打开企业微信 → 群聊 → 添加群机器人
2. 复制 WebHook 地址
3. 填入 `.env`：
```env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的密钥
```

---

## 💰 提高收益的技巧

### ✅ 已实施的优化

| 优化项 | 效果 |
|-------|------|
| 减少模板化表达 | 避免被识别为低质内容 |
| 多样化开头结尾 | 提高用户阅读兴趣 |
| 首发激励合规检查 | 符合条件可获5倍补贴 |
| 自动发布审核 | 确保文章质量 |

### 📈 手动优化建议

#### 1. 标题优化
- ✅ 使用数字：`"3大趋势"` `"5个关键"`
- ✅ 使用疑问：`"为什么..."` `"如何..."`
- ✅ 避免标题党：不用`"震惊"` `"竟然"`

#### 2. 内容优化
- ✅ 前3段要抓住读者
- ✅ 每段要有实质内容
- ✅ 结尾要有互动引导

#### 3. 发布时机
推荐发布时间：
- 🌅 早间 07:00-09:00
- 🌞 午间 12:00-13:30
- 🌙 晚间 18:00-20:30

#### 4. 首发激励
发布时勾选"声明首发"：
- 💰 基础分成 + 额外补贴（最高5倍）
- 🛡️ 全网维权服务

---

## 🔧 常用命令

```powershell
# 生成一篇测试文章
python .\main.py

# 仅采集话题，不发布
python .\main.py --topics-only

# 发布已审核通过的文章
python .\main.py --publish-latest-approved

# 查看帮助
python .\main.py --help
```

---

## ❓ 常见问题

### Q1: 文章没有自动发布？

**A**: 检查 `.env` 中的配置：
```env
TARGET_PLATFORM=all
TOUTIAO_AUTO_PUBLISH=1
WECHAT_ENABLED=1
WECHAT_AUTO_PUBLISH=1
```

如果公众号没动作，再检查：
```env
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

### Q2: 任务没有按时执行？

**A**: 
1. 打开任务计划程序
2. 检查任务是否被禁用
3. 确认"允许唤醒计算机"已勾选

### Q3: 今日头条返回 `code=2010` 或“无头条广告权限”？

**A**:
1. 这不是项目代码报错，而是当前登录的头条后台账号没有可用发布权限
2. 重新运行 `.\scripts\init_publish_login.ps1`
3. 确认你登录的是正确的创作者后台账号
4. 手动验证能否打开并发布：
   `https://mp.toutiao.com/profile_v4/graphic/publish`

### Q4: 收到违规通知？

**A**: 
1. 查看具体违规原因
2. 检查文章内容是否符合规范
3. 参考 `FIRST_PUBLISH_OPTIMIZATION.md`

---

## 📚 相关文档

| 文档 | 说明 |
|-----|------|
| [PROJECT_GUIDE.md](PROJECT_GUIDE.md) | 完整项目指南 |
| [FIRST_PUBLISH_OPTIMIZATION.md](FIRST_PUBLISH_OPTIMIZATION.md) | 首发激励优化 |
| [OPTIMIZATION_GUIDE.md](OPTIMIZATION_GUIDE.md) | 系统优化指南 |

---

## 🚀 下一步

想要更精细的控制？编辑 `.env` 中的高级参数：

```env
# 提高质量阈值（更高质量，但可能更少发布）
TOUTIAO_AUTO_PUBLISH_MIN_OVERALL_SCORE=85

# 每天最多尝试话题数
TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS=5

# 启用AI审核（更严格）
TOUTIAO_SKIP_REVIEW_AGENT=0
```

---

**🎉 恭喜！配置完成！**

系统将在每天的设定时间自动运行，生成并发布文章。
有问题随时查看相关文档或重新运行配置脚本。
