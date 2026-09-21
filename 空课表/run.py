"""程序入口：开发时用 `python run.py`，打包时也用这个文件作为入口。"""

import os
import sys
import time
import traceback


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _log_path() -> str:
    return os.path.join(_base_dir(), "启动错误.log")


def _run_log_path() -> str:
    return os.path.join(_base_dir(), "运行日志.log")


def write_log(line: str, path: str = "") -> str:
    """追加一行日志（失败时静默忽略，不能因为写日志影响启动）。"""
    target = path or _run_log_path()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] {line}\n")
    except Exception:
        pass
    return target


def write_error(text: str, path: str = "") -> None:
    """把启动失败的详细信息写到 启动错误.log。"""
    try:
        with open(path or _log_path(), "w", encoding="utf-8") as stream:
            stream.write("程序启动失败，详细信息如下：\n\n")
            stream.write(text)
    except Exception:
        pass


def show_error_box(message: str, dialog=None) -> None:
    """弹窗提示启动失败，避免双击后只看到一闪而过。"""
    try:
        if dialog is None:
            import ctypes

            dialog = ctypes.windll.user32.MessageBoxW
        dialog(None, str(message), "多人空课表 启动失败", 0x10)
    except Exception:
        pass


def main() -> None:
    write_log("开始启动")
    try:
        from app.main import main as app_main

        app_main()
        write_log("正常关闭")
    except Exception:
        detail = traceback.format_exc()
        write_log("启动失败：" + detail.strip().splitlines()[-1])
        write_error(detail)
        show_error_box(
            "程序启动失败，详细信息已写入同目录的「启动错误.log」。\n\n"
            + detail.strip().splitlines()[-1]
        )
        raise


if __name__ == "__main__":
    main()
