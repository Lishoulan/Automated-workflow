# 快捷启动脚本 - 立即运行一次
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  今日头条内容工厂 - 立即运行" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "项目目录：$ProjectRoot" -ForegroundColor Green
Write-Host ""

Set-Location $ProjectRoot

Write-Host "正在运行..." -ForegroundColor Yellow
Write-Host ""

# 尝试多种方式运行
try {
    Write-Host "方式 1：使用 Python" -ForegroundColor Gray
    python .\main.py
    exit
} catch {
    Write-Host "方式 1 失败，尝试方式 2..." -ForegroundColor Yellow
    try {
        py .\main.py
        exit
    } catch {
        Write-Host ""
        Write-Host "请在 PowerShell 中手动运行：" -ForegroundColor Red
        Write-Host "cd $ProjectRoot" -ForegroundColor White
        Write-Host "python .\main.py" -ForegroundColor White
    }
}
