"""
Folia 舞台連接：把 Hummi 正在播的歌 (歌名、封面、時間、同步歌詞，Apple Music 來源還有逐字時間)
送進 Folia (github.com/chthollyphile/folia-major) 的舞台，讓 Folia 用它那些炫麗的歌詞動畫演出。
音樂照樣在 Spotify / YouTube Music 播，Folia 只負責畫面 — 適合開唱歌台，OBS 擷取 Folia 的視窗就好。

用的是 Folia 公開的 Stage API (「接入外部播放器」)：
  1. 讀 Folia 寫的連線檔 (%APPDATA%\\Folia\\stage\\connection.json) 拿到網址和 token
  2. 換歌 / 歌詞找到時 POST /stage/lyrics
  3. 播放、暫停、拖動時 POST /stage/lyrics/clock；平常每 5 秒校正一次
  4. 沒在播放時 DELETE /stage/state
兩個程式只透過本機 HTTP 溝通。
"""
import base64
import ctypes
import json
import os
import threading
import time
import urllib.error
import urllib.request

FOLIA_URL = "https://github.com/chthollyphile/folia-major"
CANDIDATE_DIRS = ("Folia", "folia-major", "folia")


def connection_file():
    base = os.environ.get("APPDATA", "")
    for name in CANDIDATE_DIRS:
        p = os.path.join(base, name, "stage", "connection.json")
        if os.path.exists(p):
            return p
    return None


def pid_alive(pid):
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return True


def _ts(sec):
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    return f"{m:02d}:{sec - m * 60:05.2f}"


def to_lrc(lines):
    """回傳 (lrc 內容, 翻譯 lrc 或 None, formatHint)。有逐字時間就用 enhanced-lrc。"""
    timed = [l for l in lines if l.get("t") is not None]
    words = any(l.get("w") for l in timed)
    out, tr = [], []
    for i, l in enumerate(timed):
        text = (l.get("text") or "").replace("\n", " ")
        if words and l.get("w"):
            parts = []
            for t, d, s in l["w"]:
                parts.append(f"<{_ts(t)}>{s}")
            last = l["w"][-1]
            parts.append(f"<{_ts(last[0] + (last[1] or 0.3))}>")
            out.append(f"[{_ts(l['t'])}]" + "".join(parts))
        else:
            out.append(f"[{_ts(l['t'])}]{text}")
        if l.get("tr"):
            tr.append(f"[{_ts(l['t'])}]{l['tr']}")
    return "\n".join(out), ("\n".join(tr) if tr else None), ("enhanced-lrc" if words else "lrc")


