# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 时枢 ChronoHub。

构建（在仓库根目录执行）：
    python -m PyInstaller packaging/ChronoHub.spec --noconfirm --clean

产出：
    dist/ChronoHub/        文件夹版（onedir）
      ChronoHub.exe
      _internal/           PySide6 / QtWebEngine / web / 模板 都在这里
      data/                运行时生成（账号、人员、值班表、日志）

为什么改 onedir：onefile 每次启动都要把 242MB 解压到临时目录，
实测「双击 → 窗口出现」要 16 秒，其中 15 秒全在解压。
onedir 没有这一步，启动直接降到几秒。
代价：分发的是一个文件夹（要整体压缩或整个目录拷走），不再是一个 exe。

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

# 仅用作产物文件名（ASCII，避免中文+英文混在路径里）
APP_NAME = "ChronoHub"
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

# 启动图：bootloader 阶段就显示，覆盖「解压 exe」那段黑屏。
# 图和 web/index.html 里的 HTML 开屏同构图（黑底 + 台标 + 同位置进度槽），
# 所以窗口出现时看不出切换。
splash = Splash(
    os.path.join(SPEC_DIR, "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(380, 338),      # 画布中心锚点，正好在进度槽下方
    text_size=10,
    text_color="#7a7a7a",
    text_default="正在启动…",
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # 依赖交给下面的 COLLECT 收拾，onenfile 改 onedir 的关键
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
    splash=splash,
)

# onedir 的产物收集：所有二进制与数据都放进来，输出 dist/ChronoHub/
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
