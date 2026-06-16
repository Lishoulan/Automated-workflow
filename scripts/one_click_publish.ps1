# 一键发布脚本 — 开电脑后运行一次即可
# 功能：发布头条最新审核通过的文章 + 发布公众号草稿箱

$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
$projectDir = "F:\Automated-workflow-main"

Set-Location $projectDir

Write-Host "===== 一键发布开始 =====" -ForegroundColor Green

# 1. 发布头条（最新审核通过的文章）
Write-Host "`n[1/2] 发布头条..." -ForegroundColor Cyan
& $pythonExe main.py --publish-latest-approved 2>&1

# 2. 发布公众号草稿箱
Write-Host "`n[2/2] 发布公众号草稿箱..." -ForegroundColor Cyan
& $pythonExe main.py --wechat-publish-latest-approved 2>&1

Write-Host "`n===== 一键发布完成 =====" -ForegroundColor Green
Write-Host "请到头条后台和公众号后台确认发布状态" -ForegroundColor Yellow
