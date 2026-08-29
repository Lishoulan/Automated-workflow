# 注册「每周浏览量反馈闭环」Windows 计划任务
# 用法：powershell -ExecutionPolicy Bypass -File .\scripts\register_feedback_task.ps1 -AccountKey robot_notes
param(
    [string]$AccountKey = "default",
    [string]$TaskName = "",
    [string]$PythonPath = "",
    [string]$DayOfWeek = "SUNDAY",
    [string]$Time = "10:00",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AccountSlug = (($AccountKey -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
if ([string]::IsNullOrWhiteSpace($AccountSlug)) { $AccountSlug = "default" }

$ResolvedTaskName = if ($TaskName) {
    $TaskName
} else {
    "ToutiaoFeedbackLoop-$AccountSlug"
}

$Py = if ($PythonPath) {
    $PythonPath
} elseif (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    "python"
}

$ScriptPath = Join-Path $ProjectRoot "scripts\run_feedback_loop.ps1"
$WrapperPath = Join-Path $ProjectRoot "scripts\daily_run_wrapper.vbs"

$Arguments = "-ExecutionPolicy Bypass -File `"$ScriptPath`" -AccountKey $AccountKey"

if ($DryRun) {
    Write-Host "DryRun：将注册任务：$ResolvedTaskName"
    Write-Host "  Python: $Py"
    Write-Host "  脚本:   $ScriptPath"
    Write-Host "  参数:   $Arguments"
    Write-Host "  时间:   $DayOfWeek $Time"
    exit 0
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $ResolvedTaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "每周采集头条浏览量数据并运行演化学习（AI Content Factory 反馈闭环，账号 $AccountKey）" -Force
    Write-Host "✅ 已注册计划任务：$ResolvedTaskName（每周 $DayOfWeek $Time）" -ForegroundColor Green
    Write-Host "运行 `schtasks /Query /TN $ResolvedTaskName` 可查看。" -ForegroundColor Gray
} catch {
    Write-Host "注册失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "请用管理员 PowerShell 重试，或使用 schtasks 手动注册。" -ForegroundColor Yellow
    exit 1
}
