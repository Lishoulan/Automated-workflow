# 一键发布脚本 — 开电脑后运行一次即可
# 功能：发布公众号草稿箱（头条已在云端自动发布）

$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
$projectDir = "F:\Automated-workflow-main"

Set-Location $projectDir

Write-Host "===== 公众号发布开始 =====" -ForegroundColor Green

# 发布公众号草稿箱
Write-Host "发布公众号草稿箱..." -ForegroundColor Cyan
& $pythonExe main.py --wechat-publish-latest-approved 2>&1

Write-Host "`n===== 公众号发布完成 =====" -ForegroundColor Green
Write-Host "请到公众号后台确认发布状态" -ForegroundColor Yellow
