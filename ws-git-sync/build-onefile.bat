@echo off
setlocal
cd /d "%~dp0"

if not exist "pyproject.toml" goto wrong_folder
if not exist "packaging\run_app.py" goto wrong_folder
if not exist "src\ws_git_sync\assets\ws-git-sync.ico" goto wrong_folder
where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found. Add uv to PATH and reopen this window.
    goto failed
)

echo [1/3] Preparing the Windows x64 build environment...
uv run --frozen --extra dev --python 3.12 python -c "import sys,struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8 and sys.version_info[:2] == (3,12), 'Windows x64 / Python 3.12 is required'"
if errorlevel 1 goto failed

echo [2/3] Building a single EXE...
uv run --frozen --extra dev --python 3.12 python -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --name wsGitSync ^
    --specpath "%~dp0build\onefile-spec" ^
    --workpath "%~dp0build\onefile" ^
    --distpath "%~dp0dist" ^
    --paths "%~dp0src" ^
    --icon "%~dp0src\ws_git_sync\assets\ws-git-sync.ico" ^
    --add-data "%~dp0src\ws_git_sync\assets:ws_git_sync/assets" ^
    --add-data "%~dp0README.md:." ^
    --hidden-import argparse --hidden-import json --hidden-import re ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    "%~dp0packaging\run_app.py"
if errorlevel 1 goto failed
if not exist "dist\wsGitSync.exe" goto failed

echo [3/3] Checking the packaged application...
uv run --frozen --extra dev --python 3.12 python -c "from pathlib import Path; Path('build/onefile-smoke.json').unlink(missing_ok=True)"
if errorlevel 1 goto failed
start "" /wait "%~dp0dist\wsGitSync.exe" --smoke-test --smoke-report "%~dp0build\onefile-smoke.json"
if errorlevel 1 goto failed
uv run --frozen --extra dev --python 3.12 python -c "import json; from pathlib import Path; r=json.loads(Path('build/onefile-smoke.json').read_text(encoding='utf-8')); assert r.get('status') == 'started' and r.get('frozen') is True and (r.get('width'),r.get('height'),r.get('columns')) == (1080,720,4), 'Packaged application startup check failed'"
if errorlevel 1 goto failed

echo.
echo Build and startup check completed.
echo Output: "%~dp0dist\wsGitSync.exe"
echo Distribute this EXE only. Git must still be installed on the target PC.
pause
exit /b 0

:wrong_folder
echo Put this BAT in the ws-git-sync folder beside pyproject.toml.
echo Use the complete 0.1.4 or newer source package.
:failed
echo.
echo Build failed. Read the error above. First-time dependency setup needs network access.
pause
exit /b 1
