@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ========================================
echo 手蓝 AI 内容工厂 - 全自动调度启动
echo ========================================
echo.
echo 说明:
echo 1. 这会启动常驻 Web 调度台
echo 2. 定时任务会按 config.yaml 里的 automation.daily_run_times 自动执行
echo 3. 微信发布会强制走 135 编辑器同步流程
echo.

set "ACCOUNT_KEY=%TOUTIAO_ACCOUNT%"
if "%ACCOUNT_KEY%"=="" set "ACCOUNT_KEY=default"

powershell -ExecutionPolicy Bypass -File ".\scripts\start_auto_scheduler.ps1" -Account "%ACCOUNT_KEY%" -BindHost "127.0.0.1" -OpenBrowser

echo.
echo 调度服务已退出。
pause
