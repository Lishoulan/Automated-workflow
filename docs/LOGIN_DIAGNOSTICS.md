# 头条登录诊断与恢复

这份说明用于区分三类问题：浏览器登录态失效、发布请求已经提交但页面未确认、以及本地浏览器/解释器环境异常。诊断脚本只读取已有持久化 profile，不生成或注入 Cookie，也不会点击发布按钮。

## 1. 先做无副作用诊断

在项目根目录执行：

```powershell
py -3.10 scripts/diag_toutiao_login.py --account robot_notes
```

多账号只替换 `--account`。脚本会输出 JSON，并将结果写入当前账号的 `output/.../automation/toutiao_login_diagnostic.json`；截图写入同一目录。退出码含义：

- `0`：检测到创作者后台页面，登录态可用。
- `2`：被重定向到登录页或页面出现登录提示，需要重新扫码。
- `1`：导航、浏览器启动或配置读取失败，需要检查 JSON 中的 `error`。

`state=unknown` 不应当被当成已登录。先查看 `final_url`、`body_excerpt` 和截图，再决定是否重新登录。

## 2. 重新建立登录态

诊断结果为 `login_required` 时，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_publish_login.ps1 -Account robot_notes
```

在打开的浏览器中完成扫码，并确认创作者后台可以访问。关闭窗口后再运行一次诊断：

```powershell
py -3.10 scripts/diag_toutiao_login.py --account robot_notes
```

登录初始化脚本和日运行脚本会优先使用项目虚拟环境，也支持通过 `TOUTIAO_PYTHON` 指定解释器；没有 `python` 命令时会自动从 Windows `py` 启动器解析可执行文件。

## 3. 发布结果的处理

查看当前账号的结果文件：

```powershell
Get-Content .\output\accounts\robot-notes\automation\last_daily_run_result.json
Get-Content .\output\accounts\robot-notes\automation\scheduled_status.json
```

重点状态如下：

| 状态 | 含义 | 下一步 |
|---|---|---|
| `publish_login_required` | 发布前登录检查失败 | 重新登录后再运行 |
| `publish_uncertain` | 点击或请求后没有足够证据确认结果 | 先人工检查作品管理，确认未发布前不要自动重试 |
| `submitted_for_review` | 后端返回了有效提交证据 | 按平台审核状态跟踪，不重复提交 |
| `draft_saved` / `draft_only` | 只保存了草稿，没有公开提交 | 人工检查草稿，确认后再发布 |
| `failed` | 发布没有完成 | 查看 `publish/publish_result.json` 和诊断截图 |

`publish_uncertain` 不能按普通失败自动重试，否则可能造成重复发布。只有在平台后台明确没有对应作品时，才重新执行发布。

## 4. 每日任务检查

```powershell
Get-Content .\output\accounts\robot-notes\automation\last_daily_run_result.json -Raw | ConvertFrom-Json | Select-Object status,message,publish_status,wechat_status,run_dir
Get-Content .\output\accounts\robot-notes\automation\scheduled_status.json -Raw | ConvertFrom-Json | Select-Object overall_status,exit_code,overall_message
```

计划任务的成功通知只应在结果状态确实可接受时发送。草稿、登录失败、发布未确认和平台部分失败会保留为独立状态，不计入公开发布收益归因。
