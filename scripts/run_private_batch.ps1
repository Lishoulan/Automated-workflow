<#
.SYNOPSIS
    Run ONE batch (default 40 items) of "set works to private only" for a Toutiao account.

.DESCRIPTION
    Why this wrapper exists:
      The DSH automation session runs pwsh inside a sandbox that forbids starting ANY
      child process. Verified 2026-09-19 with four independent mechanisms:
          & cmd.exe /c "echo hi"                      -> Program 'cmd.exe' failed to run: Access is denied
          Start-Process cmd.exe ...                   -> This command cannot be run due to the error: Access is denied.
          [System.Diagnostics.Process]::Start(...)     -> Access is denied
          .venv\Scripts\python.exe --version           -> Program 'python.exe' failed to run: Access is denied
      Clearing HTTP_PROXY/http_proxy first changes nothing, so it is not an env problem:
      process creation itself is blocked (approval prompts are disabled, so no wider mode
      could be requested, and the attempt was auto-rejected).
      Confirmed again 2026-09-19 15:0x: & cmd.exe, & python.exe (relative AND absolute),
      Start-Process and Process::Start are ALL denied, while workspace FILE writes and all
      read-only cmdlets still work -- so the missing capability is specifically process
      launch, not disk access.
      A NORMAL PowerShell window has none of these restrictions, so run this file there.

    It also fixes three things that have bitten this workflow before:
      1. Proxy env vars (HTTP_PROXY / http_proxy / ...) make Playwright route through a
         local proxy and can break the session on mp.toutiao.com -> removed below.
      2. PYTHONUTF8=1 keeps the Chinese log text readable.
      3. python -u plus byte-level redirection into .runtime/private_batch.log so progress
         is visible while it runs (buffered output otherwise looks like a hang).
         The redirection goes through cmd.exe on purpose: PowerShell 5.1 would otherwise
         transcode the native output and turn the Chinese lines into mojibake.

    Progress is recorded per page + row in .runtime/visibility_progress.json, so stopping
    and re-running only skips what is already confirmed done. Repeat until it reports
    private >= 160.

.PARAMETER Account
    Account key from config.yaml. Default robot_notes (renamed to the social-story track).

.PARAMETER DryRun
    Only list what would be processed; changes nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_private_batch.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_private_batch.ps1 -StartPage 5 -Limit 40

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_private_batch.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Account  = 'robot_notes',
    [int]$StartPage   = 1,
    [int]$Limit       = 40,
    [double]$MinDelay = 1.5,
    [double]$MaxDelay = 3.0,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "Virtualenv interpreter not found: $python" }

$target = Join-Path $root 'scripts\set_works_private.py'
if (-not (Test-Path $target)) { throw "Target script not found: $target" }

# 1) a local proxy hijacks the request to mp.toutiao.com and sinks the login state
foreach ($name in 'HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','NO_PROXY','no_proxy','ALL_PROXY','all_proxy') {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
# 2) keep the Chinese log readable
$env:PYTHONUTF8 = '1'

$logDir = Join-Path $root '.runtime'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'private_batch.log'

$inv = [System.Globalization.CultureInfo]::InvariantCulture
$pyArgs = @(
    '-u', $target,
    '--account', $Account,
    '--start-page', $StartPage.ToString($inv),
    '--limit', $Limit.ToString($inv),
    '--min-delay', $MinDelay.ToString($inv),
    '--max-delay', $MaxDelay.ToString($inv)
)
if ($DryRun) { $pyArgs += '--dry-run' }

# 3) byte-level redirect: python writes UTF-8, cmd.exe does not re-encode it
$quoted = ($pyArgs | ForEach-Object { '"' + $_ + '"' }) -join ' '
$cmdline = '"{0}" {1} > "{2}" 2>&1' -f $python, $quoted, $log

Write-Host "account=$Account startPage=$StartPage limit=$Limit delay=$MinDelay-$MaxDelay dryRun=$DryRun" -ForegroundColor Cyan
Write-Host "log -> $log"
if (-not $DryRun) {
    Write-Host "roughly 20-40 minutes; to watch progress live run in another window:" -ForegroundColor DarkGray
    Write-Host "    Get-Content .runtime\private_batch.log -Wait -Encoding UTF8" -ForegroundColor DarkGray
}

$proc = Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -ArgumentList '/c', $cmdline -NoNewWindow -Wait -PassThru
$code = $proc.ExitCode

Write-Host ""
Write-Host "exit code = $code" -ForegroundColor Yellow
if ($code -eq 2) {
    Write-Host "exit 2 = NOT LOGGED IN: log in to mp.toutiao.com in the robot-notes profile first." -ForegroundColor Red
}

& $python -c "import json;from pathlib import Path;d=json.loads(Path('.runtime/visibility_progress.json').read_text(encoding='utf-8')).get('done',{});p=sum(1 for v in d.values() if v=='private');f=sum(1 for v in d.values() if v=='failed');print('private=%d failed=%d total=%d' % (p,f,len(d)));print('DONE: cleanup finished, this automation can be paused.' if p>=160 else 'still short of 160 by %d' % (160-p))"

Write-Host ""
Write-Host "--- last interesting log lines ---" -ForegroundColor Cyan
Select-String -Path $log -Pattern '\[OK|\[FAIL|ERROR|=====|====' -Encoding UTF8 |
    Select-Object -Last 20 | ForEach-Object { $_.Line }
