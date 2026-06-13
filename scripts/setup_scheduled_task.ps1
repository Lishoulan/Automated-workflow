# Windows任务计划程序配置脚本
# 用于设置定时发布任务

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("install", "uninstall", "status")]
    [string]$Action = "install",
    
    [Parameter(Mandatory=$false)]
    [string]$TaskName = "ToutiaoAutoPublish",
    
    [Parameter(Mandatory=$false)]
    [string]$RunTime = "09:00"
)

$ErrorActionPreference = "Stop"

# 获取脚本所在目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LocalPublishScript = Join-Path $ScriptDir "local_publish.py"
$LogFile = Join-Path $ProjectRoot ".runtime\logs\task_scheduler.log"

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    Write-Host $logMessage
    
    # 确保日志目录存在
    $logDir = Split-Path -Parent $LogFile
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    
    Add-Content -Path $LogFile -Value $logMessage -Encoding UTF8
}

function Install-ScheduledTask {
    Write-Log "开始安装定时任务: $TaskName"
    
    # 检查任务是否已存在
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Log "任务已存在，正在删除..."
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    
    # 解析运行时间
    try {
        $timeParts = $RunTime.Split(":")
        $hour = [int]$timeParts[0]
        $minute = [int]$timeParts[1]
    } catch {
        Write-Log "错误: 时间格式不正确，请使用 HH:MM 格式（例如 09:00）"
        return $false
    }
    
    # 创建任务操作
    # 使用 pythonw.exe 后台运行，不显示窗口
    $pythonPath = (Get-Command python).Source
    $pythonwPath = $pythonPath -replace "python\.exe$", "pythonw.exe"
    
    if (-not (Test-Path $pythonwPath)) {
        Write-Log "警告: 未找到 pythonw.exe，使用 python.exe 代替"
        $pythonwPath = $pythonPath
    }
    
    $action = New-ScheduledTaskAction `
        -Execute $pythonwPath `
        -Argument "`"$LocalPublishScript`"" `
        -WorkingDirectory $ProjectRoot
    
    # 创建触发器（每天指定时间）
    $trigger = New-ScheduledTaskTrigger -Daily -At $RunTime
    
    # 配置任务设置
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    
    # 创建任务主体（使用当前用户）
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    
    # 注册任务
    try {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -Principal $principal `
            -Description "头条自动发布任务 - 从云端下载内容并发布到头条和公众号" `
            -Force | Out-Null
        
        Write-Log "定时任务安装成功"
        Write-Log "任务名称: $TaskName"
        Write-Log "运行时间: 每天 $RunTime"
        Write-Log "工作目录: $ProjectRoot"
        Write-Log "发布脚本: $LocalPublishScript"
        
        return $true
    } catch {
        Write-Log "错误: 安装任务失败 - $($_.Exception.Message)"
        return $false
    }
}

function Uninstall-ScheduledTask {
    Write-Log "开始卸载定时任务: $TaskName"
    
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Log "任务不存在"
        return $true
    }
    
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Log "定时任务卸载成功"
        return $true
    } catch {
        Write-Log "错误: 卸载任务失败 - $($_.Exception.Message)"
        return $false
    }
}

function Show-TaskStatus {
    Write-Log "查询任务状态: $TaskName"
    
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Log "任务不存在"
        return
    }
    
    Write-Log "任务名称: $($task.TaskName)"
    Write-Log "任务状态: $($task.State)"
    Write-Log "描述: $($task.Description)"
    
    # 显示触发器信息
    foreach ($trigger in $task.Triggers) {
        if ($trigger -is [Microsoft.PowerShell.Cmdletization.GeneratedTypes.ScheduledTask.TimeTrigger]) {
            Write-Log "触发时间: 每天 $($trigger.StartBoundary)"
        }
    }
    
    # 显示最近运行结果
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($taskInfo) {
        Write-Log "上次运行时间: $($taskInfo.LastRunTime)"
        Write-Log "上次运行结果: $($taskInfo.LastTaskResult)"
        Write-Log "下次运行时间: $($taskInfo.NextRunTime)"
    }
}

# 主逻辑
switch ($Action) {
    "install" {
        $success = Install-ScheduledTask
        if ($success) {
            Write-Host "`n提示: 任务已配置为后台运行，不会影响正常使用" -ForegroundColor Green
            Write-Host "日志位置: $LogFile" -ForegroundColor Gray
        } else {
            Write-Host "`n安装失败，请检查错误信息" -ForegroundColor Red
            exit 1
        }
    }
    
    "uninstall" {
        $success = Uninstall-ScheduledTask
        if ($success) {
            Write-Host "`n任务已卸载" -ForegroundColor Green
        } else {
            Write-Host "`n卸载失败" -ForegroundColor Red
            exit 1
        }
    }
    
    "status" {
        Show-TaskStatus
    }
}
