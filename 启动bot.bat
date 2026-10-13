@echo off
chcp 65001 >nul
cd /d %~dp0
echo ================================
echo        洛书bot 启动器
echo ================================
echo.
echo [提示] 请先确认已运行 LLNT/Main.exe 和 Ollama
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" bot.py
) else (
    echo [错误] 未找到虚拟环境，请先运行 安装依赖.bat
    pause
    exit /b 1
)

pause
