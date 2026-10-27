@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
if errorlevel 1 goto failed
set "TASK_BUILD_PY=build\.venv\Scripts\python.exe"
if exist "ws_pack_tool.py" goto modern
if exist "ws_pack_tool_v114.py" goto legacy
echo 未找到打包工具源码，请把本脚本放在 996-pack-tool 项目目录。
goto failed_pop
:modern
set "TASK_BUILD_ENTRY=ws_pack_tool.py"
set "TASK_BUILD_NAME=ws_pack_tool"
goto environment
:legacy
set "TASK_BUILD_ENTRY=ws_pack_tool_v114.py"
set "TASK_BUILD_NAME=ws_pack_tool_v114"
:environment
echo 正在准备独立打包环境，首次运行需要联网下载依赖...
where uv >nul 2>nul
if errorlevel 1 goto python_environment
if exist "%TASK_BUILD_PY%" goto uv_install
uv venv --python 3.12 "build\.venv"
if errorlevel 1 goto failed_pop
:uv_install
uv pip install --python "%TASK_BUILD_PY%" "pyinstaller>=6,<7"
if errorlevel 1 goto failed_pop
if not exist "ws_pack_tool.py" goto preflight
uv pip install --python "%TASK_BUILD_PY%" -r requirements.txt
if errorlevel 1 goto failed_pop
goto preflight
:python_environment
if exist "%TASK_BUILD_PY%" goto pip_install
where py >nul 2>nul
if errorlevel 1 goto python_venv
py -3 -m venv "build\.venv"
if errorlevel 1 goto failed_pop
goto pip_install
:python_venv
python -m venv "build\.venv"
if errorlevel 1 goto failed_pop
:pip_install
"%TASK_BUILD_PY%" -m pip install "pyinstaller>=6,<7"
if errorlevel 1 goto failed_pop
if not exist "ws_pack_tool.py" goto preflight
"%TASK_BUILD_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto failed_pop
:preflight
"%TASK_BUILD_PY%" -c "import sys, tkinter, PyInstaller; assert sys.version_info >= (3, 10), 'Python 3.10+ required'; print('Build Python:', sys.executable)"
if errorlevel 1 goto failed_pop
if not exist "ws_pack_tool.py" goto build_legacy
"%TASK_BUILD_PY%" -c "from lupa.lua51 import LuaRuntime; from wspack.protection.processor import protect, ProtectionOptions; from pathlib import Path; r=protect(b'local n=7; return n', ProtectionOptions(True, True), 'utf-8', '@build-check.lua'); assert LuaRuntime().execute(r.output.decode('ascii')) == 7; print('Lua protection preflight OK')"
if errorlevel 1 goto failed_pop
"%TASK_BUILD_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name "%TASK_BUILD_NAME%" --distpath "%CD%\dist" --workpath "%CD%\build\pyinstaller" --specpath "%CD%\build\spec" --icon "%CD%\app-v1.ico" --add-data "%CD%\icons:icons" --add-data "%CD%\app-v1.ico:." --collect-all lupa --hidden-import lupa.lua51 --add-data "%CD%\wspack\protection\chacha20_lua51.lua:wspack/protection" --add-data "%CD%\wspack\protection\UPSTREAM_LICENSE.txt:wspack/protection" "%CD%\%TASK_BUILD_ENTRY%"
if errorlevel 1 goto failed_pop
goto success
:build_legacy
"%TASK_BUILD_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name "%TASK_BUILD_NAME%" --distpath "%CD%\dist" --workpath "%CD%\build\pyinstaller" --specpath "%CD%\build\spec" --icon "%CD%\app-v1.ico" --add-data "%CD%\icons:icons" --add-data "%CD%\app-v1.ico:." "%CD%\%TASK_BUILD_ENTRY%"
if errorlevel 1 goto failed_pop
:success
if not exist "dist\%TASK_BUILD_NAME%.exe" goto failed_pop
echo.
echo 打包成功：%CD%\dist\%TASK_BUILD_NAME%.exe
echo 只需分发该 EXE；用户配置在 EXE 旁边创建，不嵌入 EXE。
echo 本脚本不会复制或覆盖个人配置。沿用旧配置时请复制到 EXE 所在文件夹。
echo 重新打包只更新同名 EXE，不清空 dist，也不删除旁边的配置。
popd
pause
exit /b 0
:failed_pop
popd
:failed
echo.
echo 打包失败，请查看上方错误。关闭正在运行的旧 EXE 后再试。
echo 需要 Windows、Python 3.10+ 含 Tkinter或 uv，以及可用的网络连接。
pause
exit /b 1
