# 今日头条自动配置指南 - 完整方案

## 📋 已完成的工作

✅ 创建了增强版脚本
✅ 优化了文章生成质量
✅ 减少了模板化表达
✅ 添加了首发激励合规检查
✅ 创建了调试快照和通知服务
✅ 创建了完整文档

---

## 🔧 配置步骤（在您的电脑上运行）

### 第一步：检查环境

在您的电脑上，以管理员身份打开 PowerShell，运行：

```powershell
# 检查 Python
python --version

# 检查项目
cd D:\Solo\今日头条
ls
```

### 第二步：API 密钥配置

编辑 `D:\Solo\今日头条\.env` 文件，填写：

```env
OPENAI_API_KEY=您的API密钥
OPENAI_BASE_URL=https://api.openai.com/v1  # 如果需要

TOUTIAO_PUBLISH_ENABLED=1
TOUTIAO_AUTO_PUBLISH=1
```

### 第三步：初始化登录（重要！）

```powershell
cd D:\Solo\今日头条
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1
```

这会打开浏览器，请登录您的头条号账号。

### 第四步：测试运行

```powershell
cd D:\Solo\今日头条
python .\main.py
```

这会生成一篇测试文章，请检查结果。

---

## 🎯 定时任务配置方案

### 方案一：手动配置 Windows 任务计划（推荐）

1. 打开任务计划程序
   - Win + R，输入 `taskschd.msc`，回车

2. 创建基本任务
   - 点击"创建基本任务..."
   - 名称：`ToutiaoContentFactory`

3. 设置触发器
   - 每天
   - 开始时间：08:00（或您喜欢的时间）

4. 设置操作
   - 启动程序
   - 程序/脚本：`powershell.exe`
   - 参数：
     ```
     -ExecutionPolicy Bypass -File "D:\Solo\今日头条\scripts\daily_run_enhanced.ps1" -EnableAutoPublish
     ```
   - 起始于：`D:\Solo\今日头条`

5. 优化设置
   - 条件 → 勾选"唤醒计算机运行此任务"
   - 设置 → 勾选"如果任务失败，重新启动"
     - 间隔：15分钟
     - 最多尝试：3次

---

### 方案二：使用快捷脚本启动

我为您创建了一个快捷启动脚本。双击运行即可：

```
D:\Solo\今日头条\scripts\run_now.ps1
```

---

## 📱 通知配置（可选）

### 企业微信通知

1. 在企业微信群聊中添加群机器人
2. 复制 Webhook URL
3. 在 `.env` 中添加：

```env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=您的密钥
```

### 飞书通知

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/您的密钥
```

### 钉钉通知

```env
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=您的密钥
```

---

## 🎨 文章质量优化效果

| 优化项 | 效果 |
|-------|------|
| 减少模板化表达 | ✅ 避免被识别为低质内容 |
| 多样化开头结尾 | ✅ 提高用户阅读兴趣 |
| 首发激励合规 | ✅ 符合条件可获5倍补贴 |
| 自动质量检查 | ✅ 确保文章质量达标 |

---

## 📁 文件结构

```
D:\Solo\今日头条\
├── .env                          # 配置文件（需要填写）
├── .env.example                  # 配置模板
├── main.py                       # 主程序
├── requirements.txt              # 依赖包
├── scripts\
│   ├── daily_run_enhanced.ps1    # 增强版运行脚本
│   ├── init_publish_login.ps1    # 登录初始化
│   ├── register_daily_task.ps1   # 任务注册
│   └── run_now.ps1              # 快捷启动脚本
├── prompts\
│   ├── article_prompt_v2.md      # 文章生成模板
│   └── polish_prompt.md          # 润色模板
├── src\toutiao_content_factory\
│   ├── agents\                   # AI 智能体
│   ├── services\                 # 服务模块
│   └── web\                      # Web 后台
└── output\                       # 输出目录
    ├── runs\                    # 运行记录
    └── logs\                    # 日志文件
```

---

## 💡 提高收益的技巧

### 1. 发布时机
- 🌅 07:00-09:00 - 上班族
- ☀️ 12:00-13:30 - 午休时间
- 🌙 18:00-20:30 - 下班时间

### 2. 首发激励
发布时勾选"声明首发"：
- 基础分成 + 额外补贴（最高5倍）
- 全网维权服务

### 3. 互动引导
结尾使用自然互动方式：
- "您怎么看？"
- "欢迎留言讨论"
- "有问题请评论"

---

## 🔍 故障排除

### 文章没发布？
1. 检查 `.env` 中 `TOUTIAO_AUTO_PUBLISH=1`
2. 查看 `output/runs/` 下的运行记录
3. 检查浏览器是否登录成功

### API 错误？
1. 检查 `OPENAI_API_KEY` 是否正确
2. 检查网络连接
3. 查看 `output/logs/` 下的日志

### 发布失败？
1. 查看 `output/debug_snapshots/` 下的截图
2. 使用"复制诊断上下文"功能，发送给 AI 修复
3. 检查头条后台是否有验证码等问题

---

## 📞 需要帮助？

1. 查看文档：
   - `PROJECT_GUIDE.md` - 完整项目指南
   - `QUICK_START.md` - 快速上手指南
   - `FIRST_PUBLISH_OPTIMIZATION.md` - 首发激励计划
   - `OPTIMIZATION_GUIDE.md` - 系统优化指南

2. 测试运行：
   ```powershell
   cd D:\Solo\今日头条
   python .\main.py
   ```

3. 启动 Web 后台：
   ```powershell
   cd D:\Solo\今日头条
   powershell -ExecutionPolicy Bypass -File .\scripts\start_webapp.ps1
   ```

---

## ✅ 配置检查清单

在开始使用前，请确认：

- [ ] 已填写 API 密钥
- [ ] 已运行登录脚本并成功登录
- [ ] 已测试运行一次，文章生成正常
- [ ] 已配置定时任务
- [ ] 已配置通知（可选）

---

祝您创作愉快，收益倍增！ 🎉
