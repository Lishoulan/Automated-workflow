param(
    [Parameter(Mandatory = $true)][string]$RunnerRoot,
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [ValidateRange(5, 300)][int]$RetrySeconds = 30,
    [switch]$CheckOnly,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
if (-not [System.IO.Path]::IsPathRooted($RunnerRoot)) {
    throw 'RunnerRoot must be absolute.'
}
$RunnerRoot = [System.IO.Path]::GetFullPath($RunnerRoot)
if (-not [System.IO.Path]::IsPathRooted($RunnerRoot) -or
    -not (Test-Path -LiteralPath (Join-Path $RunnerRoot 'run.cmd')) -or
    -not (Test-Path -LiteralPath (Join-Path $RunnerRoot 'bin/Runner.Listener.exe')) -or
    -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'An installed runner and an existing Python executable are required.'
}
$StateDir = Join-Path $RunnerRoot 'supervision'
$PausePath = Join-Path $StateDir 'paused'

function Get-OwnedRunnerProcesses {
    $ListenerPath = Join-Path $RunnerRoot 'bin/Runner.Listener.exe'
    $WorkerPath = Join-Path $RunnerRoot 'bin/Runner.Worker.exe'
    @(Get-CimInstance Win32_Process -Filter "Name='Runner.Listener.exe' OR Name='Runner.Worker.exe'" | Where-Object {
        $_.ExecutablePath -and (
            [string]::Equals([System.IO.Path]::GetFullPath($_.ExecutablePath), $ListenerPath, [System.StringComparison]::OrdinalIgnoreCase) -or
            [string]::Equals([System.IO.Path]::GetFullPath($_.ExecutablePath), $WorkerPath, [System.StringComparison]::OrdinalIgnoreCase)
        )
    })
}

function Write-RunnerState([string]$Status, [int]$Count = 0, $ExitCode = $null) {
    $State = [ordered]@{
        schema = 1; observed_at = [DateTimeOffset]::UtcNow.ToString('o')
        status = $Status; owned_processes = $Count; last_exit_code = $ExitCode
        supervisor_pid = $PID
    }
    $Serialized = $State | ConvertTo-Json -Compress
    if (-not $CheckOnly) {
        $Temporary = Join-Path $StateDir 'status.tmp'
        [System.IO.File]::WriteAllText($Temporary, $Serialized, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $Temporary -Destination (Join-Path $StateDir 'status.json') -Force
    }
    Write-Output $Serialized
}

if ($CheckOnly) {
    $Owned = @(Get-OwnedRunnerProcesses)
    $Status = if (Test-Path -LiteralPath $PausePath) { 'paused' } elseif ($Owned.Count) { 'running' } else { 'stopped' }
    Write-RunnerState $Status $Owned.Count
    exit 0
}

New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
# FileShare.None provides one owner across sessions and releases on process exit.
try {
    $OwnerLock = [System.IO.File]::Open((Join-Path $StateDir 'owner.lock'), [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch [System.IO.IOException] {
    Write-Output '{"status":"already_supervised"}'
    exit 0
}

try {
    $env:PATH = (Split-Path -Parent $PythonPath) + ';C:/Program Files/Git/bin;C:/Program Files/GitHub CLI;' + $env:PATH
    $env:ACTIONS_RUNNER_ACTION_ARCHIVE_CACHE = Join-Path $RunnerRoot 'action-archive-cache'
    # Preserve the working transport setting; inherit system networking unchanged.
    $env:DOTNET_SYSTEM_NET_HTTP_SOCKETSHTTPHANDLER_HTTP2SUPPORT = '0'
    Set-Location -LiteralPath $RunnerRoot
    while ($true) {
        if (Test-Path -LiteralPath $PausePath) {
            Write-RunnerState 'paused'
            break
        }
        $Owned = @(Get-OwnedRunnerProcesses)
        if ($Owned.Count) {
            # An existing listener or orphan worker must finish before a new owner starts.
            Write-RunnerState 'existing_process' $Owned.Count
        } else {
            Write-RunnerState 'starting'
            & (Join-Path $RunnerRoot 'run.cmd') >> (Join-Path $StateDir 'runner.log') 2>&1
            Write-RunnerState 'runner_exited' 0 $LASTEXITCODE
        }
        if ($Once) { break }
        Start-Sleep -Seconds $RetrySeconds
    }
} finally {
    $OwnerLock.Dispose()
}
