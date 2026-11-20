"""
系統匣：每個連線中的裝置一個電量圓環圖示 (像 HaloBattery)。
  ・圓環長度 = 電量；30% 以上綠、15~30% 橘、以下紅
  ・充電中：綠色圓環慢慢呼吸；快沒電：紅色閃動
  ・滑鼠移上去：「G502 X PLUS：67%，大約還能用 37 小時」
  ・右鍵：打開裝置頁 / 隱藏這個裝置的圖示
"""
import math
import os
import threading
import time

from PIL import Image, ImageDraw, ImageFont

GREEN, AMBER, RED, GRAY = (48, 209, 88), (255, 176, 32), (255, 69, 58), (150, 150, 150)
GLYPH = {"headphones": "\uE7F6", "controller": "\uE7FC", "mouse": "\uE962", "keyboard": "\uE765", "phone": "\uE8EA",
         "laptop": "\uE7F8", "speaker": "\uE7F5", "device": "\uE772", "pen": "\uE76D"}
KIND_ZH = {"headphones": "耳機", "controller": "手把", "mouse": "滑鼠", "keyboard": "鍵盤", "phone": "手機",
           "laptop": "電腦", "speaker": "喇叭", "pen": "觸控筆", "device": "裝置"}
S = 64  # 畫的大小 (Windows 會縮成系統匣大小)
_font = {}


def icon_font(size):
    if size not in _font:
        f = None
        for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
            p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                break
        _font[size] = f
    return _font[size]


def level_color(level, charging=False):
    if charging or level > 30:
        return GREEN
    return AMBER if level > 15 else RED


def light_taskbar():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def ring_icon(d, phase=0.0, light=False):
    """phase：動畫時間 (秒)；充電中會呼吸、快沒電會閃。"""
    k = 4
    big = Image.new("RGBA", (S * k, S * k), (0, 0, 0, 0))
    dr = ImageDraw.Draw(big)
    lv = max(0, min(100, d["level"]))
    col = level_color(lv, d["charging"]) if d["connected"] else GRAY
    alpha = 1.0
    glow = 0.0
    if d["charging"]:
        b = 0.5 + 0.5 * math.sin(phase * 2 * math.pi / 2.4)  # 2.4 秒一次呼吸
        alpha, glow = 0.55 + 0.45 * b, b
    elif d["connected"] and lv <= 15:
        alpha = 0.45 + 0.55 * (0.5 + 0.5 * math.cos(phase * 2 * math.pi / 1.1))
    w = 9 * k
    pad = w // 2 + 1 * k
    box = (pad, pad, S * k - pad, S * k - pad)
    track = (0, 0, 0, 70) if light else (255, 255, 255, 60)
    dr.ellipse(box, outline=track, width=w)
    if glow > 0:  # 呼吸時圓環內側的光
        for i, a in enumerate((70, 34, 14)):
            o = (i + 1) * 3 * k
            dr.arc((box[0] + o, box[1] + o, box[2] - o, box[3] - o), -90, -90 + 360 * max(lv, 3) / 100,
                   fill=col + (int(glow * a),), width=w // 2)
    if lv > 0:
        dr.arc(box, -90, -90 + 360 * max(lv, 3) / 100, fill=col + (int(255 * alpha),), width=w)
    f = icon_font(int(30 * k))
    if f:
        g = GLYPH.get(d["kind"], GLYPH["device"])  # \u5145\u96FB\u7528\u547C\u5438\u7684\u7DA0\u74B0\u8868\u793A\uFF0C\u5716\u793A\u4E0D\u63DB
        fg = (30, 30, 30, 255) if light else (255, 255, 255, 255)
        dr.text((S * k / 2, S * k / 2), g, font=f, fill=fg, anchor="mm")
    return big.resize((S, S), Image.LANCZOS)


def tooltip(d):
    s = f"{d['name']}：{d['level']}%"
    if d["charging"]:
        s += "，充電中" + ("，已充飽" if d["level"] >= 100 else "")
    elif d.get("hours"):
        h = d["hours"]
        s += f"，大約還能用 {h:.0f} 小時" if h >= 2 else f"，大約還能用 {round(h * 60)} 分鐘"
    return s[:120]


class TrayDevices:
    def __init__(self, service, config, open_devices):
        self.svc, self.config, self.open_devices = service, config, open_devices
        self.icons = {}        # id -> (pystray.Icon, 最後的裝置資料)
        self.lock = threading.Lock()
        self.light = light_taskbar()
        service.listeners.append(self.sync)

    def enabled(self):
        return self.config.get("devices", {}).get("tray", True)

    def start(self):
        threading.Thread(target=self.animate, daemon=True).start()
        self.sync()
        return self

    def _menu(self, dev_id):
        import pystray
        P = pystray.MenuItem
        return pystray.Menu(
            P("打開裝置頁", lambda i, it: self.open_devices(), default=True),
            P("隱藏這個裝置的圖示", lambda i, it: self.svc.set_hidden(dev_id, True)),
        )

    def sync(self):
        """裝置清單變了：新增 / 移除 / 更新圖示。"""
        try:
            import pystray
        except Exception:
            return
        want = {d["id"]: d for d in self.svc.connected()} if self.enabled() else {}
        with self.lock:
            for dev_id in list(self.icons):
                if dev_id not in want:
                    icon, _ = self.icons.pop(dev_id)
                    try:
                        icon.stop()
                    except Exception:
                        pass
            for dev_id, d in want.items():
                if dev_id in self.icons:
                    icon, old = self.icons[dev_id]
                    if (old["level"], old["charging"], old.get("hours"), old["name"]) != \
                            (d["level"], d["charging"], d.get("hours"), d["name"]):
                        icon.icon = ring_icon(d, time.time(), self.light)
                        icon.title = tooltip(d)
                    self.icons[dev_id] = (icon, d)
                else:
                    icon = pystray.Icon(f"hummi-dev-{abs(hash(dev_id)) % 100000}", ring_icon(d, 0, self.light),
                                        tooltip(d), self._menu(dev_id))
                    threading.Thread(target=icon.run, daemon=True).start()
                    self.icons[dev_id] = (icon, d)

    def animate(self):
        """充電中 / 快沒電的圖示要動。"""
        while True:
            busy = False
            with self.lock:
                items = list(self.icons.values())
            now = time.time()
            for icon, d in items:
                if d["charging"] or (d["connected"] and d["level"] <= 15):
                    busy = True
                    try:
                        icon.icon = ring_icon(d, now, self.light)
                    except Exception:
                        pass
            time.sleep(0.2 if busy else 1.0)  # 呼吸動畫每秒 5 張就夠順，重新編碼圖示很花 CPU
