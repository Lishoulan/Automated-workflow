param(
    [string]$AccountKey = "default",
    [string]$PythonPath = "",
    [string]$Style = "",
    [string]$TopicRank = "",
    [string]$TopicTitle = "",
    [switch]$VisibleBrowser,
    [switch]$CollectPerformanceOnly,
    [switch]$DryRun,
    [switch]$RefreshCache,
    [switch]$NoArchive,
    [switch]$EnableAutoPublish,
    [switch]$SendDailyReport,
    [int]$RetryCount = -1,
    [int]$RetryDelaySeconds = -1
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
    Join-Path $LogRoot "daily_run_$Timestamp.log"
}
else {
    Join-Path $LogRoot "daily_run_${AccountSlug}_$Timestamp.log"
}
$StatusFile = Join-Path $AutomationRoot "scheduled_status.json"
$NotificationsFile = Join-Path $AutomationRoot "notifications.json"
$RunResultFile = Join-Path $AutomationRoot "last_daily_run_result.json"

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

function Get-ScopedEnvName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseName
    )

    $slug = ($AccountSlug -replace "-", "_").ToUpper()
    return "${BaseName}__${slug}"
}

function Set-ScopedProcessEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseName,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $scopedName = Get-ScopedEnvName -BaseName $BaseName
    [Environment]::SetEnvironmentVariable($scopedName, $Value, "Process")
}

function Normalize-TargetPlatform {
    param([string]$Value)

    $normalized = ([string]$Value).Trim().ToLower()
    if (-not $normalized) {
        return "toutiao"
    }
    if ($normalized -in @("wechat", "weixin", "official_account")) {
        return "wechat"
    }
    if ($normalized -in @("toutiao")) {
        return "toutiao"
    }
    if ($normalized -in @("douyin")) {
        return "douyin"
    }
    if ($normalized -in @("all", "multi")) {
        return "all"
    }
    return "toutiao"
}

function Apply-TargetPlatformOverrides {
    param([string]$TargetPlatform)

    $resolvedTarget = Normalize-TargetPlatform -Value $TargetPlatform
    $env:TARGET_PLATFORM = $resolvedTarget
    Set-ScopedProcessEnv -BaseName "TARGET_PLATFORM" -Value $resolvedTarget

    switch ($resolvedTarget) {
        "toutiao" {
            $env:WECHAT_ENABLED = "0"
            $env:WECHAT_AUTO_PUBLISH = "0"
            $env:DOUYIN_ENABLED = "0"
            $env:DOUYIN_AUTO_PUBLISH = "0"
            Set-ScopedProcessEnv -BaseName "WECHAT_ENABLED" -Value "0"
            Set-ScopedProcessEnv -BaseName "WECHAT_AUTO_PUBLISH" -Value "0"
            Set-ScopedProcessEnv -BaseName "DOUYIN_ENABLED" -Value "0"
            Set-ScopedProcessEnv -BaseName "DOUYIN_AUTO_PUBLISH" -Value "0"
        }
        "wechat" {
            $env:TOUTIAO_PUBLISH_ENABLED = "0"
            $env:TOUTIAO_AUTO_PUBLISH = "0"
            $env:WECHAT_ENABLED = if ($env:WECHAT_ENABLED -eq "1") { "1" } else { "1" }
            $env:WECHAT_AUTO_PUBLISH = if ($env:WECHAT_AUTO_PUBLISH -eq "1") { "1" } else { "0" }
            $env:DOUYIN_ENABLED = "0"
            $env:DOUYIN_AUTO_PUBLISH = "0"
            Set-ScopedProcessEnv -BaseName "WECHAT_ENABLED" -Value $env:WECHAT_ENABLED
            Set-ScopedProcessEnv -BaseName "WECHAT_AUTO_PUBLISH" -Value $env:WECHAT_AUTO_PUBLISH
            Set-ScopedProcessEnv -BaseName "DOUYIN_ENABLED" -Value "0"
            Set-ScopedProcessEnv -BaseName "DOUYIN_AUTO_PUBLISH" -Value "0"
        }
        "douyin" {
            $env:TOUTIAO_PUBLISH_ENABLED = "0"
            $env:TOUTIAO_AUTO_PUBLISH = "0"
            $env:WECHAT_ENABLED = "0"
            $env:WECHAT_AUTO_PUBLISH = "0"
            $env:DOUYIN_ENABLED = if ($env:DOUYIN_ENABLED -eq "1") { "1" } else { "1" }
            $env:DOUYIN_AUTO_PUBLISH = if ($env:DOUYIN_AUTO_PUBLISH -eq "1") { "1" } else { "0" }
            Set-ScopedProcessEnv -BaseName "WECHAT_ENABLED" -Value "0"
            Set-ScopedProcessEnv -BaseName "WECHAT_AUTO_PUBLISH" -Value "0"
            Set-ScopedProcessEnv -BaseName "DOUYIN_ENABLED" -Value $env:DOUYIN_ENABLED
            Set-ScopedProcessEnv -BaseName "DOUYIN_AUTO_PUBLISH" -Value $env:DOUYIN_AUTO_PUBLISH
        }
        default {
            Set-ScopedProcessEnv -BaseName "WECHAT_ENABLED" -Value $env:WECHAT_ENABLED
            Set-ScopedProcessEnv -BaseName "WECHAT_AUTO_PUBLISH" -Value $env:WECHAT_AUTO_PUBLISH
            Set-ScopedProcessEnv -BaseName "DOUYIN_ENABLED" -Value $env:DOUYIN_ENABLED
            Set-ScopedProcessEnv -BaseName "DOUYIN_AUTO_PUBLISH" -Value $env:DOUYIN_AUTO_PUBLISH
        }
    }

    return $resolvedTarget
}

