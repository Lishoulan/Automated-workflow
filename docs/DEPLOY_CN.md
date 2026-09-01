# 自动化内容工厂部署说明

这份文档面向当前仓库的实际使用方式，重点说明：

- 新电脑如何快速拉起
- Windows 云主机如何部署
- 今日头条自动发布如何初始化
- 定时任务如何开启
- 常见报错如何排查

## 一、部署前提

当前项目最适合运行在：

- Windows 10 / Windows 11
- Windows Server 2019 / 2022

原因很简单：

- 今日头条发布链路依赖 Playwright 浏览器自动化
- 需要保存持久化浏览器登录态
- 需要兼容 Edge 和本地用户目录

不建议第一次部署就放到纯 Linux 环境里直接跑自动发布。  
如果你只是做选题、写稿、出图，Linux 可以用；但如果你要“自动登录头条并发布”，优先用 Windows。

## 二、推荐目录结构

建议仓库放在一个固定目录，比如：

```text
D:\Projects\Automated-workflow
```

不要放到太深的中文嵌套路径里。  
虽然当前项目已经兼容中文目录，但 Windows 下某些第三方工具仍可能在极端情况下出现编码问题。

## 三、新电脑部署步骤

### 1. 安装基础软件

需要先安装：

- Python 3.11 或更高版本
- Git for Windows
- Microsoft Edge

建议确认这些命令能在 PowerShell 里运行：

```powershell
python --version
git --version
```

### 2. 克隆仓库

```powershell
git clone https://github.com/Lishoulan/Automated-workflow.git
cd Automated-workflow
```

### 3. 一键初始化环境

项目已经提供了初始化脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

这个脚本会自动完成：

- 创建 `.venv`
- 安装 `requirements.txt`
- 安装 Playwright Chromium
- 如果本地没有 `.env`，自动从 `.env.example` 复制一份

### 4. 配置 `.env`

你必须手工填写 `.env` 里的密钥和模型配置。

至少要配置：

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
```

如果你要走图片接口，还要配置：

```env
IMAGE_BACKEND=image2
IMAGE_API_KEY=
IMAGE_BASE_URL=
IMAGE_MODEL=
```

如果你要启用头条自动发布，确认这些项正确：

```env
TOUTIAO_PUBLISH_ENABLED=1
TOUTIAO_AUTO_PUBLISH=1
TOUTIAO_PUBLISH_CHANNEL=msedge
TOUTIAO_BROWSER_EXECUTABLE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
TOUTIAO_BROWSER_PROFILE_DIR=.runtime/toutiao_profile
```

如果你要接入公众号草稿/发布，还要配置：

```env
WECHAT_ENABLED=1
WECHAT_APP_ID=
WECHAT_APP_SECRET=
WECHAT_ACCOUNT_NAME=
WECHAT_AUTHOR_NAME=手蓝
WECHAT_BASE_URL=https://mp.weixin.qq.com/
WECHAT_API_BASE_URL=https://api.weixin.qq.com
WECHAT_TITLE_MAX_LENGTH=64
WECHAT_DIGEST_MAX_LENGTH=120
WECHAT_LAYOUT_BACKEND=builtin
WECHAT_MD2WECHAT_ENABLED=0
MD2WECHAT_API_KEY=
MD2WECHAT_BASE_URL=https://www.md2wechat.com/api/v1
MD2WECHAT_THEME=default
MD2WECHAT_FONT_SIZE=medium
MD2WECHAT_CONVERT_VERSION=v1
MD2WECHAT_CLI_PATH=
MD2WECHAT_FALLBACK_TO_BUILTIN=1
```

如果你不想依赖 `MD2WECHAT_API_KEY`，可以只安装本地 `md2wechat` CLI，然后这样配置：

```env
WECHAT_LAYOUT_BACKEND=md2wechat
WECHAT_MD2WECHAT_ENABLED=1
MD2WECHAT_CLI_PATH=
MD2WECHAT_FALLBACK_TO_BUILTIN=1
```

说明：
- 有 `MD2WECHAT_API_KEY` 时，优先走官方托管 API。
- 没有 Key 时，会尝试调用本地 `md2wechat` 命令。
- 本地 CLI 也不可用时，会自动回退到项目内置公众号排版。

### 5. 初始化头条登录态

首次部署到一台新电脑时，必须重新登录头条创作者后台：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1
```

