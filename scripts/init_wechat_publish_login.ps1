param(
    [string]$AccountKey = "default"
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
    throw "No usable Python interpreter was found. Set TOUTIAO_PYTHON if needed."
}

Write-Host "Using Python: $PythonExe"
Write-Host "Launching WeChat login session initialization..."
& $PythonExe (Join-Path $ProjectRoot "main.py") --account $AccountKey --init-wechat-login
exit $LASTEXITCODE
