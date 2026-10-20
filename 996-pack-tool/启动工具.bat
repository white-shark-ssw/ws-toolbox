@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto localenv
where uv >nul 2>nul
if errorlevel 1 goto python
uv run --no-project python ws_pack_tool.py
goto finished
:localenv
".venv\Scripts\python.exe" ws_pack_tool.py
goto finished
:python
python ws_pack_tool.py
:finished
if errorlevel 1 (
    echo.
    echo 启动失败，请查看上方错误。需要 Python 3.10+ 和 Tkinter；保护功能请先运行 安装保护依赖.bat。
    pause
)
