@echo off
chcp 65001 >nul
echo ========================================
echo   ?????? - ????
echo ========================================
echo.
echo [1/3] ?????????????...
taskkill /f /im msedge.exe >nul 2>&1
taskkill /f /im chrome.exe >nul 2>&1
if exist "F:\Automated-workflow-main\output\.publish_session.lock" del "F:\Automated-workflow-main\output\.publish_session.lock"
echo Done

echo [2/3] ??Web???...
set PYTHONPATH=F:\Automated-workflow-main\src
start /MIN "" "F:\Automated-workflow-main\.venv\Scripts\python.exe" -m uvicorn toutiao_content_factory.web.app:create_app_factory --host 0.0.0.0 --port 8000 --factory
timeout /t 8 /nobreak >nul
echo Done

echo [3/3] ???????????...
echo ????????:
schtasks /query /tn "ShouLan_default_1" /v /fo LIST 2>nul | findstr "Status:"
schtasks /query /tn "ShouLan_default_2" /v /fo LIST 2>nul | findstr "Status:"
echo.
echo ???????:
powershell -Command "Get-ScheduledTask -TaskName 'ShouLan_*' | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime | Format-Table -AutoSize"
echo.
echo ========================================
echo  ????!
echo  ???: http://127.0.0.1:8000/
echo  ?????Windows????????????Web???
echo ========================================
echo.
pause
