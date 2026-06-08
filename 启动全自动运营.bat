@echo off
chcp 65001 >nul
echo ========================================
echo 🚀 手蓝AI内容工厂 - 全自动运营启动
echo ========================================
echo.

cd /d "%~dp0"

echo [1/3] 检查Python环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

echo [2/3] 检查项目配置...
if not exist ".env" (
    echo ⚠️  未找到.env文件，正在复制.env.example...
    copy ".env.example" ".env"
)

echo [3/3] 启动全自动定时工作流...
echo.
echo 📋 配置信息：
echo    - 账号: robot_notes
echo    - 定时时间: 07:00, 11:30, 18:00, 22:00
echo    - 洞察增强: 已启用
echo.
echo ⚙️  正在启动定时工作流...
echo.
echo 提示：如需停止，按 Ctrl+C
echo.

:loop
echo.
echo -------------------------------------------------
echo [%date% %time%] 启动每日工作流...
python main.py --account robot_notes --daily-run

echo.
echo [%date% %time%] 工作流完成，等待下次运行...
echo.

timeout /t 3600 /nobreak >nul
goto loop
