# daily_run_enhanced.ps1
# 增强版每日运行脚本 - 包含网络拨测、启动延迟和优化重试机制

param(
    [Alias("AccountKey")]
    [string]$Account = "default",
    [string]$PythonPath = "",
    [string]$Style = "",
    [switch]$VisibleBrowser,
    [switch]$DryRun,
    [switch]$RefreshCache,
    [switch]$EnableAutoPublish,
    [switch]$SkipTopicCollection,
    [switch]$CollectPerformanceOnly,
    [switch]$EnableFeedbackLoop,
    [int]$MaxRetries = 3,
    [int]$RetryIntervalMinutes = 15,
    [int]$RetryCount = -1,
    [int]$RetryDelaySeconds = -1,
    [int]$NetworkTimeoutSeconds = 30,
    [int]$StartupDelaySeconds = 60
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

if ($RetryCount -ge 0) {
    $MaxRetries = [Math]::Max(1, $RetryCount + 1)
}
if ($RetryDelaySeconds -ge 0) {
    $RetryIntervalMinutes = [Math]::Max(1, [Math]::Ceiling($RetryDelaySeconds / 60.0))
}

# 解析 Python 可执行文件（优先 venv，兼容 py launcher / 系统 python）
$PythonCandidates = @(
    $PythonPath,
    $env:TOUTIAO_PYTHON,
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"),
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
            $null = & python --version 2>$null
            if ($LASTEXITCODE -eq 0) {
                $PythonExe = "python"
                break
            }
        } catch {
            continue
        }
    } elseif (Test-Path -LiteralPath $candidate) {
        $PythonExe = $candidate
        break
    }
}
if (-not $PythonExe) {
    Write-Host "[致命] 未找到可用的 Python 解释器，请设置 TOUTIAO_PYTHON 环境变量" -ForegroundColor Red
    exit 1
}
Write-Host "[Python] $PythonExe" -ForegroundColor Gray