$ResolvedTargetPlatform = Apply-TargetPlatformOverrides -TargetPlatform $env:TARGET_PLATFORM

if ([string]::IsNullOrWhiteSpace($env:SCHEDULE_WECHAT_ENABLED)) {
    $scopedWechatEnabled = [Environment]::GetEnvironmentVariable("WECHAT_ENABLED__" + $AccountSlug.Replace("-", "_").ToUpper(), "Process")
    $scopedWechatAuto = [Environment]::GetEnvironmentVariable("WECHAT_AUTO_PUBLISH__" + $AccountSlug.Replace("-", "_").ToUpper(), "Process")
    if ([string]::IsNullOrWhiteSpace($scopedWechatEnabled) -and [string]::IsNullOrWhiteSpace($env:WECHAT_ENABLED)) {
        $env:WECHAT_ENABLED = "0"
    }
    if ([string]::IsNullOrWhiteSpace($scopedWechatAuto) -and [string]::IsNullOrWhiteSpace($env:WECHAT_AUTO_PUBLISH)) {
        $env:WECHAT_AUTO_PUBLISH = "0"
    }
}

$scheduledVisibleBrowser = $VisibleBrowser.IsPresent -or ($env:TOUTIAO_SCHEDULE_VISIBLE_BROWSER -eq "1")
if ($scheduledVisibleBrowser) {
    $env:TOUTIAO_PUBLISH_HEADLESS = "0"
}
else {
    $env:TOUTIAO_PUBLISH_HEADLESS = "0"
}

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

function Convert-ToHashtable {
    param($Value)

    $result = @{}
    if (-not $Value) {
        return $result
    }

    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($item in $Value.GetEnumerator()) {
            $result[[string]$item.Key] = $item.Value
        }
        return $result
    }

    foreach ($property in $Value.PSObject.Properties) {
        $result[[string]$property.Name] = $property.Value
    }
    return $result
}

