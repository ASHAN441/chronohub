"""依赖自检 —— 供 启动.bat 判断某个 Python 解释器是否能直接用。

只用 find_spec 查模块是否存在，不真正 import（PySide6 导入较慢）。
全部就绪退出码 0，否则 1，并把缺失的模块名打到 stderr。

单独放一个文件而不是在 .bat 里写内联 python -c，原因有二：
  1. 内联代码里的括号会破坏批处理 for/if 块的解析（踩过）
  2. 独立文件可以直接用 python 跑起来验证
"""

import importlib.util
import sys

REQUIRED = ("webview", "qtpy", "pdfplumber", "PySide6")


def missing_modules() -> list:
    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


def main() -> int:
    missing = missing_modules()
    if missing:
        sys.stderr.write("missing: " + ", ".join(missing) + "\n")
        return 1
    sys.stdout.write("ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
