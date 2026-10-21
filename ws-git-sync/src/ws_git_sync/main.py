"""应用组装入口。单实例锁只约束本工具，不约束其他 Git 客户端。"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .git_ops import Git
from .models import AppSettings, Profile, SyncError, TaskResult
from .native_windows import application_icon, set_app_identity
from .qt_tasks import TaskRunner
from .settings import SettingsStore, config_directory, migrate_legacy_settings
from .tasks import run_task
from .ui import MainWindow
from .versions import resolve_context


def execute_request(request, emit):
    """在组装入口注入 Git；实际探测和任务执行均由 worker 调用。"""
    try:
        git = Git(Path(request.settings.repo), request.settings.git_path)
    except Exception as exc:
        return TaskResult(request.task_id, "FAILED", "VALIDATING", str(exc), error_code="GIT_START")
    return run_task(request, git, emit)


def main() -> int:
    parser = argparse.ArgumentParser(description="ws 多版本 Git 同步工具")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-version-repo", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-version-id", default="ver_001", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_version_repo and not args.smoke_test:
        parser.error("版本解析启动检查只能配合 --smoke-test 使用")
    set_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("wsGitSync")
    app.setOrganizationName("ws")
    app.setWindowIcon(application_icon())
    temporary = tempfile.TemporaryDirectory(prefix="ws-git-ui-smoke-") if args.smoke_test else None
    directory = Path(temporary.name) if temporary else config_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.critical(None, "无法启动", f"无法创建程序旁的配置目录，请使用可写目录：{exc}")
        return 1
    lock = QLockFile(str(directory / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if lock.error() == QLockFile.LockError.PermissionError:
            QMessageBox.critical(
                None, "目录不可写", f"程序所在目录不可写，请移至有写入权限的目录：\n{directory}"
            )
        else:
            QMessageBox.information(
                None, "无法获取程序锁", f"此目录的程序可能已在运行，请检查：\n{directory}"
            )
        return 1
    store = SettingsStore(directory / "settings.json")
    try:
        if not args.smoke_test:
            migrate_legacy_settings(directory)
        settings = AppSettings() if args.smoke_test else store.load()
    except SyncError as exc:
        QMessageBox.critical(None, "无法读取配置", str(exc))
        lock.unlock()
        return 1
    runner = TaskRunner(execute_request)
    window = MainWindow(settings, store, runner)
    window.show()
    if args.smoke_test:

        def finish_smoke():
            observed = {
                "width": window.width(),
                "height": window.height(),
                "columns": settings.ui.columns,
                "frozen": bool(getattr(sys, "frozen", False)),
                "python": sys.version.split()[0],
                "status": "started",
            }
            exit_code = 0
            if args.smoke_version_repo:
                try:
                    profile = Profile("smoke", "smoke", str(directory), str(directory), args.smoke_version_id)
                    context = resolve_context(args.smoke_version_repo, profile)
                    observed["resolver_version_id"] = context.version_id
                except Exception as exc:
                    observed.update(status="failed", error=str(exc))
                    exit_code = 1
            if args.smoke_report:
                args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
                args.smoke_report.write_text(
                    json.dumps(observed, indent=2),
                    encoding="utf-8",
                )
            app.exit(exit_code)

        QTimer.singleShot(250, finish_smoke)
    result = app.exec()
    lock.unlock()
    if temporary:
        temporary.cleanup()
    return result
