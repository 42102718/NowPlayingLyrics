"""主視窗的「原生」外框：無標題列，但跟一般 Windows 視窗一樣能做的事都能做。

- 拉四邊和四個角調整大小 (Windows 本身的隱形外框，游標一靠近就變)
- 拖到螢幕邊緣貼齊、Win + 方向鍵、Win + Z 貼齊版面、最小化 / 還原動畫、陰影
- 放大時不會超出螢幕、不會蓋住工作列 (自動隱藏工作列時也留一條讓它跑出來)
- 工作列縮圖下方的 上一首 / 播放 / 下一首 按鈕，工作列圖示上的播放進度
- 記住視窗位置和大小 (換了螢幕配置、位置跑到畫面外會自動拉回來)

做法跟 Windows Terminal 一樣：視窗保留 WS_THICKFRAME，在 WM_NCCALCSIZE 把標題列那塊拿掉，
左右下的外框仍是 Windows 自己的 (隱形、在視窗外面)；上緣：網頁的標題列 (app-region: drag) 會把滑鼠判斷
交回給視窗，最上面 TOP_GRIP 像素在這裡回報成「上緣」，所以上緣也能拉，畫面上看不到任何多出來的邊。
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.windll.user32
comctl32 = ctypes.windll.comctl32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32
gdi32.DeleteObject.argtypes = [wt.HANDLE]

LRESULT = ctypes.c_ssize_t
SUBCLASSPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, ctypes.c_size_t, ctypes.c_size_t)
comctl32.SetWindowSubclass.argtypes = [wt.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
comctl32.DefSubclassProc.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
comctl32.DefSubclassProc.restype = LRESULT
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsZoomed.argtypes = [wt.HWND]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetDpiForWindow.argtypes = [wt.HWND]
user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wt.UINT]
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HANDLE
user32.MonitorFromRect.argtypes = [ctypes.POINTER(wt.RECT), wt.DWORD]
user32.MonitorFromRect.restype = wt.HANDLE
user32.RegisterWindowMessageW.restype = wt.UINT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetWindowPlacement.argtypes = [wt.HWND, ctypes.c_void_p]
user32.SetWindowPlacement.argtypes = [wt.HWND, ctypes.c_void_p]

WM_NCCALCSIZE, WM_NCHITTEST, WM_COMMAND, WM_GETMINMAXINFO = 0x83, 0x84, 0x111, 0x24
HTCLIENT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 1, 12, 13, 14
SM_CXFRAME, SM_CYFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SWP_FRAMECHANGED, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x20, 0x2, 0x1, 0x4, 0x10
THBN_CLICKED = 0x1800
WM_FIX_RESTORE = 0x8000 + 0x48  # WM_APP + n
TOP_GRIP = 5  # 上緣可以拉的高度 (邏輯像素)


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wt.RECT * 3), ("lppos", ctypes.c_void_p)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT), ("uEdge", wt.UINT),
                ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT), ("ptMinPosition", wt.POINT),
                ("ptMaxPosition", wt.POINT), ("rcNormalPosition", wt.RECT)]


def _frame(hwnd):
    dpi = user32.GetDpiForWindow(hwnd) or 96
    return (user32.GetSystemMetricsForDpi(SM_CYFRAME, dpi) + user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi),
            dpi / 96)


def _autohide_taskbar_edge(mon_rect):
    """自動隱藏的工作列在哪一邊 (放大時要留 1 像素讓它滑出來)。"""
    abd = APPBARDATA(ctypes.sizeof(APPBARDATA))
    if not (shell32.SHAppBarMessage(4, ctypes.byref(abd)) & 1):  # ABM_GETSTATE / ABS_AUTOHIDE
        return None
    for edge in (3, 0, 1, 2):  # 下、左、上、右
        abd.uEdge, abd.rc = edge, mon_rect
        if shell32.SHAppBarMessage(11, ctypes.byref(abd)):  # ABM_GETAUTOHIDEBAREX
            return edge
    return None


class Chrome:
    """裝在 pywebview 的 WinForms Form 上 (要在 UI 執行緒呼叫 install)。"""

    def __init__(self, form, on_thumb=None):
        self.form = form
        self.hwnd = None
        self.on_thumb = on_thumb          # 縮圖按鈕：on_thumb("prev" / "toggle" / "next")
        self._proc = SUBCLASSPROC(self._wndproc)  # 要一直留著，不然會被回收
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarButtonCreated")
        self.thumbs = None
        self.normal = None   # 放大 / 縮小前的一般大小 (WinForms 還原時會算錯，自己補回來)
        self.state = 0

    # ───── 安裝 (UI 執行緒) ─────
    def install(self):
        self.hwnd = wt.HWND(self.form.Handle.ToInt64())
        comctl32.SetWindowSubclass(self.hwnd, self._proc, 0x4855, 0)
        self.restyle()

    def restyle(self):
        """加上 WS_THICKFRAME 等樣式 (四邊可拉、貼齊、動畫、陰影)。
        不透過 WinForms 的 FormBorderStyle：那樣 WinForms 會以為有標題列，每次放大再還原視窗就長高一截。
        WinForms 重設樣式後 (例如離開全螢幕) 要再呼叫一次。"""
        GWL_STYLE = -16
        WS_EXTRA = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000  # CAPTION THICKFRAME SYSMENU MIN MAX
        st = user32.GetWindowLongW(self.hwnd, GWL_STYLE)
        if (st & WS_EXTRA) != WS_EXTRA:
            user32.SetWindowLongW(self.hwnd, GWL_STYLE, st | WS_EXTRA)
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    # ───── 視窗訊息 ─────
    def _wndproc(self, hwnd, msg, wp, lp, uid, ref):
        try:
            if msg == WM_NCCALCSIZE and wp and user32.GetWindowLongW(hwnd, -16) & 0x40000:  # 有 WS_THICKFRAME (全螢幕時沒有)
                p = ctypes.cast(lp, ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
                top = p.rgrc[0].top
                r = comctl32.DefSubclassProc(hwnd, msg, wp, lp)
                p.rgrc[0].top = top  # 拿掉標題列：左右下維持 Windows 的隱形外框
                if user32.IsZoomed(hwnd):
                    frame, _ = _frame(hwnd)
                    p.rgrc[0].top += frame  # 放大時視窗會超出螢幕一個外框寬，補回來
                    mi = MONITORINFO(ctypes.sizeof(MONITORINFO))
                    if user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
                        edge = _autohide_taskbar_edge(mi.rcMonitor)
                        if edge == 3:
                            p.rgrc[0].bottom -= 1
                        elif edge == 0:
                            p.rgrc[0].left += 1
                        elif edge == 1:
                            p.rgrc[0].top += 1
                        elif edge == 2:
                            p.rgrc[0].right -= 1
                return 0 if r == 0 else r
            if msg == WM_NCHITTEST:
                r = comctl32.DefSubclassProc(hwnd, msg, wp, lp)
                if r == HTCLIENT and not user32.IsZoomed(hwnd):
                    x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
                    rc = wt.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rc))
                    frame, scale = _frame(hwnd)
                    if y < rc.top + round(TOP_GRIP * scale):
                        if x < rc.left + frame * 2:
                            return HTTOPLEFT
                        if x >= rc.right - frame * 2:
                            return HTTOPRIGHT
                        return HTTOP
                return r
            if msg == 0x0005:  # WM_SIZE
                if wp in (1, 2) and self.state == 0:  # 要縮小 / 放大了：記下一般狀態的位置
                    wpl = WINDOWPLACEMENT(ctypes.sizeof(WINDOWPLACEMENT))
                    if user32.GetWindowPlacement(hwnd, ctypes.byref(wpl)):
                        self.normal = wt.RECT(wpl.rcNormalPosition.left, wpl.rcNormalPosition.top,
                                              wpl.rcNormalPosition.right, wpl.rcNormalPosition.bottom)
                elif wp == 0 and self.state in (1, 2) and self.normal is not None:
                    user32.PostMessageW(hwnd, WM_FIX_RESTORE, 0, 0)
                self.state = wp if wp in (0, 1, 2) else self.state
            if msg == WM_FIX_RESTORE:
                wpl = WINDOWPLACEMENT(ctypes.sizeof(WINDOWPLACEMENT))
                n = self.normal
                if n is not None and user32.GetWindowPlacement(hwnd, ctypes.byref(wpl)) and wpl.showCmd == 1:
                    c = wpl.rcNormalPosition
                    if (c.right - c.left, c.bottom - c.top) != (n.right - n.left, n.bottom - n.top):
                        wpl.rcNormalPosition = n
                        user32.SetWindowPlacement(hwnd, ctypes.byref(wpl))
                return 0
            if msg == WM_COMMAND and (wp >> 16) == THBN_CLICKED and self.on_thumb:
                act = {1: "prev", 2: "toggle", 3: "next"}.get(wp & 0xFFFF)
                if act:
                    threading.Thread(target=self.on_thumb, args=(act,), daemon=True).start()
                return 0
            if msg == self.taskbar_created and self.thumbs is not None:
                self.thumbs.reset()  # 檔案總管重新啟動：按鈕要重加
        except Exception as e:
            print("[chrome]", repr(e), flush=True)
        return comctl32.DefSubclassProc(hwnd, msg, wp, lp)


# ───────────────── 位置記憶 ─────────────────
def get_placement(hwnd):
    wp = WINDOWPLACEMENT(ctypes.sizeof(WINDOWPLACEMENT))
    if not user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return None
    r = wp.rcNormalPosition
    return {"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
            "max": wp.showCmd == 3 or bool(user32.IsZoomed(hwnd))}


def set_placement(hwnd, g, show=True):
    """還原上次的位置；整個視窗都跑到螢幕外 (拔掉了螢幕) 就不還原。"""
    try:
        rc = wt.RECT(int(g["x"]), int(g["y"]), int(g["x"] + g["w"]), int(g["y"] + g["h"]))
        if g["w"] < 200 or g["h"] < 200 or not user32.MonitorFromRect(ctypes.byref(rc), 0):
            return False
        wp = WINDOWPLACEMENT(ctypes.sizeof(WINDOWPLACEMENT))
        user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
        wp.rcNormalPosition = rc
        wp.showCmd = (3 if g.get("max") else 1) if show else 0  # SW_SHOWMAXIMIZED / SW_SHOWNORMAL / SW_HIDE
        wp.flags = 0
        return bool(user32.SetWindowPlacement(hwnd, ctypes.byref(wp)))
    except Exception as e:
        print("[placement]", repr(e))
        return False


# ───────────────── 工作列：縮圖按鈕 + 進度 ─────────────────
class THUMBBUTTON(ctypes.Structure):
    _fields_ = [("dwMask", wt.DWORD), ("iId", wt.UINT), ("iBitmap", wt.UINT), ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 260), ("dwFlags", wt.DWORD)]


def _taskbar_iface():
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class ITaskbarList3(IUnknown):
        _iid_ = GUID("{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}")
        _methods_ = [
            COMMETHOD([], HRESULT, "HrInit"),
            COMMETHOD([], HRESULT, "AddTab", (["in"], wt.HWND)),
            COMMETHOD([], HRESULT, "DeleteTab", (["in"], wt.HWND)),
            COMMETHOD([], HRESULT, "ActivateTab", (["in"], wt.HWND)),
            COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], wt.HWND)),
            COMMETHOD([], HRESULT, "MarkFullscreenWindow", (["in"], wt.HWND), (["in"], ctypes.c_int)),
            COMMETHOD([], HRESULT, "SetProgressValue", (["in"], wt.HWND), (["in"], ctypes.c_ulonglong), (["in"], ctypes.c_ulonglong)),
            COMMETHOD([], HRESULT, "SetProgressState", (["in"], wt.HWND), (["in"], ctypes.c_int)),
            COMMETHOD([], HRESULT, "RegisterTab", (["in"], wt.HWND), (["in"], wt.HWND)),
            COMMETHOD([], HRESULT, "UnregisterTab", (["in"], wt.HWND)),
            COMMETHOD([], HRESULT, "SetTabOrder", (["in"], wt.HWND), (["in"], wt.HWND)),
            COMMETHOD([], HRESULT, "SetTabActive", (["in"], wt.HWND), (["in"], wt.HWND), (["in"], wt.DWORD)),
            COMMETHOD([], HRESULT, "ThumbBarAddButtons", (["in"], wt.HWND), (["in"], wt.UINT), (["in"], ctypes.POINTER(THUMBBUTTON))),
            COMMETHOD([], HRESULT, "ThumbBarUpdateButtons", (["in"], wt.HWND), (["in"], wt.UINT), (["in"], ctypes.POINTER(THUMBBUTTON))),
        ]
    return comtypes, GUID, ITaskbarList3


def _glyph_icon(ch, px):
    """用 Segoe Fluent Icons 畫一個白色圖示 → HICON。"""
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            font = ImageFont.truetype(name, int(px * 0.8))
            break
        except OSError:
            continue
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if font is not None:
        d.text((px / 2, px / 2), ch, font=font, fill=(255, 255, 255, 255), anchor="mm")
    bgra = img.tobytes("raw", "BGRA")

    class BITMAPV5HEADER(ctypes.Structure):
        _fields_ = [("size", wt.DWORD), ("w", wt.LONG), ("h", wt.LONG), ("planes", wt.WORD), ("bits", wt.WORD),
                    ("comp", wt.DWORD), ("sizeimg", wt.DWORD), ("xp", wt.LONG), ("yp", wt.LONG), ("used", wt.DWORD),
                    ("imp", wt.DWORD), ("rm", wt.DWORD), ("gm", wt.DWORD), ("bm", wt.DWORD), ("am", wt.DWORD),
                    ("cs", wt.DWORD), ("ep", ctypes.c_byte * 36), ("gr", wt.DWORD), ("gg", wt.DWORD), ("gb", wt.DWORD),
                    ("intent", wt.DWORD), ("pd", wt.DWORD), ("ps", wt.DWORD), ("res", wt.DWORD)]

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wt.BOOL), ("x", wt.DWORD), ("y", wt.DWORD), ("mask", wt.HBITMAP), ("color", wt.HBITMAP)]

    bi = BITMAPV5HEADER()
    bi.size, bi.w, bi.h, bi.planes, bi.bits, bi.comp = ctypes.sizeof(BITMAPV5HEADER), px, -px, 1, 32, 3
    bi.rm, bi.gm, bi.bm, bi.am = 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000
    bits = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
    color = gdi32.CreateDIBSection(None, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, bgra, len(bgra))
    gdi32.CreateBitmap.restype = wt.HBITMAP
    mask = gdi32.CreateBitmap(px, px, 1, 1, None)
    ii = ICONINFO(True, 0, 0, mask, color)
    user32.CreateIconIndirect.restype = wt.HICON
    h = user32.CreateIconIndirect(ctypes.byref(ii))
    gdi32.DeleteObject(color)
    gdi32.DeleteObject(mask)
    return h


class Thumbs:
    """工作列縮圖下的三顆按鈕 + 工作列圖示上的播放進度 (自己的執行緒，每秒更新)。"""
    GLYPH = {"prev": "", "play": "", "pause": "", "next": ""}

    def __init__(self, hwnd, get_state, show_progress):
        self.hwnd = hwnd
        self.get_state = get_state            # -> (playing: bool|None, position, duration)
        self.show_progress = show_progress    # -> bool
        self._reset = threading.Event()
        self._reset.set()

    def reset(self):
        self._reset.set()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def _buttons(self, playing):
        out = (THUMBBUTTON * 3)()
        for i, (bid, key, tip) in enumerate([(1, "prev", "上一首"), (2, "pause" if playing else "play", "暫停" if playing else "播放"),
                                             (3, "next", "下一首")]):
            out[i].dwMask = 0x2 | 0x4 | 0x8  # 圖示、提示、狀態
            out[i].iId = bid
            out[i].hIcon = self.icons[key]
            out[i].szTip = tip
            out[i].dwFlags = 0
        return out

    def _loop(self):
        ctypes.windll.ole32.CoInitializeEx(None, 2)
        comtypes, GUID, ITaskbarList3 = _taskbar_iface()
        from comtypes.client import CreateObject
        px = round(16 * (user32.GetDpiForWindow(self.hwnd) or 96) / 96)
        self.icons = {k: _glyph_icon(ch, px) for k, ch in self.GLYPH.items()}
        tb, last_play, last_prog = None, None, None
        while True:
            try:
                if self._reset.is_set():
                    self._reset.clear()
                    tb = CreateObject(GUID("{56FDF344-FD6D-11d0-958A-006097C9A090}"), interface=ITaskbarList3)
                    tb.HrInit()
                    playing, _, _ = self.get_state()
                    try:
                        tb.ThumbBarAddButtons(self.hwnd, 3, self._buttons(bool(playing)))
                    except Exception:  # 已經加過了 (只能加一次)：改成更新
                        tb.ThumbBarUpdateButtons(self.hwnd, 3, self._buttons(bool(playing)))
                    last_play, last_prog = bool(playing), None
                playing, pos, dur = self.get_state()
                if bool(playing) != last_play:
                    tb.ThumbBarUpdateButtons(self.hwnd, 3, self._buttons(bool(playing)))
                    last_play = bool(playing)
                if self.show_progress() and playing is not None and dur:
                    state = 2 if playing else 8  # 一般 (綠) / 暫停 (黃)
                    if last_prog != state:
                        tb.SetProgressState(self.hwnd, state)
                        last_prog = state
                    tb.SetProgressValue(self.hwnd, int(max(0, min(pos, dur)) * 1000), int(dur * 1000))
                elif last_prog != 0:
                    tb.SetProgressState(self.hwnd, 0)
                    last_prog = 0
            except Exception as e:
                print("[thumbs]", repr(e), flush=True)
                time.sleep(3)
                self._reset.set()
            time.sleep(1)
