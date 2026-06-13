# Direct Publish Script - Skip preflight checks
# This script directly runs the publish without waiting for locks

param(
    [string]$Account = "default",
    [int]$WaitMinutes = 1
)

$ErrorActionPreference = "Continue"

Write-Host "======================================"
Write-Host "  Direct Publish Script"
Write-Host "======================================"
Write-Host ""

# Step 1: Clean all lock files
Write-Host "[1/4] Cleaning lock files..."
Get-ChildItem -Path "f:\Automated-workflow-main" -Filter ".publish_session*" -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Removing: $($_.FullName)"
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
Write-Host ""

# Step 2: Wait a moment
Write-Host "[2/4] Waiting $WaitMinutes minute(s) for any stale locks to clear..."
Start-Sleep -Seconds ($WaitMinutes * 60)
Write-Host ""

# Step 3: Clean locks again
Write-Host "[3/4] Cleaning lock files again..."
Get-ChildItem -Path "f:\Automated-workflow-main" -Filter ".publish_session*" -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Removing: $($_.FullName)"
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
Write-Host ""

# Step 4: Run the daily publish
Write-Host "[4/4] Running daily publish for account: $Account"
Write-Host ""

$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe"
$mainScript = "f:\Automated-workflow-main\main.py"

# Run with --topics-only to skip collection, or full daily-run
$process = Start-Process -FilePath $pythonExe -ArgumentList "`"$mainScript`" --account `"$Account`" --daily-run --topics-only" -WorkingDirectory "f:\Automated-workflow-main" -NoNewWindow -Wait -PassThru

Write-Host ""
Write-Host "======================================"
Write-Host "  Complete"
Write-Host "======================================"
Write-Host "Exit Code: $($process.ExitCode)"
Write-Host ""
