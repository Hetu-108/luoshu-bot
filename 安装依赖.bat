@echo off
chcp 65001 >nul
cd /d %~dp0
echo ================================
echo     洛书bot 依赖安装
echo ================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 python，请先安装 Python 3.10 或更高版本
    pause
    exit /b 1
)

echo [1/3] 创建虚拟环境 .venv ...
if not exist ".venv" (
    python -m venv .venv
)

echo [2/3] 升级 pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q

echo [3/3] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo 安装完成！现在可以双击 启动bot.bat 运行机器人
pause
