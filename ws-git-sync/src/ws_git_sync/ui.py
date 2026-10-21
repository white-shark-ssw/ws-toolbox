"""桌面主窗口；布局与交互沿用确认的 1080×720 网页。"""

from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .models import AppSettings, Layout, Operation, Profile, SyncError, TaskRequest
from .native_windows import WindowsFrame, application_icon
from .settings import SettingsStore, validate_clone_url, validate_profile
from .window_frame import WindowControl, create_resize_handles, position_resize_handles

COLORS = {"preview": "#2865cb", "sync": "#21824c", "pull": "#7850bd", "push": "#b76920"}
STAGES = {
    "INITIALIZING": "初始化",
    "VALIDATING": "检查",
    "UPDATING": "拉取",
    "RESOLVING": "版本",
    "SCANNING": "扫描",
    "APPLYING": "应用",
    "STAGING": "暂存",
    "COMMITTING": "提交",
    "PUSHING": "上传",
}


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName("titlebar")
        self.setFixedHeight(42)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(5)
        logo = QLabel("ws")
        logo.setObjectName("logo")
        logo.setFixedSize(25, 25)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        layout.addWidget(QLabel("ws 多版本 Git 同步工具"))
        layout.addStretch()
        version = QLabel(f"v{__version__}")
        version.setObjectName("versionBadge")
        version.setFixedHeight(22)
        layout.addWidget(version)
        layout.addSpacing(8)
        for key, text, callback in (
            ("minimize", "最小化", window.showMinimized),
            ("maximize", "最大化", self.toggle_maximized),
            ("close", "关闭", window.close),
        ):
            button = WindowControl(key, text)
            button.clicked.connect(callback)
            layout.addWidget(button)
            setattr(self, key + "_button", button)

    def update_maximize_icon(self):
        maximized = self.window.isMaximized()
        self.maximize_button.restored = maximized
        self.maximize_button.update()
        self.maximize_button.setToolTip("还原" if maximized else "最大化")
        self.maximize_button.setAccessibleName("还原" if maximized else "最大化")

    def toggle_maximized(self):
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window.windowHandle():
            self.window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()


class QuietInput(QLineEdit):
    focused = Signal()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit()


