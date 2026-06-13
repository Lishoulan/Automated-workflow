$ErrorActionPreference = "Continue"

Write-Host "正在清理锁文件..." -ForegroundColor Cyan

$lockFiles = @(
    "f:\Automated-workflow-main\output\accounts\robot-notes\.publish_session.lock",
    "f:\Automated-workflow-main\output\accounts\robot-notes\.login_session.lock",
    "f:\Automated-workflow-main\output\accounts\energy-notes\.publish_session.lock",
    "f:\Automated-workflow-main\output\accounts\energy-notes\.login_session.lock",
    "f:\Automated-workflow-main\output\accounts\default\.publish_session.lock",
    "f:\Automated-workflow-main\output\accounts\default\.login_session.lock"
)

foreach ($lockFile in $lockFiles) {
    if (Test-Path $lockFile) {
        try {
            Remove-Item $lockFile -Force -ErrorAction Stop
            Write-Host "已删除: $lockFile" -ForegroundColor Green
        }
        catch {
            Write-Host "无法删除 $lockFile - 可能被其他进程占用" -ForegroundColor Yellow
        }
    }
}

Write-Host "`n查找并删除所有 .lock 文件..." -ForegroundColor Cyan
Get-ChildItem "f:\Automated-workflow-main" -Recurse -Filter "*.lock" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        Remove-Item $_.FullName -Force -ErrorAction Stop
        Write-Host "已删除: $($_.FullName)" -ForegroundColor Green
    }
    catch {
        Write-Host "无法删除 $($_.FullName) - 可能被其他进程占用" -ForegroundColor Yellow
    }
}

Write-Host "`n清理完成！" -ForegroundColor Cyan
Write-Host "下一步：运行测试命令" -ForegroundColor White
Write-Host "python main.py --account robot_notes --daily-run --fast-mode" -ForegroundColor Yellow