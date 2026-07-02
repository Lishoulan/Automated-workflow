@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "ACCOUNT_KEY=%TOUTIAO_ACCOUNT%"
if "%ACCOUNT_KEY%"=="" set "ACCOUNT_KEY=default"

set "PYTHON_EXE=%TOUTIAO_PYTHON%"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=py -3"

echo ========================================
echo 手蓝 AI 内容工厂 - 立即运行一次
echo ========================================
echo 账号: %ACCOUNT_KEY%
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --account "%ACCOUNT_KEY%" --daily-run --refresh-cache
) else (
    py -3 main.py --account "%ACCOUNT_KEY%" --daily-run --refresh-cache
)

echo.
echo 本次流程执行完成。
pause
