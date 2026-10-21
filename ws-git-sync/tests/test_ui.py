import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from ws_git_sync.main import execute_request
from ws_git_sync.models import AppSettings, Operation, Profile
from ws_git_sync.qt_tasks import TaskRunner
from ws_git_sync.settings import SettingsStore
from ws_git_sync.ui import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    first = Profile("one", "版本一", "/source/MirServer", "/source/GUILayout", "ver_001")
    second = replace(first, key="two", name="版本二", version_id="ver_002")
    settings = AppSettings(repo="/git", profiles=(first, second), selected_profile_key="one")
    runner = TaskRunner(execute_request)
    win = MainWindow(settings, SettingsStore(tmp_path / "settings.json"), runner)
    win.show()
    app.processEvents()
    yield win
    for edit in win.fields.values():
        edit.setText(getattr(win.current_profile(), next(k for k, v in win.fields.items() if v is edit)))
    win.close()
    app.processEvents()


def test_geometry_colors_and_saved_fields_are_editable(window, app):
    win = window
    assert (win.width(), win.height()) == (1080, 720)
    assert win.panels["editor"].height() == 175
    assert len({button.y() for button in win.actions.values()}) == 1
    assert not win.save_button.isVisible()
    assert all(edit.isEnabled() and not edit.isReadOnly() for edit in win.fields.values())
    assert len({button.styleSheet() for button in win.actions.values()}) == 4
    win.horizontal.setSizes([240, 800])
    app.processEvents()
    assert (win.width(), win.height()) == (1080, 720)


def test_cancel_switch_keeps_edit_draft(window, monkeypatch):
    win = window
    win.fields["name"].setText("未保存名称")
    assert win.save_button.isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    win.profiles.setCurrentRow(1)
    assert win.profile_key == "one" and win.profiles.currentRow() == 0
    assert win.fields["name"].text() == "未保存名称"


def test_save_then_switch_and_reload(window, monkeypatch):
    win = window
    win.fields["name"].setText("保存后的名称")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Save)
    win.profiles.setCurrentRow(1)
    assert win.profile_key == "two"
    loaded = win.store.load()
    assert loaded.profiles[0].name == "保存后的名称" and loaded.selected_profile_key == "two"


def test_discard_switch_does_not_persist_draft(window, monkeypatch):
    window.fields["name"].setText("不要保存")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    window.profiles.setCurrentRow(1)
    assert window.profile_key == "two" and window.settings.profiles[0].name == "版本一"


def test_qt_task_runs_off_main_thread_and_unlocks_after_finish(app, project, tmp_path):
    win = MainWindow(project.settings, SettingsStore(tmp_path / "ui.json"), TaskRunner(execute_request))
    win.show()
    observed = []
    original = win.runner.execute

    def observe(request, emit):
        observed.append(QThread.currentThread() != app.thread())
        time.sleep(0.03)
        return original(request, emit)

    win.runner.execute = observe
    win.run(Operation.PREVIEW)
    assert win.runner.busy and not win.add_button.isEnabled()
    assert not win.actions[Operation.SYNC].isEnabled()
    end = time.monotonic() + 8
    while win.runner.busy and time.monotonic() < end:
        app.processEvents()
        QTest.qWait(5)
    assert not win.runner.busy and observed == [True]
    assert win.last_result.status == "SUCCEEDED" and win.add_button.isEnabled()
    assert "未拉取" in "\n".join(win.log_lines)
    win.close()


def test_scrolling_back_does_not_force_follow_bottom(window, app):
    for i in range(100):
        window.append_log("测试", str(i))
    app.processEvents()
    window.log.verticalScrollBar().setValue(0)
    window.append_log("测试", "新日志")
    assert window.log.verticalScrollBar().value() == 0
    assert window.bottom_button.isVisible()
    window.scroll_bottom()
    assert window.log.verticalScrollBar().value() == window.log.verticalScrollBar().maximum()


def test_repeated_background_tasks_keep_qobject_event_and_thread_methods(app, window):
    import shiboken6

    from ws_git_sync.models import TaskRequest, TaskResult

    runner = window.runner
    runner.execute = lambda request, emit: TaskResult(request.task_id, "SUCCEEDED", "SCANNING", "done")
    assert callable(runner.event) and callable(runner.thread)
    for i in range(12):
        request = TaskRequest(str(i), Operation.PREVIEW, window.settings, window.current_profile())
        assert runner.start(request)
        end = time.monotonic() + 3
        while runner.busy and time.monotonic() < end:
            app.processEvents()
            QTest.qWait(1)
        assert not runner.busy and shiboken6.isValid(window.fields["name"])
        assert window.fields["name"].text() == "版本一"


@pytest.mark.parametrize("sidebar,font", [(150, 14), (180, 14), (150, 18), (260, 18)])
def test_profile_text_wraps_without_clipping(window, app, sidebar, font):
    from PySide6.QtWidgets import QLabel

    profile = replace(window.current_profile(), name="示例版本配置中文长名称用于测试自动换行文本")
    window.settings = replace(
        window.settings, profiles=(profile,), ui=replace(window.settings.ui, sidebar=sidebar, font=font)
    )
    window.apply_layout(window.settings.ui)
    window.refresh_profiles()
    app.processEvents()
    window.profiles.update_card_sizes()
    app.processEvents()
    card = window.profiles.itemWidget(window.profiles.item(0))
    name, identity = card.findChildren(QLabel)
    for label in (name, identity):
        assert label.height() >= label.heightForWidth(label.width())
        assert card.rect().contains(label.geometry())
    assert identity.text() == "ver_001"
    assert name.geometry().bottom() < identity.geometry().top()
    assert window.profiles.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("index", range(8))
