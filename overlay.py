"""
浮動歌詞視窗 — 透明、永遠置頂、滑鼠可穿透的 Win32 分層視窗 (layered window)。
用 Pillow 畫字 (陰影、卡拉OK掃光)，再用 UpdateLayeredWindow 送上螢幕，所以有真正的半透明邊緣。

全域快捷鍵：
  Ctrl+Alt+L  解鎖 / 鎖定位置 (解鎖時可拖曳)
  Ctrl+Alt+H  顯示 / 隱藏
  Ctrl+Alt+← / →  歌詞提早 / 延後 0.25 秒
"""
import ctypes
import math
import ctypes.wintypes as wt
import os
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
kernel32 = ctypes.WinDLL("kernel32")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, wt.LPVOID, wt.UINT]
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x8
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
GWL_EXSTYLE = -20
WM_HOTKEY, WM_NCHITTEST, WM_EXITSIZEMOVE, WM_DESTROY = 0x312, 0x84, 0x232, 0x2
HTCAPTION = 2
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
VK_L, VK_H, VK_LEFT, VK_RIGHT = 0x4C, 0x48, 0x25, 0x27
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_FILES = {
    "zh": ["msjhbd.ttc", "msyhbd.ttc", "segoeuib.ttf"],
    "ja": ["YuGothB.ttc", "meiryob.ttc", "msjhbd.ttc"],
    "ko": ["malgunbd.ttf", "msjhbd.ttc"],
}
_font_cache = {}


def ease(x):  # easeOutCubic
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


CUSTOM_FONT = None  # fonts.get(...) \u7684\u7d50\u679c\uff1b\u7531 Overlay.layout() \u8a2d\u5b9a


def _load_custom(info, size):
    key = ("custom", info["path"], info["index"], size)
    if key not in _font_cache:
        f = ImageFont.truetype(info["path"], size, index=info["index"])
        try:  # \u53ef\u8b8a\u5b57\u9ad4 (\u4f8b\u5982 Noto Sans TC VF) \u9810\u8a2d\u5f88\u7d30\uff0c\u8abf\u6210\u7c97\u9ad4
            axes = f.get_variation_axes()
            f.set_variation_by_axes([700 if a.get("name") in (b"Weight", "Weight") else a["default"]
                                     for a in axes])
        except Exception:
            pass
        _font_cache[key] = f
    return _font_cache[key]


def pick_font(text, size):
    lang = "ko" if any("\uac00" <= c <= "\ud7af" for c in text) else \
           "ja" if any("\u3040" <= c <= "\u30ff" for c in text) else "zh"
    cf = CUSTOM_FONT
    if cf:
        has_cjk = any(ord(c) >= 0x2E80 for c in text)
        if not has_cjk or cf.get(lang):  # \u81ea\u9078\u5b57\u9ad4\u7f3a\u4e2d\u65e5\u97d3\u5b57\u6642\uff0c\u9000\u56de\u9810\u8a2d\u5b57\u9ad4\uff0c\u4e0d\u6703\u8b8a\u65b9\u6846
            try:
                return _load_custom(cf, size)
            except Exception:
                pass
    key = (lang, size)
    if key not in _font_cache:
        for f in FONT_FILES[lang]:
            p = os.path.join(FONT_DIR, f)
            if os.path.exists(p):
                _font_cache[key] = ImageFont.truetype(p, size)
                break
        else:
            _font_cache[key] = ImageFont.load_default(size)
    return _font_cache[key]


def scale_alpha(img, f):
    if f >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point([int(i * f) for i in range(256)]))
    return out


