# 每日自动运行脚本
# 1. 头条日更（采集+生成+审核+发布）
# 2. 公众号发布到草稿箱

$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
$projectDir = "F:\Automated-workflow-main"
$logDir = "$projectDir\.runtime\logs"

# 确保日志目录存在
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force }

$logFile = "$logDir\daily_run_$(Get-Date -Format 'yyyyMMdd').log"

Write-Output "===== Daily Run Started: $(Get-Date) =====" | Tee-Object -FilePath $logFile -Append

# 切换到项目目录
Set-Location $projectDir

# 运行日更流程
& $pythonExe main.py --daily-run 2>&1 | Tee-Object -FilePath $logFile -Append

# 运行公众号发布
& $pythonExe main.py --wechat-publish-latest-approved 2>&1 | Tee-Object -FilePath $logFile -Append

Write-Output "===== Daily Run Finished: $(Get-Date) =====" | Tee-Object -FilePath $logFile -Append