执行后会打开浏览器，请完成：

- 登录你的头条创作者账号
- 确认能进入图文发布页
- 回到终端按提示继续

初始化成功后，浏览器登录信息会保存在：

```text
.runtime/toutiao_profile
```

### 6. 启动 Web 控制台

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_webapp.ps1
```

默认地址：

```text
http://127.0.0.1:8000
```

如果端口被占用：

```powershell
python .\webapp.py --port 8001
```

## 四、Windows 云主机部署

如果你希望 24 小时运行，推荐使用：

- 腾讯云 Windows Lighthouse
- 阿里云 Windows ECS
- 任何可远程桌面的 Windows VPS

部署流程和本地电脑完全一致，本质上就是把“你的常开电脑”换成“云上的 Windows 电脑”。

推荐流程：

1. 开一台 Windows 云主机
2. 安装 Python / Git / Edge
3. 克隆仓库
4. 跑 `bootstrap.ps1`
5. 填写 `.env`
6. 跑 `init_publish_login.ps1`
7. 测试一次手动发布
8. 再注册自动定时任务

注意：

- 云主机第一次登录后，Edge 可能会弹首次初始化提示，先手工点掉
- 不要在未验证登录态前直接启用自动发布
- 云主机重启后，一般不需要重新登录头条，除非浏览器 profile 损坏

## 五、定时自动运行

当前项目的默认自动发布时间是：

- 内容任务：08:00、12:00、18:30（多账号注册脚本默认）

每日通知日报统一在北京时间 22:00 发送。成功、失败和人工审核门禁不会在每个时段单独推送，只会先写入通知日志；当天有事件才发送日报，没有事件不消耗方糖额度。

注册单账号定时任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -EnableAutoPublish
```

注册脚本会额外创建 `*-daily-report` 任务，在 22:00 只发送通知汇总，不执行内容生成或模型调用。方糖免费额度按每天 5 条保护，并为日报预留 1 条，日报最多占用 1 条。

