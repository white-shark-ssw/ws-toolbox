"""Qt 线程适配；核心事件仅在主线程呈现。"""

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from .models import TaskRequest


class Worker(QObject):
    progress = Signal(object)
    result = Signal(object)
    done = Signal()

    def __init__(self, request: TaskRequest, execute: Callable):
        super().__init__()
        self.request = request
        self.execute = execute

    @Slot()
    def run(self):
        try:
            self.result.emit(self.execute(self.request, self.progress.emit))
        except Exception as exc:
            from .models import TaskResult

            self.result.emit(
                TaskResult(self.request.task_id, "FAILED", "VALIDATING", str(exc), error_code="UNEXPECTED")
            )
        finally:
            self.done.emit()


class TaskRunner(QObject):
    progress = Signal(object)
    result = Signal(object)
    finished = Signal()

    def __init__(self, execute: Callable, parent=None):
        super().__init__(parent)
        self.execute = execute
        self._thread = None
        self.worker = None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, request: TaskRequest):
        if self.busy:
            return False
        self._thread = QThread(self)
        self.worker = Worker(request, self.execute)
        self.worker.moveToThread(self._thread)
        self._thread.started.connect(self.worker.run)
        # 不用 event/thread 作为自定义成员名，保留 QObject 同名方法。
        self.worker.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.worker.result.connect(self._result, Qt.ConnectionType.QueuedConnection)
        self.worker.done.connect(self._thread.quit)
        self.worker.done.connect(self.worker.deleteLater)
        self._thread.finished.connect(self._finished)
        self._thread.start()
        return True

    @Slot(object)
    def _progress(self, event):
        self.progress.emit(event)

    @Slot(object)
    def _result(self, result):
        self.result.emit(result)

    @Slot()
    def _finished(self):
        # finished 发出后线程局部清理仍可能继续，wait 确认释放完成再解锁界面。
        self._thread.wait()
        self._thread.deleteLater()
        self._thread = None
        self.worker = None
        self.finished.emit()
