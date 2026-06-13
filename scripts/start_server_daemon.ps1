$env:PYTHONPATH = "F:\Automated-workflow-main\src"
$PythonExe = "F:\Automated-workflow-main\.venv\Scripts\python.exe"
$ProjectRoot = "F:\Automated-workflow-main"

while ($true) {
    $proc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match "8000" }
    if (-not $proc) {
        $now = Get-Date -Format "MM/dd/yyyy HH:mm:ss"
        Write-Host "$now: Server not running. Starting..."
        Set-Location $ProjectRoot
        Start-Process -WindowStyle Hidden -FilePath $PythonExe -ArgumentList "-m uvicorn toutiao_content_factory.web.app:create_app_factory --host 0.0.0.0 --port 8000 --factory"
        Write-Host "$now: Server started."
    }
    Start-Sleep -Seconds 30
}
