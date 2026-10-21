import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPlainTextEdit

from ws_git_sync.main import execute_request
from ws_git_sync.models import AppSettings
from ws_git_sync.qt_tasks import TaskRunner
from ws_git_sync.settings import SettingsStore
from ws_git_sync.ui import MainWindow


def test_new_configuration_has_no_personal_repository_or_filter_defaults(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json").load()
    assert settings.clone_url == "" and settings.ignore_file_names == ()


def test_saved_repository_and_filters_override_public_defaults(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    settings = AppSettings(
        clone_url="https://example.invalid/team/repo.git", ignore_file_names=("local-only.lua",)
    )
    store.save(settings)
    assert store.load() == settings


def test_filter_settings_can_be_saved_without_an_initialization_url(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = SettingsStore(tmp_path / "settings.json")
    window = MainWindow(store.load(), store, TaskRunner(execute_request))

    def accept(dialog):
        dialog.findChild(QPlainTextEdit).setPlainText("local-only.lua")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", accept)

    def unexpected_warning(*args):
        raise AssertionError("Saving filters should not require an unused clone URL")

    monkeypatch.setattr(QMessageBox, "warning", unexpected_warning)
    try:
        window.edit_settings()
        saved = store.load()
        assert saved.clone_url == "" and saved.ignore_file_names == ("local-only.lua",)
    finally:
        window.close()
        app.processEvents()
