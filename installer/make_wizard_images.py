"""安裝精靈圖片：左側大圖 (164x314) 與右上小圖 (55x55)，各輸出 100%~250% 多種尺寸。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import os

HERE = Path(__file__).parent
ICON = Image.open(HERE.parent / "icon.png").convert("RGBA")
FONT = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoeuib.ttf")


def gradient(w, h, c1=(58, 34, 150), c2=(214, 62, 140)):
    g = Image.new("RGB", (w, h))
    px = g.load()
    for y in range(h):
        for x in range(w):
            t = min(1, max(0, (y / h) * 0.8 + (x / w) * 0.2))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return g


def big(scale):
    w, h = int(164 * scale), int(314 * scale)
    img = gradient(w, h).convert("RGBA")
    # 柔光
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-w * 0.4, -h * 0.15, w * 1.1, h * 0.45), fill=(255, 255, 255, 60))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(30 * scale)))
    # 圖示
    s = int(96 * scale)
    ic = ICON.resize((s, s), Image.LANCZOS)
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sh.paste((20, 0, 50, 120), (int((w - s) / 2), int(58 * scale) + int(6 * scale)), ic)
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(8 * scale)))
    img.alpha_composite(ic, (int((w - s) / 2), int(58 * scale)))
    d = ImageDraw.Draw(img)
    # 標題
    f1 = ImageFont.truetype(FONT, int(17 * scale))
    for i, line in enumerate(("Now Playing", "Lyrics")):
        tw = d.textlength(line, font=f1)
        d.text(((w - tw) / 2, 172 * scale + i * 22 * scale), line, font=f1, fill=(255, 255, 255, 255))
    # 歌詞線裝飾 (中間那條最亮，像正在唱的那句)
    lines = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    for i, (lw, a) in enumerate(((0.55, 70), (0.7, 230), (0.45, 70), (0.6, 40))):
        y = (240 + i * 14) * scale
        x0 = (w - w * lw) / 2
        ld.rounded_rectangle((x0, y, x0 + w * lw, y + 5 * scale), radius=3 * scale, fill=(255, 255, 255, a))
    img = Image.alpha_composite(img, lines)
    return img.convert("RGB")


def small(scale):
    s = int(55 * scale)
    img = Image.new("RGB", (s, s), (255, 255, 255))
    ic = ICON.resize((s, s), Image.LANCZOS)
    img.paste(ic, (0, 0), ic)
    return img


for pct in (100, 125, 150, 175, 200, 250):
    sc = pct / 100
    big(sc).save(HERE / f"wizard_{pct}.bmp")
    small(sc).save(HERE / f"wizard_small_{pct}.bmp")
print("ok")
