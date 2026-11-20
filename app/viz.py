"""
真實的音樂頻譜：只錄播放器的聲音 (跟 AI 歌詞同一套 Process Loopback)，算出 4 個頻段的音量給膠囊的音量條用。
只有在膠囊開著、歌正在播的時候才錄；不會錄到遊戲、Discord。

    v = Visualizer(state, config); v.start()
    v.levels  -> [0~1] * 4 (低音 → 高音)，v.energy -> 0~1 整體響度，v.fresh() -> 最近有沒有資料
"""
import threading
import time

import numpy as np

SR = 16000
N = 1024
# 頻段 (Hz)：低音、中低、中高、高音
BANDS = [(40, 250), (250, 1000), (1000, 3500), (3500, 7800)]


class Visualizer:
    def __init__(self, state, config, extra=None):
        self.state, self.config = state, config
        self.extra = extra or (lambda: False)  # 舞台頁面開著時也要錄
        self.levels = [0.0] * 4
        self.energy = 0.0
        self.updated = 0.0
        self.peaks = [1e-4] * 4         # 自動增益：每個頻段最近的最大值
        self.window = np.hanning(N).astype(np.float32)
        freqs = np.fft.rfftfreq(N, 1 / SR)
        self.idx = [(np.searchsorted(freqs, lo), np.searchsorted(freqs, hi)) for lo, hi in BANDS]

    def fresh(self):
        return time.time() - self.updated < 0.4

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def wanted(self):
        t = self.state.get("track")
        on = (self.config.get("notch") and self.config.get("notch_opts", {}).get("spectrum", True)) or self.extra()
        return (t if on and t and t.get("playing") else None)

    def run(self):
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        from procloop import ProcessLoopback, exe_for_app, root_pid
        cap, cap_pid = None, None
        buf = np.zeros(N, np.float32)
        pid_cache = (None, None, 0.0)  # (exe, pid, 時間)：列舉所有程序很花時間，3 秒才重找一次
        while True:
            try:
                t = self.wanted()
                pid = None
                if t:
                    app = t.get("app", "")
                    if pid_cache[0] != app or time.time() - pid_cache[2] > 3:
                        exe = exe_for_app(app)
                        p = root_pid(exe) if exe else None
                        if not p:  # Store App 或不認得的播放器：看是哪個程序在發出聲音
                            import audio_ctl
                            p = audio_ctl.session_pid(app)
                        pid_cache = (app, p, time.time())
                    pid = pid_cache[1]
                if pid != cap_pid:
                    if cap:
                        cap.close()
                    cap, cap_pid = None, None
                    if pid:
                        cap = ProcessLoopback(pid).start()
                        cap_pid = pid
                if not cap:
                    self.levels = [0.0] * 4
                    self.energy = 0.0
                    time.sleep(0.5)
                    continue
                a = cap.read(0.04)
                if len(a):
                    a = a[-N:]
                    buf = np.concatenate([buf[len(a):], a])
                self.analyse(buf)
            except Exception as e:
                print("[viz]", repr(e), flush=True)
                try:
                    if cap:
                        cap.close()
                except Exception:
                    pass
                cap, cap_pid = None, None
                time.sleep(2)

    def analyse(self, buf):
        spec = np.abs(np.fft.rfft(buf * self.window))
        out = []
        for i, (a, b) in enumerate(self.idx):
            v = float(np.sqrt(np.mean(spec[a:b] ** 2))) if b > a else 0.0
            # 自動增益：最大值慢慢衰退，安靜的歌也會跳
            self.peaks[i] = max(v, self.peaks[i] * 0.995, 1e-3)
            out.append(min(1.0, (v / self.peaks[i]) ** 0.8))
        rms = float(np.sqrt(np.mean(buf ** 2)))
        self.energy = min(1.0, rms * 6)
        self.levels = out
        self.updated = time.time()
