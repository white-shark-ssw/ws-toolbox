"""在 Windows 原生构建目录包并验证冻结程序能启动。"""

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "win32" or platform.architecture()[0] != "64bit" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Windows 发行包必须在 Windows x64 的 Python 3.12 环境构建。")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(root / "packaging/wsGitSync.spec"),
        ],
        cwd=root,
        check=True,
    )
    report = root / "build/frozen-smoke.json"
    subprocess.run(
        [str(root / "dist/wsGitSync/wsGitSync.exe"), "--smoke-test", "--smoke-report", str(report)],
        cwd=root,
        check=True,
        timeout=60,
    )
    observed = json.loads(report.read_text(encoding="utf-8"))
    if observed.get("frozen") is not True or (observed["width"], observed["height"], observed["columns"]) != (
        1080,
        720,
        4,
    ):
        raise SystemExit("冻结程序启动结果与布局契约不符")
    output = shutil.make_archive(str(root / "dist/wsGitSync-windows-x64"), "zip", root / "dist", "wsGitSync")
    print(output)


if __name__ == "__main__":
    main()
