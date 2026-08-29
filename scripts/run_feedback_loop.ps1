# 浏览量反馈闭环脚本 — 采集真实阅读数据 → 演化学习 → 更新标题/文章 Prompt
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\run_feedback_loop.ps1 -AccountKey robot_notes
param(
    [string]$AccountKey = "default",
    [string]$PythonPath = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Py = if ($PythonPath) {
    $PythonPath
} elseif (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    "python"
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  浏览量反馈闭环 - 账号: $AccountKey" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1) 从创作者后台采集今日/近期发布文章的阅读量、点赞、评论、转发
Write-Host "[1/3] 采集平台真实表现数据..." -ForegroundColor Yellow
& $Py .\main.py --account $AccountKey --collect-performance
if ($LASTEXITCODE -ne 0) {
    Write-Host "采集未完成（可能后台登录态过期或页面结构变化）。将尝试用本地发布记录兜底。" -ForegroundColor DarkYellow
}

# 2) 运行完整反馈闭环：读取表现数据 → EvolutionAgent 提炼规则 → 更新 Prompt 与自对标样本
Write-Host "[2/3] 运行演化学习（feedback-loop）..." -ForegroundColor Yellow
if ($DryRun) {
    Write-Host "（DryRun 跳过 feedback-loop）" -ForegroundColor DarkGray
} else {
    & $Py .\main.py --account $AccountKey --feedback-loop
    if ($LASTEXITCODE -ne 0) {
        Write-Host "feedback-loop 未完全成功，继续生成报告。" -ForegroundColor DarkYellow
    }
}

# 3) 生成表现分析报告（哪些标题/选题/体裁带来了真实阅读）
Write-Host "[3/3] 生成表现分析报告..." -ForegroundColor Yellow
& $Py .\main.py --account $AccountKey --build-performance-report
if ($LASTEXITCODE -ne 0) {
    Write-Host "表现报告生成失败，请查看日志。" -ForegroundColor DarkYellow
}

$ReportPath = Join-Path $ProjectRoot "output\growth_ops\performance\performance_report.md"
if (Test-Path $ReportPath) {
    Write-Host ""
    Write-Host "✅ 反馈闭环完成，报告已生成：" -ForegroundColor Green
    Write-Host "  $ReportPath" -ForegroundColor White
} else {
    $AccountReport = Join-Path $ProjectRoot "output\accounts\$AccountKey\growth_ops\performance\performance_report.md"
    if (Test-Path $AccountReport) {
        Write-Host ""
        Write-Host "✅ 反馈闭环完成，报告已生成：" -ForegroundColor Green
        Write-Host "  $AccountReport" -ForegroundColor White
    } else {
        Write-Host ""
        Write-Host "⚠️ 报告路径未找到，请检查 output/growth_ops/performance/ 目录。" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "提示：浏览量数据通常需要发布 24-48 小时后才有参考价值，建议每周跑 1-2 次。" -ForegroundColor Gray
