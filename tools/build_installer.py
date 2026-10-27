"""编译 Windows 安装程序（Inno Setup）。

用法：
    python tools/build_installer.py            # 用 dist/ChronoHub 现有构建
    python tools/build_installer.py --build    # 先重新打包再编译安装程序

为什么要有这个脚本：
1. **版本号只有一处**（app/config.py），编译时读出来传给 Inno，
   省得 .iss 里再手写一遍、两边不同步。
2. 顺手做几个前置检查：dist 在不在、data 有没有被误打进去、
   ISCC 装没装 —— 这些错了 Inno 只会给一句难懂的报错。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist", "ChronoHub")
ISS = os.path.join(ROOT, "packaging", "ChronoHub.iss")
ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
)


def app_version() -> str:
    """从 app/config.py 读版本号（唯一真相来源）。"""
    path = os.path.join(ROOT, "app", "config.py")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r'\s*VERSION\s*=\s*["\']([^"\']+)["\']', line)
            if match:
                return match.group(1)
    raise SystemExit("没能从 app/config.py 读到 VERSION")


def find_iscc() -> str:
    for path in ISCC_CANDIDATES:
        if os.path.exists(path):
            return path
    raise SystemExit(
        "没找到 Inno Setup 的 ISCC.exe。\n"
        "装一个：https://jrsoftware.org/isdl.php\n"
        "或者放到这几个位置的任意一个：\n  " + "\n  ".join(ISCC_CANDIDATES)
    )


def ensure_dist(rebuild: bool) -> None:
    if rebuild or not os.path.exists(os.path.join(DIST, "ChronoHub.exe")):
        print("== 先打包程序（PyInstaller）==")
        result = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "packaging/ChronoHub.spec",
             "--noconfirm", "--distpath", "dist", "--workpath", "build"],
            cwd=ROOT,
        )
        if result.returncode != 0:
            raise SystemExit("PyInstaller 打包失败")
    if not os.path.exists(os.path.join(DIST, "ChronoHub.exe")):
        raise SystemExit(f"没有找到 {DIST}\\ChronoHub.exe")


def check_no_data() -> None:
    """确认 dist 里的 data\\ 不会被带进安装包。

    安装脚本里已经 Excludes 了 data\\，这里只是再喊一声，
    免得有人哪天改了 .iss 把开发机上的课表打进发布包。
    """
    with open(ISS, encoding="utf-8-sig") as handle:
        text = handle.read()
    if "data\\*" not in text:
        raise SystemExit("packaging/ChronoHub.iss 里没有排除 data\\，安装包会带上你的个人数据！")


def main() -> int:
    version = app_version()
    iscc = find_iscc()
    print(f"== 版本：{version} ==")
    print(f"== ISCC：{iscc} ==")

    ensure_dist("--build" in sys.argv)
    check_no_data()

    out_dir = os.path.join(ROOT, "dist", "installer")
    os.makedirs(out_dir, exist_ok=True)

    print("== 编译安装程序 ==")
    result = subprocess.run(
        [iscc, f"/DAppVersion={version}", ISS],
        cwd=ROOT,
    )
    if result.returncode != 0:
        return result.returncode

    exe = os.path.join(out_dir, f"ChronoHub-Setup-v{version}.exe")
    if os.path.exists(exe):
        print("")
        print(f"完成：{exe}")
        print(f"体积：{os.path.getsize(exe) / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
