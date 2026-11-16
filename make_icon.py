"""產生 App 圖示 (Hummi 哼哼：戴耳機的小貓)：icon.ico (exe 用) 與 icon.png (系統匣用)。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 1024  # 以大尺寸繪製再縮小，邊緣才會平滑
ROOT = Path(__file__).parent

CREAM = (255, 249, 242, 255)
EAR_IN = (255, 170, 196, 255)
PHONES = (58, 40, 112, 255)
PAD = (255, 122, 168, 255)
INK = (48, 34, 72, 255)
BLUSH = (255, 140, 175, 150)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# 背景：斜向漸層 (淡紫 → 粉)，圓角方塊
grad = Image.new("RGB", (S, S))
px = grad.load()
c1, c2 = (124, 96, 255), (255, 120, 172)
for y in range(S):
    for x in range(S):
        px[x, y] = lerp(c1, c2, (x * 0.45 + y * 0.55) / S)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(grad, (0, 0), mask)

# 柔光
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse((-200, -300, 700, 500), fill=(255, 255, 255, 80))
glow = glow.filter(ImageFilter.GaussianBlur(120))
icon = Image.alpha_composite(icon, Image.composite(glow, Image.new("RGBA", (S, S)), mask))

# ───── 小貓 ─────
art = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(art)
CX, CY = 512, 612  # 臉的中心

# 耳機頭帶 (在耳朵後面)
d.arc((205, 250, 819, 900), start=180, end=360, fill=PHONES, width=58)

# 耳朵 (外圈奶油色 + 內圈粉紅)
for sx in (-1, 1):
    def P(x, y):
        return (CX + sx * x, y)
    d.polygon([P(255, 520), P(205, 250), P(60, 420)], fill=CREAM)
    d.polygon([P(220, 470), P(195, 305), P(105, 420)], fill=EAR_IN)

# 頭 (扁圓)
d.ellipse((CX - 305, CY - 245, CX + 305, CY + 240), fill=CREAM)

# 耳罩
for sx in (-1, 1):
    x0 = CX + sx * 300
    box = (x0 - 72, CY - 120, x0 + 72, CY + 95)
    d.rounded_rectangle(box, radius=62, fill=PHONES)
    inner = (x0 - 40 - (12 if sx < 0 else -12), CY - 82, x0 + 40 - (12 if sx < 0 else -12), CY + 57)
    d.rounded_rectangle(inner, radius=36, fill=PAD)

# 眼睛 (亮晶晶)
for sx in (-1, 1):
    ex, ey = CX + sx * 112, CY + 10
    d.ellipse((ex - 46, ey - 52, ex + 46, ey + 52), fill=INK)
    d.ellipse((ex - 26, ey - 36, ex + 2, ey - 8), fill=(255, 255, 255, 255))
    d.ellipse((ex + 10, ey + 14, ex + 24, ey + 28), fill=(255, 255, 255, 200))

# 腮紅
blush = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bd = ImageDraw.Draw(blush)
for sx in (-1, 1):
    bx, by = CX + sx * 190, CY + 92
    bd.ellipse((bx - 52, by - 28, bx + 52, by + 28), fill=BLUSH)
art = Image.alpha_composite(art, blush.filter(ImageFilter.GaussianBlur(6)))
d = ImageDraw.Draw(art)

# 鼻子 + 嘴巴 (ω)
d.rounded_rectangle((CX - 22, CY + 70, CX + 22, CY + 92), radius=11, fill=PAD)
d.arc((CX - 46, CY + 70, CX + 2, CY + 128), start=10, end=170, fill=INK, width=12)
d.arc((CX - 2, CY + 70, CX + 46, CY + 128), start=10, end=170, fill=INK, width=12)

# 小音符 (右上)
nx, ny = 800, 205
d.ellipse((nx - 44, ny + 70, nx + 14, ny + 116), fill=(255, 255, 255, 255))
d.rectangle((nx + 2, ny - 40, nx + 16, ny + 96), fill=(255, 255, 255, 255))
d.polygon([(nx + 2, ny - 40), (nx + 70, ny - 8), (nx + 70, ny + 24), (nx + 16, ny + 0)], fill=(255, 255, 255, 255))

# 陰影
shadow = art.filter(ImageFilter.GaussianBlur(20))
shadow = Image.composite(Image.new("RGBA", (S, S), (60, 20, 90, 100)), Image.new("RGBA", (S, S)), shadow.getchannel("A"))
icon.alpha_composite(shadow, (0, 16))
icon.alpha_composite(art)

icon.resize((256, 256), Image.LANCZOS).save(ROOT / "icon.png")
icon.save(ROOT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.ico / icon.png saved")
