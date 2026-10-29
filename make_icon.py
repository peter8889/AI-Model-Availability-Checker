# -*- coding: utf-8 -*-
"""生成应用图标 app.ico（蓝色圆角方形 + 白色放大镜）。"""
from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角底板
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=(37, 99, 235, 255))

# 镜片
w = int(S * 0.062)
cx, cy, r = int(S * 0.435), int(S * 0.415), int(S * 0.205)
d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 255), width=w)

# 手柄（圆头）
x1, y1 = int(cx + r * 0.72), int(cy + r * 0.72)
x2, y2 = int(S * 0.755), int(S * 0.745)
d.line([x1, y1, x2, y2], fill=(255, 255, 255, 255), width=w)
for (px, py) in ((x1, y1), (x2, y2)):
    d.ellipse([px - w // 2, py - w // 2, px + w // 2, py + w // 2], fill=(255, 255, 255, 255))

img.resize((256, 256), Image.LANCZOS).save(
    "app.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
)
print("app.ico written")
