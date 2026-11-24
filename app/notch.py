"""
膠囊模式 — 螢幕頂端中間的黑色小膠囊。
平常縮成一條：封面 + 現在唱到的這句歌詞 + 跟著音樂跳的頻譜條；滑鼠移上去就展開成迷你播放器
(模糊封面背景、歌名、卡拉OK歌詞、進度條、上一首 / 播放 / 下一首、裝置電量、回到主視窗)。
會自己「長出來」通知你的事 (Live Activity)：
  換歌 → 彈出封面和歌名；藍牙耳機 / 手把連上 → 電量圓環；裝置快沒電 → 紅色提醒；筆電插上電源；調音量。
手勢：在膠囊上滾輪 = 只調播放器自己的音量；觸控板左右滑 = 上一首 / 下一首。
跟浮動歌詞一樣用分層視窗 (layered window) 畫，透明的地方滑鼠會直接穿過去；全螢幕遊戲時自動藏起來。
膠囊內容用 2 倍解析度畫再縮小，邊緣和圖示才會細緻。
"""
import ctypes
import ctypes.wintypes as wt
import io
import math
import os
import random
import threading
import time

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from overlay import (BITMAPINFOHEADER, BLENDFUNCTION, FONT_DIR, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE,
                     SWP_NOSIZE, WNDCLASSEXW, WNDPROC, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
                     WS_EX_TOPMOST, WS_POPUP, gdi32, kernel32, user32)

WM_LBUTTONUP, WM_RBUTTONUP, WM_MOUSEACTIVATE = 0x202, 0x205, 0x21
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x20A, 0x20E
MA_NOACTIVATE = 3
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

SS = 2                           # 膠囊內容的超取樣倍數
# 尺寸 (dp，乘上螢幕縮放)
WIN_W, WIN_H = 640, 236          # 視窗本身 (最大展開 + 邊緣)
MIN_W, MAX_W, PILL_H = 196, 560, 36
EXP_W, EXP_H, DEV_ROW = 460, 186, 36
EAR = 10                         # 頂端兩側往外彎的小弧
FLOAT_GAP = 6                    # 浮動膠囊離螢幕上緣多遠 (dp)
BAR_W, BAR_H = 220, 6            # 極簡細條收起來的大小 (dp)
# 跟著主題：(底色, 邊框)；字一律是白的，所以底色都要夠深
THEME_FILL = {
    "night": ((24, 16, 48), (150, 120, 255, 80)),
    "dawn": ((46, 52, 70), (215, 222, 240, 130)),
    "mono": ((0, 0, 0), (255, 255, 255, 70)),
    "neon": ((10, 8, 34), (34, 240, 255, 170)),
    "paper": ((60, 44, 30), (240, 226, 196, 140)),
    "oasis": ((18, 18, 18), (43, 212, 107, 150)),
    "crimson": ((40, 16, 22), (255, 55, 95, 130)),
    "cassette": ((42, 32, 22), (255, 159, 28, 150)),
    "vapor": ((36, 12, 52), (255, 113, 206, 160)),
    "ocean": ((6, 26, 44), (76, 201, 240, 140)),
    "sakura": ((70, 34, 50), (255, 200, 220, 150)),
    "matcha": ((34, 48, 28), (214, 232, 190, 140)),
}
FILL = (0, 0, 0, 255)

GREEN, AMBER, RED = (48, 209, 88), (255, 176, 32), (255, 69, 58)

# Windows 11 Fluent 圖示 (Win10 用 MDL2，字碼一樣)
GLYPH = {"play": "", "pause": "", "prev": "", "next": "", "window": "",
         "headphones": "", "controller": "", "mouse": "", "keyboard": "", "phone": "",
         "laptop": "", "speaker": "", "device": "", "pen": "", "bluetooth": "",
         "vol0": "", "vol1": "", "vol2": "", "vol3": "", "charging": "",
         "settings": "", "back": "", "chevron": "", "download": "", "star": "",
         "timer": "\uE916", "wifi": "\uE701", "wifi_off": "\uEB5E", "bell": "\uEA8F"}

# 膠囊裡的設定頁：(key, 標籤)
SETTINGS = [("cat", "哼哼貓 (關 = 音量條)"), ("peek", "換歌時彈出"), ("spectrum", "真實頻譜光暈"), ("battery", "裝置電量"), ("gestures", "滾輪 / 滑動手勢"),
            ("overlay", "浮動歌詞"), ("ai", "AI 歌詞"), ("folia", "送到 Folia 舞台"), ("autostart", "開機自動啟動")]

DEFAULT_OPTS = {"battery": True, "peek": True, "spectrum": True, "gestures": True, "cat": True}
TIMER_PRESETS = (1, 3, 5, 10, 15, 25, 30, 45, 60)  # 分鐘
URGENT = ("timer", "net_off", "net_on", "low")      # 遊戲中也要彈出來的提醒
ORANGE = (255, 159, 10)


def clamp(x, a, b):
    return a if x < a else b if x > b else x


def ease_out(k):
    k = clamp(k, 0, 1)
    return 1 - (1 - k) ** 3


# ───── 字體：膠囊一律用系統介面字體，跟 Windows 本身的質感一致 ─────
_fc = {}


def _load(path, size, index=0, weight=None):
    f = ImageFont.truetype(path, size, index=index)
    if weight:
        try:
            axes = f.get_variation_axes()
            f.set_variation_by_axes([weight if a.get("name") in (b"Weight", "Weight") else a["default"] for a in axes])
        except Exception:
            pass
    return f


def ui_font(text, size, weight=600):
    """weight: 400 一般 / 600 半粗 / 700 粗。中文用微軟正黑體 UI，英文用 Segoe UI Variable。"""
    if any("가" <= c <= "힯" for c in text):
        kind = "ko"
    elif any(ord(c) >= 0x2E80 for c in text):
        kind = "cjk"
    else:
        kind = "latin"
    key = (kind, size, weight)
    if key in _fc:
        return _fc[key]
    bold = weight >= 600
    tries = {
        "latin": [("SegUIVar.ttf", 0, weight), ("segoeuib.ttf" if bold else "segoeui.ttf", 0, None)],
        "cjk": [("msjhbd.ttc" if bold else "msjh.ttc", 1, None), ("msyhbd.ttc" if bold else "msyh.ttc", 1, None)],
        "ko": [("malgunbd.ttf" if bold else "malgun.ttf", 0, None)],
    }[kind] + [("msjh.ttc", 0, None)]
    f = None
    for name, idx, w in tries:
        p = os.path.join(FONT_DIR, name)
        if os.path.exists(p):
            try:
                f = _load(p, size, idx, w)
                break
            except Exception:
                continue
    _fc[key] = f or ImageFont.load_default(size)
    return _fc[key]


def icon_font(size):
    key = ("icons", size)
    if key not in _fc:
        for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
            p = os.path.join(FONT_DIR, name)
            if os.path.exists(p):
                _fc[key] = ImageFont.truetype(p, size)
                break
        else:
            _fc[key] = None
    return _fc[key]


def paste_clipped(dst, src, x, y):
    """alpha_composite，但 src 超出 dst 邊界也沒關係 (自動裁掉)。"""
    sx, sy = max(0, -x), max(0, -y)
    x, y = max(0, x), max(0, y)
    w, h = min(src.width - sx, dst.width - x), min(src.height - sy, dst.height - y)
    if w > 0 and h > 0:
        dst.alpha_composite(src.crop((sx, sy, sx + w, sy + h)), (x, y))


def fit(text, size, max_w, weight=600, min_size=None):
    """回傳 (font, 文字)：放不下先縮字，再不行就截斷加「…」。"""
    font = ui_font(text, size, weight)
    w = font.getlength(text)
    if w > max_w and min_size and size > min_size:
        size = max(min_size, int(size * max_w / max(w, 1)))
        font = ui_font(text, size, weight)
        w = font.getlength(text)
    if w > max_w:
        while text and font.getlength(text + "…") > max_w:
            text = text[:-1]
        text = text.rstrip() + "…"
    return font, text


def rounded(img, radius):
    """把圖片切成圓角 (超取樣遮罩)。"""
    w, h = img.size
    k = 3
    m = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=radius * k, fill=255)
    img = img.copy()
    img.putalpha(ImageChops.multiply(img.getchannel("A"), m.resize((w, h), Image.LANCZOS)))
    return img


_cat_cache = {}


