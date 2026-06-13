param([string]$AccountKey = "default")

$ErrorActionPreference = "Continue"
$ProjectRoot = "F:\Automated-workflow-main"
$PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
$ScriptPath = "$ProjectRoot\scripts\standalone_pipeline.py"

# Random delay to avoid 3 accounts conflicting
$delay = Get-Random -Minimum 5 -Maximum 120
Start-Sleep -Seconds $delay

$env:PYTHONPATH = "$ProjectRoot\src"
$env:ACCOUNT_KEY = $AccountKey
$env:TOUTIAO_PUBLISH_CHANNEL = ""
$env:TOUTIAO_PUBLISH_HEADLESS = "1"
$env:WECHAT_ENABLED = "1"
$env:WECHAT_AUTO_PUBLISH = "1"
$env:TOUTIAO_AUTO_PUBLISH = "1"
$env:TOUTIAO_PUBLISH_ENABLED = "1"

$LockFile = "$ProjectRoot\output\.publish_session.lock"
if (Test-Path $LockFile) { Remove-Item -Force $LockFile -ErrorAction SilentlyContinue }

$proc = Start-Process -FilePath $PythonExe -ArgumentList "`"$ScriptPath`"" -NoNewWindow -Wait -PassThru
exit $proc.ExitCode
