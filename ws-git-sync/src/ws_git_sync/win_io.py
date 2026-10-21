"""Windows 源文件共享只读。允许对方读写/删除共享，不能绕过独占锁。"""

import ctypes
import os
from pathlib import Path


def read_shared(path: Path) -> bytes:
    if os.name != "nt":
        return path.read_bytes()
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        close(handle)
        raise
    # 文件描述符接管 HANDLE；fdopen 关闭时释放，不再额外 CloseHandle。
    with os.fdopen(fd, "rb") as stream:
        return stream.read()