def cat_sprite(size, mood):
    """膠囊裡的哼哼貓 (果凍質感，戴耳機)。mood：happy 瞇眼笑 / sleep 睡覺 / open 睜眼。"""
    size = max(8, int(size))
    key = (size, mood)
    if key in _cat_cache:
        return _cat_cache[key]
    N = size * 4  # 先畫大再縮小，邊緣才平滑
    P = lambda x, y: (x * N, y * N)
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 耳機頭帶 (在頭後面)
    d.arc((*P(0.10, 0.16), *P(0.90, 0.98)), start=198, end=342, fill=(92, 60, 190, 255), width=int(N * 0.075))
    # 頭 + 耳朵 的形狀
    shape = Image.new("L", (N, N), 0)
    sd = ImageDraw.Draw(shape)
    sd.ellipse((*P(0.11, 0.28), *P(0.89, 0.92)), fill=255)
    for sx in (0, 1):
        f = (lambda x: x) if sx == 0 else (lambda x: 1 - x)
        sd.polygon([P(f(0.15), 0.50), P(f(0.21), 0.13), P(f(0.44), 0.33)], fill=255)
    shape = shape.filter(ImageFilter.GaussianBlur(N * 0.012)).point(lambda v: 255 if v > 120 else 0)
    # 果凍：紫 → 粉的漸層，中間亮一點
    grad = Image.linear_gradient("L").rotate(-35, expand=False).resize((N, N))
    c0, c1 = (176, 128, 255), (255, 128, 214)
    body = Image.merge("RGB", [grad.point(lambda v, a=a, b=b: int(a + (b - a) * v / 255)) for a, b in zip(c0, c1)]).convert("RGBA")
    glow = Image.new("L", (N, N), 0)
    ImageDraw.Draw(glow).ellipse((*P(0.28, 0.40), *P(0.72, 0.84)), fill=90)
    body.alpha_composite(Image.merge("RGBA", [Image.new("L", (N, N), 255)] * 3 + [glow.filter(ImageFilter.GaussianBlur(N * 0.08))]))
    body.putalpha(shape.point(lambda v: v * 245 // 255))
    img.alpha_composite(body)
    # 耳朵內側 (另外一層再疊上去：直接畫半透明會把底下的貓挖空)
    ears = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ed = ImageDraw.Draw(ears)
    for sx in (0, 1):
        f = (lambda x: x) if sx == 0 else (lambda x: 1 - x)
        ed.polygon([P(f(0.20), 0.44), P(f(0.225), 0.22), P(f(0.37), 0.35)], fill=(255, 182, 222, 200))
    img.alpha_composite(ears.filter(ImageFilter.GaussianBlur(N * 0.006)))
    # 邊緣亮一圈 (果凍的反光)
    edge = ImageChops.subtract(shape, shape.filter(ImageFilter.MinFilter(max(3, (N // 40) | 1))))
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", (N, N), 255)] * 3 + [edge.point(lambda v: v * 140 // 255)]))
    # 左上的高光
    hl = Image.new("L", (N, N), 0)
    ImageDraw.Draw(hl).ellipse((*P(0.22, 0.34), *P(0.46, 0.46)), fill=200)
    img.alpha_composite(Image.merge("RGBA", [Image.new("L", (N, N), 255)] * 3 + [hl.filter(ImageFilter.GaussianBlur(N * 0.025))]))
    # 耳罩
    for sx in (0, 1):
        x0 = 0.02 if sx == 0 else 0.80
        d.rounded_rectangle((*P(x0, 0.50), *P(x0 + 0.18, 0.80)), radius=N * 0.07, fill=(120, 84, 230, 255))
        d.rounded_rectangle((*P(x0 + 0.04, 0.55), *P(x0 + 0.14, 0.75)), radius=N * 0.04, fill=(255, 140, 205, 255))
    # 眼睛
    ink = (70, 30, 120, 255)
    lw = max(2, int(N * 0.045))
    for cx in (0.355, 0.645):
        if mood == "happy":    # ∩ 瞇眼笑
            d.arc((*P(cx - 0.075, 0.55), *P(cx + 0.075, 0.69)), start=200, end=340, fill=ink, width=lw)
        elif mood == "sleep":  # ∪ 閉眼睡覺
            d.arc((*P(cx - 0.07, 0.53), *P(cx + 0.07, 0.63)), start=20, end=160, fill=ink, width=lw)
        else:                  # 睜眼
            d.ellipse((*P(cx - 0.055, 0.55), *P(cx + 0.055, 0.68)), fill=ink)
            d.ellipse((*P(cx - 0.03, 0.565), *P(cx + 0.0, 0.605)), fill=(255, 255, 255, 255))
    # 嘴巴 ω + 腮紅
    mw = max(2, int(N * 0.032))
    d.arc((*P(0.445, 0.66), *P(0.505, 0.73)), start=10, end=170, fill=ink, width=mw)
    d.arc((*P(0.495, 0.66), *P(0.555, 0.73)), start=10, end=170, fill=ink, width=mw)
    blush = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    for cx in (0.25, 0.75):
        bd.ellipse((*P(cx - 0.065, 0.675), *P(cx + 0.065, 0.745)), fill=(255, 110, 170, 150))
    img.alpha_composite(blush.filter(ImageFilter.GaussianBlur(N * 0.012)))
    out = img.resize((size, size), Image.LANCZOS)
    if len(_cat_cache) > 40:
        _cat_cache.clear()
    _cat_cache[key] = out
    return out


def level_color(level, charging=False):
    if charging or level > 30:
        return GREEN
    return AMBER if level > 15 else RED


class Notch:
    def __init__(self, state, config, runtime):
        self.state, self.config, self.runtime = state, config, runtime
        self.hwnd = None
        self.visible_win = False
        self.click = None
        self.rclick = False
        self.wheel = self.hwheel = 0
        self.hwheel_at = self.swipe_cool = 0.0
        # 動畫 (彈簧)：大小 + 速度
        self.cw = self.ch = None
        self.vw = self.vh = 0.0
        self.exk = 0.0                 # 0 = 收起, 1 = 展開 (內容淡入淡出)
        self.expanded = False
        self.hover_since = self.leave_since = None
        self.last_frame = time.perf_counter()
        self.last_topmost = self.last_fs_check = 0.0
        self.picker, self.mon_rect = None, None
        self.disp_sig, self.disp_at = None, 0.0
        self.healer = None
        self.fullscreen = False
        # 時鐘
        self.anchor_pos, self.anchor_at, self.playing, self.track_id = 0.0, time.perf_counter(), False, None
        # 歌詞切換淡入淡出
        self.line_key, self.line_text, self.prev_text, self.switch_at = None, "", "", 0.0
        self.covers, self.bgs = {}, {}
        self.bars = [0.2] * 4
        self.bar_tgt = [0.2] * 4
        self.bar_next = 0.0
        self.glow = 0.0
        self.static_sig = None
        self.hit = {}
        self.pill_left = self.exp_offset = 0
        # Live Activity
        self.activity = None           # {"kind", "until", "start", ...}
        self.act_prev = None           # 換掉的那個 (淡出)
        self.last_track_seen = None
        self.devices = []              # 目前連線中的裝置 (從 DeviceService 拿)
        self.dev_version = -1
        self.pending_low = []          # 全螢幕時先不提醒，遊戲關掉再說
        self.volume = None             # (app, level, 時間)
        self.page = "main"             # 展開時的頁面：main / settings / devices / timer
        self.cat_blink_at = -10.0      # 哼哼貓眨眼 (換歌時)
        self.timer_end, self.timer_total = None, 0  # 計時器 (perf_counter 時間, 秒數)
        self.timer_custom = int((config.get("timer") or {}).get("custom", 20))  # 自訂的分鐘數 (會記住)
        # 第二顆泡泡 (像 iPhone 同時有兩件事)：0 = 縮在膠囊裡，1 = 分開
        self.bub_k, self.bub_v, self.bub_kind, self.bub_rect = 0.0, 0.0, None, None
        self.pill_w = 0
        self.pill_top = 0
        self.hov_pts = []              # 滑鼠最近 0.2 秒的位置 (判斷是停住還是路過)
        self.fill_f, self.fillc, self.border = [0.0, 0.0, 0.0], (0, 0, 0), None
        self.thin_k = 0.0              # 極簡細條：1 = 完全是細條
        self.act_shown_at = 0.0
        self.opt_cache = {}            # 設定頁顯示用 (開機啟動要讀登錄檔，不要每幀讀)
        self.opt_cache_at = 0.0

    def opt(self, k):
        return (self.config.get("notch_opts") or {}).get(k, DEFAULT_OPTS[k])

    def shape(self):
        """island 動態島 (貼在上緣) / float 浮動膠囊 / bar 極簡細條"""
        v = (self.config.get("notch_style") or {}).get("shape")
        return v if v in ("island", "float", "bar") else "island"

    def target_fill(self, t):
        """膠囊的底色和邊框：black 黑色 / album 專輯色 / theme 跟著主題。"""
        c = (self.config.get("notch_style") or {}).get("color")
        if c == "album":
            a = (t or {}).get("accent")
            if a:
                r, g, b = a
                k = 74 / max(r, g, b, 1)  # 保留色相，壓暗到白字看得清楚
                fill = tuple(int(v * k) for v in (r, g, b))
                return fill, self.accent(t) + (70,)
            return (0, 0, 0), None
        if c == "theme":
            return THEME_FILL.get(self.config.get("theme"), THEME_FILL["night"])
        return (0, 0, 0), None

    def step_fill(self, t, dt):
        """換歌 / 換主題時底色慢慢變過去；回傳是不是還在變。"""
        tgt, self.border = self.target_fill(t)
        cur = self.fill_f
        k = clamp(dt / 0.45, 0, 1)
        self.fill_f = [c + (g - c) * k if abs(g - c) > 0.6 else g for c, g in zip(cur, tgt)]
        self.fillc = tuple(int(round(c)) for c in self.fill_f)
        return self.fill_f != list(tgt)

    # ───── 視窗 ─────
    def setup_scale(self, dpi_scale):
        self.u = dpi_scale             # 螢幕像素 / dp
        self.s = dpi_scale * SS        # 膠囊內容畫布像素 / dp
        self.W, self.H = int(WIN_W * self.u), int(WIN_H * self.u)

    def create(self):
        self.setup_scale(user32.GetDpiForSystem() / 96 if hasattr(user32, "GetDpiForSystem") else 1.0)
        hinst = kernel32.GetModuleHandleW(None)
        if not hasattr(self, "_wndproc"):  # 視窗類別只註冊一次 (重建視窗時沿用)
            self._wndproc = WNDPROC(self.wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "HummiNotch"
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32649))  # IDC_HAND
        user32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self.hwnd = user32.CreateWindowExW(ex, wc.lpszClassName, "Hummi", WS_POPUP, 0, 0, 10, 10,
                                           None, None, hinst, None)
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)
        self.bits = ctypes.c_void_p()
        self.dib, self.dib_size = None, (0, 0)
        sw = user32.GetSystemMetrics(0)  # 主螢幕寬
        self.x, self.y = (sw - self.W) // 2, 0
        self.make_ears()

    def recreate(self):
        """顯示設定變了 (例如遊戲把螢幕切到 HDR)：重建膠囊視窗，不然透明的地方會變黑。"""
        old = self.hwnd
        if self.dib:
            gdi32.DeleteObject(self.dib)
        gdi32.DeleteDC(self.mem_dc)
        user32.ReleaseDC(None, self.screen_dc)
        self.create()
        user32.DestroyWindow(old)
        self.visible_win = False
        self.mon_rect = None
        self.last_fs_check = 0.0
        self.static_sig = None
        self.picker = None
        print("[notch] 膠囊視窗已重建", flush=True)

    def heal_check(self, now):
        """收起來的膠囊上緣兩側一定是純黑；畫面上變灰 = 被顯示卡畫壞了，重建視窗。"""
        if self.healer is None:
            import screens
            self.healer = screens.Healer()
        if self.expanded or self.exk > 0.01 or self.activity or not self.visible_win or not self.dib_size[0]:
            return
        if not self.healer.due(now):
            return
        try:
            import screens
            w, h = self.dib_size
            x0 = self.x + (self.W - w) // 2
            e, pr = self.pill_left, self.pill_left + self.pill_w
            shape = self.shape()
            if shape == "bar":
                return
            if shape == "float":
                y = self.pill_top + self.px(4)
                pts = [(e + self.px(18), y), (e + self.px(26), y), (pr - self.px(18), y), (pr - self.px(26), y)]
            else:
                pts = [(e + self.px(4), 2), (e + self.px(10), 3), (pr - self.px(5), 2), (pr - self.px(11), 3)]
            px = screens.grab_points((x0, self.y, x0 + w, self.y + h), pts)
            broken = all(min(c) >= max(60, max(self.fillc) + 50) for c in px)
            if self.healer.report(broken, now):
                print("[notch] 膠囊被畫成灰色了，重建視窗", flush=True)
                self.recreate()
        except Exception as e:
            print("[notch] heal", repr(e), flush=True)

    def check_display(self, now):
        if now - self.disp_at < 2:
            return
        self.disp_at = now
        import screens
        sig = screens.display_signature()
        if self.disp_sig is not None and sig != self.disp_sig:
            self.recreate()
        self.disp_sig = sig

    def wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE  # 點膠囊不會搶走遊戲 / 其他視窗的焦點
        if msg == WM_LBUTTONUP:
            self.click = (ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
            return 0
        if msg == WM_RBUTTONUP:
            self.rclick = True
            return 0
        if msg == WM_MOUSEWHEEL:
            self.wheel += ctypes.c_short((wp >> 16) & 0xFFFF).value
            return 0
        if msg == WM_MOUSEHWHEEL:
            self.hwheel += ctypes.c_short((wp >> 16) & 0xFFFF).value
            self.hwheel_at = time.perf_counter()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def present(self, img):
        """img 只有膠囊那麼大；視窗跟著縮放、置中，每幀要處理的像素少很多。"""
        w, h = img.size
        if (w, h) != self.dib_size:
            if self.dib:
                gdi32.DeleteObject(self.dib)
            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            self.dib = gdi32.CreateDIBSection(self.mem_dc, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
            gdi32.SelectObject(self.mem_dc, self.dib)
            self.dib_size = (w, h)
        r, g, b, a = img.split()
        data = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                    ImageChops.multiply(r, a), a)).tobytes()
        ctypes.memmove(self.bits, data, len(data))
        x = self.x + (self.W - w) // 2
        user32.UpdateLayeredWindow(self.hwnd, self.screen_dc, ctypes.byref(wt.POINT(x, self.y)),
                                   ctypes.byref(wt.SIZE(w, h)), self.mem_dc,
                                   ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(BLENDFUNCTION(0, 0, 255, 1)), 2)

    def show(self, on):
        if on != self.visible_win:
            user32.ShowWindow(self.hwnd, 4 if on else 0)
            self.visible_win = on

    # ───── 小工具 ─────
    def d(self, v):
        """dp → 膠囊畫布像素 (2 倍)。"""
        return int(round(v * self.s))

    def px(self, v):
        """dp → 螢幕像素。"""
        return int(round(v * self.u))

    def make_ears(self, fill=(0, 0, 0), border=None):
        """膠囊頂端兩側往外彎的小弧，看起來像從螢幕邊緣長出來的。"""
        e = self.px(EAR)
        k = 4
        big = Image.new("L", (e * k, e * k), 255)
        ImageDraw.Draw(big).ellipse((0, 0, 2 * e * k, 2 * e * k), fill=0)  # 圓心在右下角 (e, e)
        ear = Image.new("RGBA", (e, e), tuple(fill) + (255,))
        if border:  # 邊框沿著弧線接到膠囊兩側
            bw = max(k, int(round(1.4 * self.u * k)))
            ring = Image.new("RGBA", (e * k, e * k), (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((-bw, -bw, 2 * e * k + bw, 2 * e * k + bw), outline=border, width=bw)
            ear.alpha_composite(ring.resize((e, e), Image.LANCZOS))
        ear.putalpha(big.resize((e, e), Image.LANCZOS))
        self.ear_key = (e, tuple(fill), border)
        self.ear_r = ear
        self.ear_l = ear.transpose(Image.FLIP_LEFT_RIGHT)
        self.e = e

    _mask_cache = {}

    def pill_mask(self, w, h, r, round_top=False):
        """w, h, r：畫布像素 (2 倍)。上緣是平的，下面兩角圓 (浮動膠囊四個角都圓)。"""
        key = (w, h, r, round_top)
        m = self._mask_cache.get(key)
        if m is None:
            m = Image.new("L", (w, h), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0 if round_top else -r, w - 1, h - 1), radius=r, fill=255)
            if len(self._mask_cache) > 60:
                self._mask_cache.clear()
            self._mask_cache[key] = m
        return m

    def art_image(self, t):
        tid = t["track_id"] if t else None
        try:
            data = self.runtime["art"](tid) if tid and self.runtime.get("art") else None
        except Exception:
            data = None
        if not data:
            return None
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
            w, h = im.size
            m = min(w, h)
            return im.crop(((w - m) // 2, (h - m) // 2, (w + m) // 2, (h + m) // 2))
        except Exception:
            return None

    def cover(self, t, size, radius):
        key = (t and t["track_id"], t and t.get("art_v", 0), t and t.get("has_art"), size, radius)
        if key in self.covers:
            return self.covers[key]
        im = self.art_image(t)
        if im is None:  # 沒有封面 (或還沒載入)：紫色方塊 + 音符；不快取，封面到了才換上
            pk = (None, size, radius)
            if pk not in self.covers:
                img = Image.new("RGBA", (size, size), (92, 70, 190, 255))
                f = ui_font("♪", int(size * 0.55), 600)
                ImageDraw.Draw(img).text((size / 2, size / 2), "♪", font=f, fill=(255, 255, 255, 230), anchor="mm")
                self.covers[pk] = rounded(img, radius)
            return self.covers[pk]
        img = rounded(im.resize((size, size), Image.LANCZOS).convert("RGBA"), radius)
        if len(self.covers) > 16:
            self.covers.clear()
        self.covers[key] = img
        return img

    def backdrop(self, t, w, h):
        """展開時的背景：封面放大、模糊、壓暗，像 Apple Music 那種有顏色的黑。"""
        key = (t and t["track_id"], w, h)
        if key in self.bgs:
            return self.bgs[key]
        im = self.art_image(t)
        if im is None:
            return None
        small = im.resize((48, 48), Image.BILINEAR).filter(ImageFilter.GaussianBlur(6))
        small = ImageEnhance.Color(small).enhance(1.5)
        small = ImageEnhance.Brightness(small).enhance(0.42)
        bg = small.resize((w, h), Image.BICUBIC).convert("RGBA")
        # 上面壓得更黑 (跟收起來的純黑銜接)，下面留一點顏色
        shade = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(235 - v * 0.55))
        black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        black.putalpha(shade)
        bg.alpha_composite(black)
        if len(self.bgs) > 6:
            self.bgs.clear()
        self.bgs[key] = bg
        return bg

    # ───── 時鐘 / 內容 ─────
    def position(self):
        t = self.state.get("track")
        if not t:
            return None, 0.0
        server_pos = t["position"] + ((time.time() - t["ts"]) if t["playing"] else 0)
        local = self.anchor_pos + ((time.perf_counter() - self.anchor_at) if self.playing else 0)
        if t["track_id"] != self.track_id or t["playing"] != self.playing or abs(local - server_pos) > 0.35:
            self.anchor_pos, self.anchor_at = server_pos, time.perf_counter()
            self.playing, self.track_id = t["playing"], t["track_id"]
            local = server_pos
        return t, local

    def lyric(self, t, pos):
        """回傳 (key, 這一句, 說明文字, 這句唱到幾成)。沒有同步歌詞時這一句是空的。"""
        lyr = self.state.get("lyrics")
        if not t:
            return None, "", "沒有在播放", 0
        if not lyr or lyr.get("track_id") != t["track_id"] or lyr.get("loading"):
            return None, "", "正在找歌詞…", 0
        if lyr.get("kind") == "short":
            return None, "", "短影片", 0
        lines = lyr.get("lines") or []
        if not lyr.get("synced") or not lines:
            return None, "", "沒有同步歌詞" if not lines else "這首歌詞沒有時間軸", 0
        tt = pos + self.config.get("offset", 0)
        idx, lo, hi = -1, 0, len(lines) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if lines[mid]["t"] is not None and lines[mid]["t"] <= tt:
                idx, lo = mid, mid + 1
            else:
                hi = mid - 1
        if idx < 0:
            return ("intro", t["track_id"]), "", "♪", 0
        text = (lines[idx]["text"] or "").strip()
        if not text:
            return ("gap", t["track_id"], idx), "", "♪", 0
        start = lines[idx]["t"]
        end = lines[idx + 1]["t"] if idx + 1 < len(lines) and lines[idx + 1]["t"] else start + 5
        dur = max(0.3, min(end - start, len(text) * 0.35 + 2.5))
        return ("line", t["track_id"], idx), text, "", clamp((tt - start) / dur, 0, 1)

    # ───── Live Activity ─────
    def push(self, kind, seconds, **data):
        now = self.last_frame  # 跟畫面用同一個時鐘
        if self.activity and self.activity["kind"] == kind == "volume":
            self.activity.update(data, until=now + seconds)  # 一直滾就一直延長，不重新彈
            return
        if kind == "volume":
            self.vol_disp = data.get("level")  # 新跳出來的音量條從現在的音量開始，不要從上次的位置滑過來
        self.act_prev = self.activity
        self.activity = {"kind": kind, "start": now, "until": now + seconds, **data}

    # ───── 畫面元件 ─────
    def step_bars(self, now, playing):
        viz = self.runtime.get("viz")
        if playing and viz is not None and viz.fresh():
            self.bar_tgt = [0.16 + 0.84 * v for v in viz.levels]
            self.glow += (viz.energy - self.glow) * 0.25
            att, rel = 0.6, 0.22   # 真的頻譜：上升快、下降慢
        else:
            if playing and now >= self.bar_next:
                self.bar_tgt = [random.uniform(0.3, 1.0) for _ in range(4)]
                self.bar_next = now + 0.13
            elif not playing:
                self.bar_tgt = [0.18] * 4
            self.glow *= 0.9
            att, rel = 0.32, 0.32
        self.bars = [b + (t - b) * (att if t > b else rel) for b, t in zip(self.bars, self.bar_tgt)]

    def eq(self, dr, x, y, h, color):
        """四根跳動的小條。x, y = 左上角 (畫布像素)，回傳寬度。"""
        bw, gap = self.d(3), self.d(2.2)
        for i, v in enumerate(self.bars):
            bh = max(bw, int(h * v))
            bx = x + i * (bw + gap)
            dr.rounded_rectangle((bx, y + (h - bh) / 2, bx + bw, y + (h + bh) / 2), radius=bw / 2, fill=color)
        return 4 * bw + 3 * gap

    def glyph(self, dr, kind, cx, cy, size, fill=(255, 255, 255, 240)):
        f = icon_font(self.d(size))
        if f:
            dr.text((cx, cy), GLYPH[kind], font=f, fill=fill, anchor="mm")

    def ring(self, dr, cx, cy, r, level, color, width, kind=None, gsize=None, charging=False, now=0.0):
        """電量圓環 (中間放裝置圖示)。充電中：綠環慢慢呼吸 + 內側光暈；快沒電：紅色閃動。"""
        K = 4  # 先畫在 4 倍大的圖上再縮小，圓弧才會平滑 (直接畫會一格一格的)
        width = max(2, width * 0.8)
        pad = int(width * 4) + 2
        size = int(2 * r + 2 * pad)
        big = Image.new("RGBA", (size * K, size * K), (0, 0, 0, 0))
        bd = ImageDraw.Draw(big)
        c, R, w = size * K / 2, r * K, max(2, int(width * K))
        box = (c - R, c - R, c + R, c + R)
        bd.ellipse(box, outline=(255, 255, 255, 34), width=w)  # 底圈淡淡的
        end = -90 + 360 * clamp(level / 100, 0.03, 1)
        a = 255
        if charging:
            b = 0.5 + 0.5 * math.sin(now * 2 * math.pi / 2.4)
            a = int(255 * (0.6 + 0.4 * b))
            for i, ga in enumerate((70, 32, 14)):
                o = w * (i + 1) * 0.95
                bd.arc((box[0] + o, box[1] + o, box[2] - o, box[3] - o), -90, end, fill=color + (int(ga * b),), width=max(2, w // 2))
        elif level <= 15:
            a = int(255 * (0.45 + 0.55 * (0.5 + 0.5 * math.cos(now * 2 * math.pi / 1.1))))
        if level > 0:
            bd.arc(box, start=-90, end=end, fill=color + (a,), width=w)
            rc = R - w / 2  # 兩端圓頭
            for ang in (-90, end):
                x, y = c + rc * math.cos(math.radians(ang)), c + rc * math.sin(math.radians(ang))
                bd.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color + (a,))
        small = big.resize((size, size), Image.LANCZOS)
        paste_clipped(dr._image, small, int(round(cx - size / 2)), int(round(cy - size / 2)))
        if kind:
            self.glyph(dr, kind if kind in GLYPH else "device", cx, cy, gsize or (r / self.s) * 0.95)

    def sync_devices(self):
        """從 DeviceService 拿裝置清單和事件 (連上 / 快沒電 / 充電 / 充飽)。"""
        svc = self.runtime.get("devices")
        if svc is None:
            self.devices = []
            return
        if svc.version != self.dev_version:
            self.dev_version = svc.version
            self.devices = svc.connected() if self.opt("battery") else []
        evs = self.pending_low + svc.take_events()
        self.pending_low = []
        if not self.opt("battery"):
            return
        for kind, dv in evs:
            self.push(kind, {"device": 3.4, "low": 4.5, "charge": 3.0, "full": 3.0}.get(kind, 3.0), dev=dv)

    def accent(self, t):
        a = (t or {}).get("accent")
        if not a:
            return (255, 255, 255)
        r, g, b = a  # 太暗的顏色在黑底上看不清楚：往白色拉
        lum = 0.3 * r + 0.59 * g + 0.11 * b
        if lum < 150:
            k = (150 - lum) / 150
            r, g, b = (int(c + (255 - c) * k) for c in (r, g, b))
        return (r, g, b)

    # 收起來的大小 (螢幕像素)
    def collapsed_size(self, t, text):
        act = self.activity
        if act:
            k = act["kind"]
            if k == "song":
                at = act["track"]
                f1, s1 = fit(at.get("title") or "", self.d(15), self.d(300), 600)
                f2, s2 = fit(at.get("artist") or "", self.d(12), self.d(300), 400)
                tw = max(f1.getlength(s1), f2.getlength(s2)) / SS
                return int(self.px(clamp(16 + 46 + 14 + tw + 18 + 19 + 18, 300, 460))), self.px(66)
            if k in ("device", "low", "charge", "full"):
                dv = act["dev"]
                f, s = fit(self.act_label(k, dv), self.d(13), self.d(260), 600)
                return int(self.px(clamp(14 + 26 + 12 + f.getlength(s) / SS + 14 + 44 + 16, 250, 440))), self.px(PILL_H + 6)
            if k == "volume":
                return self.px(320), self.px(PILL_H + 6)
            if k in ("update", "updated"):
                return self.px(340), self.px(PILL_H + 6)
            if k == "timer":
                return self.px(320), self.px(PILL_H + 6)
            if k in ("net_off", "net_on"):
                return self.px(250), self.px(PILL_H + 6)
        if not t:
            return self.px(150), self.px(PILL_H)
        if not text:
            return self.px(MIN_W), self.px(PILL_H)
        base = 14 + 24 + 12 + 12 + (24 if self.opt("cat") else 19) + 16  # dp：左邊距 + 封面 (或倒數) + 間距 … 間距 + 貓 / 音量條 + 右邊距
        font, body = fit(text, self.d(14), self.d(MAX_W - base), 600, min_size=self.d(12))
        return int(clamp(self.px(base) + font.getlength(body) / SS, self.px(MIN_W), self.px(MAX_W))), self.px(PILL_H)

    def draw_activity(self, layer, act, t, now):
        d = self.d
        w, h = layer.size
        dr = ImageDraw.Draw(layer)
        k = act["kind"]
        if k == "song":
            at = act["track"]
            cs = d(46)
            layer.alpha_composite(self.cover(at, cs, d(11)), (d(16), (h - cs) // 2))
            tx = d(16) + cs + d(14)
            eqw = d(19)
            maxw = w - tx - d(18) - eqw - d(14)
            f1, s1 = fit(at.get("title") or "", d(15), maxw, 600)
            f2, s2 = fit(at.get("artist") or "", d(12), maxw, 400)
            dr.text((tx, h / 2 - d(2)), s1, font=f1, fill=(255, 255, 255, 250), anchor="ls")
            dr.text((tx, h / 2 + d(15)), s2, font=f2, fill=(255, 255, 255, 140), anchor="ls")
            if self.opt("cat"):
                self.draw_cat(layer, w - d(16) - d(28), h / 2, d(28), True, now)
            else:
                self.eq(dr, w - d(18) - eqw, (h - d(16)) // 2, d(16), self.accent(at) + (255,))
        elif k in ("device", "low", "charge", "full"):
            dv = act["dev"]
            col = RED if k == "low" else level_color(dv["level"], dv.get("charging"))
            r = d(13)
            cx, cy = d(14) + r, h / 2
            lv = 12 if k == "low" and dv["level"] > 15 else dv["level"]
            self.ring(dr, cx, cy, r, lv, col, d(2.6), dv["kind"], 11, charging=dv.get("charging"), now=now)
            tx = cx + r + d(12)
            pct = f"{dv['level']}%"
            pf = ui_font(pct, d(14), 600)
            pw = pf.getlength(pct)
            f, name = fit(self.act_label(k, dv), d(13), w - tx - pw - d(30), 600)
            dr.text((tx, h / 2), name, font=f, fill=(255, 255, 255, 240), anchor="lm")
            dr.text((w - d(16), h / 2), pct, font=pf, fill=col + (255,), anchor="rm")
        elif k in ("update", "updated"):
            col = (120, 170, 255) if k == "update" else (255, 214, 102)
            r = d(13)
            cx = d(14) + r
            dr.ellipse((cx - r, h / 2 - r, cx + r, h / 2 + r), fill=col + (60,))
            self.glyph(dr, "download" if k == "update" else "star", cx, h / 2, 12, fill=col + (255,))
            txt = f"有新版本 v{act['version']}" if k == "update" else f"已更新到 v{act['version']}"
            dr.text((cx + r + d(12), h / 2), txt, font=ui_font(txt, d(13), 600), fill=(255, 255, 255, 240), anchor="lm")
            btn = "更新" if k == "update" else "看新功能"
            bf = ui_font(btn, d(12), 600)
            bw = bf.getlength(btn) + d(20)
            dr.rounded_rectangle((w - d(12) - bw, h / 2 - d(12), w - d(12), h / 2 + d(12)), radius=d(12), fill=col + (255,))
            dr.text((w - d(12) - bw / 2, h / 2), btn, font=bf, fill=(20, 20, 30, 255), anchor="mm")
        elif k == "timer":
            r = d(13)
            cx = d(14) + r
            dr.ellipse((cx - r, h / 2 - r, cx + r, h / 2 + r), fill=ORANGE + (70,))
            self.glyph(dr, "bell", cx, h / 2, 12, fill=ORANGE + (255,))
            mins = max(1, round(act.get("total", 60) / 60))
            dr.text((cx + r + d(12), h / 2 - d(1)), "時間到！", font=ui_font("時間到", d(14), 700), fill=(255, 255, 255, 250), anchor="ls")
            sub = f"{mins} 分鐘計時"
            dr.text((cx + r + d(12), h / 2 + d(13)), sub, font=ui_font(sub, d(11), 400), fill=(255, 255, 255, 140), anchor="ls")
            btn = "知道了"
            bf = ui_font(btn, d(12), 600)
            bw = bf.getlength(btn) + d(20)
            dr.rounded_rectangle((w - d(12) - bw, h / 2 - d(12), w - d(12), h / 2 + d(12)), radius=d(12), fill=ORANGE + (255,))
            dr.text((w - d(12) - bw / 2, h / 2), btn, font=bf, fill=(20, 20, 30, 255), anchor="mm")
        elif k in ("net_off", "net_on"):
            col = RED if k == "net_off" else GREEN
            r = d(13)
            cx = d(14) + r
            dr.ellipse((cx - r, h / 2 - r, cx + r, h / 2 + r), fill=col + (60,))
            self.glyph(dr, "wifi_off" if k == "net_off" else "wifi", cx, h / 2, 12, fill=col + (255,))
            txt = "網路斷線了" if k == "net_off" else "網路恢復了"
            dr.text((cx + r + d(12), h / 2), txt, font=ui_font(txt, d(13), 600), fill=(255, 255, 255, 240), anchor="lm")
        elif k == "volume":
            tgt = act["level"]
            # 音量條滑過去 (不是瞬間跳)：每一幀追上目標的一大半，滾很快也看得出在跟
            lv = getattr(self, "vol_disp", tgt)
            lv += (tgt - lv) * 0.45
            if abs(tgt - lv) < 0.004:
                lv = tgt
            self.vol_disp = lv
            g = "vol0" if tgt <= 0.001 else "vol1" if tgt < 0.34 else "vol2" if tgt < 0.67 else "vol3"
            self.glyph(dr, g, d(30), h / 2, 15)
            bx0, bx1 = d(54), w - d(64)
            bh = d(6)
            dr.rounded_rectangle((bx0, h / 2 - bh / 2, bx1, h / 2 + bh / 2), radius=bh / 2, fill=(255, 255, 255, 46))
            fx = bx0 + (bx1 - bx0) * clamp(lv, 0, 1)
            if fx > bx0 + 1:
                dr.rounded_rectangle((bx0, h / 2 - bh / 2, max(bx0 + bh, fx), h / 2 + bh / 2), radius=bh / 2,
                                     fill=(255, 255, 255, 240))
            txt = f"{round(tgt * 100)}%"
            dr.text((w - d(18), h / 2), txt, font=ui_font(txt, d(13), 600), fill=(255, 255, 255, 220), anchor="rm")
            app = act.get("app") or ""
            if app:
                dr.text((bx0, h / 2 - d(10)), app, font=ui_font(app, d(9), 600), fill=(255, 255, 255, 110), anchor="ls")

    @staticmethod
    def act_label(k, dv):
        return {"low": f"{dv['name']} 快沒電了", "charge": f"{dv['name']} 充電中",
                "full": f"{dv['name']} 已充飽"}.get(k, dv["name"])

    def draw_collapsed(self, pill, t, text, now, alpha):
        if alpha <= 0.01:
            return
        d = self.d
        w, h = pill.size
        layer = Image.new("RGBA", pill.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        act = self.activity
        if act:
            ak = ease_out((now - act["start"]) / 0.38)
            al = Image.new("RGBA", pill.size, (0, 0, 0, 0))
            self.draw_activity(al, act, t, now)
            dy = int(d(10) * (1 - ak))  # 從下面滑上來
            if dy:
                moved = Image.new("RGBA", pill.size, (0, 0, 0, 0))
                moved.alpha_composite(al, (0, dy))
                al = moved
            self.blend(layer, al, ak)
        elif not t:
            f = ui_font("Hummi", d(13), 600)
            if self.opt("cat"):  # 沒在播：貓在旁邊睡覺
                dr.text((w / 2 + d(12), h / 2), "Hummi", font=f, fill=(255, 255, 255, 110), anchor="mm")
                self.draw_cat(layer, w / 2 - d(48), h / 2, d(22), False, now)
            else:
                dr.text((w / 2, h / 2), "Hummi", font=f, fill=(255, 255, 255, 110), anchor="mm")
        else:
            cs = d(24)
            layer.alpha_composite(self.cover(t, cs, d(7)), (d(14), (h - cs) // 2))
            ac = self.accent(t) + (255,)
            if self.opt("cat"):
                eqw = d(24)
                self.draw_cat(layer, w - d(16) - eqw, h / 2, eqw, t["playing"], now)
            else:
                eqw = d(19)
                self.eq(dr, w - d(16) - eqw, (h - d(14)) // 2, d(14), ac)
            x0, x1 = d(14) + cs + d(12), w - d(16) - eqw - d(12)
            k = ease_out((now - self.switch_at) / 0.32)
            for txt, a, dy in ((self.prev_text, 1 - k, -d(9) * k), (text, k, d(9) * (1 - k))):
                if not txt or a <= 0.01:
                    continue
                font, txt = fit(txt, d(14), x1 - x0, 600, min_size=d(12))
                tw = font.getlength(txt)
                cx = x0 + max(0, (x1 - x0 - tw) / 2)
                dr.text((cx, h / 2 + dy), txt, font=font, fill=(255, 255, 255, int(240 * a)), anchor="lm")
        self.blend(pill, layer, alpha)

    def draw_cat(self, layer, x, cy, size, playing, now):
        """哼哼貓：播放時跟著頻譜點頭、換歌時睜眼眨一下；暫停時睡覺，頭上飄 z。"""
        d = self.d
        if not playing:
            mood = "sleep"
        elif now - self.cat_blink_at < 0.32 or (now % 5.3) < 0.13:  # 換歌 / 偶爾眨一下眼
            mood = "open"
        else:
            mood = "happy"
        if playing:
            energy = sum(self.bars) / len(self.bars)
            dy = -d(2.6) * clamp((energy - 0.25) / 0.75, 0, 1) + d(0.5) * math.sin(now * 2 * math.pi * 1.6)
        else:
            dy = d(0.5) * math.sin(now * 2 * math.pi / 3.2)  # 睡覺的呼吸
        spr = cat_sprite(size, mood)
        layer.alpha_composite(spr, (int(x), int(cy - size / 2 + dy)))
        if not playing:  # z z
            dr = ImageDraw.Draw(layer)
            for i in range(2):
                ph = ((now / 2.4) + i * 0.5) % 1.0
                zx = x + size * (0.78 + 0.25 * ph)
                zy = cy - size * (0.45 + 0.55 * ph)
                zs = int(d(7 + 3 * ph))
                a = int(200 * math.sin(ph * math.pi))
                dr.text((zx, zy), "z", font=ui_font("z", zs, 700), fill=(220, 200, 255, a), anchor="mm")

    def timer_left(self, now):
        return max(0.0, self.timer_end - time.perf_counter()) if self.timer_end else 0.0

    def draw_timer_chip(self, dr, x, cy, now):
        """收起來時左邊的倒數 (碼錶圖示 + 剩下的時間)，回傳寬度。"""
        d = self.d
        left = self.timer_left(now)
        txt = f"{int(left // 60)}:{int(left % 60):02d}"
        f = ui_font(txt, d(12), 600)
        w = d(18) + f.getlength(txt) + d(10)
        dr.rounded_rectangle((x, cy - d(11), x + w, cy + d(11)), radius=d(11), fill=ORANGE + (50,))
        self.glyph(dr, "timer", x + d(11), cy, 10, fill=ORANGE + (255,))
        dr.text((x + d(20), cy), txt, font=f, fill=ORANGE + (255,), anchor="lm")
        return w

    def draw_timer(self, layer, dr, w, h, hit, now):
        """計時器頁：選時間；計時中顯示大大的倒數。"""
        d = self.d
        pad = d(20)
        self.glyph(dr, "back", pad + d(6), d(26), 12, fill=(255, 255, 255, 200))
        dr.text((pad + d(24), d(26)), "計時器", font=ui_font("計時器", d(15), 600), fill=(255, 255, 255, 245), anchor="lm")
        hit["back"] = (0, 0, pad + d(80), d(48))
        if self.timer_end:
            left = self.timer_left(now)
            cx, cy, r = w / 2, d(100), d(40)
            frac = left / max(1, self.timer_total)
            dr.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 40), width=d(4))
            if frac > 0:
                dr.arc((cx - r, cy - r, cx + r, cy + r), start=-90, end=-90 + 360 * frac, fill=ORANGE + (255,), width=d(4))
            txt = f"{int(left // 60)}:{int(left % 60):02d}"
            dr.text((cx, cy), txt, font=ui_font(txt, d(22), 700), fill=(255, 255, 255, 250), anchor="mm")
            for i, (name, label) in enumerate((("t_plus", "+1 分鐘"), ("t_cancel", "取消"))):
                bw, bh = d(96), d(28)
                bx = cx - bw - d(6) if i == 0 else cx + d(6)
                by = h - d(44)
                dr.rounded_rectangle((bx, by, bx + bw, by + bh), radius=d(14),
                                     fill=(255, 255, 255, 30) if i == 0 else RED + (200,))
                dr.text((bx + bw / 2, by + bh / 2), label, font=ui_font(label, d(12), 600), fill=(255, 255, 255, 245), anchor="mm")
                hit[name] = (bx, by, bx + bw, by + bh)
            return
        gap = d(8)
        cols = 3
        cw = (w - 2 * pad - gap * (cols - 1)) / cols
        ch, y0 = d(30), d(52)
        for i, m in enumerate(TIMER_PRESETS):
            x = pad + (i % cols) * (cw + gap)
            y = y0 + (i // cols) * (ch + d(6))
            dr.rounded_rectangle((x, y, x + cw, y + ch), radius=d(10), fill=(255, 255, 255, 18))
            label = f"{m} 分鐘"
            dr.text((x + cw / 2, y + ch / 2), label, font=ui_font(label, d(13), 600), fill=(255, 255, 255, 230), anchor="mm")
            hit[f"t:{m}"] = (x, y, x + cw, y + ch)
        # 自訂：[−] 20 分鐘 [+] [開始]；滑鼠在這頁滾滾輪也可以調
        y = y0 + 3 * (ch + d(6)) + d(4)
        dr.text((pad + d(4), y + ch / 2), "自訂", font=ui_font("自訂", d(12), 600), fill=(255, 255, 255, 150), anchor="lm")
        bx = pad + d(44)
        bs = ch
        for name, glyph_txt, x in (("t_minus", "−", bx), ("t_more", "+", bx + bs + d(96))):
            dr.rounded_rectangle((x, y, x + bs, y + ch), radius=ch / 2, fill=(255, 255, 255, 26))
            dr.text((x + bs / 2, y + ch / 2 - d(1)), glyph_txt, font=ui_font("+", d(16), 600), fill=(255, 255, 255, 230), anchor="mm")
            hit[name] = (x, y, x + bs, y + ch)
        val = f"{self.timer_custom} 分鐘"
        dr.text((bx + bs + d(48), y + ch / 2), val, font=ui_font(val, d(14), 700), fill=ORANGE + (255,), anchor="mm")
        go = "開始"
        gx1 = w - pad
        gx0 = gx1 - d(76)
        dr.rounded_rectangle((gx0, y, gx1, y + ch), radius=ch / 2, fill=ORANGE + (255,))
        dr.text(((gx0 + gx1) / 2, y + ch / 2), go, font=ui_font(go, d(13), 700), fill=(20, 20, 30, 255), anchor="mm")
        hit["t_go"] = (gx0, y, gx1, y + ch)

    def start_timer(self, minutes):
        self.timer_total = int(minutes * 60)
        self.timer_end = time.perf_counter() + self.timer_total

    def collect_events(self, now):
        """不管膠囊有沒有顯示都要跑：計時到了、網路斷線 / 恢復。"""
        if self.timer_end and time.perf_counter() >= self.timer_end:
            total, self.timer_end = self.timer_total, None
            self.last_frame = now
            self.push("timer", 10.0, total=total)
            self.static_sig = None
            try:
                import winsound
                winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except Exception:
                pass
        ev = self.runtime.pop("net_event", None)
        if ev:
            self.last_frame = now
            self.push("net_off" if ev == "off" else "net_on", 6.0 if ev == "off" else 3.0)

    def karaoke(self, layer, text, cx, cy, max_w, prog, color):
        font, line = fit(text, self.d(17), max_w, 600, min_size=self.d(13))
        tw = font.getlength(line)
        base = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(base).text((cx, cy), line, font=font, fill=(255, 255, 255, 255), anchor="mm")
        a = base.getchannel("A")
        dim = Image.new("RGBA", layer.size, (255, 255, 255, 0))
        dim.putalpha(a.point(lambda v: v * 95 // 255))
        layer.alpha_composite(dim)
        if prog > 0:
            x0 = cx - tw / 2
            edge = self.d(26)
            bx = x0 + prog * (tw + edge)
            # 掃光遮罩：左邊亮 → 柔邊 → 右邊暗
            mask = Image.new("L", (layer.width, 1), 0)
            sb = int(bx - edge)
            if sb > 0:
                mask.paste(255, (0, 0, min(sb, layer.width), 1))
            grad = Image.linear_gradient("L").rotate(-90).resize((edge, 1))
            mask.paste(grad, (max(0, sb), 0))
            mask = mask.resize(layer.size)
            hi = Image.new("RGBA", layer.size, color + (0,))
            hi.putalpha(ImageChops.multiply(a, mask))
            layer.alpha_composite(hi)

    def exp_height(self):
        if self.page == "timer" and not self.timer_end:
            return EXP_H + 44  # 多一列「自訂」
        return EXP_H + (DEV_ROW if self.devices and self.opt("battery") else 0)

    def options(self):
        """設定頁的開關狀態 (0.5 秒更新一次)。"""
        if time.perf_counter() - self.opt_cache_at > 0.5 and self.runtime.get("get_option"):
            try:
                self.opt_cache = {k: self.runtime["get_option"](k) for k, _ in SETTINGS}
            except Exception:
                pass
            self.opt_cache_at = time.perf_counter()
        return self.opt_cache

    def draw_settings(self, layer, dr, w, h, hit):
        d = self.d
        pad = d(20)
        self.glyph(dr, "back", pad + d(6), d(26), 12, fill=(255, 255, 255, 200))
        dr.text((pad + d(24), d(26)), "設定", font=ui_font("設定", d(15), 600), fill=(255, 255, 255, 245), anchor="lm")
        hit["back"] = (0, 0, pad + d(70), d(48))
        more = "更多設定"
        mf = ui_font(more, d(12), 500)
        mw = mf.getlength(more)
        dr.text((w - pad - d(14), d(26)), more, font=mf, fill=(255, 255, 255, 150), anchor="rm")
        self.glyph(dr, "chevron", w - pad - d(5), d(26), 9, fill=(255, 255, 255, 150))
        hit["more"] = (w - pad - d(14) - mw - d(6), 0, w, d(48))
        opts = self.options()
        items = [(k, lb) for k, lb in SETTINGS if k != "autostart" or self.runtime.get("can_autostart")]
        gap = d(8)
        cw = (w - 2 * pad - gap) / 2
        ch, y0 = d(28), d(50)
        for i, (k, label) in enumerate(items):
            col, row = i % 2, i // 2
            x = pad + col * (cw + gap)
            y = y0 + row * (ch + d(5))
            dr.rounded_rectangle((x, y, x + cw, y + ch), radius=d(9), fill=(255, 255, 255, 16))
            f, lb = fit(label, d(12), cw - d(54), 500)
            dr.text((x + d(10), y + ch / 2), lb, font=f, fill=(255, 255, 255, 225), anchor="lm")
            on = bool(opts.get(k))
            sw, sh = d(30), d(17)
            sx, sy = x + cw - d(10) - sw, y + (ch - sh) / 2
            dr.rounded_rectangle((sx, sy, sx + sw, sy + sh), radius=sh / 2,
                                 fill=(GREEN + (255,)) if on else (255, 255, 255, 50))
            kx = sx + sw - sh / 2 if on else sx + sh / 2
            kr = sh / 2 - d(2)
            dr.ellipse((kx - kr, sy + sh / 2 - kr, kx + kr, sy + sh / 2 + kr), fill=(255, 255, 255, 255))
            hit["opt:" + k] = (x, y, x + cw, y + ch)

    def draw_devices(self, layer, dr, w, h, hit, now):
        """裝置頁：每個連線中的裝置一張卡片，大圓環 + 名稱 + 電量 + 剩餘時間。"""
        d = self.d
        pad = d(20)
        self.glyph(dr, "back", pad + d(6), d(26), 12, fill=(255, 255, 255, 200))
        dr.text((pad + d(24), d(26)), "裝置", font=ui_font("裝置", d(15), 600), fill=(255, 255, 255, 245), anchor="lm")
        hit["back"] = (0, 0, pad + d(70), d(48))
        more = "全部裝置"
        mf = ui_font(more, d(12), 500)
        mw = mf.getlength(more)
        dr.text((w - pad - d(14), d(26)), more, font=mf, fill=(255, 255, 255, 150), anchor="rm")
        self.glyph(dr, "chevron", w - pad - d(5), d(26), 9, fill=(255, 255, 255, 150))
        hit["devices_main"] = (w - pad - d(14) - mw - d(6), 0, w, d(48))
        devs = list(self.devices)[:6]
        if not devs:
            dr.text((w / 2, h / 2), "沒有連線中的裝置", font=ui_font("沒", d(13), 400), fill=(255, 255, 255, 120), anchor="mm")
            return
        cols = 2 if len(devs) == 4 else min(3, len(devs))
        rows = (len(devs) + cols - 1) // cols
        gap = d(10)
        cw = (w - 2 * pad - gap * (cols - 1)) / cols
        top = d(50)
        ch = min(d(124), (h - top - d(14) - gap * (rows - 1)) / rows)
        for i, dv in enumerate(devs):
            x = pad + (i % cols) * (cw + gap)
            y = top + (i // cols) * (ch + gap)
            dr.rounded_rectangle((x, y, x + cw, y + ch), radius=d(14), fill=(255, 255, 255, 14))
            col = level_color(dv["level"], dv.get("charging"))
            big = ch > d(80)
            r = d(21) if big else d(13)
            cx = x + (cw / 2 if big else d(12) + r)
            cy = y + (d(12) + r if big else ch / 2)
            self.ring(dr, cx, cy, r, dv["level"], col, d(3.2 if big else 2.4), dv["kind"], 15 if big else 10,
                      charging=dv.get("charging"), now=now)
            pct = f"{dv['level']}%"
            sub = "充電中" if dv.get("charging") else (
                (f"約 {dv['hours']:.0f} 小時" if dv["hours"] >= 2 else f"約 {round(dv['hours'] * 60)} 分鐘")
                if dv.get("hours") else "")
            if big:
                f, nm = fit(dv["name"], d(12), cw - d(16), 600)
                dr.text((x + cw / 2, cy + r + d(16)), nm, font=f, fill=(255, 255, 255, 230), anchor="mm")
                line = pct + (f" · {sub}" if sub else "")
                dr.text((x + cw / 2, cy + r + d(33)), line, font=ui_font(line, d(11), 500), fill=col + (230,), anchor="mm")
            else:
                tx = cx + r + d(10)
                f, nm = fit(dv["name"], d(11), x + cw - tx - d(8), 600)
                dr.text((tx, cy - d(7)), nm, font=f, fill=(255, 255, 255, 230), anchor="lm")
                line = pct + (f" · {sub}" if sub else "")
                f2, line = fit(line, d(10), x + cw - tx - d(8), 500)
                dr.text((tx, cy + d(9)), line, font=f2, fill=col + (230,), anchor="lm")

    def draw_expanded(self, pill, t, pos, text, note, prog, now, alpha):
        if alpha <= 0.01:
            return
        d = self.d
        w, h = d(EXP_W), d(self.exp_height())
        off = (pill.width - w) // 2
        self.exp_offset = off
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bg = self.backdrop(t, w, h) if t else None
        if bg is not None:
            layer.alpha_composite(bg)
        dr = ImageDraw.Draw(layer)
        pad = d(20)
        hit = {}
        if self.page in ("settings", "devices", "timer"):
            if self.page == "settings":
                self.draw_settings(layer, dr, w, h, hit)
            elif self.page == "timer":
                self.draw_timer(layer, dr, w, h, hit, now)
            else:
                self.draw_devices(layer, dr, w, h, hit, now)
            self.hit = hit
            self.blend(pill, layer, alpha, (off, 0))
            return
        if t:
            ac = self.accent(t)
            cs = d(64)
            cy0 = d(18)
            layer.alpha_composite(self.cover(t, cs, d(14)), (pad, cy0))
            hit["main"] = (pad, cy0, pad + cs, cy0 + cs)
            eqw = self.eq(dr, w - pad - d(19), d(30), d(18), ac + (255,))
            tx = pad + cs + d(16)
            font, title = fit(t.get("title") or "—", d(16), w - tx - pad - eqw - d(12), 600)
            dr.text((tx, d(38)), title, font=font, fill=(255, 255, 255, 250), anchor="ls")
            font, artist = fit(t.get("artist") or "", d(13), w - tx - pad, 400)
            dr.text((tx, d(60)), artist, font=font, fill=(255, 255, 255, 140), anchor="ls")
            # 這一句歌詞 (卡拉OK)
            ly = d(108)
            if text:
                self.karaoke(layer, text, w / 2, ly, w - 2 * pad, prog, ac)
                dr = ImageDraw.Draw(layer)
            else:
                f = ui_font(note, d(14), 400)
                dr.text((w / 2, ly), note, font=f, fill=(255, 255, 255, 110), anchor="mm")
            # 進度條
            dur = t.get("duration") or 0
            py = d(140)
            tf = ui_font("0:00", d(11), 500)
            bx0, bx1 = pad + d(40), w - pad - d(40)
            dr.text((pad, py), fmt(pos), font=tf, fill=(255, 255, 255, 140), anchor="lm")
            dr.text((w - pad, py), fmt(dur), font=tf, fill=(255, 255, 255, 140), anchor="rm")
            bh = d(5)
            dr.rounded_rectangle((bx0, py - bh / 2, bx1, py + bh / 2), radius=bh / 2, fill=(255, 255, 255, 46))
            if dur > 0:
                fx = bx0 + (bx1 - bx0) * clamp(pos / dur, 0, 1)
                dr.rounded_rectangle((bx0, py - bh / 2, max(bx0 + bh, fx), py + bh / 2), radius=bh / 2,
                                     fill=(255, 255, 255, 235))
                hit["seek"] = (bx0, py - d(10), bx1, py + d(10), dur)
            # 按鈕
            by = d(166)
            cx = w // 2
            self.glyph(dr, "prev", cx - d(58), by, 15)
            self.glyph(dr, "pause" if t["playing"] else "play", cx, by, 20)
            self.glyph(dr, "next", cx + d(58), by, 15)
            hit["prev"] = (cx - d(78), by - d(16), cx - d(38), by + d(16))
            hit["toggle"] = (cx - d(22), by - d(18), cx + d(22), by + d(18))
            hit["next"] = (cx + d(38), by - d(16), cx + d(78), by + d(16))
        else:
            f = ui_font("沒有在播放", d(15), 600)
            dr.text((w / 2, d(76)), "沒有在播放", font=f, fill=(255, 255, 255, 200), anchor="mm")
            f2 = ui_font("在", d(12), 400)
            dr.text((w / 2, d(102)), "在 Spotify 或 YouTube Music 播一首歌", font=f2, fill=(255, 255, 255, 110), anchor="mm")
        # 右下：回到主視窗；左下：設定
        mx, my = w - pad - d(6), d(166)
        self.glyph(dr, "window", mx, my, 14, fill=(255, 255, 255, 150))
        hit["main_btn"] = (mx - d(18), my - d(16), mx + d(18), my + d(16))
        gx = pad + d(6)
        self.glyph(dr, "settings", gx, my, 14, fill=(255, 255, 255, 150))
        hit["settings"] = (gx - d(18), my - d(16), gx + d(18), my + d(16))
        tx = gx + d(34)  # 計時器 (計時中是橘色)
        self.glyph(dr, "timer", tx, my, 14, fill=(ORANGE + (230,)) if self.timer_end else (255, 255, 255, 150))
        hit["timer"] = (tx - d(16), my - d(16), tx + d(16), my + d(16))
        # 裝置電量列
        if self.devices and self.opt("battery"):
            devs = list(self.devices)[:4]
            ry = d(EXP_H + DEV_ROW / 2 - 4)
            dr.line((pad, d(EXP_H - 6), w - pad, d(EXP_H - 6)), fill=(255, 255, 255, 24), width=max(1, d(0.6)))
            chips = []
            for dv in devs:
                pct = f"{dv['level']}%"
                pf = ui_font(pct, d(12), 600)
                chips.append((dv, pct, pf, d(22) + d(6) + pf.getlength(pct)))
            gap = d(22)
            total = sum(c[3] for c in chips) + gap * (len(chips) - 1)
            x = (w - total) / 2
            for dv, pct, pf, cw in chips:
                col = level_color(dv["level"], dv.get("charging"))
                self.ring(dr, x + d(11), ry, d(11), dv["level"], col, d(2), dv["kind"], 9,
                          charging=dv.get("charging"), now=now)
                dr.text((x + d(28), ry), pct, font=pf, fill=(255, 255, 255, 210), anchor="lm")
                x += cw + gap
            hit["devices"] = (0, d(EXP_H - 6), w, h)
        self.hit = hit
        self.blend(pill, layer, alpha, (off, 0))

    def rim(self, pill, color, strength, r):
        """膠囊邊緣跟著音樂亮起來 (畫在膠囊裡面，不會擋到滑鼠)。"""
        a = int(clamp(strength, 0, 1) * 200)
        if a < 6:
            return
        w, h = pill.size
        layer = Image.new("RGBA", pill.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        for i, f in enumerate((1.0, 0.42, 0.16)):
            o = self.d(1.4) * i
            dr.rounded_rectangle((o, -r, w - 1 - o, h - 1 - o), radius=max(1, r - o), outline=color + (int(a * f),),
                                 width=self.d(1.6))
        pill.alpha_composite(layer)

    @staticmethod
    def blend(dst, layer, alpha, at=(0, 0)):
        if alpha < 0.999:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        dst.alpha_composite(layer, at)

    def bubble_kind(self):
        """第二顆泡泡要顯示什麼：計時中 > 有裝置在充電。展開時收回膠囊裡。"""
        if self.timer_end:
            return "timer"
        if self.opt("battery") and any(dv.get("charging") for dv in self.devices):
            return "charge"
        return None

    def step_bubble(self, dt):
        kind = self.bubble_kind()
        want = kind is not None and not self.expanded and self.exk < 0.05 and self.thin_k < 0.05 \
            and not (self.activity and self.activity["kind"] in ("timer", "song", "volume"))
        if kind:
            self.bub_kind = kind
        self.bub_k, self.bub_v = self.spring(self.bub_k, self.bub_v, 1.0 if want else 0.0, dt)
        if not want and self.bub_k < 0.01 and abs(self.bub_v) < 0.05:
            self.bub_k, self.bub_v = 0.0, 0.0
            if not kind:
                self.bub_kind = None
        return abs(self.bub_v) > 0.02 or 0.01 < self.bub_k < 0.99

    def draw_bubble(self, img, cx, cy, size, alpha, now):
        """泡泡裡的東西：計時 = 橘色圈 + 剩下的時間；充電 = 綠色電量圈 + 裝置圖示。"""
        if alpha <= 0.02 or size < 4:
            return
        k = SS
        layer = Image.new("RGBA", (int(size * k), int(size * k)), (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        c = size * k / 2
        r = c - self.d(4)
        if self.bub_kind == "timer" and self.timer_end:
            left = self.timer_left(now)
            frac = left / max(1, self.timer_total)
            dr.ellipse((c - r, c - r, c + r, c + r), outline=ORANGE + (60,), width=self.d(2.6))
            if frac > 0:
                dr.arc((c - r, c - r, c + r, c + r), start=-90, end=-90 + 360 * frac, fill=ORANGE + (255,), width=self.d(2.6))
            txt = f"{int(left // 60)}:{int(left % 60):02d}" if left < 600 else f"{int(left // 60)}m"
            f, txt = fit(txt, self.d(9), r * 1.6, 700)
            dr.text((c, c), txt, font=f, fill=ORANGE + (255,), anchor="mm")
        elif self.bub_kind == "charge":
            dv = next((dv for dv in self.devices if dv.get("charging")), None)
            if dv:
                self.ring(dr, c, c, r, dv["level"], GREEN, self.d(2.6), dv["kind"], 10, charging=True, now=now)
        layer = layer.resize((int(size), int(size)), Image.LANCZOS)
        if alpha < 0.999:
            layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
        img.alpha_composite(layer, (int(cx - size / 2), int(cy - size / 2)))

    def render(self, t, pos, now):
        """回傳只包含膠囊 (加兩側小弧) 的圖；寬度一定是偶數+兩側，置中才不會左右晃。"""
        w, h = max(4, int(round(self.cw))), max(4, int(round(self.ch)))
        w += (self.W - w) % 2
        shape = self.shape()
        fill, border = self.fillc, self.border
        if shape == "island":
            if getattr(self, "ear_key", None) != (self.px(EAR), fill, border):
                self.make_ears(fill, border)
            e = self.e
        else:
            e = 0  # 浮動膠囊、細條沒有兩側的小弧
        top = self.px(FLOAT_GAP) if shape == "float" else 0
        sp = self.px(12) if shape == "float" else 0  # 陰影的空間
        # 有第二顆泡泡時，左右各多留一顆泡泡的空間 (膠囊才會維持在正中間)
        bs, gap = self.px(PILL_H), self.px(8)
        ext = (bs + gap) if self.bub_kind else 0
        img = Image.new("RGBA", (w + 2 * e + 2 * ext + 2 * sp, top + h + sp), (0, 0, 0, 0))
        W2, H2 = w * SS, h * SS
        r = int(min(H2 / 2, self.d(18) + (self.d(30) - self.d(18)) * self.exk))
        pill = Image.new("RGBA", (W2, H2), fill + (255,))
        key, text, note, prog = self.lyric(t, pos)
        self.hit = {}
        self.exp_offset = 0
        thin = self.thin_k
        self.draw_collapsed(pill, t, text, now, clamp(1 - self.exk * 2.4, 0, 1) * (1 - thin))
        self.draw_expanded(pill, t, pos, text, note, prog, now, clamp((self.exk - 0.4) / 0.6, 0, 1))
        if thin > 0.01:
            self.draw_bar(pill, t, pos, thin)
        if t and t["playing"] and self.opt("spectrum") and thin < 0.5:
            self.rim(pill, self.accent(t), self.glow * 0.9, r)
        if border and thin < 0.99:
            box = (0, 0 if shape == "float" else -r, W2 - 1, H2 - 1)
            bd = ImageDraw.Draw(pill)
            if self.config.get("theme") == "neon" and (self.config.get("notch_style") or {}).get("color") == "theme":
                bd.rounded_rectangle(box, radius=r, outline=border[:3] + (int(38 * (1 - thin)),), width=self.d(3))  # 霓虹的光暈
            bd.rounded_rectangle(box, radius=r, outline=border[:3] + (int(border[3] * (1 - thin)),), width=max(2, self.d(1.4)))
        if thin < 0.99:
            self.glass_sheen(pill, r, shape == "float", 1 - thin)
        if 0.02 < self.exk < 0.98:
            self.liquid_sweep(pill, self.exk if self.expanded else 1 - self.exk)
        mask = self.pill_mask(W2, H2, r, round_top=shape == "float")
        pill.putalpha(mask)
        pill = pill.resize((w, h), Image.LANCZOS)
        x0 = ext + e + sp
        if sp:  # 浮動膠囊下面淡淡的影子
            skey = (img.size, w, h, r, x0)
            if getattr(self, "_shadow_key", None) != skey:
                sh = Image.new("L", img.size, 0)
                sh.paste(mask.resize((w, h), Image.BILINEAR), (x0, top + self.px(3)))
                sh = sh.filter(ImageFilter.GaussianBlur(self.px(5))).point(lambda v: v * 45 // 100)
                shadow = Image.new("RGBA", img.size, (0, 0, 0, 255))
                shadow.putalpha(sh)
                self._shadow_key, self._shadow = skey, shadow
            img.alpha_composite(self._shadow)
        self.bub_rect = None
        if self.bub_kind and self.bub_k > 0.005:
            # 泡泡從膠囊右邊「啵」地分出來：還很近的時候用模糊 + 門檻讓兩個形狀黏在一起，像液體
            kk = self.bub_k
            size = bs * clamp(0.35 + 0.65 * kk, 0, 1.08)
            cx = x0 + w - bs / 2 + (bs / 2 + gap + bs / 2) * kk
            cy = top + min(h, self.px(PILL_H)) / 2
            G = 3  # 泡泡在 3 倍解析度畫，縮小後邊緣才圓滑
            goo = Image.new("L", (img.width * G, img.height * G), 0)
            gd = ImageDraw.Draw(goo)
            gd.rounded_rectangle(((x0 + w - bs * 1.2) * G, top * G, (x0 + w - 1) * G, (top + self.px(PILL_H) - 1) * G), radius=bs / 2 * G, fill=255)
            gd.ellipse(((cx - size / 2) * G, (cy - size / 2) * G, (cx + size / 2) * G, (cy + size / 2) * G), fill=255)
            if kk < 0.98:
                goo = goo.filter(ImageFilter.GaussianBlur(self.px(3.2) * G)).point(lambda v: 0 if v < 96 else 255 if v > 150 else (v - 96) * 255 // 54)
            goo = goo.resize(img.size, Image.LANCZOS)
            black = Image.new("RGBA", img.size, fill + (255,))
            black.putalpha(goo)
            img.alpha_composite(black)
            if kk > 0.9:  # 分開之後，泡泡外圈一道細細的玻璃高光
                hl = Image.new("RGBA", (int(size * G) + 4, int(size * G) + 4), (0, 0, 0, 0))
                ImageDraw.Draw(hl).ellipse((1, 1, hl.width - 2, hl.height - 2), outline=(255, 255, 255, int(34 * clamp((kk - 0.9) * 10, 0, 1))),
                                           width=max(2, int(self.px(1) * G * 0.8)))
                hl = hl.resize((max(1, hl.width // G), max(1, hl.height // G)), Image.LANCZOS)
                paste_clipped(img, hl, int(round(cx - hl.width / 2)), int(round(cy - hl.height / 2)))
            self.draw_bubble(img, cx, cy, size, clamp((kk - 0.35) / 0.5, 0, 1), now)
            if kk > 0.6:
                self.bub_rect = (cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2)
        img.alpha_composite(pill, (x0, top))
        if e:
            img.alpha_composite(self.ear_l, (ext, 0))
            img.alpha_composite(self.ear_r, (x0 + w, 0))
        self.pill_left = x0  # 膠囊在視窗裡的 x, y (點擊座標換算用)
        self.pill_top = top
        self.pill_w = w
        return img

    _sheen_cache = {}

    def glass_sheen(self, pill, r, round_top, k):
        """黑玻璃的質感：上方微亮往下漸暗、邊緣一圈極細的高光 (只疊在本體上，不影響文字)。"""
        W2, H2 = pill.size
        key = (W2, H2, r, round_top)
        layer = self._sheen_cache.get(key)
        if layer is None:
            layer = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
            grad = Image.linear_gradient("L").resize((1, 256)).point(lambda v: int((255 - v) * 0.075))  # 上方 7%
            g = grad.resize((W2, H2))
            white = Image.new("RGBA", (W2, H2), (255, 255, 255, 0))
            white.putalpha(g)
            layer.alpha_composite(white)
            edge = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
            ImageDraw.Draw(edge).rounded_rectangle((0, 0 if round_top else -r, W2 - 1, H2 - 1), radius=r,
                                                   outline=(255, 255, 255, 30), width=max(2, self.d(1)))
            layer.alpha_composite(edge)
            if len(self._sheen_cache) > 40:
                self._sheen_cache.clear()
            self._sheen_cache[key] = layer
        if k >= 0.99:
            pill.alpha_composite(layer)
        else:
            l2 = layer.copy()
            l2.putalpha(l2.getchannel("A").point(lambda v: int(v * k)))
            pill.alpha_composite(l2)

    _tick_path = None

    def click_sound(self):
        """很輕的「咔」(只在設定打開時)。第一次用到時產生一個很短的 wav。"""
        if not self.config.get("ui_sound"):
            return
        try:
            import os, struct, tempfile, wave, winsound
            if Notch._tick_path is None or not os.path.exists(Notch._tick_path):
                rate, n = 44100, int(44100 * 0.07)
                frames = bytearray()
                for i in range(n):
                    t = i / rate
                    f = 1300 - 4000 * t  # 往下滑的音高
                    env = min(1.0, t / 0.004) * math.exp(-t / 0.018)
                    frames += struct.pack("<h", int(32767 * 0.16 * env * math.sin(2 * math.pi * f * t)))
                path = os.path.join(tempfile.gettempdir(), "hummi_tick.wav")
                with wave.open(path, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(bytes(frames))
                Notch._tick_path = path
            winsound.PlaySound(Notch._tick_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception as e:
            print("[notch] sound", repr(e), flush=True)

    def liquid_sweep(self, pill, k):
        """展開 / 收起的動畫中：一道斜斜的光帶從左掃到右，像光線穿過玻璃。k = 0~1 動畫進度。"""
        W2, H2 = pill.size
        band = max(8, int(W2 * 0.22))
        x = int(-band + (W2 + band * 2) * k)
        strength = math.sin(math.pi * k)  # 中間最亮，頭尾淡出
        row = Image.linear_gradient("L").rotate(90, expand=True).resize((band, 1))  # 0→255
        prof = row.point(lambda v: int(255 * math.sin(math.pi * v / 255) ** 2 * 0.16 * strength))  # 中間亮兩邊淡
        alpha = Image.new("L", (W2 + band * 2, H2), 0)
        alpha.paste(prof.resize((band, H2)), (x + band, 0))
        alpha = alpha.transform(alpha.size, Image.AFFINE, (1, 0.35, -H2 * 0.175, 0, 1, 0), Image.BILINEAR).crop((band, 0, band + W2, H2))  # 斜一點
        layer = Image.new("RGBA", (W2, H2), (255, 255, 255, 0))
        layer.putalpha(alpha)
        pill.alpha_composite(layer)

    def draw_bar(self, pill, t, pos, alpha):
        """極簡細條：一條播放進度，顏色是專輯的顏色。"""
        w, h = pill.size
        layer = Image.new("RGBA", pill.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        if t and t.get("duration"):
            frac = clamp(pos / max(1, t["duration"]), 0, 1)
            a = 255 if t["playing"] else 140
            dr.rectangle((0, 0, int(w * frac), h), fill=self.accent(t) + (a,))
        self.blend(pill, layer, alpha)

    # ───── 互動 ─────
    def handle_click(self, x, y):
        b = self.bub_rect
        if b and b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            self.expanded = True
            self.activity = None
            self.hover_since = time.perf_counter()
            self.leave_since = None  # 不然「滑鼠離開多久」還在算之前的，展開馬上又收起來
            self.page = "timer" if self.bub_kind == "timer" else "devices"
            if self.page == "devices" and self.runtime.get("devices") is not None:
                self.runtime["devices"].refresh()
            self.static_sig = None
            return
        if self.activity and self.activity["kind"] in ("timer", "net_off", "net_on") and self.exk < 0.3:
            self.activity = None  # 點「知道了」
            self.static_sig = None
            return
        if self.activity and self.activity["kind"] in ("update", "updated") and self.exk < 0.3:
            self.activity = None  # 點「有新版本 / 已更新」：打開主視窗的更新畫面
            if self.runtime.get("open_update"):
                self.runtime["open_update"]()
            return
        if not self.expanded or self.exk < 0.7:
            self.expanded = True
            self.activity = None
            self.hover_since = time.perf_counter()
            return
        px = (x - self.pill_left) * SS - self.exp_offset
        py = (y - self.pill_top) * SS
        cmd = self.runtime.get("media_cmd")
        for name, box in self.hit.items():
            if box[0] <= px <= box[2] and box[1] <= py <= box[3]:
                if name == "seek" and cmd:
                    frac = clamp((px - box[0]) / max(1, box[2] - box[0]), 0, 1)
                    cmd("seek", frac * box[4])
                    self.anchor_pos, self.anchor_at = frac * box[4], time.perf_counter()
                elif name in ("prev", "next", "toggle") and cmd:
                    cmd(name)
                elif name in ("main", "main_btn") and self.runtime.get("open_main"):
                    self.runtime["open_main"]()
                elif name == "settings":
                    self.page = "settings"
                    self.opt_cache_at = 0.0
                elif name == "timer":
                    self.page = "timer"
                elif name.startswith("t:"):
                    self.start_timer(int(name[2:]))
                    self.page = "main"
                elif name == "t_plus" and self.timer_end:
                    self.timer_end += 60
                    self.timer_total += 60
                elif name == "t_cancel":
                    self.timer_end = None
                elif name in ("t_minus", "t_more"):
                    self.set_custom(self.timer_custom + (-1 if name == "t_minus" else 1))
                elif name == "t_go":
                    self.start_timer(self.timer_custom)
                    self.page = "main"
                elif name == "back":
                    self.page = "main"
                elif name == "more" and self.runtime.get("open_settings"):
                    self.runtime["open_settings"]()
                elif name == "devices":
                    self.page = "devices"
                    if self.runtime.get("devices") is not None:
                        self.runtime["devices"].refresh()
                elif name == "devices_main" and self.runtime.get("open_devices"):
                    self.runtime["open_devices"]()
                elif name.startswith("opt:") and self.runtime.get("set_option"):
                    k = name[4:]
                    new = not self.options().get(k)
                    self.opt_cache[k] = new  # 先畫出來，不用等
                    self.runtime["set_option"](k, new)
                    self.opt_cache_at = time.perf_counter()
                self.static_sig = None  # 重畫
                return

    def set_custom(self, minutes):
        self.timer_custom = int(clamp(minutes, 1, 180))
        self.config.setdefault("timer", {})["custom"] = self.timer_custom
        self.static_sig = None
        save = self.runtime.get("save_config")
        if save:
            save()

    def handle_wheel(self, t, now):
        """滾輪：只調播放器自己的音量 (計時器頁是調自訂分鐘)；觸控板左右滑：切歌。"""
        if self.wheel and self.expanded and self.page == "timer" and not self.timer_end:
            steps, self.wheel = self.wheel / 120, 0
            self.set_custom(self.timer_custom + (1 if steps > 0 else -1) * max(1, round(abs(steps))))
            return
        if self.wheel:
            steps, self.wheel = self.wheel / 120, 0
            if t and self.opt("gestures"):
                try:
                    fn = self.runtime.get("music_volume")  # 背景引擎套用，畫面馬上更新 (滾很快也跟得上)
                    if fn and fn(steps * 0.04) is not None:
                        self.expanded = False
                except Exception as e:
                    print("[notch-volume]", repr(e), flush=True)
        if self.hwheel and now - self.hwheel_at > 0.25:
            self.hwheel = 0  # 滑完了，重來
        if abs(self.hwheel) >= 240 and now > self.swipe_cool and self.opt("gestures"):
            cmd = self.runtime.get("media_cmd")
            if cmd and t:
                cmd("next" if self.hwheel > 0 else "prev")
            self.hwheel = 0
            self.swipe_cool = now + 0.9

    @staticmethod
    def cursor():
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def update_hover(self, now):
        if self.cw is None:
            return
        px, py = self.cursor()
        w, h = self.cw, self.ch
        m = self.px(10) if self.expanded else 0
        mv = m
        shape = self.shape()
        if shape == "bar" and not self.expanded:
            m, mv = self.px(10), self.px(14)
        top = self.px(FLOAT_GAP) if shape == "float" else 0
        left = self.x + (self.W - w) / 2
        inside = (left - m <= px <= left + w + m) and (self.y <= py <= self.y + top + h + mv)
        if inside:
            self.leave_since = None
            if self.hover_since is None:
                self.hover_since, self.hov_pts = now, []
            self.hov_pts = [q for q in self.hov_pts if now - q[0] <= 0.2] + [(now, px, py)]
            if not self.expanded and not (self.activity and self.activity["kind"] in ("volume", "update", "updated")) \
                    and self.hover_intent(now, px, py):
                self.expanded = True
                self.activity = None
        else:
            self.hover_since = None
            if self.leave_since is None:
                self.leave_since = now
            elif self.expanded and now - self.leave_since > (0.8 if self.page != "main" else 0.35):
                self.expanded = False
                self.page = "main"

    def hover_intent(self, now, px, py):
        """滑鼠是真的要看膠囊，還是只是路過 (要去點瀏覽器分頁、拖視窗)？
        smart：停在膠囊上 (0.2 秒內幾乎沒動) 才展開，按著滑鼠鍵 (拖分頁、選字) 時不展開
        fast：移上去就展開；click：只有點一下才展開"""
        mode = (self.config.get("notch_style") or {}).get("hover", "smart")
        dwell = now - self.hover_since
        if mode == "click":
            return False
        if mode == "fast":
            return dwell > 0.12
        if user32.GetAsyncKeyState(0x01) & 0x8000 or user32.GetAsyncKeyState(0x02) & 0x8000:
            self.hover_since, self.hov_pts = now, [(now, px, py)]  # 放開後要重新停一下
            return False
        if dwell < 0.22:
            return False
        moved = max(math.hypot(px - x, py - y) for _, x, y in self.hov_pts)
        return moved < self.px(14)

    def check_fullscreen(self, now):
        """膠囊那個螢幕上有全螢幕遊戲 / 影片：膠囊先藏起來。順便跟著設定換螢幕。"""
        if now - self.last_fs_check < 0.5:
            return
        self.last_fs_check = now
        import screens
        if self.picker is None:
            self.picker = screens.Picker([self.hwnd])
        mon = self.picker.pick(self.config.get("notch_monitor", "primary"))
        if mon["rect"] != self.mon_rect:
            self.mon_rect = mon["rect"]
            l, t, r, b = mon["rect"]
            self.x, self.y = l + (r - l - self.W) // 2, t
            w = self.dib_size[0] or self.W  # 畫面沒變時不會重畫：直接把視窗搬過去
            user32.SetWindowPos(self.hwnd, None, self.x + (self.W - w) // 2, self.y, 0, 0, 0x1 | 0x4 | 0x10)
            self.static_sig = None
        self.fullscreen = screens.fullscreen_app(self.mon_rect, own=[self.hwnd])

    # ───── 主迴圈 ─────
    def run(self):
        self.create()
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # 調音量要用 COM
        msg = wt.MSG()
        while True:
            t0 = time.perf_counter()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            delay = 0.25
            try:
                self.check_display(t0)
                self.check_fullscreen(t0)
                self.collect_events(t0)
                game = self.config.get("game") or {}
                fs = self.fullscreen and game.get("enabled", True) and game.get("notch", "alerts") != "show"  # 選「照常顯示」= 遊戲中也一直在
                urgent = bool(self.activity and self.activity["kind"] in URGENT)
                if fs and urgent and game.get("notch", "alerts") == "alerts":
                    import screens
                    show_alert = not screens.exclusive_fullscreen()  # 獨佔全螢幕疊不上去
                else:
                    show_alert = False
                want = bool(self.config.get("notch")) and (not fs or show_alert)
                if not want:
                    self.expanded, self.exk, self.cw = False, 0.0, None
                    self.click = None
                    if not (fs and urgent):  # 重要提醒先留著，等可以顯示再說
                        self.activity = None
                    self.wheel = self.hwheel = 0
                    svc = self.runtime.get("devices")
                    if svc is not None:  # 快沒電的等遊戲關掉再說，其他的就算了
                        self.pending_low += [e for e in svc.take_events() if e[0] == "low"]
                    self.show(False)
                else:
                    delay = self.frame(t0)
                    self.show(True)
                    if t0 - self.last_topmost > 1.5:
                        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                        self.last_topmost = t0
            except Exception as e:
                print("[notch]", repr(e), flush=True)
            time.sleep(max(0.002, delay - (time.perf_counter() - t0)))

    @staticmethod
    def spring(x, v, target, dt):
        """有一點點回彈的彈簧 (像手機的動態島)。"""
        k, c = 380.0, 30.0
        steps = max(1, int(dt / (1 / 240)))
        h = dt / steps
        for _ in range(steps):
            v += (k * (target - x) - c * v) * h
            x += v * h
        return x, v

    def frame(self, now):
        """畫一幀，回傳下一幀要等多久。"""
        dt = clamp(now - self.last_frame, 0.0, 0.05)
        self.last_frame = now
        t, pos = self.position()
        if t and t.get("duration"):
            pos = clamp(pos, 0, t["duration"])
        self.update_hover(now)
        if self.click:
            x, y = self.click
            self.click = None
            self.handle_click(x, y)
        if self.rclick:
            self.rclick = False
            if self.runtime.get("open_main"):
                self.runtime["open_main"]()
        self.handle_wheel(t, now)
        # 換歌 → 彈出封面和歌名
        tid = t["track_id"] if t else None
        if tid and tid != self.last_track_seen:
            short = (t.get("source") == "瀏覽器" and 0 < (t.get("duration") or 0) < 60) or "#short" in (t.get("title") or "").lower()
            self.cat_blink_at = now
            if self.last_track_seen is not None and self.opt("peek") and not self.expanded and not short:
                self.push("song", 3.0, track=dict(t))
            self.last_track_seen = tid
        lyr = self.state.get("lyrics") or {}
        if self.activity and self.activity["kind"] == "song" and lyr.get("kind") == "short" \
                and lyr.get("track_id") == self.activity["track"]["track_id"]:
            self.activity = None  # 後來才知道是短影片：收回去
        if self.activity and self.activity["kind"] == "song" and t and self.activity["track"]["track_id"] == tid:
            self.activity["track"] = dict(t)  # 封面晚一點才到：跟著更新
        self.sync_devices()
        ev = self.runtime.pop("notch_event", None)  # 有新版本 / 剛更新完
        if ev:
            self.push(ev[0], 7.0, version=ev[1])
        if self.activity and now > self.activity["until"]:
            self.act_prev, self.activity = self.activity, None
            self.switch_at = now  # 歌詞淡入回來
        key, text, note, prog = self.lyric(t, pos)
        if not self.expanded and self.config.get("overlay", {}).get("auto_hide", True):
            import overlay
            if overlay.lyrics_window_open():  # 主視窗或舞台已經在顯示歌詞：膠囊只留封面和音量條
                key, text = ("covered",), ""
        if key != self.line_key:
            self.prev_text, self.line_text, self.line_key, self.switch_at = self.line_text, text, key, now
        shape = self.shape()
        if self.expanded:
            tw, th = self.px(EXP_W), self.px(self.exp_height())
        elif shape == "bar" and not self.activity:
            tw, th = self.px(BAR_W), self.px(BAR_H)
        else:
            tw, th = self.collapsed_size(t, text)
        if self.cw is None:
            self.cw, self.ch = float(tw), float(th)
        self.cw, self.vw = self.spring(self.cw, self.vw, tw, dt)
        self.ch, self.vh = self.spring(self.ch, self.vh, th, dt)
        self.ch = max(self.ch, self.px(BAR_H if shape == "bar" else PILL_H) * 0.9)
        if shape == "bar":
            self.thin_k = clamp((self.px(PILL_H) - self.ch) / max(1, self.px(PILL_H) - self.px(BAR_H)), 0, 1)
        else:
            self.thin_k = 0.0
        recolor = self.step_fill(t, dt)
        if self.expanded and not getattr(self, "_was_expanded", False):
            self.click_sound()
        self._was_expanded = self.expanded
        self.exk = clamp(self.exk + (dt / 0.24) * (1 if self.expanded else -1), 0, 1)
        bubbling = self.step_bubble(dt)
        playing = bool(t and t["playing"])
        self.step_bars(now, playing)
        act = self.activity
        moving = abs(tw - self.cw) > 0.4 or abs(th - self.ch) > 0.4 or abs(self.vw) > 2 or abs(self.vh) > 2 \
            or 0 < self.exk < 1 or now - self.switch_at < 0.35 \
            or max(abs(b - g) for b, g in zip(self.bars, self.bar_tgt)) > 0.01 \
            or (act is not None and (now - act["start"] < 0.4 or act["kind"] in ("low", "charge", "full") or (act.get("dev") or {}).get("charging"))) \
            or (self.expanded and any(dv.get("charging") or dv["level"] <= 15 for dv in self.devices))
        moving = moving or recolor
        sig = (round(self.cw), round(self.ch), self.exk, key, tid, playing, int(pos) if self.expanded else 0,
               shape, self.fillc, self.border, self.config.get("theme"),
               id(act), len(self.devices), self.page, tuple(sorted(self.opt_cache.items())),
               int(self.timer_left(now)) if self.timer_end else -1, round(self.bub_k, 3), self.bub_kind)
        sleepy_cat = self.opt("cat") and not playing and not self.expanded and not act  # 睡覺的 z 要動
        moving = moving or bubbling or (self.bub_kind == "charge" and self.bub_k > 0.5)  # 充電圈會呼吸
        if moving or playing or sleepy_cat or sig != self.static_sig:
            self.static_sig = sig
            self.present(self.render(t, pos, now))
        self.heal_check(now)
        if moving and (abs(self.vw) > 2 or abs(self.vh) > 2 or 0 < self.exk < 1 or bubbling):
            return 1 / 60
        if moving or (playing and (self.expanded or act)):
            return 1 / 30
        if playing:
            viz = self.runtime.get("viz")
            return 1 / 24 if viz is not None and viz.fresh() else 1 / 15  # 真的頻譜跳得比較細，多畫幾張
        if sleepy_cat:
            return 1 / 12  # z 慢慢飄就好
        return 1 / 20  # 沒在動也要常常看滑鼠，移上去才會馬上展開


def fmt(sec):
    sec = max(0, int(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


def run(state, config, runtime):
    n = Notch(state, config, runtime)
    runtime["notch_obj"] = n  # 全域快捷鍵調音量時用
    n.run()
