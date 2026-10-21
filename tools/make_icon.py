"""生成应用图标（.ico）—— 用 SYNUTV 台标。

为什么用台标而不是自绘字形：用户希望桌面/任务栏上的图标就是台标本身，
和开屏、界面里的品牌保持一致。

一个已知的先天问题：台标是 2.4:1 的横条，而图标必须是正方形。
按 88% 宽度塞进正方画布后，竖向只占 38%（上下留白）。
256px 看着正常；16-32px 时「校园电视台」那行小字会糊，
且它在深色任务栏上本身是深灰、会消失（只剩 SYNU 彩色字母）。
如果以后要优化小尺寸，两个方向：
  1. 只取 SYNU 那部分（更接近方形，丢掉小字）
  2. 在台标后面垫一块浅色圆角底（保证深色背景上也看得清）

用脚本生成而不是塞一个二进制文件进仓库：可复现、可调、来源清楚。
运行：python tools/make_icon.py
产出：packaging/ChronoHub.ico
"""

from __future__ import annotations

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "packaging")
OUT_PATH = os.path.join(OUT_DIR, "ChronoHub.ico")

# 图标源：台标原色版（透明底）。同一个文件也供界面使用。
LOGO_PATH = os.path.join(ROOT, "web", "assets", "logo.png")

# 台标占方形图标的宽度比例。留白留太多会显得图标很小，太满又会贴边。
LOGO_WIDTH_RATIO = 0.88

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    """渲染一张 size×size 的 RGBA 图标（台标居中，透明底）。"""
    if not os.path.exists(LOGO_PATH):
        raise SystemExit(f"找不到台标源图：{LOGO_PATH}")
    logo = Image.open(LOGO_PATH).convert("RGBA")

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    width = max(1, round(size * LOGO_WIDTH_RATIO))
    height = max(1, round(logo.size[1] * width / logo.size[0]))
    resized = logo.resize((width, height), Image.LANCZOS)
    canvas.alpha_composite(resized, ((size - width) // 2, (size - height) // 2))
    return canvas


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    base = render(max(ICO_SIZES))
    base.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print("已生成:", OUT_PATH, "尺寸:", ICO_SIZES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
