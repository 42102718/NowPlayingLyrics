"""全域快捷鍵：在遊戲、瀏覽器裡也能用 (Windows RegisterHotKey，不用監聽鍵盤，不會被防毒擋)。

每個快捷鍵註冊在自己的執行緒的訊息迴圈上；被別的程式佔走的那組會記在 failed，設定頁會顯示。"""
import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK = {"Space": 0x20, "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, ",": 0xBC, ".": 0xBE,
      **{chr(c): c for c in range(0x41, 0x5B)}}

# (按鍵, 動作名稱, 說明)；修飾鍵一律 Ctrl+Alt，不容易跟遊戲撞到。
# Ctrl+Alt+L / H / ← / → 是浮動歌詞原本就有的 (overlay.py)，這裡不重複註冊。
KEYS = [
    ("P", "toggle", "播放 / 暫停"),
    (".", "next", "下一首"),
    (",", "prev", "上一首"),
    ("Up", "vol_up", "音樂音量 +"),
    ("Down", "vol_down", "音樂音量 −"),
    ("M", "duck", "一鍵壓低音樂 / 恢復"),
    ("W", "window", "主視窗 顯示 / 隱藏"),
    ("S", "stage", "打開舞台"),
]


def label(key):
    return "Ctrl + Alt + " + {"Space": "空白鍵", "Left": "←", "Right": "→", "Up": "↑", "Down": "↓"}.get(key, key)


class Hotkeys:
    def __init__(self, actions):
        self.actions = actions  # 動作名稱 -> 函式
        self.failed: list[str] = []
        self.thread = None
        self.tid = 0

    def start(self):
        if self.thread is None:
            ready = threading.Event()
            self.thread = threading.Thread(target=self._loop, args=(ready,), daemon=True)
            self.thread.start()
            ready.wait(2)
        return self

    def stop(self):
        if self.thread is not None and self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)
            self.thread.join(2)
        self.thread, self.tid = None, 0

    def _loop(self, ready):
        self.tid = kernel32.GetCurrentThreadId()
        ids = {}
        self.failed = []
        for i, (key, act, _) in enumerate(KEYS, 1):
            if user32.RegisterHotKey(None, i, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK[key]):
                ids[i] = act
            else:
                self.failed.append(key)
        if self.failed:
            print("[hotkeys] 被別的程式佔用:", ", ".join(label(k) for k in self.failed), flush=True)
        ready.set()
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in ids:
                    fn = self.actions.get(ids[msg.wParam])
                    if fn:
                        try:
                            fn()
                        except Exception as e:
                            print("[hotkeys]", ids[msg.wParam], repr(e), flush=True)
        finally:
            for i in ids:
                user32.UnregisterHotKey(None, i)
