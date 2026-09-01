param(
    [string]$TaskPrefix = "ToutiaoContentFactory",
    [string[]]$AccountKeys = @(),
    [string]$PythonPath = "",
    [string]$Style = "",
    [switch]$DryRun,
    [switch]$EnableAutoPublish,
    [int]$RetryCount = 2,
    [int]$RetryDelaySeconds = 90,
    [string[]]$Times = @("08:00", "12:00", "18:30"),
    [string]$DailyReportTime = "22:00"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 > $null

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RegisterScript = Join-Path $ProjectRoot "scripts\register_daily_task.ps1"
$ConfigPath = Join-Path $ProjectRoot "config.yaml"
$PythonCandidates = @(
    $PythonPath,
    $env:TOUTIAO_PYTHON,
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    "python"
) | Where-Object { $_ -and $_.Trim() }

if (-not (Test-Path -LiteralPath $RegisterScript)) {
    throw "Script not found: $RegisterScript"
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Config not found: $ConfigPath"
}

$PythonExe = $null
foreach ($candidate in $PythonCandidates) {
    if ($candidate -eq "python") {
        try {
            $null = & python --version
            $PythonExe = "python"
            break
        }
        catch {
            continue
        }
    }

    if (Test-Path -LiteralPath $candidate) {
        $PythonExe = $candidate
        break
    }
}

if (-not $PythonExe) {
    throw "No usable Python interpreter was found. Set TOUTIAO_PYTHON or pass -PythonPath if needed."
}

$pythonCommand = @"
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path('src').resolve()))
from toutiao_content_factory.config import _load_config_payload  # type: ignore
payload = _load_config_payload(Path('config.yaml'))
accounts = payload.get('accounts', {}) if isinstance(payload, dict) else {}
keys = ['default']
if isinstance(accounts, dict):
    for key, meta in accounts.items():
        key = str(key).strip()
        if not key or key in keys:
            continue
        enabled = True
        if isinstance(meta, dict):
            enabled = bool(meta.get('enabled', True))
        if enabled:
            keys.append(key)
print(json.dumps(keys, ensure_ascii=False))
"@

$accountsJson = $pythonCommand | & $PythonExe -
$accounts = $accountsJson | ConvertFrom-Json

if ($AccountKeys -and $AccountKeys.Count -gt 0) {
    $requestedKeys = @(
        $AccountKeys |
        Where-Object { $_ -and $_.Trim() } |
        ForEach-Object { $_ -split "," } |
        Where-Object { $_ -and $_.Trim() } |
        ForEach-Object { $_.Trim() }
    )
    if ($requestedKeys.Count -eq 0) {
        throw "AccountKeys was provided, but no valid account key was found."
    }

    $availableKeys = @($accounts | ForEach-Object { [string]$_ })
    $missingKeys = @($requestedKeys | Where-Object { $_ -notin $availableKeys })
    if ($missingKeys.Count -gt 0) {
        throw "Unknown account key(s): $($missingKeys -join ', '). Available accounts: $($availableKeys -join ', ')"
    }

    $accounts = @($availableKeys | Where-Object { $_ -in $requestedKeys })
}

foreach ($accountKey in $accounts) {
    Write-Host "Registering scheduled tasks for account: $accountKey"
    $invokeParameters = @{
        TaskPrefix = $TaskPrefix
        AccountKey = [string]$accountKey
        RetryCount = $RetryCount
        RetryDelaySeconds = $RetryDelaySeconds
        DailyReportTime = $DailyReportTime
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
    if ($EnableAutoPublish.IsPresent) {
        $invokeParameters["EnableAutoPublish"] = $true
    }

    & $RegisterScript @invokeParameters
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register scheduled tasks for account: $accountKey"
    }
}

Write-Host "All account tasks registered."
