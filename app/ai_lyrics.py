"""
AI 歌詞：找不到現成同步歌詞時，聽電腦正在播放的聲音，用 Whisper 自動
  1. 聽寫：完全沒有歌詞 → 邊聽邊寫出歌詞 (live)，聽完整首再精修一次並存起來
  2. 對時：有歌詞但沒有時間 → 聽完整首後算出每句的時間

需要的大檔案 (第一次用到時自動下載到 %LOCALAPPDATA%\\NowPlayingLyrics)：
  - Whisper large-v3-turbo 模型 (~1.6 GB，Hugging Face)
  - NVIDIA cuBLAS (~0.7 GB，PyPI)，只有 NVIDIA 顯示卡才下載；沒有就用 CPU
"""
import ctypes
import difflib
import gc
import importlib.util
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

SR = 16000
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NowPlayingLyrics"
MODEL_DIR = LOCAL / "models" / "faster-whisper-large-v3-turbo"
CUDA_DIR = LOCAL / "cuda"
MODEL_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
MODEL_FILES = ["config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json", "model.bin"]
CUBLAS_PKG = ("nvidia-cublas-cu12", "12.9.2.10")
UA = "NowPlayingLyrics (AI lyrics downloader)"

LIVE_EVERY = 4.0      # 幾秒聽寫一次 (CPU；顯示卡夠快時會自動縮短到 LIVE_FAST 秒)
LIVE_FAST = 1.0       # 顯示卡最快幾秒聽寫一次
LIVE_TENT = 1.8       # 結尾離「現在」這麼近的句子可能還沒唱完：先淡淡地顯示
LIVE_WINDOW = 15.0    # 每次看最近幾秒
FINAL_COVERAGE = 0.75  # 聽到幾成以上才做整首精修並存檔
IDLE_UNLOAD = 600     # 閒置幾秒後釋放模型 (把顯示卡記憶體還給遊戲)
MIN_LEN, MAX_LEN = 30, 600  # 只處理歌曲長度 (30 秒 ~ 10 分鐘) 的內容

# Whisper 在沒有人聲時常見的「幻聽」字句
HALLU = re.compile(
    r"(字幕|訂閱|订阅|點贊|点赞|打賞|打赏|明鏡|明镜|Amara|感謝觀看|感谢观看|謝謝觀看|谢谢观看|謝謝大家|谢谢大家|"
    r"請不吝|请不吝|優優|优优|獨播|独播|中文字幕|thank(s)? (you )?for watching|please subscribe|subtitles? by|"
    r"MING PAO|作詞|作词|作曲|編曲|编曲|sous-?titr|untertitel|subt[ií]tulos|sottotitoli|amara\.org|"
    r"ST' ?501|\bMFP\b|napisy|ondertitel|see you (next time|in the next)|next video|bye[- ]bye)"
    r"|^\W*(thank you|thanks|you|bye|謝謝|谢谢|感謝|感谢)\W*$", re.I)  # 安靜時最常出現的整句幻聽


def log(*a):
    print("[ai]", *a, flush=True)


# ───────────────────────── 下載 ─────────────────────────
def fmt_eta(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec} 秒"
    if sec < 3600:
        return f"{sec // 60} 分 {sec % 60:02d} 秒"
    return f"{sec // 3600} 小時 {sec % 3600 // 60} 分"


