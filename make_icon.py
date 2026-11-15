"""產生 App 圖示：icon.ico (exe 用) 與 icon.png (系統匣用)。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 1024  # 以大尺寸繪製再縮小，邊緣才會平滑
ROOT = Path(__file__).parent


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# 背景：斜向漸層 (紫 → 粉)
grad = Image.new("RGB", (S, S))
px = grad.load()
c1, c2 = (108, 76, 255), (255, 92, 158)
for y in range(S):
    for x in range(S):
        px[x, y] = lerp(c1, c2, (x * 0.45 + y * 0.55) / S)

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(grad, (0, 0), mask)

# 柔光
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse((-200, -300, 700, 500), fill=(255, 255, 255, 70))
glow = glow.filter(ImageFilter.GaussianBlur(120))
icon = Image.alpha_composite(icon, Image.composite(glow, Image.new("RGBA", (S, S)), mask))

# 白色圖案：雙八分音符 + 三條歌詞線
art = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(art)
W = (255, 255, 255, 255)
# 音符頭 (略傾斜的橢圓)
for cx, cy in ((330, 600), (610, 545)):
    head = Image.new("RGBA", (260, 200), (0, 0, 0, 0))
    ImageDraw.Draw(head).ellipse((10, 10, 250, 190), fill=W)
    head = head.rotate(22, expand=True, resample=Image.BICUBIC)
    art.alpha_composite(head, (cx - head.width // 2, cy - head.height // 2))
# 符桿
d.rectangle((420, 250, 462, 590), fill=W)
d.rectangle((700, 195, 742, 535), fill=W)
# 符槓 (平行四邊形)
d.polygon([(420, 250), (742, 195), (742, 290), (420, 345)], fill=W)
# 歌詞線
for i, (x1, a) in enumerate(((780, 255), (650, 190), (560, 130))):
    y = 735 + i * 70
    d.rounded_rectangle((230, y, x1, y + 36), radius=18, fill=(255, 255, 255, a))

shadow = art.filter(ImageFilter.GaussianBlur(18))
shadow = Image.composite(Image.new("RGBA", (S, S), (40, 10, 80, 110)), Image.new("RGBA", (S, S)), shadow.getchannel("A"))
icon.alpha_composite(shadow, (0, 14))
icon.alpha_composite(art)

icon.resize((256, 256), Image.LANCZOS).save(ROOT / "icon.png")
icon.save(ROOT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon.ico / icon.png saved")
