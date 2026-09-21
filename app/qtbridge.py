"""把调用投递到 Qt 主线程执行。

为什么需要它：pywebview 的 js_api 方法运行在**子线程**，而 Qt 控件（QMainWindow、
QWebEngineView、QTimer）只能在主线程创建和操作。

踩过的坑：一开始在子线程里创建 QObject 后直接 `QMetaObject.invokeMethod(...,
Qt.QueuedConnection)`，结果事件被投递到**没有事件循环的子线程**，槽函数永远不执行、
进程挂死。必须先 `moveToThread(app.thread())` 把对象挪到主线程。

本模块把这个样板封装成唯一入口，并把主线程里的异常兜住——否则异常会冲进 Qt 事件
循环，轻则静默丢失，重则崩溃，而且在 pythonw 下没有控制台可看。
"""

from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QMetaObject, QObject, Qt, Slot


class _Invoker(QObject):
    """任务队列的宿主。构造于子线程，随即被挪到 Qt 主线程。"""

    def __init__(self, qt_app) -> None:
        super().__init__()
        self.moveToThread(qt_app.thread())
        self._lock = threading.Lock()
        self._queue: list = []

    def push(self, func) -> None:
        with self._lock:
            self._queue.append(func)

    @Slot()
    def run(self) -> None:
        """在主线程里把队列排空。

        一次 invoke 会排空整个队列：多次 push 只换来多次（后来的会空跑）投递，
        顺序与执行都不受影响。
        """
        while True:
            with self._lock:
                if not self._queue:
                    return
                func = self._queue.pop(0)
            try:
                func()
            except Exception:  # noqa: BLE001
                _record_error(traceback.format_exc())


_error_lock = threading.Lock()
_last_error: str | None = None


def _record_error(text: str) -> None:
    global _last_error
    with _error_lock:
        _last_error = text


def last_error() -> str | None:
    """最近一次主线程任务抛出的异常（供 api 层读取并展示）。"""
    with _error_lock:
        return _last_error


def clear_last_error() -> None:
    global _last_error
    with _error_lock:
        _last_error = None


class QtBridge:
    """对外的投递入口。api.py 在子线程里调用 `run()`。"""

    def __init__(self, qt_app) -> None:
        self._app = qt_app
        self._invoker = _Invoker(qt_app) if qt_app is not None else None

    def ready(self) -> bool:
        return self._invoker is not None

    def run(self, func) -> bool:
        """把 func 排到 Qt 主线程执行；返回是否成功投递。"""
        if self._invoker is None:
            return False
        self._invoker.push(func)
        return bool(
            QMetaObject.invokeMethod(self._invoker, "run", Qt.QueuedConnection)
        )
