# -*- coding: utf-8 -*-
"""把选定的图标方案 A（蓝底白「码」）转成多尺寸 .ico。

Windows 图标需要包含多个尺寸：任务栏/资源管理器用大图，
托盘与标题栏用 16/32px。只放一张 256px 大图会让小尺寸被
系统缩放算法糊掉，所以这里从高清源逐档重绘并生成。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (37, 99, 201, 255)
WHITE = (255, 255, 255, 255)
FONT_PATH = "msyh.ttc"


def render(px: int) -> Image.Image:
    """按目标尺寸直接重绘，而不是缩放大图：小尺寸下笔画更清楚。"""
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(4, int(px * 0.22))
    draw.rounded_rectangle((0, 0, px - 1, px - 1), radius=radius, fill=ACCENT)
    font = ImageFont.truetype(FONT_PATH, int(px * 0.62))
    bbox = draw.textbbox((0, 0), "码", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((px - w) / 2 - bbox[0], (px - h) / 2 - bbox[1]), "码",
              font=font, fill=WHITE)
    return img


out = Path(__file__).resolve().parent / "app.ico"
sizes = [16, 24, 32, 48, 64, 128, 256]
render(256).save(out, format="ICO", sizes=[(s, s) for s in sizes],
                 append_images=[render(s) for s in sizes[1:]])
print(f"已生成 {out}")