function Merge-Hashtables {
    param(
        $Base,
        $Override
    )

    $merged = Convert-ToHashtable -Value $Base
    $overrideTable = Convert-ToHashtable -Value $Override
    foreach ($item in $overrideTable.GetEnumerator()) {
        $merged[[string]$item.Key] = $item.Value
    }
    return $merged
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

function Convert-ToPlatformState {
    param([string]$State)

    $normalized = ([string]$State).Trim().ToLower()
    switch ($normalized) {
        "running" { return "running" }
        "success" { return "success" }
        "completed" { return "success" }
        "published" { return "success" }
        "submitted" { return "success" }
        "submitted_for_review" { return "success" }
        "draft_created" { return "success" }
        "draft_saved" { return "success" }
        "approved" { return "success" }
        "review_required" { return "failed" }
        "publish_login_required" { return "failed" }
        "publish_uncertain" { return "failed" }
        "partial_success" { return "partial_success" }
        "already_published" { return "success" }
        "draft_only" { return "success" }
        "blocked" { return "failed" }
        "failed" { return "failed" }
        "retrying" { return "running" }
        "no_topics" { return "idle" }
        default { return "idle" }
    }
}

function Build-PlatformStatusMap {
    param(
        [string]$CurrentState,
        [string]$Message,
        [hashtable]$Extra,
        [object]$Current,
        [string[]]$ActiveTargets
    )

    $platforms = [ordered]@{}
    $existingPlatforms = @{}
    if ($Current -and $Current.PSObject.Properties.Name -contains "platforms") {
        $existingPlatforms = Convert-ToHashtable -Value $Current.platforms
    }

    foreach ($platform in @("toutiao", "wechat", "douyin", "weitoutiao")) {
        $previous = @{}
        if ($existingPlatforms.ContainsKey($platform)) {
            $previous = Convert-ToHashtable -Value $existingPlatforms[$platform]
        }
        $platforms[$platform] = [ordered]@{
            status = if ($previous.ContainsKey("status")) { [string]$previous["status"] } else { "idle" }
            last_run = if ($previous.ContainsKey("last_run")) { [string]$previous["last_run"] } else { "" }
            message = if ($previous.ContainsKey("message")) { [string]$previous["message"] } else { "" }
        }
    }

    $now = Get-Date -Format s
    $state = Convert-ToPlatformState -State $CurrentState
    $extraTable = Convert-ToHashtable -Value $Extra

    if ($ActiveTargets -contains "toutiao") {
        $toutiaoMessage = if ($extraTable.ContainsKey("publish_message") -and [string]$extraTable["publish_message"]) {
            [string]$extraTable["publish_message"]
        } elseif ($Message) {
            $Message
        } else {
            "Toutiao task updated."
        }
        if ($extraTable.ContainsKey("publish_status") -and [string]$extraTable["publish_status"]) {
            $state = Convert-ToPlatformState -State ([string]$extraTable["publish_status"])
        }
        $platforms["toutiao"] = [ordered]@{
            status = $state
            last_run = $now
            message = $toutiaoMessage
        }
    }

    if ($ActiveTargets -contains "wechat") {
        $wechatState = if ($extraTable.ContainsKey("wechat_status") -and [string]$extraTable["wechat_status"]) {
            Convert-ToPlatformState -State ([string]$extraTable["wechat_status"])
        } elseif ($CurrentState -eq "blocked" -and $Message -like "*wechat*") {
            "failed"
        } else {
            $state
        }
        $wechatMessage = if ($extraTable.ContainsKey("wechat_message") -and [string]$extraTable["wechat_message"]) {
            [string]$extraTable["wechat_message"]
        } elseif ($Message) {
            $Message
        } else {
            "WeChat task updated."
        }
        $platforms["wechat"] = [ordered]@{
            status = $wechatState
            last_run = $now
            message = $wechatMessage
        }
    }

    if ($ActiveTargets -contains "douyin") {
        $douyinState = if ($extraTable.ContainsKey("douyin_status") -and [string]$extraTable["douyin_status"]) {
            Convert-ToPlatformState -State ([string]$extraTable["douyin_status"])
        } else {
            $state
        }
        $douyinMessage = if ($extraTable.ContainsKey("douyin_message") -and [string]$extraTable["douyin_message"]) {
            [string]$extraTable["douyin_message"]
        } elseif ($Message) {
            $Message
        } else {
            "Douyin task updated."
        }
        $platforms["douyin"] = [ordered]@{
            status = $douyinState
            last_run = $now
            message = $douyinMessage
        }
    }

    if ($extraTable.ContainsKey("weitoutiao_status") -and [string]$extraTable["weitoutiao_status"]) {
        $weitoutiaoState = Convert-ToPlatformState -State ([string]$extraTable["weitoutiao_status"])
        $weitoutiaoMessage = if ($extraTable.ContainsKey("weitoutiao_message") -and [string]$extraTable["weitoutiao_message"]) {
            [string]$extraTable["weitoutiao_message"]
        } elseif ($Message) {
            $Message
        } else {
            "WeiToutiao task updated."
        }
        $platforms["weitoutiao"] = [ordered]@{
            status = $weitoutiaoState
            last_run = $now
            message = $weitoutiaoMessage
        }
    }

    return $platforms
}

function Resolve-OverallStatus {
    param([hashtable]$Platforms)

    $values = @($Platforms.Values | ForEach-Object { ([string]$_.status).Trim().ToLower() })
    if ($values -contains "running") {
        return "running"
    }
    if ($values -contains "partial_success") {
        return "partial_success"
    }
    $successCount = @($values | Where-Object { $_ -eq "success" }).Count
    $failedCount = @($values | Where-Object { $_ -eq "failed" }).Count
    if ($successCount -gt 0 -and $failedCount -gt 0) {
        return "partial_success"
    }
    if ($failedCount -gt 0) {
        return "failed"
    }
    if ($successCount -gt 0) {
        return "success"
    }
    return "idle"
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
    $currentTable = Convert-ToHashtable -Value $current
    $extraTable = Convert-ToHashtable -Value $Extra
    $platformTargets = if ($extraTable.ContainsKey("active_platform_targets")) {
        @([string[]]$extraTable["active_platform_targets"])
    } else {
        @(Get-ActivePlatformTargets)
    }
    $platforms = Build-PlatformStatusMap -CurrentState $CurrentState -Message $Message -Extra $extraTable -Current $current -ActiveTargets $platformTargets
    $overallStatus = Resolve-OverallStatus -Platforms $platforms
    $now = Get-Date -Format s

    $payload = [ordered]@{
        updated_at = $now
        last_run_time = $now
        last_heartbeat = $now
        pid = $PID
        status = $CurrentState
        overall_status = $overallStatus
        overall_message = $Message
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
        platforms = $platforms
    }

    if ($currentTable.ContainsKey("last_success_at")) {
        $payload["last_success_at"] = $currentTable["last_success_at"]
    }

    if ($currentTable.ContainsKey("last_failure_at")) {
        $payload["last_failure_at"] = $currentTable["last_failure_at"]
    }

    if ($overallStatus -in @("success", "partial_success")) {
        $payload["last_success_at"] = $now
    }

    if ($overallStatus -eq "failed") {
        $payload["last_failure_at"] = $now
    }

    foreach ($item in $extraTable.GetEnumerator()) {
        $payload[[string]$item.Key] = $item.Value
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
    $wechatPublishResult = Read-JsonFile -Path (Join-Path $latestRun.FullName "publish\wechat\wechat_publish_result.json")
    $douyinPublishResult = Read-JsonFile -Path (Join-Path $latestRun.FullName "publish\douyin\douyin_publish_result.json")
    $workflowReport = Read-JsonFile -Path (Join-Path $AutomationRoot "last_daily_workflow_report.json")

    $backend = @{}
    if ($publishResult -and $publishResult.backend_response_summary) {
        $backend = $publishResult.backend_response_summary
    }

    $wechatStatus = ""
    $wechatSubmitStatus = ""
    $wechatPublishUrl = ""
    $wechatDraftMediaId = ""
    if ($wechatPublishResult) {
        $wechatStatus = [string]$wechatPublishResult.status
        $wechatSubmitStatus = [string]$wechatPublishResult.submit_status
        $wechatPublishUrl = [string]$wechatPublishResult.external_url
        $wechatDraftMediaId = [string]$wechatPublishResult.draft_media_id
        if (-not $wechatStatus) {
            if ($wechatSubmitStatus -or $wechatPublishUrl) {
                $wechatStatus = "submitted"
            }
            elseif ($wechatDraftMediaId) {
                $wechatStatus = "draft_created"
            }
        }
    }

    $douyinStatus = ""
    $douyinPublishUrl = ""
    if ($douyinPublishResult) {
        $douyinStatus = [string]$douyinPublishResult.status
        $douyinPublishUrl = [string]$douyinPublishResult.external_url
        if (-not $douyinStatus -and $douyinPublishUrl) {
            $douyinStatus = "published"
        }
    }

    $workflowErrors = @()
    if ($workflowReport -and $workflowReport.final_attempt -and $workflowReport.final_attempt.platform_errors) {
        $workflowErrors = @($workflowReport.final_attempt.platform_errors)
    }

    return @{
        run_name = $latestRun.Name
        run_dir = $latestRun.FullName
        workflow_status = if ($workflowReport) { [string]$workflowReport.status } else { "" }
        workflow_errors = $workflowErrors
        review_status = if ($reviewStatus) { [string]$reviewStatus.status } else { "" }
        selected_title = if ($reviewStatus) { [string]$reviewStatus.selected_title } else { "" }
        publish_status = if ($publishResult) { [string]$publishResult.status } else { "" }
        publish_url = if ($publishResult) { [string]$publishResult.external_url } else { "" }
        publish_message = if ($backend.message) { [string]$backend.message } else { "" }
        publish_pgc_id = if ($backend.pgc_id) { [string]$backend.pgc_id } else { "" }
        wechat_status = $wechatStatus
        wechat_submit_status = $wechatSubmitStatus
        wechat_publish_url = $wechatPublishUrl
        wechat_draft_media_id = $wechatDraftMediaId
        douyin_status = $douyinStatus
        douyin_publish_url = $douyinPublishUrl
    }
}

function Run-HealthPreflight {
    Write-Log "Running platform health preflight before scheduled workflow."
    $healthScript = @"
from pathlib import Path
import sys

project_root = Path(r'$ProjectRoot')
sys.path.insert(0, str(project_root / 'src'))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.app_service import ContentFactoryAppService
from toutiao_content_factory.utils.logging_utils import configure_logging

settings = load_settings(project_root, account_key=r'$AccountKey')
logger = configure_logging(settings.logs_dir)
app_service = ContentFactoryAppService(settings, logger)
result = app_service.collect_platform_health()
for name, value in result.items():
    print(f"{name}: {value}")
"@
    $healthScriptPath = Join-Path $AutomationRoot "health_preflight_$Timestamp.py"
    Set-Content -LiteralPath $healthScriptPath -Value $healthScript -Encoding UTF8
    $healthArgs = @($healthScriptPath)
    $process = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $healthArgs `
        -WorkingDirectory $ProjectRoot `
        -NoNewWindow `
        -Wait `
        -PassThru

    Remove-Item -LiteralPath $healthScriptPath -Force -ErrorAction SilentlyContinue

    $healthPath = Join-Path $AutomationRoot "platform_health.json"
    $healthPayload = Read-JsonFile -Path $healthPath
    $blockingFlags = @()
    if ($healthPayload -and $healthPayload.blocking_flags) {
        $blockingFlags = @($healthPayload.blocking_flags)
    }

    return @{
        ExitCode = $process.ExitCode
        HealthPath = $healthPath
        BlockingFlags = $blockingFlags
    }
}

function Get-ActivePlatformTargets {
    $targets = @()
    if ($env:TOUTIAO_PUBLISH_ENABLED -eq "1" -and $env:TOUTIAO_AUTO_PUBLISH -eq "1") {
        $targets += "toutiao"
    }
    if ($env:WECHAT_ENABLED -eq "1" -and $env:WECHAT_AUTO_PUBLISH -eq "1") {
        $targets += "wechat"
    }
    if ($env:DOUYIN_ENABLED -eq "1" -and $env:DOUYIN_AUTO_PUBLISH -eq "1") {
        $targets += "douyin"
    }
    return @($targets | Select-Object -Unique)
}

function Filter-RelevantBlockingFlags {
    param(
        [string[]]$BlockingFlags,
        [string[]]$ActiveTargets
    )

    if (-not $BlockingFlags -or $BlockingFlags.Count -eq 0) {
        return @()
    }

    if (-not $ActiveTargets -or $ActiveTargets.Count -eq 0) {
        return @()
    }

    $relevant = @()
    foreach ($flag in $BlockingFlags) {
        $text = [string]$flag
        $matchedTarget = $false
        foreach ($target in $ActiveTargets) {
            if ($text.StartsWith($target + ":", [System.StringComparison]::OrdinalIgnoreCase)) {
                $relevant += $text
                $matchedTarget = $true
                break
            }
        }
        if (-not $matchedTarget -and $text -notmatch '^[a-z0-9_-]+:') {
            $relevant += $text
        }
    }
    return @($relevant | Select-Object -Unique)
}

$PythonCandidates = @(
    $PythonPath,
    $env:TOUTIAO_PYTHON,
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    "py",
    "python"
) | Where-Object { $_ -and $_.Trim() }

$PythonExe = $null
foreach ($candidate in $PythonCandidates) {
    if ($candidate -eq "py") {
        try {
            $pyCommand = Get-Command py.exe -ErrorAction Stop
            $resolved = (& $pyCommand.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
            $resolved = if ($resolved) { ([string]$resolved).Trim() } else { "" }
            if ($resolved -and (Test-Path -LiteralPath $resolved)) {
                $PythonExe = $resolved
                break
            }
        }
        catch {
            continue
        }
    }

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

function New-DailyRunCommand {
    $args = @((Join-Path $ProjectRoot "main.py"), "--daily-run", "--account", $AccountKey)

    if ($env:TOUTIAO_STYLE) {
        $args += @("--style", $env:TOUTIAO_STYLE)
    }

    if ($env:TOUTIAO_TOPIC_RANK) {
        $args += @("--topic-rank", $env:TOUTIAO_TOPIC_RANK)
    }

    if ($env:TOUTIAO_TOPIC_TITLE) {
        $args += @("--topic-title", $env:TOUTIAO_TOPIC_TITLE)
    }

    if ($env:TOUTIAO_DRY_RUN -eq "1") {
        $args += "--dry-run"
    }

    if ($env:TOUTIAO_REFRESH_CACHE -eq "1") {
        $args += "--refresh-cache"
    }

    if ($env:TOUTIAO_NO_ARCHIVE -eq "1") {
        $args += "--no-archive"
    }

    return ,$args
}

function New-DailyReportCommand {
    return ,@((Join-Path $ProjectRoot "main.py"), "--send-daily-report", "--account", $AccountKey)
}

function New-CollectPerformanceCommand {
    $script = @"
from pathlib import Path
import json
import sys

project_root = Path(r"$ProjectRoot")
sys.path.insert(0, str(project_root / "src"))

from toutiao_content_factory.config import load_settings
from toutiao_content_factory.services.performance_service import collect_performance_data
from toutiao_content_factory.utils.logging_utils import configure_logging

settings = load_settings(project_root, account_key=r"$AccountKey")
logger = configure_logging(
    settings.logs_dir,
    settings.logging_level,
    logger_name=f"toutiao_content_factory.collect.{settings.account_slug}",
)
payload = collect_performance_data(settings, logger)
print(json.dumps(payload, ensure_ascii=False))
"@

    $scriptPath = Join-Path $AutomationRoot "collect_performance_$Timestamp.py"
    Set-Content -LiteralPath $scriptPath -Value $script -Encoding UTF8
    return @{
        Arguments = @($scriptPath)
        ScriptPath = $scriptPath
    }
}

if ($SendDailyReport.IsPresent) {
    $reportArguments = New-DailyReportCommand
    Write-Log "Running unified daily notification report for $AccountKey."
    Push-Location $ProjectRoot
    try {
        $reportProcess = Start-Process `
            -FilePath $PythonExe `
            -ArgumentList $reportArguments `
            -WorkingDirectory $ProjectRoot `
            -NoNewWindow `
            -Wait `
            -PassThru
        Write-Log "Daily notification report exited with code $($reportProcess.ExitCode)."
        exit $reportProcess.ExitCode
    }
    finally {
        Pop-Location
    }
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

$script:HeartbeatIntervalSeconds = 300

$maxAttempts = $script:RetryCount + 1
$exitCode = 1
$lastRunSummary = @{}

Write-Log "Using Python: $PythonExe"
Write-Log "Account: $AccountKey"
Write-Log "Run mode: $(if ($SendDailyReport.IsPresent) { 'send_daily_report' } elseif ($CollectPerformanceOnly.IsPresent) { 'collect_performance_only' } else { 'daily_run' })"
Write-Log "Scheduling mode: $(if ($CollectPerformanceOnly.IsPresent) { 'windows_task_collect_only' } else { 'windows_task_publish_orchestrator' })"
Write-Log "Auto publish enabled: $($env:TOUTIAO_AUTO_PUBLISH -eq '1')"
Write-Log "Publishing enabled: $($env:TOUTIAO_PUBLISH_ENABLED -eq '1')"
Write-Log "Scheduled browser mode: $(if ($env:TOUTIAO_PUBLISH_HEADLESS -eq '1') { 'headless (explicit override)' } else { 'visible (default for local desktop tasks)' })"
Write-Log "WeChat enabled: $($env:WECHAT_ENABLED -eq '1')"
Write-Log "WeChat auto publish: $($env:WECHAT_AUTO_PUBLISH -eq '1')"
Write-Log "Douyin enabled: $($env:DOUYIN_ENABLED -eq '1')"
Write-Log "Douyin auto publish: $($env:DOUYIN_AUTO_PUBLISH -eq '1')"
Write-Log "Resolved target platform: $ResolvedTargetPlatform"
Write-Log "Image backend: $($env:IMAGE_BACKEND)"
Write-Log "Image api mode: $($env:IMAGE_API_MODE)"
Write-Log "Image max assets: $($env:IMAGE_MAX_ASSETS)"
Write-Log "Log file: $LogFile"
Write-Log "Retry policy: up to $maxAttempts attempt(s), delay $script:RetryDelaySeconds second(s)."
$activePlatformTargets = Get-ActivePlatformTargets
Write-Log "Active platform targets for this schedule: $(if ($activePlatformTargets.Count -gt 0) { ($activePlatformTargets -join ', ') } else { 'none' })"
$shouldRunDailyWorkflow = -not $CollectPerformanceOnly.IsPresent

Push-Location $ProjectRoot
try {
    if (-not $shouldRunDailyWorkflow) {
        $collectCommand = New-CollectPerformanceCommand
        try {
            Update-Status -CurrentState "running" -Attempt 1 -MaxAttempts 1 -ExitCode 0 -Message "Performance collection is running."
            Write-Log "Running scheduled performance collection only."
            $process = Start-Process `
                -FilePath $PythonExe `
                -ArgumentList $collectCommand.Arguments `
                -WorkingDirectory $ProjectRoot `
                -NoNewWindow `
                -Wait `
                -PassThru

            $outputPath = if ($IsDefaultAccount) {
                Join-Path $ProjectRoot "output\\performance_metrics.json"
            }
            else {
                Join-Path $ProjectRoot "output\\accounts\\$AccountSlug\\performance_metrics.json"
            }

            if ($process.ExitCode -eq 0) {
                $message = "Scheduled performance collection succeeded."
                Update-Status -CurrentState "success" -Attempt 1 -MaxAttempts 1 -ExitCode 0 -Message $message -Extra @{
                    output_path = $outputPath
                    run_mode = "collect_performance_only"
                }
                Add-Notification -Level "info" -Title "Performance collection completed" -Message $message -Data @{
                    account_key = $AccountKey
                    output_path = $outputPath
                }
                Write-Log $message
                $exitCode = 0
            }
            else {
                $message = "Scheduled performance collection failed."
                Update-Status -CurrentState "failed" -Attempt 1 -MaxAttempts 1 -ExitCode $process.ExitCode -Message $message -Extra @{
                    output_path = $outputPath
                    run_mode = "collect_performance_only"
                }
                Add-Notification -Level "error" -Title "Performance collection failed" -Message $message -Data @{
                    account_key = $AccountKey
                    output_path = $outputPath
                }
                Write-Log $message
                $exitCode = $process.ExitCode
            }
        }
        finally {
            if ($collectCommand -and $collectCommand.ScriptPath) {
                Remove-Item -LiteralPath $collectCommand.ScriptPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
    if ($shouldRunDailyWorkflow) {
        $arguments = New-DailyRunCommand
        $healthResult = Run-HealthPreflight
        if ($healthResult.ExitCode -ne 0) {
            $message = "Platform health preflight command failed (exit code $($healthResult.ExitCode)). Continuing with scheduled workflow."
            Write-Log $message
            Add-Notification -Level "warn" -Title "Platform preflight failed" -Message $message -Data @{
                account_key = $AccountKey
                health_path = $healthResult.HealthPath
                exit_code = $healthResult.ExitCode
            }
            if (-not $healthResult.BlockingFlags) {
                $healthResult.BlockingFlags = @()
            }
        }
        $relevantBlockingFlags = Filter-RelevantBlockingFlags -BlockingFlags $healthResult.BlockingFlags -ActiveTargets $activePlatformTargets
        if ($healthResult.BlockingFlags.Count -gt 0 -and $relevantBlockingFlags.Count -eq 0) {
            Write-Log ("Ignoring unrelated platform blockers for this schedule: " + (($healthResult.BlockingFlags | ForEach-Object { [string]$_ }) -join " | "))
        }
        if ($relevantBlockingFlags.Count -gt 0) {
            $message = "Platform health preflight raised blocking flags, but continuing with scheduled workflow: " + (($relevantBlockingFlags | ForEach-Object { [string]$_ }) -join " | ")
            Write-Log $message
            Add-Notification -Level "warn" -Title "Preflight flags overridden" -Message $message -Data @{
                account_key = $AccountKey
                health_path = $healthResult.HealthPath
                blocking_flags = $relevantBlockingFlags
                ignored_blocking_flags = @($healthResult.BlockingFlags | Where-Object { $relevantBlockingFlags -notcontains $_ })
            }
        }
        for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
                Remove-Item -LiteralPath $RunResultFile -Force -ErrorAction SilentlyContinue
                Update-Status -CurrentState "running" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message "Scheduled pipeline is running."
                Write-Log "Attempt $attempt of $maxAttempts started."

                try {
                    $process = Start-Process `
                        -FilePath $PythonExe `
                        -ArgumentList $arguments `
                        -WorkingDirectory $ProjectRoot `
                        -NoNewWindow `
                        -PassThru

                    while (-not $process.HasExited) {
                        Update-Status -CurrentState "running" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message "Scheduled pipeline is running." -Extra @{
                            active_platform_targets = $activePlatformTargets
                        }
                        Start-Sleep -Seconds $script:HeartbeatIntervalSeconds
                        try {
                            $process.Refresh()
                        }
                        catch {
                            break
                        }
                    }

                    $process.WaitForExit()
                    $exitCode = $process.ExitCode
                    if ($null -eq $exitCode) {
                        Write-Log "Warning: Process exit code is null. Will defer to run_result.json status."
                        $exitCode = -1
                    }
                }
                finally {
                }

                $lastRunSummary = Get-LatestRunSummary
                $runResult = Read-JsonFile -Path $RunResultFile
                $runResultSummary = Convert-ToHashtable -Value $runResult
                if ($runResultSummary.ContainsKey("exported_files")) {
                    $runResultSummary.Remove("exported_files")
                }
                $lastRunSummary = Merge-Hashtables -Base $lastRunSummary -Override $runResultSummary
                $runResultStatus = if ($runResult) { [string]$runResult.status } else { "" }
                $runResultMessage = if ($runResult) { [string]$runResult.message } else { "" }

                if ($runResultStatus -eq "success" -and $exitCode -ne 0) {
                    Write-Log "Process exit code was $exitCode, but run_result.json reports status='$runResultStatus'. Overriding exit code to 0."
                    $exitCode = 0
                }

                if ($runResultStatus -eq "no_topics") {
                    $message = if ($runResultMessage) {
                        $runResultMessage
                    }
                    else {
                        "Scheduled run skipped because no eligible topics were available."
                    }
                    Update-Status -CurrentState "no_topics" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "warn" -Title "No topics available" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 0
                    break
                }

                if ($runResultStatus -eq "already_published") {
                    $message = if ($runResultMessage) {
                        $runResultMessage
                    }
                    else {
                        "Daily publish limit was already reached; no new article was generated."
                    }
                    Update-Status -CurrentState "already_published" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "info" -Title "Daily publish limit reached" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 0
                    break
                }

                if ($runResultStatus -eq "draft_only") {
                    $message = if ($runResultMessage) {
                        $runResultMessage
                    }
                    else {
                        "Scheduled pipeline saved a draft; no public submission was made."
                    }
                    Update-Status -CurrentState "draft_only" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "warn" -Title "Draft saved without publish" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 0
                    break
                }

                if ($runResultStatus -eq "publish_login_required") {
                    $message = if ($runResultMessage) {
                        $runResultMessage
                    }
                    else {
                        "Scheduled run generated content, but the Toutiao publish session requires manual login."
                    }
                    Update-Status -CurrentState "publish_login_required" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "warn" -Title "Publish login required" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 2
                    break
                }

                if ($runResultStatus -eq "publish_uncertain") {
                    $message = if ($runResultMessage) {
                        $runResultMessage
                    }
                    else {
                        "Toutiao publish verification is uncertain; manual confirmation is required before retry."
                    }
                    Update-Status -CurrentState "publish_uncertain" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "error" -Title "Publish verification uncertain" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 2
                    break
                }

                if ($runResultStatus -eq "partial_success") {
                    $message = if ($runResultMessage) {
                        $runResultMessage
                    }
                    else {
                        "At least one platform completed, but another platform failed."
                    }
                    Update-Status -CurrentState "partial_success" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "warn" -Title "Platform partial success" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 2
                    break
                }

                if ($runResultStatus -eq "review_required") {
                    $message = if ($runResultMessage) {
                        $runResultMessage
                    }
                    else {
                        "Scheduled pipeline completed, but this run still requires manual review."
                    }
                    Update-Status -CurrentState "review_required" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "warn" -Title "Manual review required" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 0
                    break
                }

                if (-not $runResult -and $exitCode -eq 0) {
                    $message = "Scheduled pipeline exited successfully but did not write last_daily_run_result.json."
                    Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "error" -Title "Scheduled result missing" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 2
                    break
                }

                if ($runResult -and $exitCode -eq 0 -and $runResultStatus -notin @("success")) {
                    $message = "Scheduled pipeline returned an unrecognized terminal status: $runResultStatus."
                    Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                    Add-Notification -Level "error" -Title "Unrecognized scheduled status" -Message $message -Data $lastRunSummary
                    Write-Log $message
                    $exitCode = 2
                    break
                }

                if ($exitCode -eq 0) {
                    $workflowStatus = [string]$lastRunSummary["workflow_status"]
                    $reviewStatus = [string]$lastRunSummary["review_status"]
                    $publishStatus = [string]$lastRunSummary["publish_status"]
                    $wechatStatus = [string]$lastRunSummary["wechat_status"]
                    $douyinStatus = [string]$lastRunSummary["douyin_status"]

                    if ($workflowStatus -eq "platform_publish_failed") {
                        $message = "Scheduled pipeline generated content, but one or more platform publish steps failed."
                        Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "warn" -Title "Platform publish failed" -Message $message -Data $lastRunSummary
                        Write-Log $message
                        $exitCode = 2
                    }
                    elseif ($workflowStatus -eq "platform_partial_success") {
                        $message = "Scheduled pipeline partially succeeded. At least one platform completed, but another platform failed."
                        Update-Status -CurrentState "partial_success" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "warn" -Title "Platform partial success" -Message $message -Data $lastRunSummary
                        $exitCode = 2
                    }
                    elseif ($publishStatus -in @("failed", "publish_uncertain", "publish_login_required", "blocked", "blocked_by_ip", "circuit_open", "missing", "invalid")) {
                        $message = "Scheduled pipeline produced a non-publishable Toutiao result: $publishStatus."
                        $terminalState = if ($publishStatus -eq "publish_login_required") { "publish_login_required" } elseif ($publishStatus -eq "publish_uncertain") { "publish_uncertain" } else { "failed" }
                        Update-Status -CurrentState $terminalState -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "error" -Title "Toutiao publish did not complete" -Message $message -Data $lastRunSummary
                        Write-Log $message
                        $exitCode = 2
                    }
                    elseif ($publishStatus -eq "draft_saved") {
                        $message = "Scheduled pipeline saved a Toutiao draft; no public submission was made."
                        Update-Status -CurrentState "draft_only" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 0 -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "warn" -Title "Toutiao draft saved" -Message $message -Data $lastRunSummary
                        Write-Log $message
                    }
                    elseif ($publishStatus -or $wechatStatus -or $douyinStatus) {
                        $parts = @()
                        if ($publishStatus) {
                            $parts += "Toutiao=$publishStatus"
                        }
                        if ($wechatStatus) {
                            $parts += "WeChat=$wechatStatus"
                        }
                        if ($douyinStatus) {
                            $parts += "Douyin=$douyinStatus"
                        }
                        $message = "Scheduled pipeline succeeded. " + ($parts -join "; ") + "."
                        Update-Status -CurrentState "success" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode $exitCode -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "info" -Title "Scheduled publish completed" -Message $message -Data $lastRunSummary
                    }
                    elseif ($reviewStatus -and $reviewStatus -ne "approved") {
                        $message = "Scheduled pipeline succeeded, but this run still requires manual review."
                        Update-Status -CurrentState "review_required" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode $exitCode -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "warn" -Title "Manual review required" -Message $message -Data $lastRunSummary
                    }
                    elseif (($env:TOUTIAO_AUTO_PUBLISH -eq "1") -and ($env:TOUTIAO_PUBLISH_ENABLED -eq "1")) {
                        $message = "Scheduled pipeline completed without a Toutiao publish result even though Toutiao auto-publish is enabled."
                        Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "error" -Title "Scheduled publish missing" -Message $message -Data $lastRunSummary
                        Write-Log $message
                        $exitCode = 2
                    }
                    elseif (($env:WECHAT_ENABLED -eq "1") -and ($env:TOUTIAO_DRY_RUN -ne "1") -and (-not $wechatStatus)) {
                        $message = "Scheduled pipeline completed without a WeChat draft/publish result even though WeChat is enabled."
                        Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "error" -Title "WeChat result missing" -Message $message -Data $lastRunSummary
                        Write-Log $message
                        $exitCode = 2
                    }
                    elseif (($env:DOUYIN_ENABLED -eq "1") -and ($env:DOUYIN_AUTO_PUBLISH -eq "1") -and ($env:TOUTIAO_DRY_RUN -ne "1") -and (-not $douyinStatus)) {
                        $message = "Scheduled pipeline completed without a Douyin publish result even though Douyin auto-publish is enabled."
                        Update-Status -CurrentState "failed" -Attempt $attempt -MaxAttempts $maxAttempts -ExitCode 2 -Message $message -Extra $lastRunSummary
                        Add-Notification -Level "error" -Title "Douyin result missing" -Message $message -Data $lastRunSummary
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
}
catch {
    $errorMessage = if ($_.Exception) { $_.Exception.Message } else { [string]$_ }
    if ([string]::IsNullOrWhiteSpace($errorMessage)) {
        $errorMessage = "Scheduled pipeline terminated unexpectedly."
    }
    if ($lastRunSummary.Count -eq 0) {
        $lastRunSummary = Get-LatestRunSummary
    }
    Update-Status -CurrentState "failed" -Attempt 0 -MaxAttempts $maxAttempts -ExitCode 1 -Message $errorMessage -Extra $lastRunSummary
    Add-Notification -Level "error" -Title "Scheduled pipeline crashed" -Message $errorMessage -Data $lastRunSummary
    Write-Log $errorMessage
    $exitCode = 1
}
finally {
    Pop-Location
}

Write-Log "Exit code: $exitCode"
exit $exitCode
