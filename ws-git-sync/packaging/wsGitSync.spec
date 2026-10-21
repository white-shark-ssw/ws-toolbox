# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / "packaging/run_app.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "README.md"), "."), (str(root / "src/ws_git_sync/assets"), "ws_git_sync/assets")],
    hiddenimports=["argparse", "json", "re"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name="wsGitSync",debug=False,
          bootloader_ignore_signals=False,strip=False,upx=False,console=False,
          icon=str(root / "src/ws_git_sync/assets/ws-git-sync.ico"))
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name="wsGitSync")
