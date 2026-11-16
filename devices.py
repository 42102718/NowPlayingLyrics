"""
裝置電量服務 (一個背景執行緒，膠囊、系統匣圖示、主視窗都從這裡拿資料)。

  ・藍牙每 4 秒、2.4G 接收器每 20 秒讀一次 (battery.py)
  ・記錄電量歷史，估計「大約還能用幾小時」
  ・事件：連上 (connect)、快沒電 (low)、開始充電 (charge)、充飽 (full)

    svc = DeviceService(config, save_config, data_dir).start()
    svc.snapshot()          -> [裝置 dict (多了 hours, hidden)]
    svc.take_events()       -> [(kind, 裝置)]  (膠囊拿走去顯示)
    svc.version             -> 每次清單有變就 +1
"""
import json
import threading
import time

DEFAULTS = {"low": 15, "tray": True, "hidden": []}
HISTORY_KEEP = 48 * 3600


class DeviceService:
    def __init__(self, config, save_config, data_dir):
        self.config, self.save_config = config, save_config
        self.path = data_dir / "battery_history.json"
        self.lock = threading.Lock()
        self.devices = []          # 目前看得到 (含沒連線、但有記錄電量的藍牙裝置)
        self.events = []
        self.listeners = []        # 清單變了就呼叫 (主視窗廣播、系統匣圖示)
        self.version = 0
        self.history = {}          # id -> [[ts, level, charging], ...]
        try:
            self.history = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.history = {}
        self.hid, self.hid_at = [], 0.0
        self.force = threading.Event()

    def cfg(self):
        c = self.config.setdefault("devices", {})
        for k, v in DEFAULTS.items():
            c.setdefault(k, list(v) if isinstance(v, list) else v)
        return c

    def enabled(self):
        return (self.config.get("notch_opts") or {}).get("battery", True)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def refresh(self):
        """馬上重讀一次 (使用者打開裝置頁時)。"""
        self.hid_at = 0.0
        self.force.set()

    # ───── 對外 ─────
    def snapshot(self, include_hidden=True):
        with self.lock:
            hidden = set(self.cfg()["hidden"])
            return [dict(d, hidden=d["id"] in hidden) for d in self.devices
                    if include_hidden or d["id"] not in hidden]

    def connected(self):
        return [d for d in self.snapshot(include_hidden=False) if d["connected"]]

    def take_events(self):
        with self.lock:
            ev, self.events = self.events, []
        return ev

    def set_hidden(self, dev_id, hidden):
        c = self.cfg()
        lst = [x for x in c["hidden"] if x != dev_id]
        if hidden:
            lst.append(dev_id)
        c["hidden"] = lst
        self.save_config()
        self._changed()

    def set_low(self, pct):
        self.cfg()["low"] = int(max(5, min(50, pct)))
        self.save_config()
        self._changed()

    def _changed(self):
        self.version += 1
        for fn in list(self.listeners):
            try:
                fn()
            except Exception as e:
                print("[devices] listener", repr(e), flush=True)

    # ───── 估計剩餘時間 ─────
    def _record(self, d, now):
        h = self.history.setdefault(d["id"], [])
        if h and h[-1][1] == d["level"] and bool(h[-1][2]) == d["charging"]:
            return False
        if h and now - h[-1][0] < 120 and h[-1][1] == d["level"]:
            return False
        h.append([round(now), d["level"], int(d["charging"])])
        cut = now - HISTORY_KEEP
        while h and h[0][0] < cut:
            h.pop(0)
        return True

    def _hours_left(self, d, now):
        if d["charging"] or not d["connected"]:
            return None
        h = self.history.get(d["id"]) or []
        # 從最近一次充電之後開始算
        seg = []
        for ts, lv, ch in reversed(h):
            if ch:
                break
            seg.append((ts, lv))
        seg.reverse()
        if len(seg) < 2:
            return None
        (t0, l0), (t1, l1) = seg[0], seg[-1]
        span, drop = t1 - t0, l0 - l1
        if span < 1200 or drop < 3:  # 至少看 20 分鐘、掉 3%
            return None
        rate = drop / span  # % / 秒
        hours = d["level"] / rate / 3600
        return round(hours, 1) if 0.2 < hours < 1000 else None

    # ───── 主迴圈 ─────
    def run(self):
        import battery
        first = True
        known = {}
        last_save = time.time()
        while True:
            try:
                if not self.enabled():
                    with self.lock:
                        changed = bool(self.devices)
                        self.devices = []
                    if changed:
                        self._changed()
                    self.force.wait(5)
                    self.force.clear()
                    continue
                now = time.time()
                if now - self.hid_at > 20:
                    try:
                        self.hid = battery.receivers()
                    except Exception as e:
                        print("[devices] receivers", repr(e), flush=True)
                    self.hid_at = now
                devs = battery.poll(self.hid)
                low = self.cfg()["low"]
                events = []
                dirty = False
                for d in devs:
                    if d["connected"]:
                        dirty |= self._record(d, now)
                    d["hours"] = self._hours_left(d, now)
                    old = known.get(d["id"])
                    if first or not d["connected"]:
                        continue
                    if old is None or not old["connected"]:
                        if d["src"] != "sys":
                            events.append(("device", d))
                        continue
                    if d["charging"] and not old["charging"]:
                        events.append(("charge", d))
                    elif d["charging"] and d["level"] >= 100 > old["level"]:
                        events.append(("full", d))
                    elif not d["charging"] and old["level"] > low >= d["level"]:
                        events.append(("low", d))
                sig_old = [(x["id"], x["level"], x["connected"], x["charging"], x.get("hours")) for x in self.devices]
                sig_new = [(x["id"], x["level"], x["connected"], x["charging"], x.get("hours")) for x in devs]
                hidden = set(self.cfg()["hidden"])
                with self.lock:
                    self.devices = devs
                    self.events += [(k, d) for k, d in events if d["id"] not in hidden]
                known = {d["id"]: d for d in devs}
                first = False
                if sig_old != sig_new:
                    self._changed()
                if dirty and now - last_save > 300:
                    self.path.write_text(json.dumps(self.history), encoding="utf-8")
                    last_save = now
            except Exception as e:
                print("[devices]", repr(e), flush=True)
            self.force.wait(4)
            self.force.clear()
