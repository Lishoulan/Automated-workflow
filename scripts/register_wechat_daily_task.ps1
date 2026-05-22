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

$invokeParameters = @{
    TaskPrefix = $TaskPrefix
    AccountKey = $AccountKey
    ScriptPath = "scripts\\wechat_daily_run.ps1"
    RetryCount = $RetryCount
    RetryDelaySeconds = $RetryDelaySeconds
}

if ($Times -and $Times.Count -gt 0) {
    $invokeParameters["Times"] = $Times
}
if ($PythonPath) {
    $invokeParameters["PythonPath"] = $PythonPath
}
if ($Style) {
    $invokeParameters["Style"] = $Style
}
if ($DryRun.IsPresent) {
    $invokeParameters["DryRun"] = $true
}
if ($VisibleBrowser.IsPresent) {
    $invokeParameters["VisibleBrowser"] = $true
}
if ($EnableAutoPublish.IsPresent) {
    $invokeParameters["EnableAutoPublish"] = $true
}

& $RegisterScript @invokeParameters
exit $LASTEXITCODE
