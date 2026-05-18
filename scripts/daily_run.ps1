param(
    [string]$AccountKey = "default",
    [string]$PythonPath = "",
    [string]$Style = "",
    [string]$TopicRank = "",
    [string]$TopicTitle = "",
    [switch]$DryRun,
    [switch]$RefreshCache,
    [switch]$NoArchive,
    [switch]$EnableAutoPublish,
    [int]$RetryCount = -1,
    [int]$RetryDelaySeconds = -1
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 > $null

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
    Join-Path $LogRoot "daily_run_$Timestamp.log"
}
else {
    Join-Path $LogRoot "daily_run_${AccountSlug}_$Timestamp.log"
}
$StatusFile = Join-Path $AutomationRoot "scheduled_status.json"
$NotificationsFile = Join-Path $AutomationRoot "notifications.json"

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
New-Item -ItemType Directory -Path $AutomationRoot -Force | Out-Null

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

if ($PythonPath) {
    $env:TOUTIAO_PYTHON = $PythonPath
}
if ($Style) {
    $env:TOUTIAO_STYLE = $Style
}
if ($TopicRank) {
    $env:TOUTIAO_TOPIC_RANK = $TopicRank
}
if ($TopicTitle) {
    $env:TOUTIAO_TOPIC_TITLE = $TopicTitle
}
if ($DryRun.IsPresent) {
    $env:TOUTIAO_DRY_RUN = "1"
}
if ($RefreshCache.IsPresent) {
    $env:TOUTIAO_REFRESH_CACHE = "1"
}
if ($NoArchive.IsPresent) {
    $env:TOUTIAO_NO_ARCHIVE = "1"
}
if ($EnableAutoPublish.IsPresent) {
    $env:TOUTIAO_PUBLISH_ENABLED = "1"
    $env:TOUTIAO_AUTO_PUBLISH = "1"
}
if ($RetryCount -ge 0) {
    $env:TOUTIAO_RETRY_COUNT = [string]$RetryCount
}
if ($RetryDelaySeconds -ge 0) {
    $env:TOUTIAO_RETRY_DELAY_SECONDS = [string]$RetryDelaySeconds
}

function Write-Log {
    param([string]$Message)

    $line = "[$(Get-Date -Format s)] $Message"
    Write-Host $line
    $line | Out-File -FilePath $LogFile -Append -Encoding utf8
}

