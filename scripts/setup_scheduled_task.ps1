param(
    [ValidateSet("install","uninstall","status")]
    [string]$Mode = "install",
    [string]$TaskName = "ToutiaoAutoPublish",
    [string]$RunTime = "09:00"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LocalPublishScript = Join-Path $ScriptDir "local_publish.py"
$LogFile = Join-Path $ProjectRoot ".runtime\logs\task_scheduler.log"

function Write-Log($Message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    Write-Host $logMessage
    $logDir = Split-Path -Parent $LogFile
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $logMessage -Encoding UTF8
}

if ($Mode -eq "install") {
    Write-Log "Installing scheduled task: $TaskName"
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Log "Task exists, removing first..."
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    $pythonPath = $null
    foreach ($cmd in @("py", "python", "python3")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) { $pythonPath = $found.Source; break }
    }
    if (-not $pythonPath) { Write-Error "Python not found. Install Python or add to PATH."; exit 1 }
    Write-Log "Found Python: $pythonPath"
    $pythonwPath = $pythonPath -replace "python\.exe$","pythonw.exe"
    if (-not (Test-Path $pythonwPath)) { $pythonwPath = $pythonPath }
    Write-Log "Using executable: $pythonwPath"
    $taskAction = New-ScheduledTaskAction -Execute $pythonwPath -Argument "`"$LocalPublishScript`"" -WorkingDirectory $ProjectRoot
    $taskTrigger = New-ScheduledTaskTrigger -Daily -At $RunTime
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal -Description "Auto publish to toutiao and wechat" -Force | Out-Null
    Write-Log "Task installed: $TaskName at $RunTime daily"
    Write-Host "Done. Task will run daily at $RunTime" -ForegroundColor Green
}
elseif ($Mode -eq "uninstall") {
    Write-Log "Uninstalling task: $TaskName"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
    Write-Log "Task uninstalled"
    Write-Host "Task removed" -ForegroundColor Green
}
elseif ($Mode -eq "status") {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "Task not found"; return }
    Write-Host "Task: $($task.TaskName)"
    Write-Host "State: $($task.State)"
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($taskInfo) {
        Write-Host "Last Run: $($taskInfo.LastRunTime)"
        Write-Host "Last Result: $($taskInfo.LastTaskResult)"
        Write-Host "Next Run: $($taskInfo.NextRunTime)"
    }
}
