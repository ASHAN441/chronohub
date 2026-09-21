"""空课表PRO 主程序入口。

架构要点
--------
`webview.start(gui="qt")` 让 pywebview 跑在 **PySide6** 后端上，
pywebview 的窗口本身就是一个 Qt 窗口，渲染用 QtWebEngine。

这样整个进程**只有一个 Qt 事件循环**，教务系统的内嵌浏览器
（同样是 PySide6 的 QWebEngineView）可以和主界面共存于同一进程，
不需要双进程或双事件循环。

实测结论（见 .workbuddy/scratch/qt_probe.py）：
- pywebview 的 QApplication 实例类型确认为 PySide6.QtWidgets.QApplication
- js_api 桥双向通信正常，前端 window.pywebview.api.* 调用无需改动
- 同一事件循环内可再创建 QWebEngineView 窗口
"""

import os
import sys

import webview

from .api import Api
from .config import APP_NAME, VERSION
from .people_store import storage_path

# 记住 Qt 侧对象，供后续创建内嵌浏览器窗口使用
QT = {"app": None}


def _html_path() -> str:
    """打包后从 _MEIPASS 取 web/index.html，开发时取仓库根目录下的 web/。"""
    if getattr(sys, "frozen", False):
        base = getattr(
            sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable))
        )
        return os.path.join(base, "web", "index.html")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "web", "index.html")


def _after_start(api: Api) -> None:
    """GUI 起来之后执行。

    注意：本函数运行在 pywebview 开启的**子线程**里。
    若要在其中创建/操作 Qt 控件，必须先把 QObject `moveToThread(app.thread())`，
    再用 `QMetaObject.invokeMethod(..., Qt.QueuedConnection)` 投递到主线程，
    否则事件会进入没有事件循环的子线程、槽函数永不执行（实测会挂死）。
    这里只做安全的主线程引用保存。
    """
    from qtpy.QtWidgets import QApplication

    QT["app"] = QApplication.instance()
    api.attach_qt(QT["app"])


def main() -> None:
    api = Api()
    window = webview.create_window(
        f"{APP_NAME} v{VERSION}",
        _html_path(),
        js_api=api,
        width=1280,
        height=800,
        min_size=(1080, 660),
    )
    api._window = window
    webview.start(
        _after_start,
        api,
        gui="qt",
        storage_path=storage_path(),
    )


if __name__ == "__main__":
    main()
