# -*- coding: utf-8 -*-
"""绘制「小狼毫用户中心」应用图标的候选方案。

用 Pillow 直接绘制，输出 256px PNG 供用户挑选，选定后再转成
多尺寸 .ico（含 16/24/32/48/64/128/256）。

方案：
  A 蓝底白字「码」——圆角方形，呼应界面强调色
  B 蓝底白字「字」——更贴合「码字」语义
  C 白底蓝「码」——浅色方案，托盘深色背景下更醒目
  D 蓝底键盘键帽——不依赖文字的图形方案
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "icon_candidates"
OUT.mkdir(exist_ok=True)

ACCENT = (37, 99, 201, 255)        # #2563C9，界面强调色
ACCENT_DARK = (29, 78, 160, 255)   # 深一档，用于渐变底部
WHITE = (255, 255, 255, 255)
SIZE = 256
RADIUS = 56                        # 圆角半径，接近 macOS/Win11 图标观感

FONT_PATH = "msyh.ttc"             # 微软雅黑，Windows 自带


def rounded_canvas(fill):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=RADIUS, fill=fill)
    return img, draw


def draw_glyph(img, char, color, px):
    font = ImageFont.truetype(FONT_PATH, px)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), char, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((SIZE - w) / 2 - bbox[0], (SIZE - h) / 2 - bbox[1]), char,
              font=font, fill=color)
    return img


# A 蓝底白「码」
img, _ = rounded_canvas(ACCENT)
draw_glyph(img, "码", WHITE, 150)
img.save(OUT / "A-blue-ma.png")

# B 蓝底白「字」
img, _ = rounded_canvas(ACCENT)
draw_glyph(img, "字", WHITE, 150)
img.save(OUT / "B-blue-zi.png")

# C 白底蓝「码」（浅色方案，托盘深色背景更醒目）
img, _ = rounded_canvas(WHITE)
draw_glyph(img, "码", ACCENT, 150)
img.save(OUT / "C-white-ma.png")

# D 蓝底键盘键帽：中央一块圆角浅色键帽，上面三颗白点示意按键
img, _ = rounded_canvas(ACCENT)
draw = ImageDraw.Draw(img)
draw.rounded_rectangle((52, 72, 204, 184), radius=24, fill=WHITE)
for i, x in enumerate((88, 118, 148)):
    draw.rounded_rectangle((x, 100, x + 20, 120), radius=5, fill=ACCENT)
draw.rounded_rectangle((88, 134, 168, 154), radius=5, fill=ACCENT)
img.save(OUT / "D-keycap.png")

print("已生成 4 个候选：", ", ".join(p.name for p in sorted(OUT.glob("*.png"))))