def test_all_window_edges_and_corners_drag(window, app, monkeypatch, index):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent, QWindow

    monkeypatch.setattr(QWindow, "startSystemResize", lambda *a: False)
    handle = window.resize_handles[index]
    point = handle.mapToGlobal(handle.rect().center())
    before = window.geometry()
    edges = handle.edges
    dx = -36 if edges & Qt.Edge.LeftEdge else 36 if edges & Qt.Edge.RightEdge else 0
    dy = -24 if edges & Qt.Edge.TopEdge else 24 if edges & Qt.Edge.BottomEdge else 0
    for kind, offset, button, buttons in (
        (QEvent.Type.MouseButtonPress, QPointF(), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseMove, QPointF(dx, dy), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, QPointF(dx, dy), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(
            kind,
            QPointF(handle.rect().center()),
            QPointF(point) + offset,
            button,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(handle, event)
    app.processEvents()
    assert window.width() == before.width() + abs(dx)
    assert window.height() == before.height() + abs(dy)
    if edges & Qt.Edge.LeftEdge:
        assert window.geometry().right() == before.right()
    if edges & Qt.Edge.TopEdge:
        assert window.geometry().bottom() == before.bottom()


def test_window_border_handles_hide_when_maximized_and_layout_saves_size(window, app):
    assert all(h.isVisible() for h in window.resize_handles)
    window.resize(1240, 820)
    app.processEvents()
    window.showMaximized()
    app.processEvents()
    assert all(not h.isVisible() for h in window.resize_handles)
    assert (window.capture_layout().width, window.capture_layout().height) == (1240, 820)
    window.showNormal()
    app.processEvents()
    assert all(h.isVisible() for h in window.resize_handles)


def test_title_controls_maximize_and_restore_with_visible_icons(window, app):
    from PySide6.QtWidgets import QPushButton

    assert all(button.text() != "调整布局" for button in window.findChildren(QPushButton))
    button = window.titlebar.maximize_button
    assert button.kind == "maximize" and not button.restored
    button.click()
    app.processEvents()
    assert window.isMaximized() and button.toolTip() == "还原"
    assert button.restored
    assert all(not h.isVisible() for h in window.resize_handles)
    button.click()
    app.processEvents()
    assert not window.isMaximized() and button.toolTip() == "最大化"
    assert not button.restored
    assert all(h.isVisible() for h in window.resize_handles)


def test_startup_centers_in_available_screen_instead_of_saved_coordinates(window, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QPoint, QRect

    screen = QRect(100, 200, 1920, 1040)
    monkeypatch.setattr(
        QApplication, "primaryScreen", lambda: SimpleNamespace(availableGeometry=lambda: screen)
    )
    window.settings = replace(window.settings, ui=replace(window.settings.ui, x=-7000, y=7000))
    window.restore_position()
    assert window.pos() == QPoint(100 + (1920 - window.width()) // 2, 200 + (1040 - window.height()) // 2)


def test_uninitialized_directory_prompts_once_without_writing_until_confirmed(
    window, app, monkeypatch, tmp_path
):
    root = tmp_path / "not-a-repo"
    window.settings = replace(window.settings, repo=str(root))
    window.repo_edit.setText(str(root))
    prompted = []
    monkeypatch.setattr(window, "confirm_initialize", lambda request: prompted.append(request))
    for _ in range(2):
        window.run(Operation.PULL)
        end = time.monotonic() + 6
        while window.runner.busy and time.monotonic() < end:
            app.processEvents()
            QTest.qWait(5)
        app.processEvents()
    assert len(prompted) == 1 and prompted[0].operation == Operation.PULL
    assert not root.exists()


def test_confirmed_initialization_runs_in_background_then_resumes_operation(
    app, project, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QDialog

    root = tmp_path / "new-clone"
    settings = replace(project.settings, repo=str(root), clone_url=str(project.remote))
    win = MainWindow(settings, SettingsStore(tmp_path / "init-ui.json"), TaskRunner(execute_request))
    win.show()
    monkeypatch.setattr(QDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    win.run(Operation.PULL)
    end = time.monotonic() + 10
    while time.monotonic() < end:
        app.processEvents()
        QTest.qWait(5)
        if not win.runner.busy and win.last_result and "Git 副本已更新" in win.last_result.message:
            break
    assert win.last_result.status == "SUCCEEDED", win.last_result
    assert (root / ".git").is_dir() and (root / "README.md").is_file()
    assert win.resume_operation is None
    win.close()


def test_application_icon_contains_multiple_sizes(window):
    assert not window.windowIcon().isNull()
    sizes = {size.width() for size in window.windowIcon().availableSizes()}
    assert {16, 32, 64, 256}.issubset(sizes)


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows 原生窗口")
def test_windows_native_styles_and_minimize(window, app):
    import ctypes

    if QApplication.platformName() != "windows":
        pytest.skip("需要 Windows QPA，不能用 offscreen 替代")
    hwnd = int(window.winId())
    assert window.native_frame.user.GetWindowLongPtrW(hwnd, -16) & 0x00CF0000 == 0x00CF0000
    assert window.native_frame.user.GetWindowLongPtrW(hwnd, -20) & 0x00040000
    window.titlebar.minimize_button.click()
    app.processEvents()
    assert ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd))
    window.showNormal()
    app.processEvents()
    assert not window.isMinimized()
