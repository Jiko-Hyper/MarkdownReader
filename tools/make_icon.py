# -*- coding: utf-8 -*-
"""生成 MDReader 的应用图标。

设计取自产品的视觉标识：紫色渐变圆角方块 + 白色 MD 字样，不带底色，
因此任务栏、桌面快捷方式和 exe 图标在各种主题下都干净。

用法（需要 Pillow）：
    python tools/make_icon.py

输出：
    assets/icon.png   512x512，用于 README 与关于窗口
    assets/icon.ico   16/24/32/48/64/128/256 多尺寸，用于 exe 与快捷方式
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")

# 与产品标识一致的取色：左上一档深青灰，右下一档亮紫。
GRADIENT = ((58, 66, 84), (139, 106, 235))
SUPERSAMPLE = 4  # 先放大再缩小，边缘不会有锯齿
ICO_SIZES = (256, 128, 64, 48, 32, 24, 16)


def _font(size: int) -> ImageFont.FreeTypeFont:
    """挑一个本机可用的粗体无衬线字体，保证 "MD" 的字重够。"""
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\seguisb.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _gradient(size: int) -> Image.Image:
    """对角渐变：从左上到右下插值，取色与标识一致。"""
    (r0, g0, b0), (r1, g1, b1) = GRADIENT
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    span = max(size - 1, 1)
    for y in range(size):
        for x in range(size):
            # 45 度方向的归一化进度
            t = (x + y) / (2 * span)
            pixels[x, y] = (
                round(r0 + (r1 - r0) * t),
                round(g0 + (g1 - g0) * t),
                round(b0 + (b1 - b0) * t),
            )
    return image


def build(size: int = 512) -> Image.Image:
    scale = size * SUPERSAMPLE
    radius = round(scale * 0.235)  # 圆角比例贴近产品标识
    mask = Image.new("L", (scale, scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, scale - 1, scale - 1), radius=radius, fill=255)

    canvas = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    canvas.paste(_gradient(scale).convert("RGBA"), (0, 0), mask)
    canvas.putalpha(mask)

    draw = ImageDraw.Draw(canvas)
    text = "MD"
    font = _font(round(scale * 0.42))
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((scale - (box[2] - box[0])) / 2 - box[0], (scale - (box[3] - box[1])) / 2 - box[1] - scale * 0.01),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> int:
    os.makedirs(ASSETS, exist_ok=True)
    icon = build(512)
    png_path = os.path.join(ASSETS, "icon.png")
    ico_path = os.path.join(ASSETS, "icon.ico")
    icon.save(png_path)
    icon.save(ico_path, sizes=[(s, s) for s in ICO_SIZES])
    print("已生成 %s" % png_path)
    print("已生成 %s" % ico_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