class FoliaBridge:
    def __init__(self, state, config, art):
        self.state, self.config, self.art = state, config, art
        self.status = {"state": "off", "detail": ""}
        self.conn = None
        self.conn_mtime = 0.0
        self.session_key = None
        self.session_at = None      # lyricsSession.updatedAt
        self.last_clock = (None, 0.0, 0.0)  # (paused, position, 送出的時間)
        self.cleared = True

    def enabled(self):
        return bool((self.config.get("folia") or {}).get("enabled"))

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def _set(self, st, detail=""):
        self.status = {"state": st, "detail": detail}

    # ───── HTTP ─────
    def _req(self, method, path, body=None, timeout=4):
        c = self.conn
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(c["baseUrl"].rstrip("/") + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + c["token"],
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(txt) if txt.strip() else {})
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
            except Exception:
                return e.code, {}

    def _load_conn(self):
        p = connection_file()
        if not p:
            self.conn = None
            return False
        try:
            mt = os.path.getmtime(p)
            if self.conn is None or mt != self.conn_mtime:
                c = json.loads(open(p, encoding="utf-8").read())
                if not c.get("baseUrl") or not c.get("token"):
                    self.conn = None
                    return False
                self.conn, self.conn_mtime = c, mt
                self.session_key = None  # 換了 Folia (重開 / 新 token)：重送
            if self.conn.get("pid") and not pid_alive(self.conn["pid"]):
                self.conn = None  # 殘留的檔案：Folia 沒在跑
                return False
            return True
        except Exception:
            self.conn = None
            return False

    # ───── 內容 ─────
    def position(self, t):
        pos = t["position"] + ((time.time() - t["ts"]) if t["playing"] else 0)
        pos += float(self.config.get("offset", 0) or 0)  # 主視窗調的歌詞快慢也套用
        return max(0, int(pos * 1000))

    def payload(self, t, lyr):
        body = {"title": t.get("title") or "—", "positionMs": self.position(t), "paused": not t["playing"]}
        if t.get("artist"):
            body["artist"] = t["artist"]
        if t.get("album"):
            body["album"] = t["album"]
        if t.get("duration"):
            body["durationMs"] = int(t["duration"] * 1000)
        data = None
        try:
            data = self.art(t["track_id"])
        except Exception:
            pass
        if data and len(data) < 1_400_000:
            mime = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
            body["cover"] = {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}
        if lyr and lyr.get("track_id") == t["track_id"] and lyr.get("synced") and lyr.get("lines") \
                and lyr.get("kind") != "short":
            lrc, tr, hint = to_lrc(lyr["lines"])
            if lrc.strip():
                src = {"type": "local", "lrcContent": lrc, "formatHint": hint}
                if tr and (self.config.get("overlay") or {}).get("show_translation"):
                    src["tLrcContent"] = tr
                body["lyricSource"] = src
        return body

    def key(self, t, lyr):
        lk = None
        if lyr and lyr.get("track_id") == t["track_id"] and not lyr.get("loading"):
            lines = lyr.get("lines") or []
            lk = (lyr.get("source"), lyr.get("synced"), len(lines), lines[0].get("text") if lines else "",
                  lyr.get("override"), bool(t.get("has_art")))
        return (t["track_id"], lk, bool(t.get("has_art")), bool((self.config.get("overlay") or {}).get("show_translation")))

    def push(self, t, lyr):
        body = self.payload(t, lyr)
        self.pushed_lyrics = "lyricSource" in body
        code, resp = self._req("POST", "/stage/lyrics", body)
        if code == 200:
            self.session_at = ((resp or {}).get("lyricsSession") or {}).get("updatedAt")
            self.session_key = self.key(t, lyr)
            self.last_clock = (not t["playing"], self.position(t), time.time())
            self.cleared = False
            return True
        if code == 401:
            self._set("error", "Folia 的 token 不對 (請在 Folia 重新開啟 Stage)")
        else:
            self._set("error", f"Folia 回應 {code} {(resp or {}).get('code', '')}")
        return False

    def clock(self, t):
        paused, pos = not t["playing"], self.position(t)
        lp, lpos, lat = self.last_clock
        expect = lpos + (0 if lp else (time.time() - lat) * 1000)
        if paused == lp and abs(pos - expect) < 1200 and time.time() - lat < 5:
            return
        body = {"positionMs": pos, "paused": paused}
        if self.session_at is not None:
            body["sessionUpdatedAt"] = self.session_at
        code, resp = self._req("POST", "/stage/lyrics/clock", body)
        if code == 200:
            self.last_clock = (paused, pos, time.time())
        elif code == 409:
            self.session_key = None  # 會話換了 / 沒了：下一輪整個重送

    # ───── 主迴圈 ─────
    def run(self):
        while True:
            try:
                if not self.enabled():
                    if not self.cleared and self.conn:
                        try:
                            self._req("DELETE", "/stage/state")
                        except Exception:
                            pass
                        self.cleared = True
                    self.session_key = None
                    self._set("off")
                    time.sleep(1)
                    continue
                if not self._load_conn():
                    self.session_key = None
                    self._set("waiting", "找不到 Folia：請打開 Folia，在設定裡開啟 Stage 模式")
                    time.sleep(2)
                    continue
                t = self.state.get("track")
                lyr = self.state.get("lyrics")
                if not t:
                    if not self.cleared:
                        self._req("DELETE", "/stage/state")
                        self.cleared = True
                        self.session_key = None
                    self._set("connected", "已連上 Folia，等待播放")
                elif self.key(t, lyr) != self.session_key:
                    if self.push(t, lyr):
                        self._set("connected", f"正在送到 Folia：{t.get('title')}" +
                                  ("" if self.pushed_lyrics else " (還沒有同步歌詞)"))
                else:
                    self.clock(t)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
                self.session_key = None
                self._set("waiting", "連不上 Folia：" + str(getattr(e, "reason", e))[:80])
                time.sleep(2)
            except Exception as e:
                print("[folia]", repr(e), flush=True)
                self._set("error", repr(e)[:120])
                time.sleep(2)
            time.sleep(0.5)
