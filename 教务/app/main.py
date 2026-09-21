"""应用入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from .security import DpapiEncryptor
from .storage import AccountStore
from .ui.main_window import MainWindow


STYLESHEET = """
* { font-family: "Microsoft YaHei"; color: #1f2329; }
QMainWindow { background: #f5f6f8; }
QLineEdit, QComboBox {
    padding: 6px 8px;
    border: 1px solid #e6e8eb;
    border-radius: 6px;
    background: #ffffff;
    color: #1f2329;
}
QListWidget {
    border: 1px solid #e6e8eb;
    border-radius: 8px;
    background: #ffffff;
    color: #1f2329;
}
QListWidget::item { padding: 8px; border-radius: 6px; }
QListWidget::item:selected { background: #e9f7ef; color: #1f2329; }
QPushButton {
    padding: 7px 12px;
    border: 1px solid #e6e8eb;
    border-radius: 6px;
    background: #ffffff;
    color: #1f2329;
}
QPushButton:hover { background: #f3f4f6; }
QPushButton#primary {
    background: #2563eb;
    color: #ffffff;
    border: none;
    font-weight: 600;
    padding: 9px 12px;
}
QPushButton#export {
    background: #16a34a;
    color: #ffffff;
    border: none;
    font-weight: 600;
    padding: 9px 12px;
}
QComboBox { padding: 6px 10px; }
"""


def default_db_path() -> Path:
    override = os.environ.get("JIAOWU_DB_PATH")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "data" / "accounts.db"


def icon_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "app.ico"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "app.ico"


def _asset_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "assets"


def _setup_fonts(app: QApplication) -> None:
    base = _asset_dir()
    for name in ("msyh.ttc", "msyhbd.ttc"):
        path = base / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei", 10))


def main() -> None:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    _setup_fonts(app)
    icon = icon_path()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    store = AccountStore(default_db_path(), DpapiEncryptor())
    window = MainWindow(store)
    window.show()
    try:
        exit_code = app.exec()
    finally:
        store.close()
    sys.exit(exit_code)
