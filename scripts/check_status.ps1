# 检查是否有其他 Python 进程在运行
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime, @{Name="CommandLine";Expression={(Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" | Select-Object -ExpandProperty CommandLine)}}

# 检查锁文件是否存在
Write-Host "`n锁文件状态：" -ForegroundColor Cyan
$lockPaths = @(
    "f:\Automated-workflow-main\output\accounts\robot-notes\.publish_session.lock",
    "f:\Automated-workflow-main\output\accounts\robot-notes\.login_session.lock"
)

foreach ($path in $lockPaths) {
    if (Test-Path $path) {
        $file = Get-Item $path
        Write-Host "存在: $path (修改时间: $($file.LastWriteTime))" -ForegroundColor Yellow
    }
    else {
        Write-Host "不存在: $path" -ForegroundColor Green
    }
}
