Write-Host "=== SCHEDULED TASKS (non-Microsoft) ===" -ForegroundColor Yellow
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '*Microsoft*' } | ForEach-Object {
    $task = $_
    $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -ErrorAction SilentlyContinue
    [PSCustomObject]@{
        TaskName    = $task.TaskName
        State       = $task.State
        Path        = $task.TaskPath
        LastRun     = if ($info.LastRunTime -gt [datetime]::MinValue) { $info.LastRunTime.ToString('yyyy-MM-dd HH:mm') } else { 'Never' }
        NextRun     = if ($info.NextRunTime -gt [datetime]::MinValue) { $info.NextRunTime.ToString('yyyy-MM-dd HH:mm') } else { 'N/A' }
    }
} | Format-Table -AutoSize