# 网络检测函数
function Test-NetworkConnectivity {
    param([int]$TimeoutSeconds = 10)
    $endpoints = @(
        @{ Name = "Baidu"; Url = "https://www.baidu.com" },
        @{ Name = "OpenAI API"; Url = "https://api.openai.com" }
    )
    
    foreach ($endpoint in $endpoints) {
        try {
            $response = Invoke-WebRequest -Uri $endpoint.Url -Method Head -TimeoutSec $TimeoutSeconds -UseBasicParsing -ErrorAction SilentlyContinue
            if ($response.StatusCode -in 200, 301, 302) {
                Write-Host "[网络检测] $($endpoint.Name) - OK" -ForegroundColor Green
                return $true
            }
        } catch {
            Write-Host "[网络检测] $($endpoint.Name) - 失败: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    return $false
}

# 等待网络就绪
function Wait-ForNetwork {
    param([int]$TimeoutSeconds = 300, [int]$CheckIntervalSeconds = 15)
    $elapsed = 0
    Write-Host "[启动] 等待网络连接..." -ForegroundColor Cyan
    
    while ($elapsed -lt $TimeoutSeconds) {
        if (Test-NetworkConnectivity -TimeoutSeconds 5) {
            Write-Host "[启动] 网络已就绪，延迟 $StartupDelaySeconds 秒后开始..." -ForegroundColor Green
            Start-Sleep -Seconds $StartupDelaySeconds
            return $true
        }
        $elapsed += $CheckIntervalSeconds
        $remaining = $TimeoutSeconds - $elapsed
        if ($remaining -gt 0) {
            Write-Host "[启动] 网络未就绪，${remaining}秒后重试..." -ForegroundColor Yellow
            Start-Sleep -Seconds $CheckIntervalSeconds
        }
    }
    
    Write-Host "[启动] 网络连接超时，但继续执行..." -ForegroundColor Red
    return $false
}

# 发送通知
function Send-Notification {
    param(
        [string]$Type,
        [string]$Title,
        [string]$Message,
        [string]$WebHookUrl = $env:WECOM_WEBHOOK_URL
    )
    
    if (-not $WebHookUrl) {
        Write-Host "[通知] 未配置WebHook，跳过通知" -ForegroundColor Yellow
        return
    }
    
    try {
        $payload = @{
            msgtype = "text"
            text = @{
                content = "$Title`n$Message"
            }
        }
        Invoke-RestMethod -Uri $WebHookUrl -Method Post -Body ($payload | ConvertTo-Json -Compress) -ContentType "application/json" -TimeoutSec 10
        Write-Host "[通知] 发送成功" -ForegroundColor Green
    } catch {
        Write-Host "[通知] 发送失败: $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Read-JsonFileWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [int]$Attempts = 5,
        [int]$DelayMilliseconds = 250
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    for ($readAttempt = 1; $readAttempt -le [Math]::Max(1, $Attempts); $readAttempt++) {
        foreach ($encoding in @("UTF8", "Unicode")) {
            try {
                $raw = Get-Content -LiteralPath $Path -Raw -Encoding $encoding -ErrorAction Stop
                if ([string]::IsNullOrWhiteSpace($raw)) {
                    throw "JSON file is empty."
                }
                return ($raw | ConvertFrom-Json -ErrorAction Stop)
            }
            catch {
                continue
            }
        }
        if ($readAttempt -lt [Math]::Max(1, $Attempts)) {
            Start-Sleep -Milliseconds ([Math]::Max(50, $DelayMilliseconds))
        }
    }

    return $null
}

# 主要执行流程
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "[每日运行] 开始执行 - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan

# 1. 网络检测
if (-not (Wait-ForNetwork -TimeoutSeconds $NetworkTimeoutSeconds)) {
    Write-Host "[警告] 网络未完全就绪，继续执行..." -ForegroundColor Yellow
}

# 2. 检查上一次运行状态
$lastRunState = $null
$lastRunPath = Join-Path $ProjectRoot ".runtime\last_run_state.json"
if (Test-Path $lastRunPath) {
    try {
        $lastRunState = Read-JsonFileWithRetry -Path $lastRunPath
        if ($lastRunState) {
            Write-Host "[状态] 上次运行状态: $($lastRunState.state) - $($lastRunState.message)" -ForegroundColor Cyan
        }
    } catch {
        Write-Host "[状态] 无法读取上次运行状态" -ForegroundColor Yellow
    }
}

# 3. 执行流水线
$attempt = 0
$success = $false
$manualFollowUp = $false

while ($attempt -lt $MaxRetries -and -not $success) {
    $attempt++
    Write-Host "`n[尝试 $attempt/$MaxRetries] 开始执行流水线..." -ForegroundColor Yellow
    
    try {
        # 构建命令
        if ($CollectPerformanceOnly) {
            Write-Host "[采集模式] 采集昨日发布数据 + 运行反馈闭环（数据→规则→prompt 优化）" -ForegroundColor Cyan
            $cmdArgs = @(
                ".\main.py",
                "--account", $Account,
                "--collect-performance"
            )
            if ($EnableFeedbackLoop) {
                $cmdArgs += "--feedback-loop"
            }
        } else {
            $cmdArgs = @(
                ".\main.py",
                "--account", $Account,
                "--daily-run",
                "--refresh-cache"
            )
        }
        
        if ($SkipTopicCollection) {
            $cmdArgs += "--topics-only"
        }
        
        # 自动发布：通过环境变量传递给 main.py（--daily-run 在 auto_publish=True 时才真正发布）
        if ($EnableAutoPublish) {
            Write-Host "[自动发布] 已启用自动发布 (TOUTIAO_AUTO_PUBLISH=1)" -ForegroundColor Green
            $env:TOUTIAO_PUBLISH_ENABLED = "1"
            $env:TOUTIAO_AUTO_PUBLISH = "1"
        } else {
            $env:TOUTIAO_AUTO_PUBLISH = "0"
        }

        $mainPath = Join-Path $ProjectRoot "main.py"
        $cmdArgs = @(
            $mainPath,
            "--account", $Account
        )
        if (-not $CollectPerformanceOnly) {
            $cmdArgs += "--daily-run"
            $cmdArgs += "--refresh-cache"
        }
        elseif ($EnableFeedbackLoop) {
            $cmdArgs += "--feedback-loop"
        }
        if ($Style) {
            $cmdArgs += @("--style", $Style)
        }
        if ($DryRun) {
            $cmdArgs += "--dry-run"
        }
        if ($SkipTopicCollection -and -not $CollectPerformanceOnly) {
            $cmdArgs += "--topics-only"
        }
        
        # 执行
        $result = & $PythonExe @cmdArgs 2>&1
        $pythonExitCode = $LASTEXITCODE
        $result | ForEach-Object { Write-Host $_ }

        $accountSlug = (($Account -replace '[^a-zA-Z0-9]+', '-') -replace '(^-+|-+$)', '').ToLower()
        if ([string]::IsNullOrWhiteSpace($accountSlug)) {
            $accountSlug = "default"
        }
        $automationRoot = if ($accountSlug -eq "default") {
            Join-Path $ProjectRoot "output\automation"
        }
        else {
            Join-Path $ProjectRoot "output\accounts\$accountSlug\automation"
        }
        $runResultPath = Join-Path $automationRoot "last_daily_run_result.json"
        $runResult = Read-JsonFileWithRetry -Path $runResultPath
        if ((Test-Path -LiteralPath $runResultPath) -and -not $runResult) {
            Write-Host "[警告] 日运行结果暂时无法解析，稍后按失败处理并重试。" -ForegroundColor Yellow
        }
        $runStatus = if ($runResult) { [string]$runResult.status } else { "" }

        if ($CollectPerformanceOnly -and $pythonExitCode -eq 0) {
            $success = $true
            Write-Host "[成功] 数据采集任务执行成功" -ForegroundColor Green
            
            # 保存状态
            $runState = @{
                state = "success"
                message = "Pipeline completed successfully"
                timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
                attempt = $attempt
            }
            $runState | ConvertTo-Json | Set-Content -LiteralPath $lastRunPath -Encoding UTF8
            
            # 发送成功通知
            Send-Notification -Type "success" -Title "【今日头条】数据采集成功" -Message "执行时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

            break
        } elseif ($runStatus -eq "partial_success" -and $pythonExitCode -in @(0, 1)) {
            $success = $true
            $manualFollowUp = $true
            $message = if ($runResult.message) { [string]$runResult.message } else { "Scheduled pipeline partially succeeded; manual follow-up is required." }
            Write-Host "[部分成功] $message" -ForegroundColor Yellow

            $runState = @{
                state = $runStatus
                message = $message
                timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
                attempt = $attempt
            }
            $runState | ConvertTo-Json | Set-Content -LiteralPath $lastRunPath -Encoding UTF8
            Send-Notification -Type "warning" -Title "【今日头条】每日任务部分成功" -Message $message
            break
        } elseif ($pythonExitCode -eq 0 -and $runStatus -in @("success", "no_topics", "already_published")) {
            $success = $true
            Write-Host "[成功] 每日运行完成: $runStatus" -ForegroundColor Green

            $runState = @{
                state = $runStatus
                message = if ($runResult.message) { [string]$runResult.message } else { "Scheduled pipeline completed: $runStatus" }
                timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
                attempt = $attempt
            }
            $runState | ConvertTo-Json | Set-Content -LiteralPath $lastRunPath -Encoding UTF8
            $noticeTitle = if ($runStatus -eq "success") { "【今日头条】每日任务成功" } else { "【今日头条】每日任务状态更新" }
            Send-Notification -Type "success" -Title $noticeTitle -Message ([string]$runState.message)
            break
        } elseif ($pythonExitCode -eq 0 -and $runStatus -in @("draft_only", "review_required")) {
            $success = $true
            $manualFollowUp = $true
            $message = if ($runResult.message) { [string]$runResult.message } else { "Scheduled pipeline requires manual follow-up: $runStatus" }
            Write-Host "[待处理] $message" -ForegroundColor Yellow
            $runState = @{
                state = $runStatus
                message = $message
                timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
                attempt = $attempt
            }
            $runState | ConvertTo-Json | Set-Content -LiteralPath $lastRunPath -Encoding UTF8
            Send-Notification -Type "warning" -Title "【今日头条】需要人工处理" -Message $message
            break
        } else {
            throw "Pipeline returned exit code: $pythonExitCode (run status: $runStatus)"
        }
    } catch {
        Write-Host "[错误] 执行失败: $($_.Exception.Message)" -ForegroundColor Red
        
        if ($attempt -lt $MaxRetries) {
            Write-Host "[重试] 等待 $RetryIntervalMinutes 分钟后重试..." -ForegroundColor Yellow
            Start-Sleep -Seconds ($RetryIntervalMinutes * 60)
        }
    }
}

# 4. 处理最终结果
if (-not $success) {
    Write-Host "`n[失败] 所有重试次数已用完" -ForegroundColor Red
    
    $runState = @{
        state = "failed"
        message = "All retry attempts exhausted"
        timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
        total_attempts = $attempt
    }
    $runState | ConvertTo-Json | Set-Content -LiteralPath $lastRunPath -Encoding UTF8
    
    # 发送失败通知
    $errorMsg = "已尝试 $attempt 次，均失败。请前往后台检查。"
    Send-Notification -Type "failure" -Title "【今日头条】每日任务失败" -Message $errorMsg
} else {
    Write-Host "`n=====================================" -ForegroundColor Green
    Write-Host "[完成] 每日运行成功完成" -ForegroundColor Green
    Write-Host "=====================================" -ForegroundColor Green
}

if ($manualFollowUp -and $runStatus -in @("draft_only", "review_required")) {
    # 生成流程本身已完成；人工审核/草稿是业务状态，不应让任务计划程序误报为脚本崩溃。
    exit 0
}
if ($manualFollowUp) {
    # 部分成功仍保留非零退出码，提醒任务监控进行人工跟进。
    exit 2
}
if ($success) {
    exit 0
}
exit 1
