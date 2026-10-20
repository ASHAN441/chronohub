# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 空课表PRO。

构建（在仓库根目录执行）：
    python -m PyInstaller packaging/KongKeBiaoPRO.spec --noconfirm --clean

产出：
    dist/空课表PRO.exe     单文件版（onefile）

为什么用 onefile：便于直接发给别人，双击即用。
代价是每次启动要把 PySide6 + QtWebEngine 解压到临时目录，首次启动偏慢。

注意点
------
- `web/` 必须作为数据一起打包，否则界面加载不出来（app/main.py 的
  `_html_path()` 打包后会从 `sys._MEIPASS` 里找 web/index.html）。
- 数据目录（people.json / 账号库 / 下载的 PDF / 日志）由 app/paths.py 决定，
  **打包后落在 exe 同级目录**，不在 _MEIPASS 里——否则重启就丢。
- 内嵌教务系统浏览器依赖 pywebview 的 Qt 后端，需要 qtpy 与 QtWebEngine。
- **不能排除 tkinter**：导入课表时用它弹文件/文件夹选择对话框。
"""

import os

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(SPEC_DIR)

APP_NAME = "空课表PRO"
ICON = os.path.join(SPEC_DIR, f"{APP_NAME}.ico")

hiddenimports = [
    # pywebview 的 Qt 后端（gui="qt" 时才会用到，静态分析看不出来）
    "webview.platforms.qt",
    "qtpy",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
]

# 刻意**不用** collect_submodules('pdfplumber')：那会把 pdfplumber.display 和
# pdfplumber.utils.generic 也收进来，而这两个模块 import pandas —— 实测
# `import pdfplumber` 本身并不会加载 pandas，只有这两个子模块会。
# 强行收集等于凭空带上 pandas + numpy（上百 MB），而我们只用 pdfplumber.open。
# 让 PyInstaller 顺着真实 import 链分析即可；pdfminer 有官方 hook 负责。
excludes = [
    "pandas",
    "numpy",
    "matplotlib",
    "PyQt5",
    "PyQt6",
    "IPython",
    "pytest",
]

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "web"), "web"),
        # 值班表导出模板。用户也可以把同名文件放在 exe 同级来替换它
        # （见 app/docx_export.template_path 的查找顺序）。
        (os.path.join(ROOT, "值班表.docx"), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 窗口程序：不留控制台；启动失败会落盘 + 弹窗（见 app/main.py）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
