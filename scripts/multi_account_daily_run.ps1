# multi_account_daily_run.ps1
# 多账号每日运行脚本 - 支持两个头条账号 + 微信公众号

param(
    [string[]]$Accounts = @("default", "robot_notes"),
    [switch]$IncludeWeChat,
    [int]$AccountDelayMinutes = 45,
    [switch]$SkipTopicCollection,
    [switch]$EnableAutoPublish,
    [int]$MaxRetries = 2,
    [int]$RetryIntervalMinutes = 15
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# 发送通知
function Send-Notification {
    param(
        [string]$Type,
        [string]$Title,
        [string]$Message,
        [string]$WebHookUrl = $env:WECOM_WEBHOOK_URL
    )
    
    if (-not $WebHookUrl) {
        Write-Host "[通知] 未配置 WebHook，跳过通知" -ForegroundColor Yellow
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

# 运行单个账号
function Invoke-AccountRun {
    param(
        [string]$Account,
        [bool]$IsWeChat = $false
    )
    
    $attempt = 0
    $success = $false
    $accountName = if ($IsWeChat) { "微信公众号" } else { $Account }
    
    Write-Host "`n=====================================" -ForegroundColor Cyan
    Write-Host "[账号] 开始运行: $accountName" -ForegroundColor Cyan
    Write-Host "=====================================" -ForegroundColor Cyan
    
    while ($attempt -lt $MaxRetries -and -not $success) {
        $attempt++
        Write-Host "`n[尝试 $attempt/$MaxRetries] 开始执行..." -ForegroundColor Yellow
        
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
                Write-Host "[成功] $accountName 执行成功" -ForegroundColor Green
                
                # 发送成功通知
                Send-Notification -Type "success" -Title "【$accountName】任务成功" -Message "执行时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
                
                break
            } else {
                throw "Pipeline returned exit code: $LASTEXITCODE"
            }
        } catch {
            Write-Host "[错误] $accountName 执行失败: $($_.Exception.Message)" -ForegroundColor Red
            
            if ($attempt -lt $MaxRetries) {
                Write-Host "[重试] 等待 $RetryIntervalMinutes 分钟后重试..." -ForegroundColor Yellow
                Start-Sleep -Seconds ($RetryIntervalMinutes * 60)
            }
        }
    }
    
    return $success
}

# 主流程
Write-Host "`n=====================================" -ForegroundColor Cyan
Write-Host "[启动] 多账号每日运行脚本" -ForegroundColor Cyan
Write-Host "[时间] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan

$successCount = 0
$failedAccounts = @()

# 运行头条账号
foreach ($account in $Accounts) {
    $result = Invoke-AccountRun -Account $account -IsWeChat $false
    
    if ($result) {
        $successCount++
    } else {
        $failedAccounts += $account
    }
    
    # 账号间延迟防关联
    if ($account -ne $Accounts[-1]) {
        Write-Host "`n[延迟] 等待 $AccountDelayMinutes 分钟后运行下一个账号..." -ForegroundColor Yellow
        Start-Sleep -Seconds ($AccountDelayMinutes * 60)
    }
}

# 运行微信公众号
if ($IncludeWeChat) {
    Write-Host "`n[微信] 准备运行微信公众号..." -ForegroundColor Cyan
    
    # 延迟后运行
    Start-Sleep -Seconds 30
    
    $result = Invoke-AccountRun -Account "default" -IsWeChat $true
    
    if ($result) {
        $successCount++
    } else {
        $failedAccounts += "微信公众号"
    }
}

# 最终总结
Write-Host "`n=====================================" -ForegroundColor Green
Write-Host "[完成] 所有任务执行完毕" -ForegroundColor Green
Write-Host "[成功] $successCount 个账号" -ForegroundColor Green

if ($failedAccounts.Count -gt 0) {
    Write-Host "[失败] $($failedAccounts.Count) 个账号: $($failedAccounts -join ', ')" -ForegroundColor Red
    
    # 发送失败总结通知
    Send-Notification -Type "failure" -Title "【多账号任务】部分失败" -Message "失败账号: $($failedAccounts -join ', ')`n执行时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
}

Write-Host "=====================================" -ForegroundColor Green
