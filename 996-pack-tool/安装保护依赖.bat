@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 goto python
if exist ".venv\Scripts\python.exe" goto uvinstall
uv venv .venv
if errorlevel 1 goto failed
:uvinstall
uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt
if errorlevel 1 goto failed
goto success
:python
if exist ".venv\Scripts\python.exe" goto pipinstall
python -m venv .venv
if errorlevel 1 goto failed
:pipinstall
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:success
echo.
echo 保护依赖已安装到本项目 .venv。请运行 启动工具.bat。
pause
exit /b 0
:failed
echo.
echo 安装未完成，请查看上方错误。需要 Python 3.10+ 或 uv，以及可用的网络连接。
pause
exit /b 1
