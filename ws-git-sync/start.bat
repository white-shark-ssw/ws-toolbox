@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found. Please add uv to PATH and reopen this window.
    goto failed
)
uv run --frozen --python 3.12 python -m ws_git_sync
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Startup failed. See the uv error above. Network access may be needed on first run.
pause
exit /b 1
