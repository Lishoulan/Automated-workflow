param(
    [string]$AccountKey = "default",
    [string]$PythonPath = "",
    [string]$Style = "",
    [string]$TopicRank = "",
    [string]$TopicTitle = "",
    [switch]$DryRun,
    [switch]$RefreshCache,
    [switch]$NoArchive,
    [switch]$EnableAutoPublish,
    [int]$RetryCount = -1,
    [int]$RetryDelaySeconds = -1
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 > $null

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DelegateScript = Join-Path $ProjectRoot "scripts\daily_run.ps1"

if (-not (Test-Path -LiteralPath $DelegateScript)) {
    throw "Script not found: $DelegateScript"
}

$env:SCHEDULE_WECHAT_ENABLED = "1"
$env:TARGET_PLATFORM = "wechat"
$env:TOUTIAO_PUBLISH_ENABLED = "0"
$env:TOUTIAO_AUTO_PUBLISH = "0"
$env:WECHAT_ENABLED = "1"
$env:WECHAT_AUTO_PUBLISH = if ($EnableAutoPublish.IsPresent) { "1" } else { "0" }

$arguments = @{
    AccountKey = $AccountKey
}
if ($PythonPath) {
    $arguments["PythonPath"] = $PythonPath
}
if ($Style) {
    $arguments["Style"] = $Style
}
if ($TopicRank) {
    $arguments["TopicRank"] = $TopicRank
}
if ($TopicTitle) {
    $arguments["TopicTitle"] = $TopicTitle
}
if ($DryRun.IsPresent) {
    $arguments["DryRun"] = $true
}
if ($RefreshCache.IsPresent) {
    $arguments["RefreshCache"] = $true
}
if ($NoArchive.IsPresent) {
    $arguments["NoArchive"] = $true
}
if ($RetryCount -ge 0) {
    $arguments["RetryCount"] = $RetryCount
}
if ($RetryDelaySeconds -ge 0) {
    $arguments["RetryDelaySeconds"] = $RetryDelaySeconds
}

Write-Host "Running WeChat-only scheduled workflow."
Write-Host "Account: $AccountKey"
Write-Host "Target platform override: $($env:TARGET_PLATFORM)"
Write-Host "WeChat auto publish: $($env:WECHAT_AUTO_PUBLISH -eq '1')"
Write-Host "Toutiao publish forced off for this run."

& $DelegateScript @arguments
exit $LASTEXITCODE
