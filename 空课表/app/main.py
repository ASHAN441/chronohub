import os
import sys

import webview

from .api import Api
from .storage import storage_path


def _html_path() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
        return os.path.join(base, "web", "index.html")
    return os.path.join(os.path.dirname(__file__), "..", "web", "index.html")


def main():
    api = Api()
    window = webview.create_window(
        "多人空课表",
        _html_path(),
        js_api=api,
        width=1280,
        height=780,
        min_size=(1024, 640),
    )
    api._window = window
    webview.start(storage_path=storage_path())


if __name__ == "__main__":
    main()
