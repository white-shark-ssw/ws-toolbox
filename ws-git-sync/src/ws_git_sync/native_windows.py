"""Windows 窗口与任务栏适配；保留系统最小化/还原，客户端仍自行绘制。"""

import ctypes
import os
from pathlib import Path

from PySide6.QtGui import QIcon

APP_ID = "WhiteShark.wsGitSync.Desktop"


def set_app_identity():
    if os.name == "nt":
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        call = shell.SetCurrentProcessExplicitAppUserModelID
        call.argtypes = [ctypes.c_wchar_p]
        call.restype = ctypes.c_long
        result = call(APP_ID)
        if result < 0:
            raise OSError(f"无法设置任务栏应用标识：0x{result & 0xFFFFFFFF:08x}")


def application_icon():
    return QIcon(str(Path(__file__).parent / "assets/ws-git-sync.ico"))


class WindowsFrame:
    def __init__(self, enabled=True):
        self.user = None
        self.dwm_error = ""
        if not enabled or os.name != "nt":
            return
        from ctypes import wintypes

        self.user = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            "SetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            "SetWindowPos": (
                [
                    wintypes.HWND,
                    wintypes.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    wintypes.UINT,
                ],
                wintypes.BOOL,
            ),
            "IsZoomed": ([wintypes.HWND], wintypes.BOOL),
            "MonitorFromWindow": ([wintypes.HWND, wintypes.DWORD], wintypes.HMONITOR),
            "GetMonitorInfoW": ([wintypes.HMONITOR, ctypes.c_void_p], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.dwm.DwmSetWindowAttribute.restype = ctypes.c_long

    def configure(self, hwnd):
        if self.user is None:
            return
        # WS_OVERLAPPEDWINDOW 包含 caption/system menu/resize/minimize/maximize。
        # 保留这些语义，只通过 WM_NCCALCSIZE 隐藏原生标题栏。
        for index, mask, remove in ((-16, 0x00CF0000, 0x80000000), (-20, 0x00040000, 0x00000080)):
            current = self.user.GetWindowLongPtrW(hwnd, index)
            ctypes.set_last_error(0)
            self.user.SetWindowLongPtrW(hwnd, index, (current & ~remove) | mask)
            if ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
        if not self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037):
            raise ctypes.WinError(ctypes.get_last_error())
        # 恢复本窗口的 DWM 渲染和过渡；不修改系统全局动画偏好。
        for attribute, number in ((2, 2), (3, 0)):
            value = ctypes.c_int(number)
            result = self.dwm.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
            if result < 0:
                self.dwm_error = "Windows 未启用本窗口的 DWM 过渡效果，请检查系统设置"

    def native_event(self, message):
        if self.user is None:
            return None
        from ctypes import wintypes

        msg = wintypes.MSG.from_address(int(message))
        if msg.message != 0x0083 or not msg.wParam:  # WM_NCCALCSIZE
            return None
        if self.user.IsZoomed(msg.hWnd):

            class MonitorInfo(ctypes.Structure):
                _fields_ = [
                    ("size", wintypes.DWORD),
                    ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT),
                    ("flags", wintypes.DWORD),
                ]

            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            monitor = self.user.MonitorFromWindow(msg.hWnd, 2)
            if self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rect = wintypes.RECT.from_address(msg.lParam)
                rect.left, rect.top, rect.right, rect.bottom = (
                    info.work.left,
                    info.work.top,
                    info.work.right,
                    info.work.bottom,
                )
        return True, 0
