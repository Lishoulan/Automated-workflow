# Clean and Publish Script
# This script cleans all lock files and then runs the daily publish

Write-Host "======================================"
Write-Host "  Clean & Publish Script"
Write-Host "======================================"
Write-Host ""

# Step 1: Clean all lock files
Write-Host "[Step 1] Cleaning lock files..."
Get-ChildItem -Path "f:\Automated-workflow-main" -Filter "*.lock" -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Removing: $($_.FullName)"
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
Write-Host "  Done cleaning locks"
Write-Host ""

# Step 2: Clean publish session locks specifically
Write-Host "[Step 2] Cleaning publish session locks..."
$publishLocks = Get-ChildItem -Path "f:\Automated-workflow-main" -Filter ".publish_session.lock" -Recurse -File -ErrorAction SilentlyContinue
if ($publishLocks) {
    $publishLocks | ForEach-Object {
        Write-Host "  Removing: $($_.FullName)"
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "  No publish locks found"
}
Write-Host ""

# Step 3: Run the daily publish
Write-Host "[Step 3] Running daily publish for robot_notes..."
Write-Host ""

$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe"
$mainScript = "f:\Automated-workflow-main\main.py"

$startTime = Get-Date
$process = Start-Process -FilePath $pythonExe -ArgumentList "`"$mainScript`" --account robot_notes --daily-run" -WorkingDirectory "f:\Automated-workflow-main" -NoNewWindow -PassThru -Wait

$endTime = Get-Date
$duration = $endTime - $startTime

Write-Host ""
Write-Host "======================================"
Write-Host "  Execution Complete"
Write-Host "======================================"
Write-Host "Duration: $($duration.ToString('hh\:mm\:ss'))"
Write-Host "Exit Code: $($process.ExitCode)"
Write-Host ""
