$taskName = "WeChatScheduledPublish"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task: $taskName"
}

$pythonPath = "C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe"
$scriptPath = "f:\Automated-workflow-main\scripts\scheduled_wechat_publish.py"

$wrapperPath = "f:\Automated-workflow-main\scripts\run_wechat_publish.cmd"
$wrapperContent = "@echo off`nstart `"`" /b `"$pythonPath`" `"$scriptPath`""
Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$wrapperPath`"" -WorkingDirectory "f:\Automated-workflow-main"

$trigger = New-ScheduledTaskTrigger -Daily -At "00:00"

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "WeChat auto publish every 3 hours" -Force

$schedule = New-Object -ComObject Schedule.Service
$schedule.Connect()
$folder = $schedule.GetFolder("\")
$task = $folder.GetTask($taskName)
$taskDef = $task.Definition
$triggers = $taskDef.Triggers
$trig = $triggers.Item(1)
$repetition = $trig.Repetition
$repetition.Interval = "PT3H"
$repetition.StopAtDurationEnd = $false
$folder.RegisterTaskDefinition($taskName, $taskDef, 6, $null, $null, 3)

Write-Host "Task registered: $taskName"
Write-Host "Wrapper: $wrapperPath"
Write-Host "Schedule: Every 3 hours, starting 00:00 daily"

$registered = Get-ScheduledTask -TaskName $taskName
$info = $registered | Get-ScheduledTaskInfo
Write-Host "Next run: $($info.NextRunTime)"
