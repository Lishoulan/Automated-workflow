param(
    [string]$TaskPrefix = "ToutiaoContentFactory",
    [string]$AccountKey = "default",
    [string]$PythonPath = "",
    [string]$Style = "",
    [string]$ScriptPath = "",
    [switch]$VisibleBrowser,
    [switch]$DryRun,
    [switch]$EnableAutoPublish,
    [int]$RetryCount = 2,
    [int]$RetryDelaySeconds = 90,
    [string[]]$Times = @("08:00", "12:00", "18:30", "22:00")
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 > $null

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
    Join-Path $ProjectRoot "scripts\daily_run.ps1"
}
$WrapperPath = Join-Path $ProjectRoot "scripts\daily_run_wrapper.cmd"
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
    "-File", "`"$ResolvedScriptPath`"",
    "-AccountKey", "`"$AccountKey`"",
    "-RetryCount", ([string][Math]::Max(0, $RetryCount)),
    "-RetryDelaySeconds", ([string][Math]::Max(5, $RetryDelaySeconds))
)
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

$index = 1
foreach ($time in $Times) {
    $taskName = "$TaskPrefix-$AccountSlug-$index"
    $psArgumentString = ($actionArguments -join " ")
    $taskCommand = "`"$WrapperPath`" $psArgumentString"
    $createOutput = schtasks /Create /TN $taskName /SC DAILY /ST $time /TR $taskCommand /F 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register scheduled task $taskName at $time. $createOutput"
    }
    $verifyOutput = schtasks /Query /TN $taskName /FO LIST 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Scheduled task $taskName was not found after creation. $verifyOutput"
    }

    Write-Host "Scheduled task registered: $taskName at $time"
    $index++
}

Write-Host "Script: $ResolvedScriptPath"
Write-Host "Account: $AccountKey"
Write-Host "Retry policy: $RetryCount retry(s), delay $RetryDelaySeconds second(s)"
Write-Host "Auto publish: $($EnableAutoPublish.IsPresent)"
Write-Host "Scheduled browser mode: $(if ($VisibleBrowser.IsPresent) { 'visible' } else { 'headless (recommended)' })"
