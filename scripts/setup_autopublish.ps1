# Auto-publish on login - install/uninstall toggle
# Run as Administrator

$taskName = "AI-AutoPublish-OnLogin"
$vbsPath  = "F:\Automated-workflow-main\scripts\silent_publish.vbs"

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument """$vbsPath"""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "[REMOVED] Auto-publish task uninstalled" -ForegroundColor Yellow
} else {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Auto publish toutiao+wechat on login (silent)" |
        Out-Null
    Write-Host "[INSTALLED] Auto-publish task created" -ForegroundColor Green
    Write-Host "  Trigger: user $env:USERNAME logon" -ForegroundColor Cyan
    Write-Host "  Mode: background silent (no window)" -ForegroundColor Cyan
    Write-Host "  Run this script again to uninstall" -ForegroundColor DarkGray
}
