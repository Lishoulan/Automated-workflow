# 每日互动任务包装脚本（修复 2026-08-27：原注册脚本把 python.exe 直接传给
# PowerShell 的 -File 参数，导致任务必失败；本脚本作为 PowerShell 入口，
# 加载 .env 后调用 python main.py --engagement）
param(
    [string]$AccountKey = "robot_notes",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 > $null
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = (Join-Path (Split-Path -Parent $PSScriptRoot) "src")
$env:TOUTIAO_SCHEDULE_SOURCE = "windows"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AccountSlug = (($AccountKey -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
if ([string]::IsNullOrWhiteSpace($AccountSlug)) {
    $AccountSlug = "default"
}
$IsDefaultAccount = $AccountSlug -eq "default"
$LogRoot = if ($IsDefaultAccount) {
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
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = if ($IsDefaultAccount) {
    Join-Path $LogRoot "engagement_$Timestamp.log"
}
else {
    Join-Path $LogRoot "engagement_${AccountSlug}_$Timestamp.log"
}

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
New-Item -ItemType Directory -Path $AutomationRoot -Force | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "[$(Get-Date -Format s)] $Message"
    Write-Host $line
    $line | Out-File -FilePath $LogFile -Append -Encoding utf8
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }
        $name = $parts[0].Trim()
        $value = $parts[1]
        if (-not $name) {
            continue
        }
        $existingValue = [Environment]::GetEnvironmentVariable($name, "Process")
        if ([string]::IsNullOrWhiteSpace($existingValue)) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

Import-DotEnv -Path (Join-Path $ProjectRoot ".env")

# 解析 python 解释器
$PythonCandidates = @()
if ($PythonPath) {
    $PythonCandidates += $PythonPath
}
if ($env:TOUTIAO_PYTHON) {
    $PythonCandidates += $env:TOUTIAO_PYTHON
}
$PythonCandidates += (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
$PythonCandidates += "python"

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
    throw "No usable Python interpreter was found."
}

Write-Log "Starting daily engagement for account: $AccountKey"
Write-Log "Python: $PythonExe"
Write-Log "Log file: $LogFile"

$engagementArgs = @(
    (Join-Path $ProjectRoot "main.py"),
    "--account", $AccountKey,
    "--engagement"
)

Push-Location $ProjectRoot
try {
    $process = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $engagementArgs `
        -WorkingDirectory $ProjectRoot `
        -NoNewWindow `
        -Wait `
        -PassThru

    if ($process.ExitCode -eq 0) {
        Write-Log "Daily engagement completed successfully (exit 0)."
        exit 0
    }
    else {
        Write-Log "Daily engagement failed with exit code $($process.ExitCode)."
        exit $process.ExitCode
    }
}
catch {
    Write-Log "Daily engagement wrapper error: $_"
    exit 1
}
finally {
    Pop-Location
}
