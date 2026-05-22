param(
    [string]$TaskPrefix = "WechatContentFactory",
    [string]$AccountKey = "default",
    [string]$PythonPath = "",
    [string]$Style = "",
    [switch]$VisibleBrowser,
    [switch]$DryRun,
    [switch]$EnableAutoPublish,
    [int]$RetryCount = 2,
    [int]$RetryDelaySeconds = 90,
    [string[]]$Times = @("07:00")
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 > $null

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RegisterScript = Join-Path $ProjectRoot "scripts\register_daily_task.ps1"

if (-not (Test-Path -LiteralPath $RegisterScript)) {
    throw "Script not found: $RegisterScript"
}

$arguments = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $RegisterScript,
    "-TaskPrefix", $TaskPrefix,
    "-AccountKey", $AccountKey,
    "-ScriptPath", "scripts\\wechat_daily_run.ps1",
    "-RetryCount", [string]$RetryCount,
    "-RetryDelaySeconds", [string]$RetryDelaySeconds
)

foreach ($time in $Times) {
    $arguments += @("-Times", $time)
}

if ($PythonPath) {
    $arguments += @("-PythonPath", $PythonPath)
}
if ($Style) {
    $arguments += @("-Style", $Style)
}
if ($DryRun.IsPresent) {
    $arguments += "-DryRun"
}
if ($VisibleBrowser.IsPresent) {
    $arguments += "-VisibleBrowser"
}
if ($EnableAutoPublish.IsPresent) {
    $arguments += "-EnableAutoPublish"
}

& powershell.exe @arguments
exit $LASTEXITCODE
