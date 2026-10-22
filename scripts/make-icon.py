#!/usr/bin/env python3
"""生成飞牛 FPK 需要的两枚图标：ICON.PNG (64x64) 与 ICON_256.PNG (256x256)。

按照 fnOS 原生图标的视觉规范绘制：
  - 底色垂直渐变 #4778FF -> #3D5DFF
  - 轮廓为超椭圆（squircle），不是普通圆角矩形
  - 图形纯白，约占画布高度 68%

图形语义：一只音箱箱体，右侧三道光弧表示正在推送声音。

用法：
    python scripts/make-icon.py [输出目录]

需要 Pillow。生成的 PNG 会直接写进 fpk/ 目录。
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

BLUE_TOP = (0x47, 0x78, 0xFF)
BLUE_BOTTOM = (0x3D, 0x5D, 0xFF)
SUPERSAMPLE = 8
# |x|^n + |y|^n = 1 里的 n。4 是常见的 squircle 近似值，
# 比 rounded_rectangle 更接近 iOS/fnOS 的圆角手感。
SQUIRCLE_N = 4.0

SIZES = (("ICON.PNG", 64), ("ICON_256.PNG", 256))


def squircle_mask(size: int) -> Image.Image:
    """参数方程绘制超椭圆，作为整枚图标的裁剪遮罩。"""
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    center = size / 2.0
    radius = center - 0.5
    exponent = 2.0 / SQUIRCLE_N

    points = []
    steps = 1440
    for i in range(steps):
        t = 2.0 * math.pi * i / steps
        cos_t, sin_t = math.cos(t), math.sin(t)
        x = center + radius * (abs(cos_t) ** exponent) * (1 if cos_t >= 0 else -1)
        y = center + radius * (abs(sin_t) ** exponent) * (1 if sin_t >= 0 else -1)
        points.append((x, y))

    draw.polygon(points, fill=255)
    return img


def vertical_gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    span = max(size - 1, 1)
    for y in range(size):
        t = y / span
        color = tuple(int(BLUE_TOP[i] + (BLUE_BOTTOM[i] - BLUE_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (size - 1, y)], fill=color)
    return img


def glyph_mask(size: int) -> Image.Image:
    """白色图形层：音箱箱体 + 三道光弧。坐标以 256 为基准等比缩放。"""
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    u = size / 256.0

    # 箱体
    box = [58 * u, 40 * u, 116 * u, 216 * u]
    draw.rounded_rectangle(box, radius=11 * u, fill=255)

    # 两个发声单元，用挖空的方式表现
    cx = (box[0] + box[2]) / 2
    draw.ellipse([cx - 9 * u, 64 * u, cx + 9 * u, 82 * u], fill=0)
    draw.ellipse([cx - 25 * u, 112 * u, cx + 25 * u, 162 * u], fill=0)

    # 三道向右扩散的声波
    arc_cx, arc_cy = 116 * u, 128 * u
    for radius in (40, 63, 86):
        draw.arc(
            [
                arc_cx - radius * u,
                arc_cy - radius * u,
                arc_cx + radius * u,
                arc_cy + radius * u,
            ],
            start=-50,
            end=50,
            fill=255,
            width=max(1, int(11 * u)),
        )

    return img


def render(target: int) -> Image.Image:
    work = target * SUPERSAMPLE
    base = vertical_gradient(work).convert("RGBA")
    base.putalpha(squircle_mask(work))

    white = Image.new("RGBA", (work, work), (255, 255, 255, 255))
    white.putalpha(glyph_mask(work))

    composed = Image.alpha_composite(base, white)
    return composed.resize((target, target), Image.LANCZOS)


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "fpk"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, size in SIZES:
        path = out_dir / name
        render(size).save(path, "PNG", optimize=True)
        print(f"{path}  {size}x{size}  {path.stat().st_size} bytes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