class ProfileList(QListWidget):
    """列表项随栏宽和字体换行，避免固定高度裁掉名称或版本 ID。"""

    def __init__(self):
        super().__init__()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.update_card_sizes)

    def update_card_sizes(self):
        width = max(1, self.viewport().width() - 2 * self.spacing() - 4)
        for i in range(self.count()):
            item = self.item(i)
            card = self.itemWidget(item)
            if card is not None:
                # item 的一像素边框也占宽高，按卡片实际内容宽度测量。
                margins = card.layout().contentsMargins()
                text_width = max(1, width - 2 - margins.left() - margins.right())
                for label in card.findChildren(QLabel):
                    label.setFixedHeight(max(label.fontMetrics().height(), label.heightForWidth(text_width)))
                height = max(48, card.layout().totalHeightForWidth(max(1, width - 2)) + 4)
                size = QSize(width, height)
                if item.sizeHint() != size:
                    item.setSizeHint(size)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, store: SettingsStore, runner):
        super().__init__()
        self.settings = settings
        self.store = store
        self.runner = runner
        self.profile_key = settings.selected_profile_key
        self.loading = False
        self.log_lines: list[str] = []
        self.last_result = None
        self.active_request = None
        self.resume_operation = None
        self.initialization_asked = set()
        self.native_frame = WindowsFrame(enabled=QApplication.platformName() == "windows")
        self.setWindowTitle("ws 多版本 Git 同步工具")
        self.setWindowIcon(application_icon())
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setMinimumSize(900, 600)
        self.resize(settings.ui.width, settings.ui.height)
        body = QWidget()
        body.setObjectName("windowSurface")
        self.setCentralWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)
        repo = QWidget()
        repo.setObjectName("repoPanel")
        repo.setFixedHeight(54)
        row = QHBoxLayout(repo)
        row.setContentsMargins(18, 9, 18, 9)
        row.setSpacing(12)
        row.addWidget(QLabel("Git 仓库"))
        self.repo_edit = QuietInput(settings.repo)
        self.repo_edit.setPlaceholderText("选择本地 ws Git 仓库根目录")
        self.repo_edit.editingFinished.connect(self.save_repo)
        row.addWidget(self.repo_edit, 1)
        self.repo_browse = QPushButton("浏览")
        self.repo_browse.clicked.connect(self.browse_repo)
        row.addWidget(self.repo_browse)
        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self.edit_settings)
        row.addWidget(self.settings_button)
        outer.addWidget(repo)
        self.horizontal = QSplitter(Qt.Orientation.Horizontal)
        self.horizontal.setHandleWidth(7)
        self.horizontal.setChildrenCollapsible(False)
        outer.addWidget(self.horizontal, 1)
        side = QWidget()
        side.setObjectName("sidebar")
        side.setMinimumWidth(150)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 12, 8, 12)
        side_layout.setSpacing(8)
        tools = QHBoxLayout()
        self.add_button = QPushButton("新增")
        self.add_button.clicked.connect(self.add_profile)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete_profile)
        tools.addWidget(self.add_button)
        tools.addWidget(self.delete_button)
        tools.addStretch()
        side_layout.addLayout(tools)
        caption = QLabel("版本配置")
        caption.setObjectName("muted")
        side_layout.addWidget(caption)
        self.profiles = ProfileList()
        self.profiles.setSpacing(4)
        self.profiles.setWordWrap(True)
        self.profiles.currentItemChanged.connect(self.select_profile)
        side_layout.addWidget(self.profiles, 1)
        note = QLabel("选择一个版本执行\n真实工程始终只读")
        note.setObjectName("muted")
        side_layout.addWidget(note)
        self.horizontal.addWidget(side)
        self.vertical = QSplitter(Qt.Orientation.Vertical)
        self.vertical.setHandleWidth(5)
        self.vertical.setChildrenCollapsible(False)
        self.horizontal.addWidget(self.vertical)
        self.panels = {}
        editor = QWidget()
        editor.setObjectName("editor")
        editor.setMinimumHeight(175)
        self.panels["editor"] = editor
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(20, 8, 20, 10)
        editor_layout.setSpacing(4)
        editor_heading = QHBoxLayout()
        editor_heading.addWidget(self.muted_label("当前版本配置"))
        editor_heading.addStretch()
        self.dirty_label = self.muted_label("")
        editor_heading.addWidget(self.dirty_label)
        editor_layout.addLayout(editor_heading)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(4)
        grid.setHorizontalSpacing(8)
        self.fields = {}
        self.path_buttons = []
        for i, (key, label, placeholder) in enumerate(
            (
                ("name", "配置名称", "例如：日常开发"),
                ("server", "服务端路径", "选择真实 MirServer 根目录"),
                ("client", "客户端路径", "选择真实 GUILayout 根目录"),
                ("version_id", "版本 ID", "ver_001"),
            )
        ):
            lab = self.muted_label(label)
            lab.setFixedWidth(76)
            grid.addWidget(lab, i, 0)
            edit = QuietInput()
            edit.setPlaceholderText(placeholder)
            edit.setMinimumHeight(30)
            edit.textChanged.connect(self.draft_changed)
            edit.setAccessibleName(label)
            self.fields[key] = edit
            grid.addWidget(edit, i, 1)
            if key in ("server", "client"):
                button = QPushButton("浏览")
                button.setFixedWidth(65)
                button.clicked.connect(lambda checked=False, k=key: self.browse_source(k))
                grid.addWidget(button, i, 2)
                self.path_buttons.append(button)
        self.save_button = QPushButton("保存")
        self.save_button.setFixedWidth(65)
        self.save_button.setObjectName("save")
        self.save_button.clicked.connect(self.save_profile)
        grid.addWidget(self.save_button, 3, 2)
        editor_layout.addLayout(grid)
        editor_layout.addStretch()
        actions = QWidget()
        actions.setObjectName("actions")
        self.panels["actions"] = actions
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(20, 4, 20, 10)
        actions_layout.setSpacing(5)
        actions_layout.addWidget(self.muted_label("工程同步 / Git 操作"))
        self.action_grid = QGridLayout()
        self.action_grid.setSpacing(12)
        self.actions = {}
        for op, text in (
            (Operation.PREVIEW, "预览文件"),
            (Operation.SYNC, "一键同步并上传"),
            (Operation.PULL, "云端 → 本地 Git"),
            (Operation.PUSH, "本地 Git → 云端"),
        ):
            button = QPushButton(text)
            button.setMinimumHeight(36)
            base = QColor(COLORS[op])
            button.setStyleSheet(
                f"QPushButton {{background:{base.name()};color:white;border:0;border-radius:6px;padding:7px;}}"
                f"QPushButton:hover {{background:{base.lighter(108).name()};}}"
                f"QPushButton:pressed {{background:{base.darker(112).name()};}}"
                "QPushButton:disabled {background:#bcc4d0;color:#f1f3f6;}"
            )
            button.clicked.connect(lambda checked=False, operation=op: self.run(operation))
            self.actions[op] = button
        actions_layout.addLayout(self.action_grid)
        logs = QWidget()
        logs.setObjectName("logs")
        logs.setMinimumHeight(160)
        self.panels["logs"] = logs
        logs_layout = QVBoxLayout(logs)
        logs_layout.setContentsMargins(20, 8, 16, 10)
        logs_layout.setSpacing(6)
        log_tools = QHBoxLayout()
        log_tools.addWidget(QLabel("运行日志 / 文件差异"))
        log_tools.addStretch()
        self.bottom_button = QPushButton("回到底部")
        self.bottom_button.clicked.connect(self.scroll_bottom)
        log_tools.addWidget(self.bottom_button)
        copy = QPushButton("复制")
        copy.clicked.connect(lambda: QApplication.clipboard().setText("\n".join(self.log_lines)))
        log_tools.addWidget(copy)
        clear = QPushButton("清空")
        clear.clicked.connect(self.clear_log)
        log_tools.addWidget(clear)
        logs_layout.addLayout(log_tools)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log.verticalScrollBar().valueChanged.connect(self.log_scrolled)
        logs_layout.addWidget(self.log, 1)
        status = QWidget()
        status.setObjectName("statusbar")
        status.setFixedHeight(28)
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(16, 0, 0, 0)
        self.status = QLabel("● 就绪")
        status_layout.addWidget(self.status)
        status_layout.addStretch()
        self.context_label = self.muted_label("")
        status_layout.addWidget(self.context_label)
        status_layout.addWidget(QSizeGrip(self))
        outer.addWidget(status)
        self.runner.progress.connect(self.task_event)
        self.runner.result.connect(self.task_result)
        self.runner.finished.connect(self.task_finished)
        self.apply_layout(settings.ui)
        self.refresh_profiles()
        self.restore_position()
        self.append_log("就绪", "请选择真实工程绑定；预览不会拉取或改写文件。")
        self.bottom_button.hide()
        self.resize_handles = create_resize_handles(self)
        position_resize_handles(self)
        self.native_frame.configure(int(self.winId()))
        if self.native_frame.dwm_error:
            self.append_log("窗口", self.native_frame.dwm_error)

    def nativeEvent(self, event_type, message):
        adapter = getattr(self, "native_frame", None)
        result = adapter.native_event(message) if adapter is not None else None
        return result if result is not None else super().nativeEvent(event_type, message)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "resize_handles"):
            position_resize_handles(self)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "resize_handles"):
            position_resize_handles(self)
            self.titlebar.update_maximize_icon()

    def muted_label(self, text):
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    def apply_layout(self, layout: Layout):
        font = QFont("Microsoft YaHei" if QApplication.platformName() == "windows" else "sans-serif")
        font.setPixelSize(layout.font)
        self.setFont(font)
        self.setStyleSheet("""
QMainWindow {background:#8e959e;} QWidget {color:#303a46;background:#f1f2f4;}
QWidget#windowSurface {background:#e6e8eb;border:2px solid #9199a3;}
QWidget#titlebar {background:#e7e9ec;border-bottom:1px solid #c3c8ce;}
QWidget#repoPanel {background:#f0f1f3;border-bottom:1px solid #c9cdd3;}
QWidget#statusbar {background:#e7e9ec;border-top:1px solid #c3c8ce;}
QWidget#sidebar,QListWidget {background:#eaecf0;}
QWidget#editor {background:#f2f3f5;border:1px solid #cbd0d7;}
QWidget#actions {background:#edeff2;border:1px solid #cbd0d7;}
QWidget#logs {background:#f0f1f3;border:1px solid #cbd0d7;}
QPlainTextEdit {background:#f7f7f8;border:1px solid #c5cbd3;border-radius:4px;padding:5px;}
QLabel#logo {background:#315b97;color:white;border-radius:6px;font-weight:bold;}
QLabel#muted {color:#62758a;font-size:12px;background:transparent;}
QLabel {background:transparent;} QPushButton {border:1px solid #bcc4cf;border-radius:5px;padding:5px 10px;background:#f6f7f8;}
QPushButton:hover {background:#e5ebf3;border-color:#8ba3bf;} QPushButton:disabled {color:#8a99aa;}
QLabel#versionBadge {color:#788392;background:#eef0f3;border-radius:5px;padding:3px 8px;font-size:11px;}
QPushButton#save {color:#285da7;border-color:#a8bedf;background:#f3f7fd;}
QLineEdit {border:1px solid transparent;border-radius:4px;background:transparent;color:#62758a;padding:4px 8px;font-size:13px;}
QLineEdit:hover {background:#e9edf2;border-color:#c0cad6;}
QLineEdit:focus {background:#fafbfc;border-color:#648bc8;color:#263142;}
QListWidget {border:0;outline:0;} QListWidget::item {padding:0;border:1px solid transparent;border-radius:5px;}
QListWidget::item:hover {background:#e2e9f2;}
QListWidget::item:selected {background:#dbe6f5;color:#245ba5;border-color:#a6bfdd;}
QSplitter::handle {background:#d1d5db;} QSplitter::handle:hover {background:#aabdd3;}
QScrollBar:vertical {background:#e9ecf0;width:10px;margin:0;border:0;border-radius:5px;}
QScrollBar::handle:vertical {background:#bac3cf;min-height:28px;border:2px solid #e9ecf0;border-radius:5px;}
QScrollBar::handle:vertical:hover {background:#98a8bc;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;border:0;background:transparent;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
""")
        for name in layout.order:
            self.vertical.addWidget(self.panels[name])
        for i in range(self.vertical.count()):
            self.vertical.setStretchFactor(i, 1 if self.vertical.widget(i) == self.panels["logs"] else 0)
        for button in self.actions.values():
            self.action_grid.removeWidget(button)
        for i, button in enumerate(self.actions.values()):
            self.action_grid.addWidget(button, i // layout.columns, i % layout.columns)
        action_height = 28 + ((4 + layout.columns - 1) // layout.columns) * 42
        self.panels["actions"].setFixedHeight(action_height)
        editor_height = max(layout.editor, 175 + max(0, layout.font - 14) * 8)
        self.panels["editor"].setMinimumHeight(175 + max(0, layout.font - 14) * 8)
        self.horizontal.setSizes([layout.sidebar, max(500, self.width() - layout.sidebar - 9)])
        self.vertical.setSizes(
            [
                editor_height
                if n == "editor"
                else action_height
                if n == "actions"
                else max(180, self.height() - 350)
                for n in layout.order
            ]
        )
        log_font = QFont("Consolas")
        log_font.setPixelSize(max(12, layout.font - 2))
        self.log.setFont(log_font)
        self.profiles.update_card_sizes()

    def restore_position(self):
        """每次启动均在主屏可用区域居中；仅恢复窗口尺寸，不恢复旧坐标。"""
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.left() + (screen.width() - self.width()) // 2,
            screen.top() + (screen.height() - self.height()) // 2,
        )

    def persist(self, candidate: AppSettings) -> bool:
        try:
            self.store.save(candidate)
            self.settings = candidate
            return True
        except SyncError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return False

    def current_profile(self):
        return next((p for p in self.settings.profiles if p.key == self.profile_key), None)

    def draft(self):
        return Profile(self.profile_key, **{k: e.text().strip() for k, e in self.fields.items()})

    def is_dirty(self):
        return self.current_profile() is not None and self.draft() != self.current_profile()

    def draft_changed(self):
        if not self.loading:
            self.update_enabled()

    def save_profile(self):
        profile = self.draft()
        try:
            validate_profile(profile)
        except SyncError as exc:
            QMessageBox.warning(self, "配置未完成", str(exc))
            return False
        profiles = tuple(profile if p.key == profile.key else p for p in self.settings.profiles)
        if self.persist(replace(self.settings, profiles=profiles, selected_profile_key=profile.key)):
            self.refresh_profiles()
            self.profiles.setFocus()
            self.append_log("配置", f"已保存 {profile.name} · {profile.version_id}")
            return True
        return False

    def resolve_unsaved(self):
        if not self.is_dirty():
            return True
        choice = QMessageBox.question(
            self,
            "尚未保存",
            "当前配置有修改，是否保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        return self.save_profile() if choice == QMessageBox.StandardButton.Save else True

    def refresh_profiles(self):
        self.loading = True
        self.profiles.blockSignals(True)
        self.profiles.clear()
        if not any(p.key == self.profile_key for p in self.settings.profiles):
            self.profile_key = self.settings.profiles[0].key if self.settings.profiles else ""
        for profile in self.settings.profiles:
            item = QListWidgetItem()
            item.setSizeHint(QSize(120, 48))
            item.setToolTip(f"{profile.name}\n{profile.version_id}")
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{profile.name} {profile.version_id}")
            item.setData(Qt.ItemDataRole.UserRole, profile.key)
            self.profiles.addItem(item)
            card = QWidget()
            card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            card.setStyleSheet("QWidget {background:transparent;}")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 4, 8, 4)
            card_layout.setSpacing(1)
            card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            name = QLabel(profile.name or "未命名配置")
            name.setFont(self.font())
            name.setStyleSheet("color:#244c7b;")
            name.setWordWrap(True)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            identity = QLabel(profile.version_id or "尚未绑定")
            identity.setStyleSheet("color:#586d85;font-size:12px;")
            identity.setWordWrap(True)
            identity.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            card_layout.addWidget(name)
            card_layout.addWidget(identity)
            self.profiles.setItemWidget(item, card)
            if profile.key == self.profile_key:
                self.profiles.setCurrentItem(item)
        self.profiles.update_card_sizes()
        self.profiles.blockSignals(False)
        profile = self.current_profile()
        for key, edit in self.fields.items():
            edit.setText(getattr(profile, key) if profile else "")
            edit.setToolTip(edit.text())
        self.loading = False
        self.update_enabled()

    def select_profile(self, current, previous):
        if self.loading or current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if key == self.profile_key:
            return
        if not self.resolve_unsaved():
            self.restore_selection()
            return
        if not self.persist(replace(self.settings, selected_profile_key=key)):
            self.restore_selection()
            return
        self.profile_key = key
        self.refresh_profiles()

    def restore_selection(self):
        # 取消切换只恢复列表选择，不能重载字段而丢失仍未保存的草稿。
        self.profiles.blockSignals(True)
        for i in range(self.profiles.count()):
            item = self.profiles.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == self.profile_key:
                self.profiles.setCurrentItem(item)
                break
        self.profiles.blockSignals(False)

    def add_profile(self):
        if not self.resolve_unsaved():
            return
        profile = Profile(uuid4().hex, "新版本配置", "", "", "ver_001")
        if self.persist(
            replace(
                self.settings, profiles=(*self.settings.profiles, profile), selected_profile_key=profile.key
            )
        ):
            self.profile_key = profile.key
            self.refresh_profiles()
            self.fields["name"].setFocus()
            self.fields["name"].selectAll()

    def delete_profile(self):
        profile = self.current_profile()
        if (
            profile
            and QMessageBox.question(self, "删除配置", f"删除“{profile.name}”绑定？仅删除配置记录。")
            == QMessageBox.StandardButton.Yes
        ):
            rest = tuple(p for p in self.settings.profiles if p.key != profile.key)
            key = rest[0].key if rest else ""
            if self.persist(replace(self.settings, profiles=rest, selected_profile_key=key)):
                self.profile_key = key
                self.refresh_profiles()

    def save_repo(self):
        if not self.runner.busy and self.repo_edit.text().strip() != self.settings.repo:
            if not self.persist(replace(self.settings, repo=self.repo_edit.text().strip())):
                self.repo_edit.setText(self.settings.repo)
        self.update_enabled()

    def browse_repo(self):
        path = QFileDialog.getExistingDirectory(self, "选择本地 Git 仓库根目录", self.repo_edit.text())
        if path:
            self.repo_edit.setText(path)
            self.save_repo()

    def browse_source(self, key):
        path = QFileDialog.getExistingDirectory(
            self, "选择 MirServer 根" if key == "server" else "选择 GUILayout 根", self.fields[key].text()
        )
        if path:
            self.fields[key].setText(path)

    def edit_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("仓库设置")
        form = QFormLayout(dialog)
        fields = {}
        for key, label in (
            ("branch", "目标分支"),
            ("remote", "远端名称"),
            ("git_path", "Git 程序（留空自动查找）"),
            ("clone_url", "初始化仓库地址（可留空，初始化时填写）"),
        ):
            fields[key] = QLineEdit(getattr(self.settings, key))
            form.addRow(label, fields[key])
        ignored = QPlainTextEdit("\n".join(self.settings.ignore_file_names))
        ignored.setMinimumSize(480, 180)
        form.addRow("完整文件名过滤（每行一个）", ignored)
        form.addRow(QLabel("命中后不新增、不更新、不删除仓库已有副本。"))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            names = tuple(dict.fromkeys(n.strip() for n in ignored.toPlainText().splitlines() if n.strip()))
            try:
                if fields["clone_url"].text().strip():
                    validate_clone_url(fields["clone_url"].text().strip())
            except SyncError as exc:
                QMessageBox.warning(self, "设置无效", str(exc))
                return
            if (
                any("/" in n or "\\" in n for n in names)
                or not fields["branch"].text().strip()
                or not fields["remote"].text().strip()
            ):
                QMessageBox.warning(self, "设置无效", "分支与远端不能为空；过滤项只填写完整文件名。")
                return
            self.persist(
                replace(
                    self.settings, **{k: v.text().strip() for k, v in fields.items()}, ignore_file_names=names
                )
            )
            self.update_enabled()

    def capture_layout(self):
        normal = self.normalGeometry() if self.isMaximized() else self.geometry()
        sizes = self.vertical.sizes()
        order = self.settings.ui.order
        height = sizes[order.index("editor")] if sizes else self.settings.ui.editor
        return replace(
            self.settings.ui,
            width=normal.width(),
            height=normal.height(),
            x=normal.x(),
            y=normal.y(),
            sidebar=self.horizontal.sizes()[0],
            editor=height,
        )

    def update_enabled(self):
        busy = self.runner.busy
        profile = self.current_profile()
        dirty = self.is_dirty()
        for widget in (
            self.repo_edit,
            self.repo_browse,
            self.settings_button,
            self.add_button,
            self.profiles,
            self.horizontal.handle(1),
        ):
            widget.setEnabled(not busy)
        for i in range(1, self.vertical.count()):
            self.vertical.handle(i).setEnabled(not busy)
        self.delete_button.setEnabled(not busy and profile is not None)
        for widget in (*self.fields.values(), *self.path_buttons):
            widget.setEnabled(not busy and profile is not None)
        self.save_button.setVisible(dirty)
        self.dirty_label.setText("有未保存修改" if dirty else "")
        for operation, button in self.actions.items():
            ready = bool(self.settings.repo) and not busy and not dirty
            if operation in (Operation.PREVIEW, Operation.SYNC):
                ready = ready and profile is not None
                if ready:
                    try:
                        validate_profile(profile)
                    except SyncError:
                        ready = False
            button.setEnabled(ready)
        self.context_label.setText(f"{profile.name if profile else '未选择配置'}  ·  {self.settings.branch}")

    def run(self, operation):
        self.save_repo()
        if self.runner.busy or self.is_dirty():
            return
        request = TaskRequest(uuid4().hex, operation, self.settings, self.current_profile())
        self.last_result = None
        self.active_request = request
        self.append_log(
            "开始",
            f"{self.actions[operation].text() if operation in self.actions else '初始化 Git 仓库'} · {request.profile.name if request.profile else self.settings.branch}",
        )
        if self.runner.start(request):
            self.status.setText("● 正在执行…")
            self.update_enabled()

    def append_log(self, tag, message, level="info"):
        bar = self.log.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 3
        line = f"{datetime.now():%H:%M:%S}  [{tag}]  {message}"
        self.log_lines.append(line)
        color = {
            "error": "#bd4949",
            "success": "#278453",
            "add": "#248352",
            "modify": "#bd7b27",
            "delete": "#c45e63",
        }.get(level, "#536175")
        cursor = QTextCursor(self.log.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(line + "\n", fmt)
        if follow:
            bar.setValue(bar.maximum())
        self.bottom_button.setVisible(not follow)

    def clear_log(self):
        self.log.clear()
        self.log_lines.clear()
        self.bottom_button.hide()

    def log_scrolled(self):
        bar = self.log.verticalScrollBar()
        self.bottom_button.setVisible(bar.value() < bar.maximum() - 3)

    def scroll_bottom(self):
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        self.bottom_button.hide()

    @Slot(object)
    def task_event(self, event):
        tag = {"add": "新增", "modify": "修改", "delete": "删除", "skip": "跳过", "summary": "汇总"}.get(
            event.code, STAGES.get(event.stage, "日志")
        )
        self.append_log(
            tag,
            event.path
            if event.path and event.code in ("add", "modify", "delete")
            else event.message + (f" · {event.path}" if event.path else ""),
            event.code if event.code in ("add", "modify", "delete") else event.level,
        )
        self.status.setText("● " + STAGES.get(event.stage, event.stage) + "…")

    @Slot(object)
    def task_result(self, result):
        self.last_result = result
        self.append_log(
            "失败" if result.status == "FAILED" else "完成",
            result.message,
            "error" if result.status == "FAILED" else "success",
        )

    @Slot()
    def task_finished(self):
        request = self.active_request
        self.active_request = None
        self.status.setText(
            "● 执行失败" if self.last_result and self.last_result.status == "FAILED" else "● 就绪"
        )
        self.update_enabled()
        if request and self.last_result and self.last_result.error_code == "REPO_UNINITIALIZED":
            if request.settings.repo not in self.initialization_asked:
                self.initialization_asked.add(request.settings.repo)
                QTimer.singleShot(0, lambda: self.confirm_initialize(request))
        elif request and request.operation == Operation.INITIALIZE:
            operation = self.resume_operation
            self.resume_operation = None
            if operation and self.last_result and self.last_result.status == "SUCCEEDED":
                self.run(operation)

    def confirm_initialize(self, request):
        if self.runner.busy or self.settings.repo != request.settings.repo:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("初始化本地 Git 仓库")
        form = QFormLayout(dialog)
        note = QLabel(
            f"该目录尚未初始化为 Git 仓库：\n{request.settings.repo}\n\n"
            "是否连接下方远端，建立本地 Git 元数据与目标分支？\n"
            "空目录会检出仓库文件；已有文件的目录只补 Git 元数据，原文件保留。\n"
            "已有文件若与远端不同，后续操作会停止，请先核对 Git 副本中的差异。\n"
            "初始化与文件下载在后台执行，不修改真实工程。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        address = QLineEdit(self.settings.clone_url)
        address.setMinimumWidth(480)
        form.addRow("远端地址", address)
        form.addRow("分支", QLabel(self.settings.branch))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("初始化并连接")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                validate_clone_url(address.text().strip())
            except SyncError as exc:
                QMessageBox.warning(self, "仓库地址无效", str(exc))
                return
            if self.persist(replace(self.settings, clone_url=address.text().strip())):
                self.resume_operation = request.operation
                self.run(Operation.INITIALIZE)

    def closeEvent(self, event):
        if self.runner.busy:
            QMessageBox.information(
                self, "任务仍在运行", "请等待当前任务结束后关闭；不会强制中断文件写入或 Git 操作。"
            )
            event.ignore()
            return
        if not self.resolve_unsaved() or not self.persist(replace(self.settings, ui=self.capture_layout())):
            event.ignore()
            return
        event.accept()
