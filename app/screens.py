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
user32.IsZoomed.argtypes = [wt.HWND]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]


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


def exclusive_fullscreen():
    """遊戲用「獨佔全螢幕」：這時 Windows 沒辦法把透明視窗疊在遊戲上 (硬疊會變成黑框)。"""
    try:
        q = ctypes.c_int()
        return shell32.SHQueryUserNotificationState(ctypes.byref(q)) == 0 and q.value == 3
    except Exception:
        return False


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
    # 放大的一般視窗 (瀏覽器、Discord…) 也會蓋滿螢幕，但不是全螢幕：有標題列 / 是「放大」狀態就不算
    if (user32.GetWindowLongW(h, -16) & 0x00C00000) == 0x00C00000:
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


# ───────────────── 顯示設定變了嗎 (HDR 開關、解析度、螢幕插拔) ─────────────────
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _PSRC(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32), ("statusFlags", ctypes.c_uint32)]


class _PTGT(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32), ("outputTechnology", ctypes.c_uint32),
                ("rotation", ctypes.c_uint32), ("scaling", ctypes.c_uint32), ("refreshRate", ctypes.c_uint32 * 2),
                ("scanLineOrdering", ctypes.c_uint32), ("targetAvailable", wt.BOOL), ("statusFlags", ctypes.c_uint32)]


class _PATH(ctypes.Structure):
    _fields_ = [("sourceInfo", _PSRC), ("targetInfo", _PTGT), ("flags", ctypes.c_uint32)]


class _MODE(ctypes.Structure):
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", _LUID), ("data", ctypes.c_byte * 48)]


class _HDR(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32), ("adapterId", _LUID), ("id", ctypes.c_uint32)]


class _ADV(ctypes.Structure):
    _fields_ = [("header", _HDR), ("value", ctypes.c_uint32), ("colorEncoding", ctypes.c_uint32), ("bitsPerColorChannel", ctypes.c_uint32)]


_dll_user32 = ctypes.WinDLL("user32")  # 自己的一份，不動到其他模組共用的宣告


class Healer:
    """透明視窗自我修復：有些顯示卡 (特別是雙顯卡 + HDR) 在開別的程式時會把透明視窗畫壞，
    透明的地方變成黑色、黑色變成灰色。定期看一下自己在畫面上的樣子，壞了就重建視窗。
    背景剛好是全黑時會誤判：重建過還是一樣就先停 5 分鐘，不會一直重建。"""

    def __init__(self, every=5.0):
        self.every, self.at, self.bad, self.last_fix, self.pause_until = every, 0.0, 0, 0.0, 0.0

    def due(self, now):
        if now < self.pause_until or now - self.at < self.every:
            return False
        self.at = now
        return True

    def report(self, broken, now):
        """回傳 True = 該重建了。"""
        if not broken:
            self.bad = 0
            return False
        self.bad += 1
        if self.bad < 2:  # 連續兩次都壞才算 (避免剛好換畫面)
            return False
        self.bad = 0
        if now - self.last_fix < 20:  # 重建完馬上又壞：多半是背景本來就黑，先別管
            self.pause_until = now + 300
            return False
        self.last_fix = now
        return True


def grab_points(rect, points):
    """抓畫面上幾個點的顏色 (rect = 視窗位置；points = 視窗內座標)。"""
    from PIL import ImageGrab
    l, t, r, b = rect
    img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
    return [img.getpixel((min(max(x, 0), img.width - 1), min(max(y, 0), img.height - 1)))[:3] for x, y in points]


def display_signature():
    """每個螢幕的位置大小 + HDR 開 / 關。遊戲開啟時常常會把螢幕切到 HDR：
    之前建立的透明視窗 (浮動歌詞、膠囊) 在那之後會整個變成黑框，要重新建立視窗才會好。"""
    sig = [m["rect"] for m in monitors()]
    try:
        u = _dll_user32
        n_p, n_m = ctypes.c_uint32(), ctypes.c_uint32()
        if u.GetDisplayConfigBufferSizes(2, ctypes.byref(n_p), ctypes.byref(n_m)) == 0:
            paths, modes = (_PATH * n_p.value)(), (_MODE * n_m.value)()
            if u.QueryDisplayConfig(2, ctypes.byref(n_p), paths, ctypes.byref(n_m), modes, None) == 0:
                for p in paths[:n_p.value]:
                    a = _ADV()
                    a.header = _HDR(9, ctypes.sizeof(_ADV), p.targetInfo.adapterId, p.targetInfo.id)
                    if u.DisplayConfigGetDeviceInfo(ctypes.byref(a)) == 0:
                        sig.append((p.targetInfo.id, bool(a.value & 2), a.bitsPerColorChannel))
    except Exception:
        pass
    return tuple(sig)