class TextLayer:
    """一行字預先畫好：陰影、暗色版、亮色版。每幀只做合成。"""

    def __init__(self, text, size, max_w, bright=(255, 255, 255), dim_alpha=1.0, karaoke=False):
        font = pick_font(text, size)
        w = font.getlength(text)
        if w > max_w and w > 0:  # 太長就縮小到放得下
            size = max(12, int(size * max_w / w))
            font = pick_font(text, size)
            w = font.getlength(text)
        asc, desc = font.getmetrics()
        self.w, self.h = int(w) + 2, asc + desc
        self.m = m = max(6, int(size * 0.4))
        self.W, self.H = self.w + 2 * m, self.h + 2 * m
        self.size = size
        self.karaoke = karaoke

        sh = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((m, m + max(1, size // 20)), text, font=font, fill=(0, 0, 0, 210),
                                stroke_width=max(1, size // 12), stroke_fill=(0, 0, 0, 210))
        self.shadow = sh.filter(ImageFilter.GaussianBlur(max(2.0, size / 9)))

        base = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        ImageDraw.Draw(base).text((m, m), text, font=font, fill=(255, 255, 255, 255))
        if karaoke:
            self.dim = scale_alpha(base, 0.5)
            hi = Image.new("RGBA", (self.W, self.H), bright + (255,))
            hi.putalpha(base.getchannel("A"))
            self.bright = hi
            # 柔邊遮罩條：左邊 255 → 漸層 → 右邊 0
            edge = max(8, int(size * 1.4))
            self.edge = edge
            strip = Image.new("L", (self.W * 3 + edge, 1), 0)
            strip.paste(255, (0, 0, self.W * 2, 1))
            grad = Image.linear_gradient("L").rotate(-90).resize((edge, 1))  # 左 255 → 右 0
            strip.paste(grad, (self.W * 2, 0))
            self.strip = strip
        else:
            self.dim = scale_alpha(base, dim_alpha)
            self.shadow = scale_alpha(self.shadow, dim_alpha)

    def draw(self, canvas, x, y, progress=None, fade=1.0):
        """x 是文字左緣 (不含邊距)，y 是文字頂端。"""
        px, py = int(x - self.m), int(y - self.m)
        if not self._fits(canvas, px, py):
            return
        canvas.alpha_composite(scale_alpha(self.shadow, fade), (px, py))
        canvas.alpha_composite(scale_alpha(self.dim, fade), (px, py))
        if self.karaoke and progress is not None:
            bx = int(self.m + progress * (self.w + self.edge))  # 掃光邊界
            st = max(0, 2 * self.W - bx)
            mask = self.strip.crop((st, 0, st + self.W, 1)).resize((self.W, self.H))
            hi = self.bright.copy()
            hi.putalpha(ImageChops.multiply(self.bright.getchannel("A"), mask))
            canvas.alpha_composite(scale_alpha(hi, fade), (px, py))

    def _fits(self, canvas, px, py):
        return px >= -self.W and py >= -self.H and px < canvas.width and py < canvas.height


class Overlay:
    def __init__(self, state, config, runtime, save_config):
        self.picker, self.mon_rect = None, None
        self.alpha, self.fs_at, self.fs_now = 255, 0.0, False
        self.state, self.config, self.runtime, self.save_config = state, config, runtime, save_config
        self.hwnd = None
        self.W = self.H = 0
        self.x = self.y = 0
        self.cfg_version = -1
        self.unlocked = False
        self.visible_win = False
        self.hidden_by_hotkey = False
        self.dib = None
        self.bits = ctypes.c_void_p()
        self.mem_dc = None
        # 時鐘
        self.anchor_pos, self.anchor_at, self.playing, self.track_id = 0.0, time.perf_counter(), False, None
        # 目前/上一個畫面區塊 (淡入淡出)
        self.block_key = None
        self.block = None
        self.prev_block = None
        self.switch_at = 0.0
        self.last_topmost = 0.0
        self.moved = False

    # ───── 視窗 ─────
    def create(self):
        # DPI 感知已在 server.main() 一開始設定好 (要在任何視窗建立前)
        self.scale = user32.GetDpiForSystem() / 96 if hasattr(user32, "GetDpiForSystem") else 1.0
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self.wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "NowPlayingLyricsOverlay"
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32646))  # IDC_SIZEALL
        user32.RegisterClassExW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self.hwnd = user32.CreateWindowExW(ex, wc.lpszClassName, "Now Playing Lyrics", WS_POPUP,
                                           0, 0, 10, 10, None, None, hinst, None)
        for i, (mod, vk) in enumerate([(MOD_CONTROL | MOD_ALT, VK_L), (MOD_CONTROL | MOD_ALT, VK_H),
                                       (MOD_CONTROL | MOD_ALT, VK_LEFT), (MOD_CONTROL | MOD_ALT, VK_RIGHT)]):
            user32.RegisterHotKey(self.hwnd, i + 1, mod | MOD_NOREPEAT, vk)
        self.screen_dc = user32.GetDC(None)
        self.mem_dc = gdi32.CreateCompatibleDC(self.screen_dc)

    def wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_NCHITTEST and self.unlocked:
            return HTCAPTION
        if msg == WM_EXITSIZEMOVE:
            self.moved = True
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def set_clickthrough(self, on):
        ex = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        ex = (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT)
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex)

    def ensure_dib(self, W, H):
        if (W, H) == (self.W, self.H) and self.dib:
            return
        if self.dib:
            gdi32.DeleteObject(self.dib)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
        self.dib = gdi32.CreateDIBSection(self.mem_dc, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        gdi32.SelectObject(self.mem_dc, self.dib)
        self.W, self.H = W, H

    def present(self, img):
        r, g, b, a = img.split()
        data = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                    ImageChops.multiply(r, a), a)).tobytes()  # 預乘 alpha 的 BGRA
        ctypes.memmove(self.bits, data, len(data))
        blend = BLENDFUNCTION(0, 0, self.alpha, 1)
        user32.UpdateLayeredWindow(self.hwnd, self.screen_dc, ctypes.byref(wt.POINT(self.x, self.y)),
                                   ctypes.byref(wt.SIZE(self.W, self.H)), self.mem_dc,
                                   ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), 2)

    def show(self, on):
        if on != self.visible_win:
            user32.ShowWindow(self.hwnd, 4 if on else 0)  # SW_SHOWNOACTIVATE / SW_HIDE
            self.visible_win = on

    # ───── 版面 ─────
    def layout(self):
        global CUSTOM_FONT
        ov = self.config["overlay"]
        try:
            import fonts
            CUSTOM_FONT = fonts.get(self.config.get("font"))
        except Exception:
            CUSTOM_FONT = None
        s = self.scale
        self.fs = int(ov["font_size"] * s)
        self.pad = int(18 * s)
        W = int(ov["width"] * s)
        H = self.pad * 2 + int(self.fs * 1.45)
        if ov["show_translation"]:
            H += int(self.fs * 0.62 * 1.45)
        if ov["show_next"]:
            H += int(self.fs * 0.6 * 1.5)
        self.ensure_dib(W, H)

        mon = self.monitor()
        self.mon_rect = mon["rect"]
        wa = wt.RECT(*mon["work"])  # 選的那個螢幕的工作區域 (扣掉工作列)
        margin = int(20 * s)
        p = ov["preset"]
        self.valign = "top" if p == "top-center" else "bottom"
        if p == "custom":
            self.x, self.y, self.align = int(ov["x"]), int(ov["y"]), ov.get("align", "left")
        else:
            self.align = {"bottom-left": "left", "bottom-right": "right"}.get(p, "center")
            self.x = {"left": wa.left + margin, "right": wa.right - W - margin}.get(
                self.align, (wa.left + wa.right - W) // 2)
            self.y = wa.top + margin if p == "top-center" else wa.bottom - H - margin
        self.block_key = None  # 重畫

    # ───── 時鐘 ─────
    def position(self):
        t = self.state.get("track")
        if not t:
            return None, None
        server_pos = t["position"] + ((time.time() - t["ts"]) if t["playing"] else 0)
        local = self.anchor_pos + ((time.perf_counter() - self.anchor_at) if self.playing else 0)
        if t["track_id"] != self.track_id or t["playing"] != self.playing or abs(local - server_pos) > 0.35:
            self.anchor_pos, self.anchor_at = server_pos, time.perf_counter()
            self.playing, self.track_id = t["playing"], t["track_id"]
            local = server_pos
        return t, local

    # ───── 內容 ─────
    def content_for(self, t, pos):
        """回傳 (key, 主句, 翻譯, 下一句, 主句起訖時間)。"""
        lyr = self.state.get("lyrics")
        if not lyr or lyr.get("track_id") != t["track_id"] or not lyr.get("synced") or not lyr.get("lines"):
            sub = "正在找歌詞…" if (lyr and lyr.get("loading")) or not lyr else "（沒有同步歌詞）"
            return ("meta", t["track_id"], sub), t["title"], t["artist"] or "", sub, None
        lines = lyr["lines"]
        tt = pos + self.config.get("offset", 0)
        idx = -1
        lo, hi = 0, len(lines) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if lines[mid]["t"] <= tt:
                idx, lo = mid, mid + 1
            else:
                hi = mid - 1
        nxt = next((l["text"] for l in lines[idx + 1:] if l["text"]), "")
        if idx < 0:
            return ("intro", t["track_id"]), t["title"], t["artist"] or "", nxt, None
        cur = lines[idx]
        if not cur["text"]:
            return ("gap", t["track_id"], idx), "•   •   •", "", nxt, None
        end = lines[idx + 1]["t"] if idx + 1 < len(lines) else cur["t"] + 5
        dur = min(end - cur["t"], len(cur["text"]) * 0.35 + 2.5)
        return ("line", t["track_id"], idx), cur["text"], cur.get("tr") or "", nxt, (cur["t"], dur, tt)

    def build_block(self, main, tr, nxt, karaoke, accent):
        ov = self.config["overlay"]
        maxw = self.W - self.pad * 2
        color = tuple(accent) if (ov["color"] == "album" and accent) else (255, 255, 255)
        items = [("main", TextLayer(main, self.fs, maxw, bright=color, karaoke=karaoke) if main else None)]
        if ov["show_translation"]:
            items.append(("tr", TextLayer(tr, int(self.fs * 0.62), maxw, dim_alpha=0.88) if tr else None))
        if ov["show_next"]:
            items.append(("next", TextLayer(nxt, int(self.fs * 0.6), maxw, dim_alpha=0.45) if nxt else None))
        heights = {"main": int(self.fs * 1.45), "tr": int(self.fs * 0.62 * 1.45), "next": int(self.fs * 0.6 * 1.5)}
        # 底部對齊時只把有內容的行往下貼
        rows = [(k, l) for k, l in items if l is not None]
        total = sum(heights[k] for k, _ in rows)
        y = self.pad if self.valign == "top" else self.H - self.pad - total
        placed, maxw_used = [], 0
        for k, l in rows:
            if self.align == "left":
                x = self.pad
            elif self.align == "right":
                x = self.W - self.pad - l.w
            else:
                x = (self.W - l.w) // 2
            placed.append((k, l, x, y + (heights[k] - l.h) // 2))
            maxw_used = max(maxw_used, l.w)
            y += heights[k]
        bg = None
        if ov["bg_opacity"] > 0 and rows:
            bx0 = min(x for _, _, x, _ in placed) - self.pad
            bx1 = max(x + l.w for _, l, x, _ in placed) + self.pad
            by0 = placed[0][3] - self.pad * 0.6
            by1 = placed[-1][3] + placed[-1][1].h + self.pad * 0.6
            bg = (max(0, bx0), max(0, by0), min(self.W - 1, bx1), min(self.H - 1, by1))
        return {"placed": placed, "bg": bg}

    def draw_block(self, canvas, block, timing, fade, dy):
        ov = self.config["overlay"]
        if block["bg"]:
            layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            x0, y0, x1, y1 = block["bg"]
            ImageDraw.Draw(layer).rounded_rectangle((x0, y0 + dy, x1, y1 + dy), radius=int(14 * self.scale),
                                                    fill=(10, 10, 16, int(255 * ov["bg_opacity"] / 100 * fade)))
            canvas.alpha_composite(layer)
        for k, l, x, y in block["placed"]:
            prog = None
            if k == "main" and l.karaoke:
                if timing:
                    start, dur, tt = timing
                    prog = min(1.0, max(0.0, (tt - start) / max(dur, 0.3)))
                else:
                    prog = 0.0
            l.draw(canvas, x, y + dy, prog, fade)

    def render(self, t, pos):
        img = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        if self.unlocked:
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((1, 1, self.W - 2, self.H - 2), radius=int(14 * self.scale),
                                fill=(20, 20, 30, 140), outline=(255, 255, 255, 160), width=max(1, int(2 * self.scale)))
            hint = "拖曳移動位置　·　Ctrl+Alt+L 鎖定"
            d.text((self.pad, int(8 * self.scale)), hint, font=pick_font(hint, int(13 * self.scale)),
                   fill=(255, 255, 255, 200))
        if t is None:
            if self.unlocked:
                txt = TextLayer("浮動歌詞預覽", self.fs, self.W, karaoke=False)
                txt.draw(img, self.pad, self.H - self.pad - txt.h)
            return img

        key, main, tr, nxt, timing = self.content_for(t, pos)
        now = time.perf_counter()
        if key != self.block_key:
            self.prev_block = self.block
            self.block = self.build_block(main, tr, nxt, karaoke=key[0] == "line", accent=t.get("accent"))
            self.block_key = key
            self.switch_at = now
        k = ease((now - self.switch_at) / 0.32)
        lift = int(self.fs * 0.45)
        if self.prev_block and k < 1:
            self.draw_block(img, self.prev_block, None, 1 - k, int(-lift * k))
        fade = k
        if key[0] == "gap":  # 間奏：呼吸
            fade *= 0.55 + 0.45 * (0.5 + 0.5 * math.sin(now * 3.5))
        self.draw_block(img, self.block, timing, fade, int(lift * (1 - k)))
        return img

    # ───── 主迴圈 ─────
    def handle_hotkey(self, hid):
        ov = self.config["overlay"]
        if hid == 1:
            self.runtime["unlocked"] = not self.runtime["unlocked"]
            self.runtime["cfg_version"] += 1
        elif hid == 2:
            self.hidden_by_hotkey = not self.hidden_by_hotkey
        elif hid in (3, 4):
            self.config["offset"] = round(self.config.get("offset", 0) + (-0.25 if hid == 3 else 0.25), 2)
            self.save_config()

    def sync_config(self):
        if self.runtime["cfg_version"] == self.cfg_version:
            return
        self.cfg_version = self.runtime["cfg_version"]
        self.unlocked = self.runtime["unlocked"]
        self.set_clickthrough(not self.unlocked)
        self.layout()
        self.prev_block = None

    def monitor(self):
        if self.picker is None:
            import screens
            self.picker = screens.Picker([self.hwnd])
        return self.picker.pick(self.config["overlay"].get("monitor", "primary"))

    def game_mode(self, now):
        """全螢幕遊戲 / 影片在浮動歌詞那個螢幕上：照常 / 變淡 / 先藏起來。"""
        if now - self.fs_at > 0.5:
            self.fs_at = now
            import screens
            self.fs_now = screens.fullscreen_app(self.mon_rect, own=[self.hwnd])
        mode = self.config["overlay"].get("fullscreen", "dim")
        self.alpha = 120 if (self.fs_now and mode == "dim") else 255
        return self.fs_now and mode == "hide"

    def covered(self, now):
        """主視窗 (沒縮小) 或舞台開著時，它們已經在顯示歌詞了：浮動歌詞先藏起來，避免畫面上同時好幾份歌詞。"""
        return self.config["overlay"].get("auto_hide", True) and lyrics_window_open()

    def commit_drag(self):
        """拖曳結束：存成自訂位置。"""
        self.moved = False
        r = wt.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        self.config["overlay"].update(preset="custom", x=r.left, y=r.top, align=self.align)
        self.save_config()
        self.runtime["cfg_version"] += 1

    def run(self):
        self.create()
        msg = wt.MSG()
        frame = 1 / 30  # 30 fps：掃光一樣順，CPU 少四分之一
        while True:
            t0 = time.perf_counter()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY:
                    self.handle_hotkey(msg.wParam)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            try:
                self.sync_config()
                if self.moved:
                    self.commit_drag()
                    self.sync_config()
                if self.config["overlay"].get("preset") != "custom" and self.monitor()["rect"] != self.mon_rect:
                    self.layout()  # 換螢幕了 (跟著前景 / 滑鼠，或螢幕插拔)
                t, pos = self.position()
                want = self.config["overlay"]["enabled"] and not self.hidden_by_hotkey and (t is not None or self.unlocked) \
                    and not (self.covered(t0) and not self.unlocked) and not (self.game_mode(t0) and not self.unlocked)
                if want:
                    self.present(self.render(t, pos))
                    if t0 - self.last_topmost > 1.5:  # 有些遊戲會搶最上層，定期搶回來
                        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                        self.last_topmost = t0
                self.show(want)
                busy = want and t is not None and t["playing"]
            except Exception as e:
                print("[overlay]", repr(e))
                busy = False
            time.sleep(max(0.002, (frame if busy else 0.2) - (time.perf_counter() - t0)))


_lw_cache = [0.0, False]


def lyrics_window_open():
    """主視窗 (沒縮小) 或舞台視窗開著嗎？(0.5 秒內重用結果；浮動歌詞和膠囊共用)"""
    now = time.perf_counter()
    if now - _lw_cache[0] < 0.5:
        return _lw_cache[1]
    cov = False
    pid = os.getpid()
    for title in ("Hummi 哼哼", "Hummi 舞台"):
        h = user32.FindWindowW(None, title)
        if not h:
            continue
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(h) and not user32.IsIconic(h):
            cov = True
            break
    _lw_cache[0], _lw_cache[1] = now, cov
    return cov


def run(state, config, runtime, save_config):
    Overlay(state, config, runtime, save_config).run()