function Read-JsonFile {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if (-not $raw) {
        return $null
    }

    try {
        return $raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Write-JsonFile {
    param(
        [string]$Path,
        [Parameter(Mandatory = $true)]
        $Data
    )

    if ($Data -is [System.Collections.IEnumerable] -and -not ($Data -is [string]) -and -not ($Data -is [hashtable])) {
        $json = @($Data) | ConvertTo-Json -Depth 8
    }
    else {
        $json = $Data | ConvertTo-Json -Depth 8
    }

    Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
}

function Add-Notification {
    param(
        [string]$Level,
        [string]$Title,
        [string]$Message,
        [hashtable]$Data = @{}
    )

    $existing = Read-JsonFile -Path $NotificationsFile
    $items = @()
    if ($existing) {
        $items = @($existing)
    }

    $entry = [ordered]@{
        created_at = (Get-Date -Format s)
        level = $Level
        title = $Title
        message = $Message
        data = $Data
    }

    $items = @($entry) + $items
    if ($items.Count -gt 50) {
        $items = $items | Select-Object -First 50
    }

    Write-JsonFile -Path $NotificationsFile -Data $items
}

function Update-Status {
    param(
        [string]$CurrentState,
        [int]$Attempt,
        [int]$MaxAttempts,
        [int]$ExitCode,
        [string]$Message,
        $Extra = @{}
    )

    $current = Read-JsonFile -Path $StatusFile
    $payload = [ordered]@{
        updated_at = (Get-Date -Format s)
        status = $CurrentState
        attempt = $Attempt
        max_attempts = $MaxAttempts
        exit_code = $ExitCode
        message = $Message
        log_file = $LogFile
        account_key = $AccountKey
        account_slug = $AccountSlug
        style = $env:TOUTIAO_STYLE
        retry_delay_seconds = $script:RetryDelaySeconds
        auto_publish = ($env:TOUTIAO_AUTO_PUBLISH -eq "1")
        publish_enabled = ($env:TOUTIAO_PUBLISH_ENABLED -eq "1")
    }

    if ($current -and $current.PSObject.Properties.Name -contains "last_success_at") {
        $payload["last_success_at"] = $current.last_success_at
    }

    if ($current -and $current.PSObject.Properties.Name -contains "last_failure_at") {
        $payload["last_failure_at"] = $current.last_failure_at
    }

    if ($CurrentState -eq "success" -or $CurrentState -eq "review_required") {
        $payload["last_success_at"] = Get-Date -Format s
    }

    if ($CurrentState -eq "failed") {
        $payload["last_failure_at"] = Get-Date -Format s
    }

    if ($Extra -is [System.Collections.IDictionary]) {
        foreach ($item in $Extra.GetEnumerator()) {
            $payload[[string]$item.Key] = $item.Value
        }
    }

    Write-JsonFile -Path $StatusFile -Data $payload
}

function Get-LatestRunSummary {
    $runsRoot = if ($IsDefaultAccount) {
        Join-Path $ProjectRoot "output\runs"
    }
    else {
        Join-Path $ProjectRoot "output\accounts\$AccountSlug\runs"
    }
    if (-not (Test-Path -LiteralPath $runsRoot)) {
        return @{}
    }

    $latestRun = Get-ChildItem -Path $runsRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $latestRun) {
        return @{}
    }

    $reviewStatus = Read-JsonFile -Path (Join-Path $latestRun.FullName "review_status.json")
    $publishResult = Read-JsonFile -Path (Join-Path $latestRun.FullName "publish\publish_result.json")

    $backend = @{}
    if ($publishResult -and $publishResult.backend_response_summary) {
        $backend = $publishResult.backend_response_summary
    }

    return @{
        run_name = $latestRun.Name
        run_dir = $latestRun.FullName
        review_status = if ($reviewStatus) { [string]$reviewStatus.status } else { "" }
        selected_title = if ($reviewStatus) { [string]$reviewStatus.selected_title } else { "" }
        publish_status = if ($publishResult) { [string]$publishResult.status } else { "" }
        publish_url = if ($publishResult) { [string]$publishResult.external_url } else { "" }
        publish_message = if ($backend.message) { [string]$backend.message } else { "" }
        publish_pgc_id = if ($backend.pgc_id) { [string]$backend.pgc_id } else { "" }
    }
}

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

$arguments = @((Join-Path $ProjectRoot "main.py"), "--daily-run", "--account", $AccountKey)

if ($env:TOUTIAO_STYLE) {
    $arguments += @("--style", $env:TOUTIAO_STYLE)
}

if ($env:TOUTIAO_TOPIC_RANK) {
    $arguments += @("--topic-rank", $env:TOUTIAO_TOPIC_RANK)
}

if ($env:TOUTIAO_TOPIC_TITLE) {
    $arguments += @("--topic-title", $env:TOUTIAO_TOPIC_TITLE)
}

if ($env:TOUTIAO_DRY_RUN -eq "1") {
    $arguments += "--dry-run"
}

if ($env:TOUTIAO_REFRESH_CACHE -eq "1") {
    $arguments += "--refresh-cache"
}

if ($env:TOUTIAO_NO_ARCHIVE -eq "1") {
    $arguments += "--no-archive"
}

$script:RetryCount = 2
if ($env:TOUTIAO_RETRY_COUNT) {
    $parsedRetryCount = 0
    if ([int]::TryParse($env:TOUTIAO_RETRY_COUNT, [ref]$parsedRetryCount)) {
        $script:RetryCount = [Math]::Max(0, $parsedRetryCount)
    }
}

$script:RetryDelaySeconds = 90
if ($env:TOUTIAO_RETRY_DELAY_SECONDS) {
    $parsedRetryDelaySeconds = 0
    if ([int]::TryParse($env:TOUTIAO_RETRY_DELAY_SECONDS, [ref]$parsedRetryDelaySeconds)) {
        $script:RetryDelaySeconds = [Math]::Max(5, $parsedRetryDelaySeconds)
    }
}

$maxAttempts = $script:RetryCount + 1
$exitCode = 1
$lastRunSummary = @{}

Write-Log "Using Python: $PythonExe"
Write-Log "Account: $AccountKey"
Write-Log "Running scheduled daily pipeline."
Write-Log "Auto publish enabled: $($env:TOUTIAO_AUTO_PUBLISH -eq '1')"
Write-Log "Publishing enabled: $($env:TOUTIAO_PUBLISH_ENABLED -eq '1')"
Write-Log "Image backend: $($env:IMAGE_BACKEND)"
Write-Log "Image api mode: $($env:IMAGE_API_MODE)"
Write-Log "Image max assets: $($env:IMAGE_MAX_ASSETS)"
Write-Log "Log file: $LogFile"
Write-Log "Retry policy: up to $maxAttempts attempt(s), delay $script:RetryDelaySeconds second(s)."

Push-Location $ProjectRoot
try {
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        Update-Status -CurrentState "running" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message "Scheduled pipeline is running."
        Write-Log "Attempt $attempt of $maxAttempts started."

        $stdOutFile = Join-Path $AutomationRoot "stdout_$Timestamp`_$attempt.log"
        $stdErrFile = Join-Path $AutomationRoot "stderr_$Timestamp`_$attempt.log"
        try {
            $process = Start-Process `
                -FilePath $PythonExe `
                -ArgumentList $arguments `
                -WorkingDirectory $ProjectRoot `
                -NoNewWindow `
                -Wait `
                -PassThru `
                -RedirectStandardOutput $stdOutFile `
                -RedirectStandardError $stdErrFile
            $exitCode = $process.ExitCode
        }
        finally {
            foreach ($captureFile in @($stdOutFile, $stdErrFile)) {
                if (-not (Test-Path -LiteralPath $captureFile)) {
                    continue
                }
                Get-Content -LiteralPath $captureFile -Encoding UTF8 -ErrorAction SilentlyContinue |
                    ForEach-Object { $_ | Out-File -FilePath $LogFile -Append -Encoding utf8 }
                Remove-Item -LiteralPath $captureFile -Force -ErrorAction SilentlyContinue
            }
        }

        $lastRunSummary = Get-LatestRunSummary

        if ($exitCode -eq 0) {
            $reviewStatus = [string]$lastRunSummary["review_status"]
            $publishStatus = [string]$lastRunSummary["publish_status"]

            if ($publishStatus) {
                $message = "Scheduled pipeline succeeded. Publish status: $publishStatus."
                Update-Status -CurrentState "success" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode $exitCode -Message $message -Extra $lastRunSummary
                Add-Notification -Level "info" -Title "Scheduled publish completed" -Message $message -Data $lastRunSummary
            }
            elseif ($reviewStatus -and $reviewStatus -ne "approved") {
                $message = "Scheduled pipeline succeeded, but this run still requires manual review."
                Update-Status -CurrentState "review_required" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode $exitCode -Message $message -Extra $lastRunSummary
                Add-Notification -Level "warn" -Title "Manual review required" -Message $message -Data $lastRunSummary
            }
            elseif (($env:TOUTIAO_AUTO_PUBLISH -eq "1") -and ($env:TOUTIAO_PUBLISH_ENABLED -eq "1")) {
                $message = "Scheduled pipeline completed without a publish result even though auto-publish is enabled."
                Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                Add-Notification -Level "error" -Title "Scheduled publish missing" -Message $message -Data $lastRunSummary
                Write-Log $message
                $exitCode = 2
            }
            else {
                $message = "Scheduled pipeline succeeded."
                Update-Status -CurrentState "success" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode $exitCode -Message $message -Extra $lastRunSummary
                Add-Notification -Level "info" -Title "Scheduled pipeline succeeded" -Message $message -Data $lastRunSummary
            }

            if ($exitCode -eq 0) {
                Write-Log "Attempt $attempt succeeded."
                break
            }
        }

        if ($attempt -lt $maxAttempts) {
            $message = "Attempt $attempt failed with exit code $exitCode. Retrying in $script:RetryDelaySeconds seconds."
            Update-Status -CurrentState "retrying" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode $exitCode -Message $message -Extra $lastRunSummary
            Add-Notification -Level "warn" -Title "Scheduled pipeline retrying" -Message $message -Data $lastRunSummary
            Write-Log $message
            Start-Sleep -Seconds $script:RetryDelaySeconds
            continue
        }

        $message = "Scheduled pipeline failed after $maxAttempts attempt(s)."
        Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode $exitCode -Message $message -Extra $lastRunSummary
        Add-Notification -Level "error" -Title "Scheduled pipeline failed" -Message $message -Data $lastRunSummary
        Write-Log $message
    }
}
finally {
    Pop-Location
}

Write-Log "Exit code: $exitCode"
exit $exitCode
