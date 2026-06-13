# ????????
$ErrorActionPreference = "Stop"
$ProjectRoot = "F:\Automated-workflow-main"
$PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
$PipelineScript = "$ProjectRoot\scripts\standalone_pipeline.py"

# ?????????
$Accounts = @{
    "default"      = @("07:00", "11:30", "18:00", "22:00")
    "energy_notes" = @("07:00", "11:30", "18:00", "22:00")
    "robot_notes"  = @("07:00", "11:30", "18:00", "22:00")
}

# ???/???????
$oldTasks = @(
    "\ToutiaoContentFactory-default-1","\ToutiaoContentFactory-default-2","\ToutiaoContentFactory-default-3","\ToutiaoContentFactory-default-4","\ToutiaoContentFactory-default-collect-performance",
    "\ToutiaoContentFactory-robot-notes-1","\ToutiaoContentFactory-robot-notes-2","\ToutiaoContentFactory-robot-notes-3","\ToutiaoContentFactory-robot-notes-4","\ToutiaoContentFactory-robot-notes-collect-performance",
    "\ToutiaoAuto_energy_notes_0715","\ToutiaoAuto_energy_notes_1145","\ToutiaoAuto_energy_notes_1815","\ToutiaoAuto_energy_notes_2215",
    "\ToutiaoAuto_robot_notes_0700","\ToutiaoAuto_robot_notes_1130","\ToutiaoAuto_robot_notes_1800","\ToutiaoAuto_robot_notes_2200",
    "\Toutiao_Default_0800","\Toutiao_Default_1200","\Toutiao_Default_1830","\Toutiao_Default_2200",
    "\Toutiao_Intl_0730","\Toutiao_Intl_1130","\Toutiao_Intl_1800","\Toutiao_Intl_2130"
)

foreach ($task in $oldTasks) {
    $name = $task.Split("\")[-1]
    try { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue } catch {}
    Write-Host "Removed old task: $name"
}

Write-Host "`nRegistering new tasks..."

# ???????
$index = 1
foreach ($account in $Accounts.Keys) {
    foreach ($time in $Accounts[$account]) {
        $hour, $minute = $time -split ":"
        $taskName = "ShouLan_${account}_$index"
        
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument @"
-NoProfile -ExecutionPolicy Bypass -File "$ProjectRoot\scripts\scheduled_task_runner.ps1" -AccountKey $account
"@ -WorkingDirectory $ProjectRoot
        
        $trigger = New-ScheduledTaskTrigger -Daily -At $time
        $principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Write-Host "  [+] $taskName => $time (account=$account)"
        $index++
    }
    $index = 1
}

Write-Host "`nAll tasks registered!"
Write-Host "`nNext runs:"
Get-ScheduledTask -TaskName "ShouLan_*" | Where-Object State -ne Disabled | Sort-Object TaskName | ForEach-Object {
    $info = $_ | Get-ScheduledTaskInfo
    Write-Host ("  {0,-30} next={1}" -f $_.TaskName, $info.NextRunTime)
}
