param(
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 > $null

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Host "Creating virtual environment..."
        & $PythonCommand -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create .venv with command: $PythonCommand"
        }
    }

    Write-Host "Installing Python dependencies..."
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }

    & $VenvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install requirements."
    }

    Write-Host "Installing Playwright browser..."
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Playwright Chromium."
    }

    if (-not (Test-Path -LiteralPath ".env") -and (Test-Path -LiteralPath ".env.example")) {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example. Fill in your API keys before running."
    }

    Write-Host ""
    Write-Host "Bootstrap completed."
    Write-Host "Virtualenv Python: $VenvPython"
    Write-Host "Next steps:"
    Write-Host "1. Edit .env"
    Write-Host "2. Run: .\\scripts\\init_publish_login.ps1"
    Write-Host "3. Run: .\\scripts\\start_webapp.ps1"
}
finally {
    Pop-Location
}
