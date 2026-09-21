"""使用 Windows DPAPI 对凭据进行加解密。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class EncryptionError(Exception):
    """加密或解密失败时抛出。"""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob_to_bytes(blob: "_DATA_BLOB") -> bytes:
    if not blob.pbData or blob.cbData == 0:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


class DpapiEncryptor:
    """使用 Windows DPAPI（当前用户作用域）加密凭据。"""

    def __init__(self) -> None:
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL

        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL

        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def encrypt(self, data: bytes) -> bytes:
        in_buffer = ctypes.create_string_buffer(data, len(data))
        in_blob = _DATA_BLOB(
            len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char))
        )
        out_blob = _DATA_BLOB()

        ok = self._crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            raise EncryptionError(f"加密失败（错误码 {ctypes.get_last_error()}）")
        try:
            return _blob_to_bytes(out_blob)
        finally:
            if out_blob.pbData:
                self._kernel32.LocalFree(out_blob.pbData)

    def decrypt(self, data: bytes) -> bytes:
        in_buffer = ctypes.create_string_buffer(data, len(data))
        in_blob = _DATA_BLOB(
            len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char))
        )
        out_blob = _DATA_BLOB()

        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            raise EncryptionError(f"解密失败（错误码 {ctypes.get_last_error()}）")
        try:
            return _blob_to_bytes(out_blob)
        finally:
            if out_blob.pbData:
                self._kernel32.LocalFree(out_blob.pbData)
