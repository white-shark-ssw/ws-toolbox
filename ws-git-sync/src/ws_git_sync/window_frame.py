"""无边框窗口的八个缩放区；优先交给系统处理拖动与 DPI。"""

from PySide6.QtCore import QLineF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget


class WindowControl(QAbstractButton):
    """窗口控制使用矢量线条绘制，避免平台图标与字体改变外观。"""

    def __init__(self, kind, text):
        super().__init__()
        self.kind = kind
        self.restored = False
        self.keyboard_focus = False
        self.setFixedSize(42, 32)
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        )
        super().focusInEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse() or self.isDown()
        close = self.kind == "close"
        if hover:
            background = "#c5353b" if close and self.isDown() else "#e34b51" if close else "#dce2e9"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        pen = QPen(QColor("#ffffff" if close and hover else "#4e5a69"), 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x, y = self.width() / 2, self.height() / 2
        if self.kind == "minimize":
            painter.drawLine(QLineF(x - 5, y + 1, x + 5, y + 1))
        elif close:
            painter.drawLine(QLineF(x - 4, y - 4, x + 4, y + 4))
            painter.drawLine(QLineF(x - 4, y + 4, x + 4, y - 4))
        elif self.restored:
            back = QPainterPath()
            back.moveTo(x - 2, y - 3)
            back.lineTo(x - 2, y - 5)
            back.lineTo(x + 5, y - 5)
            back.lineTo(x + 5, y + 2)
            back.lineTo(x + 3, y + 2)
            painter.drawPath(back)
            painter.drawRect(QRectF(x - 5, y - 2, 8, 7))
        else:
            painter.drawRoundedRect(QRectF(x - 4.5, y - 4.5, 9, 9), 1, 1)
        if self.hasFocus() and self.keyboard_focus:
            painter.setPen(QPen(QColor("#8ba4c4"), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 3, 3)


class ResizeHandle(QWidget):
    def __init__(self, window, edges, cursor):
        super().__init__(window)
        self.edges = edges
        self.origin = None
        self.initial = None
        self.setObjectName("resizeHandle")
        self.setStyleSheet("background:transparent;border:0;")
        self.setCursor(cursor)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        window = self.window()
        if window.isMaximized() or window.isFullScreen():
            return
        handle = window.windowHandle()
        if not handle or not handle.startSystemResize(self.edges):
            # 无系统缩放支持的平台仍可拖动；尺寸限制与系统路径一致。
            self.origin = event.globalPosition().toPoint()
            self.initial = window.geometry()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.origin is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return super().mouseMoveEvent(event)
        window = self.window()
        delta = event.globalPosition().toPoint() - self.origin
        rect = QRect(self.initial)
        if self.edges & Qt.Edge.LeftEdge:
            width = max(window.minimumWidth(), min(window.maximumWidth(), rect.width() - delta.x()))
            rect.setLeft(rect.right() - width + 1)
        elif self.edges & Qt.Edge.RightEdge:
            rect.setWidth(max(window.minimumWidth(), min(window.maximumWidth(), rect.width() + delta.x())))
        if self.edges & Qt.Edge.TopEdge:
            height = max(window.minimumHeight(), min(window.maximumHeight(), rect.height() - delta.y()))
            rect.setTop(rect.bottom() - height + 1)
        elif self.edges & Qt.Edge.BottomEdge:
            rect.setHeight(
                max(window.minimumHeight(), min(window.maximumHeight(), rect.height() + delta.y()))
            )
        window.setGeometry(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        self.origin = self.initial = None
        super().mouseReleaseEvent(event)


def create_resize_handles(window):
    left, top, right, bottom = Qt.Edge.LeftEdge, Qt.Edge.TopEdge, Qt.Edge.RightEdge, Qt.Edge.BottomEdge
    return [
        ResizeHandle(window, edges, cursor)
        for edges, cursor in (
            (left, Qt.CursorShape.SizeHorCursor),
            (top, Qt.CursorShape.SizeVerCursor),
            (right, Qt.CursorShape.SizeHorCursor),
            (bottom, Qt.CursorShape.SizeVerCursor),
            (left | top, Qt.CursorShape.SizeFDiagCursor),
            (right | top, Qt.CursorShape.SizeBDiagCursor),
            (left | bottom, Qt.CursorShape.SizeBDiagCursor),
            (right | bottom, Qt.CursorShape.SizeFDiagCursor),
        )
    ]


def position_resize_handles(window):
    """边缘留 6 像素，角部留 10 像素；不遮挡内容区的操作控件。"""
    w, h, band, corner = window.width(), window.height(), 6, 10
    rectangles = (
        (0, corner, band, h - 2 * corner),
        (corner, 0, w - 2 * corner, band),
        (w - band, corner, band, h - 2 * corner),
        (corner, h - band, w - 2 * corner, band),
        (0, 0, corner, corner),
        (w - corner, 0, corner, corner),
        (0, h - corner, corner, corner),
        (w - corner, h - corner, corner, corner),
    )
    for handle, rect in zip(window.resize_handles, rectangles):
        handle.setGeometry(*rect)
        handle.setVisible(not window.isMaximized() and not window.isFullScreen())
        handle.raise_()
