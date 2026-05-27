# Quick configuration script
# Run this script with Administrator privileges in PowerShell

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Toutiao Content Factory - Quick Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Get script directory and project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "INFO Script directory: $ScriptDir" -ForegroundColor Gray
Write-Host "INFO Project root: $ProjectRoot" -ForegroundColor Gray

Set-Location $ProjectRoot
Write-Host "OK Project directory set to: $ProjectRoot" -ForegroundColor Green
Write-Host ""

# 2. Check .env file
if (Test-Path ".env") {
    Write-Host "OK .env file already exists" -ForegroundColor Green
} else {
    Write-Host "WARN .env file not found, copying from .env.example..." -ForegroundColor Yellow
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "OK .env file created" -ForegroundColor Green
    } else {
        Write-Host "ERROR .env.example not found!" -ForegroundColor Red
    }
}
Write-Host ""

# 3. Remove old tasks
Write-Host "INFO Checking existing scheduled tasks..." -ForegroundColor Cyan
try {
    $tasks = Get-ScheduledTask | Where-Object { $_.TaskName -like "*Toutiao*" -or $_.TaskName -like "*ContentFactory*" }
    if ($tasks) {
        foreach ($task in $tasks) {
            Write-Host "Removing task: $($task.TaskName)" -ForegroundColor Yellow
            Unregister-ScheduledTask -TaskName $task.TaskName -Confirm:$false
        }
    } else {
        Write-Host "No existing tasks found" -ForegroundColor Gray
    }
} catch {
    Write-Host "Error removing tasks: $_" -ForegroundColor Red
}
Write-Host ""

# 4. Register new task (using enhanced script)
Write-Host "INFO Registering new scheduled task (enhanced version)..." -ForegroundColor Cyan
$registerScript = Join-Path $ScriptDir "register_daily_task.ps1"
Write-Host "INFO Register script path: $registerScript" -ForegroundColor Gray

if (Test-Path $registerScript) {
    & $registerScript -EnableAutoPublish -UseEnhancedScript
} else {
    Write-Host "ERROR register_daily_task.ps1 not found at: $registerScript" -ForegroundColor Red
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Green
Write-Host "  Task registration completed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps to configure manually:" -ForegroundColor Yellow
Write-Host "1. Edit .env and fill in your API keys" -ForegroundColor White
Write-Host "2. If you want Toutiao + WeChat in one scheduled workflow, set:" -ForegroundColor White
Write-Host "   TARGET_PLATFORM=all" -ForegroundColor Gray
Write-Host "   TOUTIAO_PUBLISH_ENABLED=1" -ForegroundColor Gray
Write-Host "   TOUTIAO_AUTO_PUBLISH=1" -ForegroundColor Gray
Write-Host "   WECHAT_ENABLED=1" -ForegroundColor Gray
Write-Host "   WECHAT_AUTO_PUBLISH=1" -ForegroundColor Gray
Write-Host "   WECHAT_APP_ID=..." -ForegroundColor Gray
Write-Host "   WECHAT_APP_SECRET=..." -ForegroundColor Gray
Write-Host "3. Run .\scripts\init_publish_login.ps1 once to initialize both login sessions" -ForegroundColor White
Write-Host "4. Open Windows Task Scheduler, find Toutiao tasks" -ForegroundColor White
Write-Host "   -> Right click -> Properties" -ForegroundColor White
Write-Host "   -> Conditions -> Check 'Wake the computer to run this task'" -ForegroundColor White
Write-Host "   -> Settings -> Check 'If the task fails, restart'" -ForegroundColor White
Write-Host "      Interval: 15 minutes, Maximum attempts: 3" -ForegroundColor White
Write-Host ""
Write-Host "If Toutiao publish returns code 2010 / no publish permission, switch to the correct Creator Center account and verify manual publishing works first." -ForegroundColor Yellow
Write-Host ""
Write-Host "For more details, see OPTIMIZATION_GUIDE.md" -ForegroundColor Cyan
