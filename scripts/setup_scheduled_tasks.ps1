$ErrorActionPreference = "Stop"
$PROJECT_ROOT = "f:\Automated-workflow-main"
$PYTHON_EXE = "$PROJECT_ROOT\.venv\Scripts\python.exe"

if (-not (Test-Path $PYTHON_EXE)) {
    Write-Host "[ERROR] Python not found: $PYTHON_EXE"
    exit 1
}

if (-not (Test-Path "$PROJECT_ROOT\main.py")) {
    Write-Host "[ERROR] Project directory incorrect: $PROJECT_ROOT"
    exit 1
}

Write-Host "======================================"
Write-Host "  Multi-account Task Setup"
Write-Host "======================================"
Write-Host "Project: $PROJECT_ROOT"
Write-Host "Python:   $PYTHON_EXE"
Write-Host ""

$accounts = @(
    @{
        Key = "robot_notes"
        Label = "RobotNotes"
        Times = @("07:00", "11:30", "18:00", "22:00")
    },
    @{
        Key = "energy_notes"
        Label = "EnergyNotes"
        Times = @("07:15", "11:45", "18:15", "22:15")
    }
)

$created = 0
$skipped = 0

foreach ($account in $accounts) {
    $accountKey = $account.Key
    $accountLabel = $account.Label
    $taskNamePrefix = "ToutiaoAuto_${accountKey}_"

    Write-Host "`nConfiguring account: $accountLabel ($accountKey)"
    Write-Host "--------------------------------------"

    foreach ($time in $account.Times) {
        $taskName = "${taskNamePrefix}$($time -replace ':', '')"

        Write-Host "  Task: $taskName @ $time"

        $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existingTask) {
            Write-Host "    [Clean] Removing old task"
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }

        $trigger = New-ScheduledTaskTrigger -Daily -At $time

        $argument = "--account `"$accountKey`" --daily-run --fast-mode"

        $action = New-ScheduledTaskAction -Execute $PYTHON_EXE -Argument $argument -WorkingDirectory $PROJECT_ROOT

        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -DontStopOnIdleEnd `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2)

        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

        try {
            Register-ScheduledTask `
                -TaskName $taskName `
                -Description "Toutiao $accountLabel - $time daily auto publish" `
                -Trigger $trigger `
                -Action $action `
                -Settings $settings `
                -Principal $principal `
                -Force | Out-Null

            Write-Host "    [OK] Task created"
            $created++
        } catch {
            Write-Host "    [FAIL] Could not create task: $_"
            $skipped++
        }
    }
}

Write-Host "`n======================================"
Write-Host "  Setup Complete"
Write-Host "======================================"
Write-Host "Created: $created"
Write-Host "Skipped/Failed: $skipped"
Write-Host ""

Write-Host "Current task list:"
$allTasks = Get-ScheduledTask | Where-Object { $_.TaskName -like "ToutiaoAuto_*" }
foreach ($task in $allTasks) {
    $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -ErrorAction SilentlyContinue
    $nextRun = if ($info.NextRunTime) { $info.NextRunTime.ToString("yyyy-MM-dd HH:mm") } else { "Not scheduled" }
    Write-Host "  [$nextRun] $($task.TaskName)"
}
