"""从应用既有 ws 标识生成多尺寸 ICO，不依赖图标字体或外部图片。"""

import os
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    images = []
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        canvas = QImage(size, size, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#315b97"))
        painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.2, size * 0.2)
        font = QFont("DejaVu Sans")
        font.setBold(True)
        font.setPixelSize(round(size * 0.55))
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(QRectF(0, -size * 0.025, size, size), Qt.AlignmentFlag.AlignCenter, "ws")
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not canvas.save(buffer, "PNG"):
            raise RuntimeError("无法生成应用图标")
        images.append((size, bytes(data)))
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = []
    for size, data in images:
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    path = Path(__file__).resolve().parents[1] / "src/ws_git_sync/assets/ws-git-sync.ico"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"".join(entries) + b"".join(data for _, data in images))
    return app


if __name__ == "__main__":
    main()
