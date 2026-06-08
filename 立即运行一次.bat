@echo off
chcp 65001 >nul
title 手蓝AI内容工厂 - 立即运行一次
echo ========================================
echo 🚀 手蓝AI内容工厂 - 立即运行一次工作流
echo ========================================
echo.

cd /d "%~dp0"

echo ⏳ 正在启动工作流...
echo.
python main.py --account robot_notes --daily-run

echo.
echo ========================================
echo ✅ 工作流执行完成！
echo ========================================
echo.
pause
