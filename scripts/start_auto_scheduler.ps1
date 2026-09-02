param(
    [string]$Account = $(if ($env:TOUTIAO_ACCOUNT) { $env:TOUTIAO_ACCOUNT } else { "default" }),
    [string]$BindHost = $(if ($env:TOUTIAO_WEB_HOST) { $env:TOUTIAO_WEB_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:TOUTIAO_WEB_PORT) { [int]$env:TOUTIAO_WEB_PORT } else { 8001 }),
    [int]$RestartDelaySeconds = 10,
    [switch]$OpenBrowser,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebAppPath = Join-Path $ProjectRoot "webapp.py"
$EnvPath = Join-Path $ProjectRoot ".env"
$EnvExamplePath = Join-Path $ProjectRoot ".env.example"

function Resolve-PythonExe {
    $candidates = @(
        $env:TOUTIAO_PYTHON,
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        "py"
    ) | Where-Object { $_ -and $_.Trim() }

    foreach ($candidate in $candidates) {
        if ($candidate -eq "py") {
            try {
                & py -3 --version *> $null
                return @("py", "-3")
            }
            catch {
                continue
            }
        }

        if (Test-Path -LiteralPath $candidate) {
            return @($candidate)
        }
    }

    throw "No usable Python interpreter was found. Set TOUTIAO_PYTHON or create .venv\Scripts\python.exe."
}

function Test-PortListening {
    param(
        [string]$BindHost,
        [int]$BindPort
    )

    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($BindHost, $BindPort, $null, $null)
        $wait = $async.AsyncWaitHandle.WaitOne(1000, $false)
        if (-not $wait) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async)
        $client.Close()
        return $true
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $EnvPath) -and (Test-Path -LiteralPath $EnvExamplePath)) {
    Copy-Item -LiteralPath $EnvExamplePath -Destination $EnvPath
    Write-Host "Created .env from .env.example"
}

$pythonCommand = Resolve-PythonExe
$pythonLabel = ($pythonCommand -join " ")
$launchArgs = @($WebAppPath, "--account", $Account, "--host", $BindHost, "--port", "$Port")
$url = "http://${BindHost}:$Port"

Write-Host "Project root: $ProjectRoot"
Write-Host "Python: $pythonLabel"
Write-Host "Account: $Account"
Write-Host "Scheduler URL: $url"
Write-Host "Schedule source: config.yaml -> automation.daily_run_times"
Write-Host "WeChat publish path: Official Account API / backend browser"

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run command:"
    Write-Host ((@($pythonCommand) + @($launchArgs)) -join " ")
    exit 0
}

$env:TOUTIAO_ACCOUNT = $Account
$env:TOUTIAO_WEB_HOST = $BindHost
$env:TOUTIAO_WEB_PORT = "$Port"
$env:WECHAT_EDITOR_135_ENABLED = "0"

if (Test-PortListening -BindHost $BindHost -BindPort $Port) {
    Write-Host "Scheduler web console is already running at $url"
    if ($OpenBrowser) {
        Start-Process $url | Out-Null
    }
    exit 0
}

if ($OpenBrowser) {
    Start-Process $url | Out-Null
}

Set-Location $ProjectRoot

while ($true) {
    $startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host ""
    Write-Host "[$startedAt] Starting scheduler web console..."

    $pythonArgs = @()
    if ($pythonCommand.Length -gt 1) {
        $pythonArgs += $pythonCommand[1..($pythonCommand.Length - 1)]
    }
    $pythonArgs += $launchArgs

    & $pythonCommand[0] @pythonArgs
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0) {
        Write-Host "Scheduler stopped normally."
        break
    }

    $failedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Warning "[$failedAt] Scheduler exited with code $exitCode. Restarting in $RestartDelaySeconds seconds..."
    Start-Sleep -Seconds $RestartDelaySeconds
}
