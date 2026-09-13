# -*- coding: utf-8 -*-
"""生成小红书竖版封面（1080x1440），文字用 STHeiti Medium 矢量绘制，100% 清晰。"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1080, 1440
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"

def font(size):
    return ImageFont.truetype(FONT, size)

def round_rect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def center_text(draw, cx, cy, text, fnt, fill):
    l, t, r, b = draw.textbbox((0, 0), text, font=fnt)
    w, h = r - l, b - t
    draw.text((cx - w / 2, cy - h / 2 - t), text, font=fnt, fill=fill)

def gradient(top, bottom):
    """垂直渐变背景（numpy）。"""
    t = np.linspace(0, 1, H)[:, None].astype(np.float32)  # (H, 1)
    arr = np.zeros((H, W, 3), np.float32)
    for c in range(3):
        arr[:, :, c] = np.array(top)[c] * (1 - t) + np.array(bottom)[c] * t
    return arr.astype(np.uint8)

def soft_spots(base, spots):
    """在透明层画大光斑并高斯模糊，以 alpha 叠加到背景。"""
    base_pil = Image.fromarray(base).convert("RGBA")
    limg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(limg)
    for (x, y, rad, col) in spots:
        ld.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col + (255,))
    limg = limg.filter(ImageFilter.GaussianBlur(120))
    base_pil.paste(limg, (0, 0), limg)
    return base_pil

def draw_laptop(draw, x0, y0, w, h):
    """毛玻璃卡片里的笔记本 + 屏幕光点 + 指令气泡。"""
    r = 26
    # 卡片
    round_rect(draw, [x0, y0, x0 + w, y0 + h], r, fill=(255, 255, 255, 38), outline=(255, 255, 255, 90), width=2)
    # 顶部高光
    round_rect(draw, [x0 + 14, y0 + 12, x0 + w - 14, y0 + 70], 18, fill=(255, 255, 255, 30))
    # 屏幕
    sx, sy, sw, sh = x0 + 40, y0 + 95, w - 80, h - 200
    round_rect(draw, [sx, sy, sx + sw, sy + sh], 16, fill=(17, 20, 38, 235), outline=(120, 140, 200, 120), width=2)
    # AI 光点
    pts = [(sx + 90, sy + 90, (255, 214, 102)), (sx + sw - 110, sy + 130, (96, 220, 200)), (sx + 150, sy + sh - 120, (244, 114, 182))]
    for (px, py, col) in pts:
        draw.ellipse([px - 22, py - 22, px + 22, py + 22], fill=col)
        draw.ellipse([px - 34, py - 34, px + 34, py + 34], outline=col + (70,), width=3)
    # 连线
    draw.line([pts[0][0], pts[0][1], pts[1][0], pts[1][1]], fill=(255, 255, 255, 60), width=2)
    draw.line([pts[0][0], pts[0][1], pts[2][0], pts[2][1]], fill=(255, 255, 255, 60), width=2)
    # 指令气泡
    bx, by, bw, bh = sx + 60, sy + sh - 130, sw - 120, 64
    round_rect(draw, [bx, by, bx + bw, by + bh], 18, fill=(255, 255, 255, 240))
    center_text(draw, bx + bw / 2, by + bh / 2 + 4, "自动整理这 100 张图", font(30), (30, 34, 58))

def make_cover(top, bottom, spots, label, out_path):
    base = gradient(top, bottom)
    img = soft_spots(base, spots)
    draw = ImageDraw.Draw(img, "RGBA")
    # 角标
    round_rect(draw, [60, 64, 320, 124], 30, fill=(30, 34, 58, 220), outline=(255, 255, 255, 120), width=2)
    center_text(draw, 190, 94, label, font(30), (255, 255, 255))
    # 笔记本卡片
    draw_laptop(draw, 120, 230, W - 240, 520)
    # 主标题（两行，大）
    center_text(draw, W / 2, 900, "你说话，", font(96), (255, 255, 255))
    center_text(draw, W / 2, 1010, "电脑自己干", font(96), (255, 255, 255))
    # 副标题
    center_text(draw, W / 2, 1120, "本地 AI 桌面员工 · 不上云不泄露", font(38), (255, 255, 255, 220))
    # 底部小字
    center_text(draw, W / 2, 1330, "说一句人话，省一小时重复活", font(30), (255, 255, 255, 180))
    img.convert("RGB").save(out_path, quality=95)
    print("saved", out_path)

palettes = {
    "A_紫蓝粉": dict(top=(124, 58, 237), bottom=(214, 70, 160),
                     spots=[(260, 360, 260, (180, 120, 255)), (820, 520, 240, (90, 150, 255)), (540, 240, 200, (255, 130, 200))],
                     label="蓝海 · 本地部署"),
    "B_深蓝青": dict(top=(13, 22, 48), bottom=(14, 130, 210),
                     spots=[(300, 340, 240, (40, 120, 220)), (820, 560, 220, (20, 200, 200)), (520, 220, 180, (120, 180, 255))],
                     label="科技 · 隐私优先"),
    "C_橙粉暖": dict(top=(249, 110, 30), bottom=(224, 60, 150),
                     spots=[(280, 360, 250, (255, 180, 80)), (840, 520, 230, (255, 110, 160)), (540, 230, 190, (255, 230, 150))],
                     label="效率 · 副业神器"),
}

if __name__ == "__main__":
    import os
    out = os.path.dirname(__file__)
    for name, p in palettes.items():
        make_cover(p["top"], p["bottom"], p["spots"], p["label"], os.path.join(out, f"cover_{name}.png"))
