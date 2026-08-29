param(
    [string]$TaskPrefix = "ToutiaoContentFactory",
    [string]$AccountKey = "default",
    [string]$PythonPath = "",
    [string]$Style = "",
    [string]$ScriptPath = "",
    [switch]$VisibleBrowser,
    [switch]$DryRun,
    [switch]$EnableAutoPublish,
    [switch]$UseEnhancedScript,  # 使用增强版脚本
    [int]$RetryCount = 2,
    [int]$RetryDelaySeconds = 90,
    [string[]]$Times = @("07:00", "09:30", "12:30", "15:30", "18:00", "21:30"),
    [string]$CollectTime = "01:00"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ResolvedScriptPath = if ($ScriptPath) {
    if ([System.IO.Path]::IsPathRooted($ScriptPath)) {
        $ScriptPath
    }
    else {
        Join-Path $ProjectRoot $ScriptPath
    }
}
else {
    if ($UseEnhancedScript.IsPresent) {
        Join-Path $ProjectRoot "scripts\daily_run_enhanced.ps1"
    }
    else {
        Join-Path $ProjectRoot "scripts\daily_run.ps1"
    }
}
$WrapperPath = Join-Path $ProjectRoot "scripts\daily_run_wrapper.vbs"
$AccountSlug = (($AccountKey -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
if ([string]::IsNullOrWhiteSpace($AccountSlug)) {
    $AccountSlug = "default"
}
$IsDefaultAccount = $AccountSlug -eq "default"
$ScheduledLogRoot = if ($IsDefaultAccount) {
    Join-Path $ProjectRoot "logs\scheduled"
}
else {
    Join-Path $ProjectRoot "logs\accounts\$AccountSlug\scheduled"
}
$AutomationRoot = if ($IsDefaultAccount) {
    Join-Path $ProjectRoot "output\automation"
}
else {
    Join-Path $ProjectRoot "output\accounts\$AccountSlug\automation"
}

if (-not (Test-Path -LiteralPath $ResolvedScriptPath)) {
    throw "Script not found: $ResolvedScriptPath"
}
if (-not (Test-Path -LiteralPath $WrapperPath)) {
    throw "Wrapper not found: $WrapperPath"
}

New-Item -ItemType Directory -Path $ScheduledLogRoot -Force | Out-Null
New-Item -ItemType Directory -Path $AutomationRoot -Force | Out-Null

if ($PythonPath) {
    $resolvedPythonPath = $PythonPath
}
else {
    $resolvedPythonPath = ""
}

if ($Style) {
    $resolvedStyle = $Style
}
else {
    $resolvedStyle = ""
}

$actionArguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$ResolvedScriptPath`""
)
if ($UseEnhancedScript.IsPresent) {
    $actionArguments += @(
        "-Account", "`"$AccountKey`"",
        "-RetryCount", ([string][Math]::Max(0, $RetryCount)),
        "-RetryDelaySeconds", ([string][Math]::Max(5, $RetryDelaySeconds))
    )
}
else {
    $actionArguments += @(
        "-AccountKey", "`"$AccountKey`"",
        "-RetryCount", ([string][Math]::Max(0, $RetryCount)),
        "-RetryDelaySeconds", ([string][Math]::Max(5, $RetryDelaySeconds))
    )
}
if ($resolvedPythonPath) {
    $actionArguments += @("-PythonPath", "`"$resolvedPythonPath`"")
}
if ($resolvedStyle) {
    $actionArguments += @("-Style", "`"$resolvedStyle`"")
}
if ($DryRun.IsPresent) {
    $actionArguments += "-DryRun"
}
if ($VisibleBrowser.IsPresent) {
    $actionArguments += "-VisibleBrowser"
}
if ($EnableAutoPublish.IsPresent) {
    $actionArguments += "-EnableAutoPublish"
}

$principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

function Register-InteractiveTask {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,
        [Parameter(Mandatory = $true)]
        [string]$AtTime,
        [string[]]$ExtraArguments = @()
    )

    $fullArguments = @($actionArguments + $ExtraArguments)
    $psArgumentString = ($fullArguments -join " ")
    $taskArguments = "//B //nologo `"$WrapperPath`" $psArgumentString"
    $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $taskArguments -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $AtTime
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    Write-Host "Scheduled task registered: $TaskName at $AtTime"
}

$index = 1
foreach ($time in $Times) {
    $taskName = "$TaskPrefix-$AccountSlug-$index"
    Register-InteractiveTask -TaskName $taskName -AtTime $time
    $index++
}

Register-InteractiveTask `
    -TaskName "$TaskPrefix-$AccountSlug-collect-performance" `
    -AtTime $CollectTime `
    -ExtraArguments @("-CollectPerformanceOnly", "-EnableFeedbackLoop")

Write-Host "Script: $ResolvedScriptPath"
Write-Host "Account: $AccountKey"
Write-Host "Retry policy: $RetryCount retry(s), delay $RetryDelaySeconds second(s)"
Write-Host "Auto publish: $($EnableAutoPublish.IsPresent)"
Write-Host "Scheduled browser mode: $(if ($VisibleBrowser.IsPresent) { 'visible (explicit)' } else { 'visible (default for local desktop tasks)' })"
Write-Host "Task logon mode: Run only when user is logged on (InteractiveToken)"
Write-Host "Desktop note: You may lock the screen, but do not sign out. The 01:00 visible browser must run inside the active desktop session."
if ($UseEnhancedScript.IsPresent) {
    Write-Host "✅ 使用增强版脚本 - 包含网络检测、自动重试和通知"
}
