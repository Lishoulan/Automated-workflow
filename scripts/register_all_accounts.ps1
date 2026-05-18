param(
    [string]$TaskPrefix = "ToutiaoContentFactory",
    [string]$PythonPath = "",
    [string]$Style = "",
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
    for key in accounts:
        key = str(key).strip()
        if key and key not in keys:
            keys.append(key)
print(json.dumps(keys, ensure_ascii=False))
"@

$accountsJson = $pythonCommand | & $PythonExe -
$accounts = $accountsJson | ConvertFrom-Json

foreach ($accountKey in $accounts) {
    $arguments = @(
        "-ExecutionPolicy", "Bypass",
        "-File", $RegisterScript,
        "-TaskPrefix", $TaskPrefix,
        "-AccountKey", [string]$accountKey,
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
    if ($EnableAutoPublish.IsPresent) {
        $arguments += "-EnableAutoPublish"
    }

    Write-Host "Registering scheduled tasks for account: $accountKey"
    & powershell.exe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register scheduled tasks for account: $accountKey"
    }
}

Write-Host "All account tasks registered."
