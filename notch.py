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
FILL = (0, 0, 0, 255)

GREEN, AMBER, RED = (48, 209, 88), (255, 176, 32), (255, 69, 58)

# Windows 11 Fluent 圖示 (Win10 用 MDL2，字碼一樣)
GLYPH = {"play": "", "pause": "", "prev": "", "next": "", "window": "",
         "headphones": "", "controller": "", "mouse": "", "keyboard": "", "phone": "",
         "laptop": "", "speaker": "", "device": "", "pen": "", "bluetooth": "",
         "vol0": "", "vol1": "", "vol2": "", "vol3": "", "charging": "",
         "settings": "", "back": "", "chevron": "", "download": "", "star": ""}

# 膠囊裡的設定頁：(key, 標籤)
SETTINGS = [("peek", "換歌時彈出"), ("spectrum", "真實頻譜光暈"), ("battery", "裝置電量"), ("gestures", "滾輪 / 滑動手勢"),
            ("overlay", "浮動歌詞"), ("ai", "AI 歌詞"), ("folia", "送到 Folia 舞台"), ("autostart", "開機自動啟動")]

DEFAULT_OPTS = {"battery": True, "peek": True, "spectrum": True, "gestures": True}


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
        self.page = "main"             # 展開時的頁面：main / settings
        self.opt_cache = {}            # 設定頁顯示用 (開機啟動要讀登錄檔，不要每幀讀)
        self.opt_cache_at = 0.0

    def opt(self, k):
        return (self.config.get("notch_opts") or {}).get(k, DEFAULT_OPTS[k])

    # ───── 視窗 ─────
    def setup_scale(self, dpi_scale):
        self.u = dpi_scale             # 螢幕像素 / dp
        self.s = dpi_scale * SS        # 膠囊內容畫布像素 / dp
        self.W, self.H = int(WIN_W * self.u), int(WIN_H * self.u)

    def create(self):
        self.setup_scale(user32.GetDpiForSystem() / 96 if hasattr(user32, "GetDpiForSystem") else 1.0)
        hinst = kernel32.GetModuleHandleW(None)
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

    def make_ears(self):
        """膠囊頂端兩側往外彎的小弧，看起來像從螢幕邊緣長出來的。"""
        e = self.px(EAR)
        k = 4
        big = Image.new("L", (e * k, e * k), 255)
        ImageDraw.Draw(big).ellipse((0, 0, 2 * e * k, 2 * e * k), fill=0)  # 圓心在右下角 (e, e)
        ear = Image.new("RGBA", (e, e), FILL)
        ear.putalpha(big.resize((e, e), Image.LANCZOS))
        self.ear_r = ear
        self.ear_l = ear.transpose(Image.FLIP_LEFT_RIGHT)
        self.e = e

    _mask_cache = {}

    def pill_mask(self, w, h, r):
        """w, h, r：畫布像素 (2 倍)。上緣是平的，下面兩角圓。"""
        key = (w, h, r)
        m = self._mask_cache.get(key)
        if m is None:
            m = Image.new("L", (w, h), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, -r, w - 1, h - 1), radius=r, fill=255)
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
        key = (t and t["track_id"], size, radius)
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
        box = (cx - r, cy - r, cx + r, cy + r)
        dr.ellipse(box, outline=(255, 255, 255, 46), width=width)
        end = -90 + 360 * clamp(level / 100, 0.03, 1)
        a = 255
        if charging:
            b = 0.5 + 0.5 * math.sin(now * 2 * math.pi / 2.4)
            a = int(255 * (0.55 + 0.45 * b))
            for i, ga in enumerate((80, 38, 16)):
                o = width * (i + 1) * 0.9
                dr.arc((box[0] + o, box[1] + o, box[2] - o, box[3] - o), -90, end, fill=color + (int(ga * b),),
                       width=max(1, width // 2))
        elif level <= 15:
            a = int(255 * (0.45 + 0.55 * (0.5 + 0.5 * math.cos(now * 2 * math.pi / 1.1))))
        if level > 0:
            dr.arc(box, start=-90, end=end, fill=color + (a,), width=width)
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
        if not t:
            return self.px(150), self.px(PILL_H)
        if not text:
            return self.px(MIN_W), self.px(PILL_H)
        base = 14 + 24 + 12 + 12 + 19 + 16  # dp：左邊距 + 封面 + 間距 … 間距 + 音量條 + 右邊距
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
        elif k == "volume":
            lv = act["level"]
            g = "vol0" if lv <= 0.001 else "vol1" if lv < 0.34 else "vol2" if lv < 0.67 else "vol3"
            self.glyph(dr, g, d(30), h / 2, 15)
            bx0, bx1 = d(54), w - d(64)
            bh = d(6)
            dr.rounded_rectangle((bx0, h / 2 - bh / 2, bx1, h / 2 + bh / 2), radius=bh / 2, fill=(255, 255, 255, 46))
            fx = bx0 + (bx1 - bx0) * clamp(lv, 0, 1)
            if fx > bx0 + 1:
                dr.rounded_rectangle((bx0, h / 2 - bh / 2, max(bx0 + bh, fx), h / 2 + bh / 2), radius=bh / 2,
                                     fill=(255, 255, 255, 240))
            txt = f"{round(lv * 100)}%"
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
            ak = ease_out((now - act["start"]) / 0.35)
            al = Image.new("RGBA", pill.size, (0, 0, 0, 0))
            self.draw_activity(al, act, t, now)
            self.blend(layer, al, ak)
        elif not t:
            f = ui_font("Hummi", d(13), 600)
            dr.text((w / 2, h / 2), "Hummi", font=f, fill=(255, 255, 255, 110), anchor="mm")
        else:
            cs = d(24)
            layer.alpha_composite(self.cover(t, cs, d(7)), (d(14), (h - cs) // 2))
            ac = self.accent(t) + (255,)
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
        if self.page in ("settings", "devices"):
            if self.page == "settings":
                self.draw_settings(layer, dr, w, h, hit)
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

    def render(self, t, pos, now):
        """回傳只包含膠囊 (加兩側小弧) 的圖；寬度一定是偶數+兩側，置中才不會左右晃。"""
        w, h = max(4, int(round(self.cw))), max(4, int(round(self.ch)))
        w += (self.W - w) % 2
        e = self.e
        img = Image.new("RGBA", (w + 2 * e, h), (0, 0, 0, 0))
        W2, H2 = w * SS, h * SS
        r = int(min(H2 / 2, self.d(18) + (self.d(30) - self.d(18)) * self.exk))
        pill = Image.new("RGBA", (W2, H2), FILL)
        key, text, note, prog = self.lyric(t, pos)
        self.hit = {}
        self.exp_offset = 0
        self.draw_collapsed(pill, t, text, now, clamp(1 - self.exk * 2.4, 0, 1))
        self.draw_expanded(pill, t, pos, text, note, prog, now, clamp((self.exk - 0.4) / 0.6, 0, 1))
        if t and t["playing"] and self.opt("spectrum"):
            self.rim(pill, self.accent(t), self.glow * 0.9, r)
        pill.putalpha(self.pill_mask(W2, H2, r))
        pill = pill.resize((w, h), Image.LANCZOS)
        img.alpha_composite(pill, (e, 0))
        img.alpha_composite(self.ear_l, (0, 0))
        img.alpha_composite(self.ear_r, (e + w, 0))
        self.pill_left = e  # 膠囊在視窗裡的 x (點擊座標換算用)
        return img

    # ───── 互動 ─────
    def handle_click(self, x, y):
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
        py = y * SS
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

    def handle_wheel(self, t, now):
        """滾輪：只調播放器自己的音量；觸控板左右滑：切歌。"""
        if self.wheel:
            steps, self.wheel = self.wheel / 120, 0
            if t and self.opt("gestures"):
                try:
                    import audio_ctl
                    app = t.get("app", "")
                    cur = audio_ctl.get_volume(app)  # 每次重讀：別的地方 (音量混音程式) 改過也會跟上
                    if cur is not None:
                        lv = audio_ctl.set_volume(app, round(clamp(cur + steps * 0.04, 0, 1), 2))
                        if lv is not None:
                            self.volume = (app, lv, now)
                            self.expanded = False
                            self.push("volume", 1.4, level=lv, app=t.get("source") or "")
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
        left = self.x + (self.W - w) / 2
        inside = (left - m <= px <= left + w + m) and (self.y <= py <= self.y + h + m)
        if inside:
            self.leave_since = None
            if self.hover_since is None:
                self.hover_since = now
            elif not self.expanded and now - self.hover_since > 0.12 and not (
                    self.activity and self.activity["kind"] in ("volume", "update", "updated")):
                self.expanded = True
                self.activity = None
        else:
            self.hover_since = None
            if self.leave_since is None:
                self.leave_since = now
            elif self.expanded and now - self.leave_since > (0.8 if self.page != "main" else 0.35):
                self.expanded = False
                self.page = "main"

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
                self.check_fullscreen(t0)
                want = bool(self.config.get("notch")) and not self.fullscreen
                if not want:
                    self.expanded, self.exk, self.cw = False, 0.0, None
                    self.click, self.activity = None, None
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
        if self.expanded:
            tw, th = self.px(EXP_W), self.px(self.exp_height())
        else:
            tw, th = self.collapsed_size(t, text)
        if self.cw is None:
            self.cw, self.ch = float(tw), float(th)
        self.cw, self.vw = self.spring(self.cw, self.vw, tw, dt)
        self.ch, self.vh = self.spring(self.ch, self.vh, th, dt)
        self.ch = max(self.ch, self.px(PILL_H) * 0.9)
        self.exk = clamp(self.exk + (dt / 0.24) * (1 if self.expanded else -1), 0, 1)
        playing = bool(t and t["playing"])
        self.step_bars(now, playing)
        act = self.activity
        moving = abs(tw - self.cw) > 0.4 or abs(th - self.ch) > 0.4 or abs(self.vw) > 2 or abs(self.vh) > 2 \
            or 0 < self.exk < 1 or now - self.switch_at < 0.35 \
            or max(abs(b - g) for b, g in zip(self.bars, self.bar_tgt)) > 0.01 \
            or (act is not None and (now - act["start"] < 0.4 or act["kind"] in ("low", "charge", "full") or (act.get("dev") or {}).get("charging"))) \
            or (self.expanded and any(dv.get("charging") or dv["level"] <= 15 for dv in self.devices))
        sig = (round(self.cw), round(self.ch), self.exk, key, tid, playing, int(pos) if self.expanded else 0,
               id(act), len(self.devices), self.page, tuple(sorted(self.opt_cache.items())))
        if moving or playing or sig != self.static_sig:
            self.static_sig = sig
            self.present(self.render(t, pos, now))
        if moving and (abs(self.vw) > 2 or abs(self.vh) > 2 or 0 < self.exk < 1):
            return 1 / 60
        if moving or (playing and (self.expanded or act)):
            return 1 / 30
        if playing:
            viz = self.runtime.get("viz")
            return 1 / 24 if viz is not None and viz.fresh() else 1 / 15  # 真的頻譜跳得比較細，多畫幾張
        return 1 / 20  # 沒在動也要常常看滑鼠，移上去才會馬上展開


def fmt(sec):
    sec = max(0, int(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


def run(state, config, runtime):
    n = Notch(state, config, runtime)
    runtime["notch_obj"] = n  # 全域快捷鍵調音量時用
    n.run()
