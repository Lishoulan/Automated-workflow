# daily_run_enhanced.ps1
# 增强版每日运行脚本 - 包含网络拨测、启动延迟和优化重试机制

param(
    [string]$Account = "default",
    [switch]$EnableAutoPublish,
    [switch]$SkipTopicCollection,
    [int]$MaxRetries = 3,
    [int]$RetryIntervalMinutes = 15,
    [int]$NetworkTimeoutSeconds = 30,
    [int]$StartupDelaySeconds = 60
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

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
        $lastRunState = Get-Content $lastRunPath | ConvertFrom-Json
        Write-Host "[状态] 上次运行状态: $($lastRunState.state) - $($lastRunState.message)" -ForegroundColor Cyan
    } catch {
        Write-Host "[状态] 无法读取上次运行状态" -ForegroundColor Yellow
    }
}

# 3. 执行流水线
$attempt = 0
$success = $false

while ($attempt -lt $MaxRetries -and -not $success) {
    $attempt++
    Write-Host "`n[尝试 $attempt/$MaxRetries] 开始执行流水线..." -ForegroundColor Yellow
    
    try {
        # 构建命令
        $cmdArgs = @(
            ".\main.py",
            "--account", $Account,
            "--daily-run",
            "--refresh-cache"
        )
        
        if ($SkipTopicCollection) {
            $cmdArgs += "--topics-only"
        }
        
        # 执行
        $result = & python @cmdArgs 2>&1
        
        if ($LASTEXITCODE -eq 0) {
            $success = $true
            Write-Host "[成功] 流水线执行成功" -ForegroundColor Green
            
            # 保存状态
            $runState = @{
                state = "success"
                message = "Pipeline completed successfully"
                timestamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
                attempt = $attempt
            }
            $runState | ConvertTo-Json | Set-Content $lastRunPath
            
            # 发送成功通知
            Send-Notification -Type "success" -Title "【今日头条】每日任务成功" -Message "执行时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
            
            break
        } else {
            throw "Pipeline returned exit code: $LASTEXITCODE"
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
    $runState | ConvertTo-Json | Set-Content $lastRunPath
    
    # 发送失败通知
    $errorMsg = "已尝试 $attempt 次，均失败。请前往后台检查。"
    Send-Notification -Type "failure" -Title "【今日头条】每日任务失败" -Message $errorMsg
} else {
    Write-Host "`n=====================================" -ForegroundColor Green
    Write-Host "[完成] 每日运行成功完成" -ForegroundColor Green
    Write-Host "=====================================" -ForegroundColor Green
}
