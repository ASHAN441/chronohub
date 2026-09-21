"""Windows 剪贴板写入。

为什么要自己写：原「教务系统管理器」用 `QApplication.clipboard()` 复制密码，
但 Qt 的 QClipboard **只能在主线程使用**，而 pywebview 的 js_api 方法运行在
子线程。这里直接用 Win32 API，任意线程都可安全调用。

「复制密码」沿用原行为：写入后 30 秒自动清空剪贴板（仅当内容仍是当初写入的密码时）。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL


def set_text(text: str) -> bool:
    """把文本写入剪贴板。成功返回 True。"""
    data = str(text).encode("utf-16-le") + b"\x00\x00"
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    pointer = _kernel32.GlobalLock(handle)
    if not pointer:
        _kernel32.GlobalFree(handle)
        return False
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        _kernel32.GlobalUnlock(handle)

    if not _user32.OpenClipboard(None):
        _kernel32.GlobalFree(handle)
        return False
    try:
        _user32.EmptyClipboard()
        # SetClipboardData 成功后所有权移交给系统，不能再 GlobalFree
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            return False
    finally:
        _user32.CloseClipboard()
    return True


def get_text() -> str:
    """读取剪贴板文本；非文本或读取失败时返回空串。"""
    if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    if not _user32.OpenClipboard(None):
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def clear_text() -> None:
    """清空剪贴板。"""
    if _user32.OpenClipboard(None):
        try:
            _user32.EmptyClipboard()
        finally:
            _user32.CloseClipboard()


def set_text_temporary(text: str, seconds: int = 30) -> bool:
    """写入文本，并在 seconds 秒后清空（仅当剪贴板内容仍是这段文本）。"""
    if not set_text(text):
        return False

    def _expire() -> None:
        if get_text() == str(text):
            clear_text()

    timer = threading.Timer(seconds, _expire)
    timer.daemon = True
    timer.start()
    return True
