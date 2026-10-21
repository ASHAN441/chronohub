"""生成应用图标（.ico）—— 用设计稿给的那张「时枢 ChronoHub」图标。

源图：packaging/icon-source.png
    就是用户提供的蓝色圆角方块稿（C + 时钟 + 日历 + 时枢 ChronoHub），
    已经在导入时把方块外面的白底挖成透明了。

用法：python tools/make_icon.py
产出：packaging/ChronoHub.ico

换图标的步骤
------------
1. 把新图放到 packaging/icon-source.png（**要 PNG，圆角外面透明**）
   JPG 那种带白底的要先挖透明，否则深色任务栏上会出现白角。
2. 跑本脚本
3. 重新打包（图标是编进 exe 的，不重新打包不生效）

已知取舍：这张图信息量偏大（大 C、时钟、日历、两行文字），
256px 看着很好，但 16-32px 会糊成一块蓝色——系统任务栏上的小图标
只能是这个效果，除非为小尺寸单独做一版简化稿。
"""

from __future__ import annotations

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "packaging")
SOURCE_PATH = os.path.join(OUT_DIR, "icon-source.png")
OUT_PATH = os.path.join(OUT_DIR, "ChronoHub.ico")

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def load_square(size: int) -> Image.Image:
    """读源图、居中裁成正方形、缩放到 size。"""
    if not os.path.exists(SOURCE_PATH):
        raise SystemExit(f"找不到图标源文件：{SOURCE_PATH}")
    image = Image.open(SOURCE_PATH).convert("RGBA")

    # 居中裁正方形：源图可能因为描边不是严格 1:1
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    image = image.crop((left, top, left + side, top + side))

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    base = load_square(max(ICO_SIZES))
    base.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print("已生成:", OUT_PATH, "尺寸:", ICO_SIZES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
