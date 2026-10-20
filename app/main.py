"""时枢 ChronoHub 主程序入口。

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

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import webview

from .api import Api
from .config import APP_NAME, VERSION
from .paths import resource_dir, sub_dir
from .people_store import storage_path

# 记住 Qt 侧对象，供后续创建内嵌浏览器窗口使用
QT = {"app": None}


def _html_path() -> str:
    """界面文件路径。打包后从 _MEIPASS 取，开发时取项目根目录下的 web/。"""
    return os.path.join(resource_dir(), "web", "index.html")


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


def _run() -> None:
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


def _message_box(message: str) -> None:
    """用 Win32 原生弹窗报错。

    此时 Qt 可能根本没起来（依赖缺失、资源缺失等），所以不能依赖 Qt 的对话框。
    """
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:  # noqa: BLE001
        pass


def _report_fatal() -> str:
    """启动失败时落盘 + 弹窗。

    打包成 exe 后是 --windowed（没有控制台），异常如果只往上抛，用户双击后
    只会看到"什么都没发生"，无从排查。所以必须写日志并弹窗告知。
    """
    text = traceback.format_exc()
    log_path = None
    try:
        log_path = Path(sub_dir("logs")) / "startup-error.log"
        with open(log_path, "a", encoding="utf-8") as handle:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"\n[{stamp}]\n{text}\n")
    except Exception:  # noqa: BLE001
        log_path = None

    last_line = text.strip().splitlines()[-1] if text.strip() else "未知错误"
    hint = (
        f"完整信息已写入：\n{log_path}"
        if log_path
        else "（日志写入失败，请确认程序所在目录可写）"
    )
    _message_box(f"{APP_NAME} 启动失败。\n\n{last_line}\n\n{hint}")
    return text


def _tk_selfcheck() -> int:
    """诊断用：逐项验证导出链路依赖在打包后是否可用。

    导入课表 / 导出值班表都靠 tkinter 弹对话框，导出还要 python-docx + lxml。
    这些在开发环境下一定正常，打包后可能因为数据文件/DLL 没带全而失败——
    而且只有用户点下去时才暴露。所以留一个可主动验证的入口：

        .\\ChronoHub.exe --selfcheck      （--tk-check 是它的别名）

    结果写到 data/logs/selfcheck.log。
    """
    lines = []

    def check(label, fn):
        try:
            detail = fn()
            lines.append(f"[OK]   {label}" + (f" — {detail}" if detail else ""))
        except Exception:  # noqa: BLE001
            lines.append(f"[FAIL] {label}\n{traceback.format_exc()}")

    def _tk():
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return ""

    def _docx():
        from docx import Document

        return f"Document={Document.__name__}"

    def _lxml():
        import lxml.etree as etree

        return f"LXML={etree.LXML_VERSION}"

    def _template():
        from .docx_export import template_path

        path = template_path()
        if not path:
            raise FileNotFoundError("找不到值班表.docx")
        return path

    def _export():
        from .docx_export import export_roster

        roster = {
            "week": 99,
            "assignments": [
                {"day": 1, "block": 0, "start": 1, "end": 2,
                 "kind": "member", "person": "SELFTEST"},
                {"day": 1, "block": 4, "start": 9, "end": 10,
                 "kind": "dual", "minister": "SELFTEST", "hall": "SELFTEST"},
            ],
        }
        out = str(Path(sub_dir("exports")) / "selftest.docx")
        export_roster(roster, {"SELFTEST": "自检"}, out)
        return f"{out} ({os.path.getsize(out)} bytes)"

    check("tkinter 创建窗口", _tk)
    check("import lxml.etree", _lxml)
    check("import python-docx", _docx)
    check("找到模板", _template)
    check("按模板导出 docx", _export)

    text = "\n".join(lines)
    try:
        log = Path(sub_dir("logs")) / "selfcheck.log"
        log.write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return 0


def main() -> int:
    if "--selfcheck" in sys.argv or "--tk-check" in sys.argv:
        return _tk_selfcheck()

    try:
        _run()
    except Exception:  # noqa: BLE001
        _report_fatal()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
