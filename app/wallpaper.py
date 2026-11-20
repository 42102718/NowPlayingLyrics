"""桌布模式：把舞台 (歌詞 + 動態背景) 放到桌面上，在桌面圖示後面。

做法跟動態桌布程式一樣：請檔案總管開出 WorkerW (訊息 0x052C)，把我們的視窗塞到它旁邊 / 裡面。
- Windows 11 24H2 以後：WorkerW 變成 Progman 的子視窗 → 我們也當 Progman 的子視窗，夾在圖示 (SHELLDLL_DefView) 和 WorkerW 之間
- 以前的版本：WorkerW 是獨立的頂層視窗 → 我們當它的子視窗
桌面整個被其他視窗蓋住時把視窗藏起來 (WebView2 會自動停止動畫，不吃 GPU)。
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
user32.FindWindowW.restype = wt.HWND
user32.FindWindowExW.restype = wt.HWND
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.SetParent.restype = wt.HWND
user32.SetParent.argtypes = [wt.HWND, wt.HWND]
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.c_void_p]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
user32.IsZoomed.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF, ctypes.c_ubyte, wt.DWORD]

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD, WS_POPUP, WS_CAPTION, WS_THICKFRAME = 0x40000000, 0x80000000, 0x00C00000, 0x00040000
WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80000, 0x80, 0x40000
SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW, SWP_NOMOVE, SWP_NOSIZE = 0x10, 0x20, 0x40, 0x2, 0x1


def _workerw():
    """回傳 (父視窗, 圖示那層 DefView 或 None, 新版結構嗎)。"""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None, None, False
    res = ctypes.c_size_t()
    user32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))
    worker = user32.FindWindowExW(progman, None, "WorkerW", None)
    if worker:  # 24H2+
        return progman, user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None), True
    # 舊版：找有 DefView 的那個頂層視窗，它後面那個 WorkerW 就是桌布層
    found = [None]
    ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, lp):
        if user32.FindWindowExW(h, None, "SHELLDLL_DefView", None):
            found[0] = user32.FindWindowExW(None, h, "WorkerW", None)
        return True
    user32.EnumWindows(ENUM(cb), 0)
    return found[0] or progman, None, False


def attach(hwnd, mon_rect):
    """把視窗放到桌面圖示後面，蓋滿 mon_rect (實體像素的螢幕座標)。"""
    parent, defview, new = _workerw()
    if not parent:
        return False
    st = user32.GetWindowLongW(hwnd, GWL_STYLE)
    st = (st | WS_CHILD) & ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME)
    user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(st & 0xFFFFFFFF).value)
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW  # 不要出現在工作列 / Alt+Tab
    if new:
        ex |= WS_EX_LAYERED  # 24H2 的桌面要分層視窗才畫得出來
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(ex & 0xFFFFFFFF).value)
    if new:
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)  # LWA_ALPHA
    user32.SetParent(hwnd, parent)
    # 父視窗蓋住整個虛擬桌面，原點在左上角那個螢幕
    pr = wt.RECT()
    user32.GetWindowRect(parent, ctypes.byref(pr))
    l, t, r, b = mon_rect
    user32.SetWindowPos(hwnd, defview if (new and defview) else None, l - pr.left, t - pr.top, r - l, b - t,
                        SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW | (0 if (new and defview) else 0x4))
    if new:
        worker = user32.FindWindowExW(parent, None, "WorkerW", None)
        if worker:  # 原本的桌布在我們後面
            user32.SetWindowPos(worker, hwnd, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
    return True


def desktop_covered(mon_rect, own=()):
    """前景視窗把那個螢幕整個蓋住了嗎 (放大 / 全螢幕)？蓋住時桌布不用畫。"""
    import screens
    h = user32.GetForegroundWindow()
    if not h or h in own:
        return False
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, buf, 64)
    if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    l, t, rr, b = mon_rect
    if not (r.left <= l + 16 and r.top <= t + 16 and r.right >= rr - 16):
        return False
    return bool(user32.IsZoomed(h)) or screens.fullscreen_app(mon_rect)
