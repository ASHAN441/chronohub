"""生成应用图标（.ico）。

设计上刻意与界面保持一致：应用里左上角和「关于」页的 logo 就是
**主题色圆角方块 + 白色「时」字**，图标沿用同一套，避免出现两套视觉。

用脚本生成而不是塞一个二进制文件进仓库：可复现、可调、来源清楚。
运行：python tools/make_icon.py
产出：packaging/ChronoHub.ico
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "packaging")
OUT_PATH = os.path.join(OUT_DIR, "ChronoHub.ico")

# 与 web/index.html 的 --win-accent 保持一致
ACCENT = (0x00, 0x78, 0xD4)
GLYPH = "时"

# 优先用粗体，退到常规，再退到黑体
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
)

# 圆角半径占画布的比例（Windows 11 风格图标大约在 18%~22%）
CORNER_RATIO = 0.20
# 字号占画布的比例
GLYPH_RATIO = 0.60

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise SystemExit("找不到可用的中文字体，请检查 C:\\Windows\\Fonts")


def render(size: int) -> Image.Image:
    """渲染一张 size×size 的 RGBA 图标。"""
    # 先按 4 倍超采样绘制再缩小，边缘更干净
    scale = 4
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    inset = round(canvas * 0.04)
    draw.rounded_rectangle(
        (inset, inset, canvas - inset - 1, canvas - inset - 1),
        radius=round(canvas * CORNER_RATIO),
        fill=ACCENT + (255,),
    )

    font = _font(round(canvas * GLYPH_RATIO))
    left, top, right, bottom = draw.textbbox((0, 0), GLYPH, font=font)
    draw.text(
        ((canvas - (right - left)) / 2 - left, (canvas - (bottom - top)) / 2 - top),
        GLYPH,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    base = render(max(ICO_SIZES))
    base.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print("已生成:", OUT_PATH, "尺寸:", ICO_SIZES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