def _download(url, dest: Path, on_progress, label, retries=6):
    """下載到 .part；斷線、關掉程式後再來會從斷掉的地方接著下載 (HTTP Range)，不用從頭。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    speed = 0.0  # 每秒幾 bytes (平滑過)
    for attempt in range(retries + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {"User-Agent": UA}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                cr = r.headers.get("Content-Range") or ""  # bytes 1000-1999/2000
                m = re.match(r"bytes (\d+)-\d+/(\d+)", cr)
                if r.status == 206 and m and int(m.group(1)) == have:
                    total, mode = int(m.group(2)), "ab"
                    if have:
                        log(f"resume {dest.name} from {have / 1e6:,.0f} MB")
                else:  # 伺服器不支援接續：從頭來
                    have, mode = 0, "wb"
                    total = int(r.headers.get("Content-Length") or 0)
                done = have
                last_t, last_d = time.perf_counter(), done
                with open(tmp, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.perf_counter()
                        if now - last_t >= 0.5:
                            inst = (done - last_d) / (now - last_t)
                            speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
                            last_t, last_d = now, done
                        if total:
                            eta = (total - done) / speed if speed > 0 else None
                            on_progress(label, done / total, done, total, speed=speed, eta=eta)
            if total and done < total:
                raise IOError(f"連線中斷 ({done}/{total})")
            tmp.replace(dest)
            return
        except Exception as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 416 and have:
                # 要的範圍超過檔案 = .part 已經是完整的了 (或是壞掉)：檢查大小
                tmp.unlink(missing_ok=True)
            if attempt >= retries:
                raise
            log(f"download {dest.name} failed ({e!r}), retry {attempt + 1}/{retries}")
            time.sleep(min(30, 2 * (attempt + 1)))


def _dev_model_path():
    """開發時如果 Hugging Face 快取裡已經有模型，就直接用，不重下載。"""
    hub = Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + MODEL_REPO.replace("/", "--")) / "snapshots"
    for snap in sorted(hub.glob("*")) if hub.exists() else []:
        if (snap / "model.bin").exists():
            return snap
    return None


def ensure_model(on_progress):
    dev = _dev_model_path()
    if dev:
        return dev
    for fn in MODEL_FILES:
        if not (MODEL_DIR / fn).exists():
            _download(f"https://huggingface.co/{MODEL_REPO}/resolve/main/{fn}", MODEL_DIR / fn, on_progress, "AI 模型")
    return MODEL_DIR


def _dev_cuda_dir():
    spec = importlib.util.find_spec("nvidia")  # 開發環境裝了 nvidia-cublas-cu12 就直接用
    if spec and spec.submodule_search_locations:
        d = Path(list(spec.submodule_search_locations)[0]) / "cublas" / "bin"
        if (d / "cublas64_12.dll").exists():
            return d
    return None


def ensure_cuda(on_progress):
    """下載 NVIDIA cuBLAS (官方 PyPI wheel)，只取出需要的 DLL。"""
    dev = _dev_cuda_dir()
    if dev:
        return dev
    need = ["cublas64_12.dll", "cublasLt64_12.dll"]
    if all((CUDA_DIR / n).exists() for n in need):
        return CUDA_DIR
    name, ver = CUBLAS_PKG
    with urllib.request.urlopen(urllib.request.Request(f"https://pypi.org/pypi/{name}/{ver}/json",
                                                       headers={"User-Agent": UA}), timeout=30) as r:
        meta = json.load(r)
    url = next(u["url"] for u in meta["urls"] if u["filename"].endswith("win_amd64.whl"))
    whl = LOCAL / "downloads" / f"{name}-{ver}.whl"
    if not whl.exists():
        _download(url, whl, on_progress, "GPU 加速元件")
    CUDA_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(whl) as z:
        for info in z.infolist():
            base = info.filename.rsplit("/", 1)[-1]
            if base in need:
                with z.open(info) as src, open(CUDA_DIR / base, "wb") as dst:
                    while True:
                        b = src.read(1 << 20)
                        if not b:
                            break
                        dst.write(b)
    try:
        whl.unlink()
    except OSError:
        pass
    return CUDA_DIR


# ───────────────────────── 全螢幕偵測 ─────────────────────────
class _RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


def fullscreen_app_running() -> bool:
    """前景是全螢幕的遊戲/程式 (不含桌面、瀏覽器、Spotify)。"""
    try:
        u = ctypes.windll.user32
        state = ctypes.c_int(0)
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if state.value == 3:  # QUNS_RUNNING_D3D_FULL_SCREEN (獨佔全螢幕遊戲)
            return True
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
        exe = ""
        if h:
            buf = ctypes.create_unicode_buffer(512)
            size = ctypes.c_ulong(512)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                exe = buf.value.rsplit("\\", 1)[-1].lower()
            ctypes.windll.kernel32.CloseHandle(h)
        if exe in ("spotify.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
                   "now playing lyrics.exe", "python.exe", "pythonw.exe", "explorer.exe"):
            return False
        r = _RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        mon = u.MonitorFromWindow(hwnd, 2)

        class MI(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("m", _RECT), ("w", _RECT), ("f", ctypes.c_ulong)]
        mi = MI()
        mi.cb = ctypes.sizeof(MI)
        u.GetMonitorInfoW(mon, ctypes.byref(mi))
        return r.l <= mi.m.l and r.t <= mi.m.t and r.r >= mi.m.r and r.b >= mi.m.b
    except Exception:
        return False


# ───────────────────────── 對時 (歌詞文字 ↔ 聽到的字) ─────────────────────────
def _chars(s, norm):
    return [c for c in norm(s) if c.strip()]


def align_lines(plain_lines, words, norm):
    """plain_lines: [{'t':None,'text':...}]；words: [(start, word)]；回傳每句加上時間的 lines。"""
    lyric, owner = [], []
    for i, l in enumerate(plain_lines):
        for c in _chars(l["text"], norm):
            lyric.append(c)
            owner.append(i)
    heard, htime = [], []
    for start, w in words:
        for c in _chars(w, norm):
            heard.append(c)
            htime.append(start)
    times = [None] * len(plain_lines)
    if lyric and heard:
        sm = difflib.SequenceMatcher(None, lyric, heard, autojunk=False)
        hit = {}
        for a, b, n in sm.get_matching_blocks():
            for k in range(n):
                hit.setdefault(owner[a + k], []).append((a + k, htime[b + k]))
        for i, hs in hit.items():
            total = sum(1 for o in owner if o == i)
            if len(hs) >= max(2, total * 0.3):
                first_idx, first_t = min(hs)
                # 對到的第一個字不一定是句首，往前推一點
                lead = first_idx - owner.index(i)
                times[i] = max(0.0, first_t - lead * 0.18)
    # 沒對到的句子：用前後句內插；並保證時間遞增
    known = [(i, t) for i, t in enumerate(times) if t is not None]
    if not known:
        return None
    for i in range(len(times)):
        if times[i] is None:
            prev = max((k for k in known if k[0] < i), default=None)
            nxt = min((k for k in known if k[0] > i), default=None)
            if prev and nxt:
                times[i] = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / (nxt[0] - prev[0])
            elif prev:
                times[i] = prev[1] + 3.0 * (i - prev[0])
            else:
                times[i] = max(0.0, nxt[1] - 3.0 * (nxt[0] - i))
    for i in range(1, len(times)):
        if times[i] < times[i - 1]:
            times[i] = times[i - 1] + 0.05
    return [{"t": round(t, 2), "text": l["text"], **({"tr": l["tr"]} if l.get("tr") else {})}
            for t, l in zip(times, plain_lines)]


FILLERS = {"yeah", "yeh", "oh", "ooh", "ah", "uh", "baby", "babe", "yo", "hey", "woo", "wooo", "ayy", "huh",
           "啊", "呀", "喔", "哦", "耶", "嘿"}


def split_lines(segs, max_words=7):
    """把 AI 的段落切成歌詞行：長句在逗號 / 句號處切開 (用每個字的時間)，連續重複的短句合併。"""
    lines = []
    for s, txt in segs:
        ws = [w for w in (s.words or []) if w.word.strip()]
        if len(ws) <= max_words:
            lines.append({"t": round(s.start, 2), "text": txt})
            continue
        cur = []
        for i, w in enumerate(ws):
            cur.append(w)
            if not (re.search(r"[,.!?;，。！？；、]$", w.word.strip()) and len(cur) >= 3):
                continue
            nxt = ws[i + 1].word.strip().lower().strip(",.!?") if i + 1 < len(ws) else ""
            if nxt in FILLERS:
                continue  # 「…for reals, yeah」：yeah 跟著前一句，下一個標點再切
            lines.append({"t": round(cur[0].start, 2), "text": "".join(x.word for x in cur).strip()})
            cur = []
        if cur:
            lines.append({"t": round(cur[0].start, 2), "text": "".join(x.word for x in cur).strip()})
    out = []
    for l in lines:
        l["text"] = re.sub(r"[.。]$", "", l["text"]).strip()
        if not l["text"]:
            continue
        if out and l["text"].lower() == out[-1]["text"].lower() and len(l["text"]) <= 6 and l["t"] - out[-1]["t"] < 4:
            continue  # 「Oh / Oh / Oh」只留一個
        out.append(l)
    return out


def fill_gaps(lines, live, gap=12.0):
    """整首精修偶爾會整段跳過；那段如果即時聽寫有寫到，就拿來補。"""
    if not live:
        return lines
    times = [0.0] + [l["t"] for l in lines] + [1e9]
    extra = []
    for a, b in zip(times, times[1:]):
        if b - a > gap:
            extra += [dict(l) for l in live if a + 2 < l["t"] < b - 2 and not HALLU.search(l["text"])]
    return sorted(lines + extra, key=lambda l: l["t"])


# ───────────────────────── 引擎 ─────────────────────────
class Job:
    def __init__(self, track, key, plain):
        self.track_id = track["track_id"]
        self.key = key
        self.title, self.artist = track["title"], track["artist"]
        self.duration = max(10.0, float(track.get("duration") or 300))
        n = int(self.duration * SR) + SR * 2
        self.audio = np.zeros(n, dtype=np.float32)
        self.filled = np.zeros(int(self.duration * 10) + 20, dtype=bool)  # 每 0.1 秒一格
        self.plain = plain  # 有歌詞沒時間時的歌詞
        self.improve = False  # 已有 AI 歌詞但沒聽完整：繼續聽，聽得更完整再更新，不做即時聽寫
        self.live = []
        self.lang = None
        self.last_live = 0.0
        self.max_pos = 0.0
        self.done = False

    def write(self, samples, end_pos):
        start = int((end_pos - len(samples) / SR) * SR)
        if start < 0:
            samples = samples[-start:]
            start = 0
        end = min(start + len(samples), len(self.audio))
        if end <= start:
            return
        self.audio[start:end] = samples[: end - start]
        self.filled[start // 1600: end // 1600 + 1] = True
        self.max_pos = max(self.max_pos, end_pos)

    def coverage(self, a=0.0, b=None):
        b = self.duration if b is None else b
        seg = self.filled[int(a * 10): max(int(a * 10) + 1, int(b * 10))]
        return float(seg.mean()) if len(seg) else 0.0


class AIEngine:
    def __init__(self, state, config, on_update, norm, store_path):
        self.state, self.config = state, config
        self.on_update = on_update  # (track_id, key, result, live) → 由伺服器廣播
        self.norm = norm
        self.store_path = Path(store_path)
        try:
            self.store = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            self.store = {}
        self.job: Job | None = None
        self.jobs: dict[str, Job] = {}  # 最近幾首的錄音；切走再切回同一首會接著錄，不會從頭來
        self.final_q: list[Job] = []
        self.lock = threading.Lock()
        self.model = None
        self.device = None
        self.last_used = 0.0
        self.live_every = LIVE_EVERY  # 依照實際聽寫花多久自動調整
        self.status = {"state": "idle", "detail": "", "progress": None, "device": None}
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._worker_loop, daemon=True).start()

    # ───── 給伺服器用 ─────
    def cfg(self):
        return self.config.get("ai", {})

    def get(self, key):
        return self.store.get(key)

    def forget(self, key):
        if self.store.pop(key, None) is not None:
            self._save()

    def set_job(self, track=None, key=None, plain=None, improve=False):
        if track and not (MIN_LEN <= float(track.get("duration") or 0) <= MAX_LEN):
            track = None  # 不是歌的長度 (直播、長影片、Podcast) 就不聽，也避免占用大量記憶體
        if track and not self.cfg().get("enabled", True):
            track = None
        with self.lock:
            old = self.job
            if old and track and old.key == key:
                old.track_id = track["track_id"]
                return
            if old:
                log(f"switch away from {old.title} ({old.coverage():.0%} heard)")
                if not old.done and old.coverage() >= FINAL_COVERAGE:
                    self.final_q.append(old)  # 聽夠了：拿去整首精修
            job = None
            if track:
                job = self.jobs.pop(key, None)
                dur = float(track.get("duration") or 0)
                if job is None or job.done or abs(job.duration - max(10.0, dur)) > 2:
                    job = Job(track, key, plain)
                    job.improve = improve
                    log(f"start listening: {job.title}")
                else:
                    job.track_id = track["track_id"]
                    job.plain = job.plain or plain
                    log(f"resume listening: {job.title} ({job.coverage():.0%} already heard)")
                self.jobs[key] = job  # 放到最後 = 最近使用
                while len(self.jobs) > 3:
                    self.jobs.pop(next(iter(self.jobs)))
            self.job = job
        if self.job:
            self._set_status("listening" if self.model else "loading",
                             "正在對時…" if plain else "正在聽寫…")

    def _set_status(self, state, detail="", progress=None):
        self.status = {"state": state, "detail": detail, "progress": progress, "device": self.device}

    def _save(self):
        try:
            self.store_path.write_text(json.dumps(self.store, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log("save", e)

    def _pos(self):
        t = self.state.get("track")
        if not t:
            return None, None
        p = t["position"] + ((time.time() - t["ts"]) if t["playing"] else 0)
        return t, p

    # ───── 錄音 ─────
    def _capture_loop(self):
        """優先只錄播放器那個程式的聲音 (不會錄到遊戲、Discord)；舊版 Windows 才退回錄整台電腦。"""
        src = None        # ("proc", pid, ProcessLoopback) 或 ("sys", device_name, (ctx, rec))
        proc_ok = True    # 這台電腦支不支援單一程式錄音
        last_check = 0
        while True:
            try:
                job = self.job
                t, _ = self._pos()
                paused_fs = (self.cfg().get("pause_fullscreen", True) and (self.config.get("game") or {}).get("enabled", True)
                             and fullscreen_app_running())
                mixed = bool(job and t) and self._mixed(t, src)
                active = (job and t and t["track_id"] == job.track_id and t["playing"]
                          and self.cfg().get("enabled", True) and not paused_fs and not mixed)
                if not active:
                    src = self._close_src(src)
                    if job and paused_fs and self.status["state"] in ("listening", "paused"):
                        self._set_status("paused", "全螢幕遊戲執行中，AI 暫停")
                    elif job and mixed and self.status["state"] in ("listening", "paused"):
                        self._set_status("paused", "同時有影片在播放，AI 暫停 (不然會聽到台詞)")
                    time.sleep(0.3)
                    continue
                if self.status["state"] == "paused":
                    self._set_status("listening", "正在聽…")
                now = time.time()
                if src is None or now - last_check > 5:
                    last_check = now
                    want = None
                    if proc_ok:
                        from procloop import exe_for_app, root_pid
                        exe = exe_for_app(t.get("app", ""))
                        pid = root_pid(exe) if exe else None
                        if pid:
                            want = ("proc", pid)
                    if want is None:
                        import soundcard as sc
                        want = ("sys", sc.default_speaker().name)
                    if src is None or src[:2] != want:
                        src = self._close_src(src)
                        try:
                            src = self._open_src(want)
                        except Exception as e:
                            if want[0] == "proc":
                                log("process capture unavailable, using system audio:", repr(e))
                                proc_ok = False
                                continue
                            raise
                if src[0] == "proc":
                    data = src[2].read(0.25)
                else:
                    data = src[2][1].record(numframes=SR // 4)[:, 0].astype(np.float32)
                if not len(data):
                    continue
                t, pos = self._pos()
                if job is self.job and t and t["track_id"] == job.track_id and pos is not None:
                    job.write(data, pos)
            except Exception as e:
                log("capture", repr(e))
                src = self._close_src(src)
                time.sleep(1)

    def _mixed(self, t, src):
        """錄到的聲音會不會混到別的影片：錄整台電腦時有別的程式在播，或同一個瀏覽器有別的分頁在播影片。"""
        others = [(app, kind) for app, title, kind in (self.state.get("playing_apps") or [])
                  if (app, title) != (t.get("app"), t.get("title"))]
        if not others:
            return False
        if src is not None and src[0] == "sys":
            return True
        from procloop import exe_for_app
        mine = (exe_for_app(t.get("app", "")) or "").lower()
        return any(kind != "music" and (exe_for_app(app) or "").lower() == mine for app, kind in others if mine)

    @staticmethod
    def _open_src(want):
        if want[0] == "proc":
            from procloop import ProcessLoopback
            log("capturing only process", want[1])
            return ("proc", want[1], ProcessLoopback(want[1]).start())
        import soundcard as sc
        mic = sc.get_microphone(id=str(want[1]), include_loopback=True)
        ctx = mic.recorder(samplerate=SR, channels=1, blocksize=1024)
        log("capturing system audio from", want[1])
        return ("sys", want[1], (ctx, ctx.__enter__()))

    @staticmethod
    def _close_src(src):
        if src:
            try:
                if src[0] == "proc":
                    src[2].close()
                else:
                    src[2][0].__exit__(None, None, None)
            except Exception:
                pass
        return None

    # ───── 模型 ─────
    def _load_model(self):
        import ctranslate2
        def prog(label, p, done, total, speed=0.0, eta=None):
            extra = ""
            if speed:
                extra += f"・{speed / 1e6:,.1f} MB/s"
            if eta is not None:
                extra += f"・約剩 {fmt_eta(eta)}"
            self._set_status("downloading", f"第一次使用，下載{label}… {done / 1e6:,.0f} / {total / 1e6:,.0f} MB{extra}"
                             "（只要下載一次，中斷了下次會接著下載）", p)
        path = ensure_model(prog)
        from faster_whisper import WhisperModel
        if ctranslate2.get_cuda_device_count() > 0:
            try:
                cdir = ensure_cuda(prog)
                os.add_dll_directory(str(cdir))
                os.environ["PATH"] = str(cdir) + os.pathsep + os.environ.get("PATH", "")
                self._set_status("loading", "載入 AI 模型 (GPU)…")
                m = WhisperModel(str(path), device="cuda", compute_type="float16")
                list(m.transcribe(np.zeros(SR * 2, np.float32), beam_size=1)[0])  # 暖機：第一次會編譯 GPU 程式
                self.device = "GPU"
                return m
            except Exception as e:
                log("cuda failed, fallback to CPU:", repr(e))
        self._set_status("loading", "載入 AI 模型 (CPU)…")
        m = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=max(2, (os.cpu_count() or 4) // 2))
        self.device = "CPU"
        return m

    def _transcribe(self, audio, job, words=False, beam=3, min_logprob=-1.2):
        # 註：試過把歌名當 hotwords 提示，反而讓 AI 跳過大段歌詞、冒出幻聽，所以不用
        segs, info = self.model.transcribe(
            audio, beam_size=beam, language=job.lang, word_timestamps=words, vad_filter=False,
            condition_on_previous_text=False, no_speech_threshold=0.6, compression_ratio_threshold=2.2)
        segs = list(segs)
        if job.lang is None and info.language_probability > 0.7:
            job.lang = info.language  # 鎖定語言，後面比較穩
        out = []
        for s in segs:
            txt = s.text.strip()
            if not txt or HALLU.search(txt) or (s.no_speech_prob > 0.6 and s.avg_logprob < -0.8) \
                    or s.avg_logprob < min_logprob:
                continue
            txt = re.sub(r"^[-–—\s]+", "", txt)
            out.append((s, txt))
        return out

    # ───── 主工作 ─────
    def _worker_loop(self):
        while True:
            try:
                self._step()
            except Exception as e:
                log("worker", repr(e))
                self._set_status("error", f"AI 發生錯誤：{e}")
                time.sleep(5)
            time.sleep(0.3)

    def _step(self):
        job = self.job
        if (job or self.final_q) and self.model is None:
            try:
                self.model = self._load_model()
            except Exception as e:
                log("load", repr(e))
                self._set_status("error", f"AI 模型載入失敗：{e}")
                time.sleep(30)
                return
            self._set_status("listening", "正在聽…")
        if self.model is not None:
            self.last_used = time.time() if (job or self.final_q) else self.last_used
            if not job and not self.final_q and time.time() - self.last_used > IDLE_UNLOAD:
                self.model = None  # 釋放顯示卡記憶體
                gc.collect()
                self._set_status("idle")
                return

        # 1) 精修：換歌後或歌快結束時，把整首重新聽一次
        if self.final_q:
            self._finalize(self.final_q.pop(0))
            return
        if not job or self.model is None:
            return
        t, pos = self._pos()
        if not t or t["track_id"] != job.track_id:
            return
        if pos >= job.duration - 1.5 and not job.done and job.coverage() >= FINAL_COVERAGE:
            self._finalize(job)
            return

        # 2) 即時聽寫 (只有完全沒歌詞時)
        if job.plain or job.improve or not t["playing"] or time.time() - job.last_live < self.live_every:
            return
        end = pos - 0.15
        start = max(0.0, end - LIVE_WINDOW)
        if end - start < 3 or job.coverage(start, end) < 0.6:
            return
        job.last_live = time.time()
        audio = job.audio[int(start * SR): int(end * SR)]
        if float(np.sqrt((audio ** 2).mean())) < 0.004:
            return  # 幾乎沒聲音
        self._set_status("listening", "正在聽寫…")
        t0 = time.perf_counter()
        segs = self._transcribe(audio, job, beam=2)
        took = time.perf_counter() - t0
        # 聽寫花的時間 × 3 當作間隔 (留空給遊戲和其他程式)；顯示卡通常 0.2～0.4 秒 → 每 1 秒一次
        self.live_every = max(LIVE_FAST, min(LIVE_EVERY, took * 3)) if self.device == "GPU" else LIVE_EVERY
        keep_before = start + 1.5 if start > 0 else 0.0
        new = []
        for s, txt in segs:
            if start + s.start < keep_before:
                continue
            ln = {"t": round(start + s.start, 2), "text": txt}
            if start + s.end > end - LIVE_TENT:
                ln["tent"] = True  # 可能還沒唱完，下一次聽寫會補完 / 修正
            new.append(ln)
        job.live = [l for l in job.live if l["t"] < keep_before] + new
        if job is self.job:
            self.on_update(job.track_id, job.key, {"source": "AI 聽寫中", "synced": True, "lines": list(job.live),
                                                   "ai": "live"}, True)

    def _finalize(self, job: Job):
        if job.done:
            return
        cov = job.coverage()
        if cov < FINAL_COVERAGE:
            log(f"not enough yet ({cov:.0%} heard): {job.title}")
            return  # 不標記完成：之後再播到這首還能接著錄
        job.done = True
        self._set_status("listening", "整首精修中…")
        n = int(min(job.max_pos, job.duration) * SR)
        audio = job.audio[:n]
        if os.environ.get("NPL_DUMP"):  # 開發用：存下錄音，可以離線反覆調整
            dump = LOCAL / "debug"
            dump.mkdir(parents=True, exist_ok=True)
            np.save(dump / (re.sub(r"[^\w\-]+", "_", job.title)[:60] + ".npy"), audio)
        if job.lang is None:  # 用歌曲中段判斷語言 (前奏常常沒人聲，會判錯)
            mid = audio[max(0, n // 2 - SR * 15): n // 2 + SR * 15]
            try:
                lang, prob, _ = self.model.detect_language(mid)
                if prob > 0.5:
                    job.lang = lang
            except Exception as e:
                log("detect_language", repr(e))
        segs = self._transcribe(audio, job, words=True, beam=5, min_logprob=-1.4)
        if job.plain:
            words = [(w.start, w.word) for s, _ in segs for w in (s.words or [])]
            lines = align_lines(job.plain, words, self.norm)
            if not lines:
                return
            result = {"source": "AI 對時", "synced": True, "lines": lines, "ai": "aligned"}
        else:
            lines = fill_gaps(split_lines(segs), job.live)
            if len(lines) < 3:
                log("final: too few lines", job.title)
                return
            result = {"source": "AI 聽寫", "synced": True, "lines": lines, "ai": "transcribed"}
        prev = self.store.get(job.key)
        if prev and prev.get("heard", 1.0) >= cov - 0.03:
            log(f"keep previous result for {job.title} (heard {prev.get('heard', 1.0):.0%} vs {cov:.0%})")
            return
        result["heard"] = round(cov, 2)
        self.store[job.key] = result
        self._save()
        log(f"saved {result['source']} for {job.title} ({len(lines)} lines, {cov:.0%} heard)")
        self.on_update(job.track_id, job.key, result, False)
        self._set_status("listening" if self.job else "idle", "完成")
