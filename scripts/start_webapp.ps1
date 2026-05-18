param(
    [string]$Host = $(if ($env:TOUTIAO_WEB_HOST) { $env:TOUTIAO_WEB_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:TOUTIAO_WEB_PORT) { [int]$env:TOUTIAO_WEB_PORT } else { 8000 })
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonCandidates = @(
    $env:TOUTIAO_PYTHON,
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    "C:\Users\jiafeng.zeng\AppData\Local\Python\bin\python.exe",
    "python"
) | Where-Object { $_ -and $_.Trim() }

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
    throw "No usable Python interpreter was found. Set TOUTIAO_PYTHON to python.exe if needed."
}

Write-Host "Using Python: $PythonExe"
Write-Host "Starting web console at http://${Host}:$Port ..."
& $PythonExe (Join-Path $ProjectRoot "webapp.py") --host $Host --port $Port