注册首批 3 个账号：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_all_accounts.ps1 -AccountKeys default,robot_notes,energy_notes -EnableAutoPublish
```

如果后续你要把 `hardware_notes` 和 `automation_notes` 也加入，再单独补注册即可。

任务注册成功后，你会在 Windows 计划任务里看到类似名称：

```text
ToutiaoContentFactory-default-1
ToutiaoContentFactory-default-2
ToutiaoContentFactory-default-3
ToutiaoContentFactory-default-4
```

## 六、手动运行命令

### 1. 只采集选题

```powershell
python .\main.py --topics-only
```

### 2. 运行完整日报流程

```powershell
python .\main.py --daily-run --refresh-cache
```

说明：
- 这个命令现在会自动尝试多个候选热点
- 只要有一篇通过阈值，就会自动进入审核和发布
- `TOUTIAO_MAX_DAILY_TOPIC_ATTEMPTS` 可控制最多尝试几个候选题
- 如果 `WECHAT_ENABLED=1`，这轮流程还会自动创建公众号草稿
- 如果再开启 `WECHAT_AUTO_PUBLISH=1`，会继续自动提交公众号发布

### 3. 发布最新已审核通过的文章

```powershell
python .\main.py --publish-latest-approved
```

公众号草稿 / 发布：

```powershell
python .\main.py --prepare-wechat-launch-day 2
python .\main.py --prepare-wechat-launch-week
python .\main.py --generate-images-run --run-dir "D:\path\to\output\runs\your_run_dir"
python .\main.py --wechat-draft-latest-approved
python .\main.py --wechat-publish-latest-approved
python .\main.py --init-wechat-login
```

说明：
- `--prepare-wechat-launch-day` 按起号周 Day1-Day7 生成单天稿件包，并落封面图/正文图 prompt。
- `--prepare-wechat-launch-week` 一次性准备整周起号稿件包。
- `--generate-images-run` 可对已有 run 单独重跑真实图片生成。
- `--wechat-draft-*` 通过公众号接口创建草稿。
- `--wechat-publish-*` 先创建草稿，再通过已登录的公众号后台浏览器会话从草稿箱点击发布。
- 第一次使用前，先执行一次 `--init-wechat-login` 完成扫码登录。

公众号草稿 / 发布：

```powershell
python .\main.py --wechat-draft-latest-approved
python .\main.py --wechat-publish-latest-approved
```

对标 / 数据复盘：

```powershell
python .\main.py --build-benchmark-report
python .\main.py --backfill-performance-ledger
python .\main.py --build-performance-report
python .\main.py --record-performance --run-dir "D:\path\to\output\runs\your_run_dir" --performance-platform wechat --snapshot-date 2026-05-20 --impression-count 10000 --read-count 860 --recommendation-read-count 520 --like-count 22 --wow-count 11 --share-count 6 --comment-count 8 --follower-gain 17
```

说明：
- 对标源文件在 `branding/benchmark_accounts.yaml`。
- 增长台账会输出到 `output/growth_ops/performance/`。
- 每次头条或公众号发布成功后，系统会自动把该文章登记进性能台账，后续只需要补录真实数据。

### 4. 发布指定批次

```powershell
python .\main.py --publish-run --run-dir "D:\path\to\output\runs\your_run_dir"
```

## 七、迁移到另一台电脑时要复制什么

必须复制：

- 整个仓库代码
- 你自己的 `.env`

可选复制：

- `.runtime/toutiao_profile`

不建议直接复制：

- `.venv`
- `output`
- `logs`
- `cache`

更稳的迁移方式是：

1. 新电脑重新 clone 仓库
2. 重新跑 `bootstrap.ps1`
3. 复制 `.env`
4. 重新跑 `init_publish_login.ps1`

## 八、常见问题排查

### 1. `git` 找不到

现象：

```text
git : The term 'git' is not recognized...
```

处理：

- 安装 Git for Windows
- 关闭当前 PowerShell，重新打开
- 再执行 `git --version`

### 2. Playwright 缺浏览器

现象：

```text
Executable doesn't exist ...
Please run: playwright install
```

处理：

```powershell
python -m playwright install chromium
```

或者直接重新跑：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

### 3. Web 端口被占用

现象：

```text
error while attempting to bind on address ... only one usage of each socket address
```

处理：

```powershell
python .\webapp.py --port 8001
```

### 4. 发布时报找不到编辑器选择器

现象：

```text
Could not locate title/editor selectors
```

处理思路：

- 头条后台 DOM 改版了
- 先确认浏览器是否真的进入图文发布页
- 再看 `publish_service.py` 里的选择器是否需要更新

### 5. 发布时报 AI 助手遮罩挡住编辑器

现象：

```text
byte-drawer-mask intercepts pointer events
```

这个项目已经对这类遮罩做了多轮兼容处理。  
如果仍出现，通常是头条后台 UI 再次变动，需要重新调一次选择器或关闭逻辑。

### 6. 自动任务到了时间没发

优先检查：

- `logs/scheduled/`
- `output/automation/scheduled_status.json`
- `output/automation/notifications.json`

常见原因：

- 登录态失效
- 发布页元素改版
- 当次文章未通过审核
- 浏览器未成功加载

## 九、发布前建议验证项

正式放到无人值守运行前，建议至少手测这 4 件事：

1. 手动跑一轮 `--daily-run`
2. 手动跑一轮 `--publish-run`
3. 确认封面图能上传
4. 确认正文内图能插入，且作品声明、头条首发勾选正确

不要一上来就直接开全自动定时任务。  
先确保单次链路稳定，再转无人值守。

## 十、建议的长期运维方式

如果你要长期稳定运行，建议：

- 使用 Windows 云主机
- 保留远程桌面权限
- 每次升级代码前先手动测试一次发布
- 定期备份 `.env`
- 定期备份 `.runtime/toutiao_profile`

如果后面你要扩展成多账号矩阵，建议把结构拆成：

- 一台主控机负责抓热点、生成内容
- 一台或多台发布机负责不同账号的浏览器登录态和自动发布

这样比把所有账号都堆在一台机器里更稳。
