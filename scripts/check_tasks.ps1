$patterns = @(
    'ToutiaoContentFactory-*',
    'WechatContentFactory-*'
)

$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $task = $_
    foreach ($pattern in $patterns) {
        if ($task.TaskName -like $pattern) {
            return $true
        }
    }
    return $false
} | Sort-Object TaskName

if (-not $tasks) {
    Write-Host "NO_TASKS_FOUND"
    exit 0
}

foreach ($t in $tasks) {
    $info = Get-ScheduledTaskInfo -TaskName $t.TaskName -ErrorAction SilentlyContinue
    $lastRun = ""
    $nextRun = ""
    $lastResult = 0
    if ($info) {
        if ($info.LastRunTime -and $info.LastRunTime -gt [datetime]::MinValue) {
            $lastRun = $info.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')
        }
        if ($info.NextRunTime -and $info.NextRunTime -gt [datetime]::MinValue) {
            $nextRun = $info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')
        }
        $lastResult = [int64]$info.LastTaskResult
    }
    Write-Host "NAME: $($t.TaskName)"
    Write-Host "STATE: $($t.State.ToString())"
    Write-Host "LAST_RUN: $lastRun"
    Write-Host "NEXT_RUN: $nextRun"
    Write-Host "LAST_RESULT: $lastResult"
    Write-Host "---"
}
