"""多螢幕 + 全螢幕偵測 (浮動歌詞、膠囊共用)。座標都是實體像素 (程式是 Per-monitor DPI aware)。

螢幕選擇 (設定值)：
  "primary"  主螢幕
  "active"   你正在用的那個螢幕 (前景視窗在哪就跟到哪；玩遊戲時就是遊戲那個螢幕)
  "mouse"    滑鼠所在的螢幕
  其他字串   指定螢幕的裝置名稱 (\\\\.\\DISPLAY2…)；拔掉了就退回主螢幕
"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HMONITOR
user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
user32.MonitorFromPoint.restype = wt.HMONITOR
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD),
                ("szDevice", wt.WCHAR * 32)]


def _info(hmon):
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return None
    r, w = mi.rcMonitor, mi.rcWork
    return {"name": mi.szDevice, "primary": bool(mi.dwFlags & 1),
            "rect": (r.left, r.top, r.right, r.bottom), "work": (w.left, w.top, w.right, w.bottom)}


def monitors():
    out = []

    def cb(h, hdc, rc, lp):
        i = _info(h)
        if i:
            out.append(i)
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    out.sort(key=lambda m: (not m["primary"], m["rect"][0], m["rect"][1]))
    return out


def label(m, i):
    l, t, r, b = m["rect"]
    return f"螢幕 {i + 1}{' (主螢幕)' if m['primary'] else ''} · {r - l}×{b - t}"


_SHELL = ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")


def _fg():
    h = user32.GetForegroundWindow()
    if not h:
        return None, ""
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, buf, 64)
    return h, buf.value


class Picker:
    """記住上一次的結果，每 0.5 秒才重新判斷一次 (浮動歌詞 / 膠囊每一格都會問)。"""

    def __init__(self, own_hwnds=()):
        self.own = {int(x) for x in own_hwnds if x}
        self.at = 0.0
        self.cache = None
        self.last_active = None

    def pick(self, mode):
        now = time.perf_counter()
        if self.cache and self.cache[0] == mode and now - self.at < 0.5:
            return self.cache[1]
        self.at = now
        mon = None
        if mode == "mouse":
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            mon = _info(user32.MonitorFromPoint(pt, 2))
        elif mode == "active":
            h, cls = _fg()
            if h and h not in self.own and cls not in _SHELL:
                mon = _info(user32.MonitorFromWindow(h, 2))
                self.last_active = mon
            else:
                mon = self.last_active  # 點了桌面 / 自己的視窗：留在原本的螢幕
        elif mode and mode != "primary":
            mon = next((m for m in monitors() if m["name"] == mode), None)
        if mon is None:
            mon = next((m for m in monitors() if m["primary"]), None) or {
                "name": "", "primary": True,
                "rect": (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)),
                "work": (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))}
        self.cache = (mode, mon)
        return mon


def fullscreen_app(mon_rect=None, own=()):
    """前景是全螢幕程式 (遊戲、影片全螢幕、簡報) 嗎？有給螢幕範圍時，只看那個螢幕。"""
    try:
        q = ctypes.c_int()
        if shell32.SHQueryUserNotificationState(ctypes.byref(q)) == 0 and q.value in (3, 4):
            # 3 = D3D 獨佔全螢幕、4 = 簡報模式
            if mon_rect is None:
                return True
            h, _ = _fg()
            if h:
                m = _info(user32.MonitorFromWindow(h, 2))
                return bool(m and m["rect"] == tuple(mon_rect))
            return True
    except Exception:
        pass
    h, cls = _fg()
    if not h or h in {int(x) for x in own if x} or cls in _SHELL:
        return False
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    m = _info(user32.MonitorFromWindow(h, 2))
    if not m:
        return False
    l, t, rr, b = m["rect"]
    if mon_rect is not None and m["rect"] != tuple(mon_rect):
        return False
    return r.left <= l and r.top <= t and r.right >= rr and r.bottom >= b
