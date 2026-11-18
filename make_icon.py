"""產生 App 圖示 (Hummi 哼哼：戴耳機的小貓)：icon.ico (exe 用) 與 icon.png (系統匣、主視窗用)。

光從左上方來：每個部位都是漸層填色 + 陰影 + 高光，看起來有立體感。
全部在 1024 px 畫，最後縮小，邊緣才會平滑。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024
ROOT = Path(__file__).parent
CX, CY = 512, 618  # 臉的中心


# ───────────────── 小工具 ─────────────────
def mask_of(draw):
    """用畫筆畫出一個形狀遮罩 (L)。"""
    m = Image.new("L", (S, S), 0)
    draw(ImageDraw.Draw(m))
    return m


def linear(c0, c1, p0, p1):
    """p0 → p1 方向的線性漸層 (RGBA)。"""
    y, x = np.mgrid[0:S, 0:S].astype(np.float32)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    t = ((x - p0[0]) * dx + (y - p0[1]) * dy) / max(dx * dx + dy * dy, 1)
    t = np.clip(t, 0, 1)[..., None]
    a, b = np.array(c0, np.float32), np.array(c1, np.float32)
    return Image.fromarray((a + (b - a) * t).astype(np.uint8), "RGBA")


def radial(c_in, c_out, center, radius, power=1.0):
    y, x = np.mgrid[0:S, 0:S].astype(np.float32)
    t = np.clip(np.hypot(x - center[0], y - center[1]) / radius, 0, 1) ** power
    t = t[..., None]
    a, b = np.array(c_in, np.float32), np.array(c_out, np.float32)
    return Image.fromarray((a + (b - a) * t).astype(np.uint8), "RGBA")


def clip(img, mask):
    """把圖片裁成遮罩的形狀 (保留圖片本身的透明度)。"""
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return out


def solid(color):
    return Image.new("RGBA", (S, S), color)


def tinted(color, alpha_mask):
    """單色圖層，透明度 = alpha_mask。
    (不能用 paste 貼到透明底上：顏色會跟黑色混在一起，陰影和光都會變髒變灰。)"""
    layer = solid(tuple(color[:3]) + (255,))
    layer.putalpha(alpha_mask)
    return layer


def soft(mask, blur, color, opacity=1.0, offset=(0, 0)):
    """模糊後的單色陰影 / 光。"""
    m = mask.filter(ImageFilter.GaussianBlur(blur))
    if offset != (0, 0):
        moved = Image.new("L", (S, S), 0)
        moved.paste(m, offset)
        m = moved
    if opacity < 1:
        m = m.point(lambda v: int(v * opacity))
    return tinted(color, m)


def inner_shadow(mask, blur, color, opacity, offset):
    """形狀裡面、靠邊的陰影 (讓東西看起來是凹進去的)。"""
    shifted = Image.new("L", (S, S), 0)
    shifted.paste(mask, offset)
    edge = ImageChops.subtract(mask, shifted).filter(ImageFilter.GaussianBlur(blur))
    edge = ImageChops.multiply(edge, mask).point(lambda v: int(min(255, v * opacity)))
    return tinted(color, edge)


def rim(mask, width, blur, color, opacity, toward=(1, 1)):
    """邊緣光：形狀邊上的一圈亮色，只出現在 toward 那一側。"""
    shrunk = Image.new("L", (S, S), 0)
    shrunk.paste(mask, (int(-toward[0] * width), int(-toward[1] * width)))
    edge = ImageChops.subtract(mask, shrunk).filter(ImageFilter.GaussianBlur(blur))
    edge = ImageChops.multiply(edge, mask).point(lambda v: int(min(255, v * opacity)))
    return tinted(color, edge)


def build(detail=True):
    """detail=False：給 16~32 px 用的簡化版 (少掉細的陰影和邊緣光，縮小後才清楚)。"""
    # ───────────────── 背景 ─────────────────
    bg_mask = mask_of(lambda d: d.rounded_rectangle((40, 40, S - 40, S - 40), radius=230, fill=255))
    bg = linear((126, 98, 255, 255), (255, 118, 170, 255), (80, 60), (944, 980))
    bg.alpha_composite(radial((255, 255, 255, 105), (255, 255, 255, 0), (250, 170), 620, 1.4))   # 左上的柔光
    bg.alpha_composite(radial((60, 20, 110, 0), (60, 20, 110, 95), (430, 380), 820, 2.2))       # 四角稍暗
    bg.alpha_composite(radial((255, 210, 240, 70), (255, 210, 240, 0), (CX, CY + 40), 430, 1.2))  # 貓背後的光暈
    icon = clip(bg, bg_mask)
    # 玻璃邊：上緣一道細亮線
    gloss = mask_of(lambda d: d.rounded_rectangle((40, 40, S - 40, S - 40), radius=230, outline=255, width=5))
    gloss = ImageChops.multiply(gloss, linear((255, 255, 255, 255), (255, 255, 255, 0), (0, 40), (0, 520)).getchannel("A"))
    icon.alpha_composite(tinted((255, 255, 255), gloss.point(lambda v: int(v * 120 / 255))))

    art = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # ───────────────── 形狀 ─────────────────
    band_box = (200, 248, 824, 912)
    band_mask = mask_of(lambda d: d.arc(band_box, start=182, end=358, fill=255, width=64))
    head_mask = mask_of(lambda d: d.ellipse((CX - 306, CY - 246, CX + 306, CY + 238), fill=255))


    def ear_poly(sx, inner=False):
        P = lambda x, y: (CX + sx * x, y)
        if inner:
            return [P(222, 478), P(196, 306), P(108, 424)]
        return [P(262, 528), P(206, 246), P(54, 424)]


    ears_mask = mask_of(lambda d: [d.polygon(ear_poly(sx), fill=255) for sx in (-1, 1)])
    ears_mask = ears_mask.filter(ImageFilter.GaussianBlur(3)).point(lambda v: 255 if v > 128 else 0)  # 耳尖圓一點
    ear_in_mask = mask_of(lambda d: [d.polygon(ear_poly(sx, True), fill=255) for sx in (-1, 1)])
    ear_in_mask = ear_in_mask.filter(ImageFilter.GaussianBlur(10)).point(lambda v: 255 if v > 110 else 0)


    def cup_box(sx, pad=False):
        x0 = CX + sx * 300
        if pad:
            o = -12 if sx > 0 else 12
            return (x0 - 42 + o, CY - 86, x0 + 42 + o, CY + 60)
        return (x0 - 76, CY - 124, x0 + 76, CY + 100)


    cups_mask = mask_of(lambda d: [d.rounded_rectangle(cup_box(sx), radius=66, fill=255) for sx in (-1, 1)])
    pads_mask = mask_of(lambda d: [d.rounded_rectangle(cup_box(sx, True), radius=38, fill=255) for sx in (-1, 1)])

    # ───────────────── 頭帶 (在頭後面) ─────────────────
    band = clip(linear((118, 92, 210, 255), (34, 22, 78, 255), (CX, 248), (CX, 560)), band_mask)
    art.alpha_composite(band)
    band_hi = mask_of(lambda d: d.arc((band_box[0] + 22, band_box[1] + 14, band_box[2] - 22, band_box[3]),
                                      start=200, end=275, fill=255, width=12))
    if detail:
        art.alpha_composite(soft(band_hi, 5, (255, 255, 255, 255), 0.55))

    # ───────────────── 耳朵 ─────────────────
    ear = clip(linear((255, 252, 248, 255), (240, 222, 214, 255), (CX, 250), (CX, 540)), ears_mask)
    art.alpha_composite(ear)
    ear_in = clip(radial((255, 128, 168, 255), (255, 196, 214, 255), (CX, 470), 330, 1.3), ear_in_mask)
    art.alpha_composite(ear_in)
    if detail:
        art.alpha_composite(inner_shadow(ear_in_mask, 14, (150, 60, 110, 255), 0.9, (0, 16)))
    if detail:
        art.alpha_composite(rim(ears_mask, 10, 6, (255, 255, 255, 255), 0.7, toward=(-0.7, -1)))

    # ───────────────── 頭 ─────────────────
    head = clip(radial((255, 255, 253, 255), (255, 233, 226, 255) if detail else (255, 246, 238, 255), (CX - 90, CY - 150), 640, 2.0), head_mask)
    art.alpha_composite(head)
    # 下巴底部的柔和陰影 (環境光遮蔽)
    chin = linear((255, 190, 196, 0), (255, 190, 196, 70), (CX, CY + 120), (CX, CY + 240))
    if detail:
        art.alpha_composite(clip(chin, head_mask))
    # 耳罩壓在臉上的影子
    if detail:
        art.alpha_composite(clip(soft(cups_mask, 24, (236, 150, 186, 255), 0.35, (0, 8)), head_mask))
    # 頭帶在額頭上的影子
    if detail:
        art.alpha_composite(clip(soft(band_mask, 22, (236, 150, 186, 255), 0.2, (0, 26)), head_mask))
    # 右下的粉色邊緣光 (背景反射)
    if detail:
        art.alpha_composite(rim(head_mask, 20, 14, (255, 160, 205, 255), 0.6, toward=(0.6, 1)))
    # 左上的白色邊緣光
    if detail:
        art.alpha_composite(rim(head_mask, 14, 10, (255, 255, 255, 255), 0.6, toward=(-0.8, -1)))

    # ───────────────── 耳罩 ─────────────────
    cups = clip(linear((128, 102, 226, 255), (44, 28, 96, 255), (CX - 300, CY - 124), (CX + 160, CY + 140)), cups_mask)
    art.alpha_composite(cups)
    if detail:
        art.alpha_composite(rim(cups_mask, 8, 4, (190, 170, 255, 255), 0.9, toward=(-0.8, -1)))  # 上緣反光
    for sx in ((-1, 1) if detail else ()):  # 塑膠的亮點
        x0 = CX + sx * 300
        hl = mask_of(lambda d, x0=x0: d.ellipse((x0 - 52, CY - 110, x0 - 8, CY - 40), fill=255))
        art.alpha_composite(soft(hl, 10, (255, 255, 255, 255), 0.5))
    # 軟墊：粉色漸層 + 往內凹的陰影 + 一點光澤
    pads = clip(linear((255, 170, 204, 255), (240, 98, 152, 255), (CX, CY - 86), (CX, CY + 60)), pads_mask)
    art.alpha_composite(pads)
    if detail:
        art.alpha_composite(inner_shadow(pads_mask, 7, (150, 40, 100, 255), 0.6, (0, 9)))
    for sx in ((-1, 1) if detail else ()):
        o = -12 if sx > 0 else 12
        x0 = CX + sx * 300 + o
        hl = mask_of(lambda d, x0=x0: d.ellipse((x0 - 26, CY - 76, x0 + 2, CY - 20), fill=255))
        art.alpha_composite(soft(hl, 8, (255, 240, 248, 255), 0.55))

    # ───────────────── 臉 ─────────────────
    # 腮紅
    for sx in (-1, 1):
        bx, by = CX + sx * 188, CY + 96
        b = mask_of(lambda d, bx=bx, by=by: d.ellipse((bx - 58, by - 30, bx + 58, by + 30), fill=255))
        art.alpha_composite(soft(b, 16, (255, 120, 166, 255), 0.62))

    # 眼睛：深色往下帶一點紫色的反光 + 兩顆亮點
    for sx in (-1, 1):
        ex, ey = CX + sx * 112, CY + 12
        eye = mask_of(lambda d, ex=ex, ey=ey: d.ellipse((ex - 48, ey - 55, ex + 48, ey + 55), fill=255))
        if detail:
            art.alpha_composite(soft(eye, 8, (120, 70, 110, 255), 0.35, (0, 8)))  # 眼窩的影子
        art.alpha_composite(clip(linear((22, 14, 44, 255), (70, 48, 140, 255), (ex, ey - 40), (ex, ey + 60)), eye))
        glow = mask_of(lambda d, ex=ex, ey=ey: d.ellipse((ex - 34, ey + 8, ex + 34, ey + 52), fill=255))
        art.alpha_composite(clip(soft(glow, 12, (170, 140, 255, 255), 0.75), eye))
        hi = mask_of(lambda d, ex=ex, ey=ey: d.ellipse((ex - 30, ey - 40, ex + 2, ey - 8), fill=255))
        art.alpha_composite(soft(hi, 1.2, (255, 255, 255, 255)))
        hi2 = mask_of(lambda d, ex=ex, ey=ey: d.ellipse((ex + 12, ey + 14, ex + 26, ey + 28), fill=255))
        art.alpha_composite(soft(hi2, 1, (255, 255, 255, 255), 0.85))

    # 鼻子 (圓角倒三角) + 小亮點
    nose = mask_of(lambda d: d.rounded_rectangle((CX - 24, CY + 70, CX + 24, CY + 96), radius=13, fill=255))
    art.alpha_composite(clip(linear((255, 150, 185, 255), (226, 88, 136, 255), (CX, CY + 70), (CX, CY + 96)), nose))
    nh = mask_of(lambda d: d.ellipse((CX - 14, CY + 73, CX + 2, CY + 81), fill=255))
    art.alpha_composite(soft(nh, 1.5, (255, 255, 255, 255), 0.8))

    # 嘴巴 ω
    mouth = mask_of(lambda d: (d.arc((CX - 48, CY + 72, CX + 2, CY + 130), start=12, end=168, fill=255, width=12),
                               d.arc((CX - 2, CY + 72, CX + 48, CY + 130), start=12, end=168, fill=255, width=12)))
    art.alpha_composite(clip(solid((60, 40, 86, 255)), mouth.filter(ImageFilter.GaussianBlur(0.8))))


    # ───────────────── 音符 ─────────────────
    nx, ny = 798, 200
    note = mask_of(lambda d: (d.ellipse((nx - 46, ny + 70, nx + 16, ny + 118), fill=255),
                              d.rectangle((nx + 2, ny - 40, nx + 16, ny + 98), fill=255),
                              d.polygon([(nx + 2, ny - 40), (nx + 72, ny - 8), (nx + 72, ny + 26), (nx + 16, ny + 0)], fill=255)))
    art.alpha_composite(soft(note, 28, (255, 230, 250, 255), 0.7))  # 發光
    art.alpha_composite(clip(linear((255, 255, 255, 255), (255, 214, 236, 255), (nx, ny - 40), (nx, ny + 118)), note))

    # ───────────────── 合成：整隻貓的影子 + 貓 ─────────────────
    cat_alpha = art.getchannel("A")
    icon.alpha_composite(clip(soft(cat_alpha.point(lambda v: 255 if v > 20 else 0), 26, (70, 20, 100, 255), 0.42, (0, 22)), bg_mask))
    icon.alpha_composite(art)

    return icon


def from_source(path):
    """用設計好的圖 (icon_source.*) 做圖示：把外面的黑底挖掉、邊緣修平滑、置中成正方形。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    # 1. 從四個角開始往內找「接近純黑」而且連在一起的區域 = 外面的背景
    dark = im.point(lambda v: 255 if v < 28 else 0).convert("L")
    r_, g_, b_ = im.split()
    dark = ImageChops.multiply(ImageChops.multiply(r_.point(lambda v: 255 if v < 28 else 0),
                                                   g_.point(lambda v: 255 if v < 28 else 0)),
                               b_.point(lambda v: 255 if v < 28 else 0))
    flood = dark.copy()
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if flood.getpixel(xy) == 255:
            ImageDraw.floodfill(flood, xy, 128)
    outside = flood.point(lambda v: 255 if v == 128 else 0)
    alpha = ImageChops.invert(outside)
    # 2. 邊緣往內收 1 px 再柔化：去掉跟黑底混在一起的暗邊
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.1))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    # 3. 裁到圖示本身，四周留一點空白，放在正方形中間
    box = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    out = out.crop(box)
    side = int(max(out.size) * 1.03)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.alpha_composite(out, ((side - out.width) // 2, (side - out.height) // 2))
    return sq.resize((S, S), Image.LANCZOS)


def small_version(img):
    """16~64 px：縮小前先把對比、飽和度拉高一點，縮小後再銳利化，果凍貓才不會糊成一團紫。"""
    from PIL import ImageEnhance
    x = ImageEnhance.Contrast(img).enhance(1.25)
    x = ImageEnhance.Color(x).enhance(1.15)
    return x


def zoomed(img, n):
    """很小的尺寸：放大中間的貓 (切掉一些方塊的邊)，再重新切成圓角方塊，臉才看得出來。"""
    z = {16: 0.70, 24: 0.74, 32: 0.80}.get(n, 1.0)
    if z >= 1.0:
        return img
    w = img.width
    m = int(w * (1 - z) / 2)
    dy = int(w * 0.03)  # 貓稍微偏下，取景往下一點點
    crop = img.crop((m, m + dy, w - m, w - m + dy)).resize((w, w), Image.LANCZOS)
    mask = Image.new("L", (w * 2, w * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 2 - 1, w * 2 - 1), radius=int(w * 2 * 0.22), fill=255)
    mask = mask.resize((w, w), Image.LANCZOS)
    crop.putalpha(ImageChops.multiply(crop.getchannel("A").point(lambda v: 255), mask))
    return crop


def sized(img, n, sharpen=False):
    if sharpen:
        img = zoomed(img, n)
    out = img.resize((n, n), Image.LANCZOS)
    if sharpen:
        rgb = out.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1, percent=55 if n <= 32 else 40, threshold=2))
        rgb.putalpha(out.getchannel("A"))
        out = rgb
    return out


SOURCE = next((ROOT / f"icon_source.{e}" for e in ("png", "webp", "jpg") if (ROOT / f"icon_source.{e}").exists()), None)
if SOURCE is not None:  # 有設計好的圖就用它
    big = from_source(SOURCE)
    small = small_version(big)
else:                   # 沒有的話用程式畫的貓
    big = build(True)
    small = build(False)
sharp = SOURCE is not None
big.resize((256, 256), Image.LANCZOS).save(ROOT / "icon.png")
big.resize((512, 512), Image.LANCZOS).save(ROOT / "icon_preview.png")
# 128 px 以上用原圖，64 px 以下用加強對比的版本 (Windows 會自己挑最接近的尺寸)
sized(small, 64, sharp).save(ROOT / "icon_small.png")  # 系統匣用
frames = [sized(small, n, sharp) for n in (16, 24, 32, 48, 64)] + [big.resize((n, n), Image.LANCZOS) for n in (128, 256)]
frames[-1].save(ROOT / "icon.ico", format="ICO", sizes=[(f.width, f.height) for f in frames], append_images=frames[:-1])
print("icon.ico / icon.png saved", "(from " + SOURCE.name + ")" if SOURCE else "(drawn)")
