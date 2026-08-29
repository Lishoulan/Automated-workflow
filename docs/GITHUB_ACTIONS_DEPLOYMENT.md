# GitHub Actions 云端自动发布

本项目使用 GitHub Actions 作为云端执行器。电脑关机不会影响 GitHub 的定时任务；任务在 GitHub 的临时 Linux Runner 上运行。

## 当前部署结构

- 定时入口：`.github/workflows/daily-run.yml`
- 云端入口：`scripts/cloud_daily_run.py`
- 默认账号：`CLOUD_DEFAULT_ACCOUNT`
- 登录态：私有 Release `browser-profiles-v1`
- 运行产物：GitHub Actions 的 `daily-output-*` Artifact

GitHub 的定时任务可能因平台负载延迟几分钟，不能保证精确到秒。如果需要固定公网 IP、稳定浏览器会话或精确时间，应改用长期 VPS 或自托管 Runner。

## 一次性配置

在仓库 `Settings -> Secrets and variables -> Actions` 中确认：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `CLOUD_DEFAULT_ACCOUNT`，例如 `robot_notes`
- `CLOUD_TOUTIAO_AUTOPUBLISH=true`

只有需要同步 135 编辑器到微信公众号时，才设置：

- `CLOUD_WECHAT_VIA_135_AUTOPUBLISH=true`
- `CLOUD_WECHAT_135_ENABLED=1`

仓库必须保持为 Private。浏览器 profile 和 Cookie 是登录凭据，不能提交到代码仓库或公开 Release。

## 刷新登录态

在本机完成头条和 135 编辑器登录后，在项目根目录运行：

```powershell
.\scripts\deploy_cloud.ps1 -Account robot_notes -Headless
```

脚本会使用项目 `.venv`，刷新本地会话、导出 Cookie、打包 profile，并上传到私有 Release。登录态失效时重新登录本机后再次运行即可。

## 首次验证

在 GitHub 的 `Actions -> Daily Run -> Run workflow` 手动验证：

- `dry_run=false`
- `fast_mode=true`
- `publish_toutiao=true`
- `publish_wechat_via_135=false`，除非 135 登录态已确认有效

查看任务摘要中的 `Enforce publish result`，并下载 `daily-output-*` 检查：

- `output/accounts/robot-notes/automation/last_daily_run_result.json`
- `output/accounts/robot-notes/automation/scheduled_status.json`
- `logs/scheduled/`

现在只要配置要求自动发布而平台实际失败，workflow 会标记失败并报警，不会再把“只生成了文章”显示为成功。
