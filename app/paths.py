"""统一的路径解析。

为什么必须集中管理：PyInstaller onefile 打包后 `__file__` 指向临时解压目录
（`sys._MEIPASS`），重启即消失。如果散落各处用 `__file__` 推算"项目根"，
可写数据（people.json、账号库、下载的 PDF、日志）就会被写进那个临时目录，
用户下次打开数据全没了。

两类目录要分清：
- `data_dir()`     可写数据，放在 **exe 同级**的 data/ 下（开发时是项目根/data）
- `resource_dir()` 只读捆绑资源（web/ 等），打包后从 `_MEIPASS` 取
"""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """是否运行在打包后的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def base_dir() -> str:
    """可写数据的根目录（其下是 data/）。

    打包后 = exe 所在目录；开发时 = 项目根目录。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """只读捆绑资源的根目录（web/ 等在其下）。

    打包后 = `sys._MEIPASS`；开发时 = 项目根目录。

    用 `or` 而不是 `getattr(..., default)`：属性存在但为 None / 空串时也要兜底，
    getattr 的默认值只在属性**缺失**时生效。
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", None) or base_dir()
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir(create: bool = True) -> str:
    """data/ 目录的绝对路径。"""
    path = os.path.join(base_dir(), "data")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def sub_dir(name: str) -> str:
    """data/ 下的子目录（如 downloads），不存在则创建。"""
    path = os.path.join(data_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path
