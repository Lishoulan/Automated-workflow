# 注册每日互动任务（用 wscript 包装，与发布任务一致，保障交互式会话）
# 修复 2026-08-27：原来把 python.exe 直接传给 PowerShell 的 -File（期待 .ps1），
# 任务必失败（Last Result=-196608）。现在指向 engagement_run.ps1 包装脚本。
param(
    [string]$AccountKey = "robot_notes",
    [string]$Time = "10:00",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$ProjectRoot = "F:\Automated-workflow-main"
$AccountSlug = (($AccountKey -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
$WrapperPath = Join-Path $ProjectRoot "scripts\daily_run_wrapper.vbs"
$EngagementScript = Join-Path $ProjectRoot "scripts\engagement_run.ps1"
$TaskName = "ToutiaoEngagement-$AccountSlug"

if (-not (Test-Path -LiteralPath $WrapperPath)) {
    throw "Wrapper not found: $WrapperPath"
}
if (-not (Test-Path -LiteralPath $EngagementScript)) {
    throw "Engagement script not found: $EngagementScript"
}

# 与发布任务同机制：wrapper 执行 powershell -File <engagement_run.ps1> -AccountKey ...
$psArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $EngagementScript + '" -AccountKey ' + $AccountKey
if ($PythonPath) {
    $psArguments += ' -PythonPath "' + $PythonPath + '"'
}
$taskArguments = '//B //nologo "' + $WrapperPath + '" ' + $psArguments

$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $taskArguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "每日互动：给头条同行点赞/评论（AI Content Factory，账号 $AccountKey）" -Force | Out-Null
Write-Host "OK registered: $TaskName (daily $Time)"
Write-Host "Command: $taskArguments"
