"""自動暫停：鎖定電腦 / 睡眠、耳機拔掉或藍牙耳機斷線時暫停音樂；回來時接著播。

- 鎖定 (Win+L)、睡眠：暫停；解鎖後繼續 (只繼續「我們暫停的」，你自己按暫停的不會被叫醒)
- 預設播放裝置消失 (拔耳機、藍牙斷線)：暫停，免得音樂突然從喇叭放出來；
  同一個裝置 30 分鐘內接回來又變成預設，就接著播
手動切換播放裝置 (兩個都還在) 不會暫停。
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
wtsapi32 = ctypes.windll.wtsapi32

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p]
wtsapi32.WTSRegisterSessionNotification.argtypes = [wt.HWND, wt.DWORD]

WM_WTSSESSION_CHANGE, WM_POWERBROADCAST, WM_TIMER = 0x02B1, 0x0218, 0x0113
WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 7, 8
PBT_APMSUSPEND, PBT_APMRESUMEAUTOMATIC = 4, 18
HWND_MESSAGE = wt.HWND(-3)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class AutoPause:
    def __init__(self, config, is_playing, pause, play, notify=None):
        self.config = config            # config["auto_pause"] = {"lock", "unplug", "resume"}
        self.is_playing = is_playing    # -> bool
        self.pause, self.play = pause, play
        self.notify = notify            # notify(訊息)：膠囊上跳一下
        self.paused_lock = False
        self.lost = None                # (裝置 ID, 時間)：因為它斷線而暫停
        self.out_id = None
        self._proc = WNDPROC(self._wndproc)

    def opt(self, k):
        return bool(self.config.get("auto_pause", {}).get(k, True))

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    # ───── 事件 ─────
    def _away(self, why):
        if self.opt("lock") and self.is_playing():
            self.pause()
            self.paused_lock = True
            print("[autopause] 暫停：", why, flush=True)

    def _back(self):
        if self.paused_lock:
            self.paused_lock = False
            if self.opt("resume") and not self.is_playing():
                time.sleep(0.8)  # 剛解鎖：等音訊裝置準備好
                self.play()
                print("[autopause] 繼續播放 (解鎖)", flush=True)

    def _check_output(self):
        import audio_ctl
        cur = audio_ctl.default_output_id()
        prev, self.out_id = self.out_id, cur
        if prev is None or cur == prev:
            return
        if self.lost and cur == self.lost[0]:  # 斷線的耳機接回來了
            lost_at, self.lost = self.lost[1], None
            if self.opt("resume") and time.time() - lost_at < 1800 and not self.is_playing():
                time.sleep(1.0)
                self.play()
                print("[autopause] 繼續播放 (耳機接回來了)", flush=True)
            return
        if self.opt("unplug") and not audio_ctl.device_active(prev) and self.is_playing():
            self.pause()
            self.lost = (prev, time.time())
            print("[autopause] 暫停：播放裝置斷線", flush=True)
            if self.notify:
                self.notify("耳機斷線，先幫你暫停")

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_WTSSESSION_CHANGE:
                if wp == WTS_SESSION_LOCK:
                    self._away("鎖定")
                elif wp == WTS_SESSION_UNLOCK:
                    threading.Thread(target=self._back, daemon=True).start()
            elif msg == WM_POWERBROADCAST:
                if wp == PBT_APMSUSPEND:
                    self._away("睡眠")
                # 睡醒後一定會先經過鎖定畫面，等解鎖再繼續
            elif msg == WM_TIMER:
                self._check_output()
        except Exception as e:
            print("[autopause]", repr(e), flush=True)
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _run(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="HummiAutoPause")
        user32.RegisterClassW(ctypes.byref(wc))
        # 一般的隱藏視窗 (不是 message-only)：才收得到 WM_POWERBROADCAST
        hwnd = user32.CreateWindowExW(0, "HummiAutoPause", "HummiAutoPause", 0, 0, 0, 0, 0, None, None, hinst, None)
        wtsapi32.WTSRegisterSessionNotification(hwnd, 0)  # NOTIFY_FOR_THIS_SESSION
        import audio_ctl
        self.out_id = audio_ctl.default_output_id()
        user32.SetTimer(hwnd, 1, 1000, None)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
