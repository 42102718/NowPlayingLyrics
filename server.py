"""
Hummi 哼哼 (舊名 Now Playing Lyrics) — 讀取 Windows 媒體工作階段 (Spotify / YouTube Music / 瀏覽器)，
自動抓同步歌詞 (LRCLIB → 網易雲 備援)，透過 WebSocket 即時推送給網頁 UI。

執行：python server.py   然後打開 http://localhost:8765
"""
import asyncio
import os
import sys

FROZEN = getattr(sys, "frozen", False)  # 打包成 .exe 時為 True
# 程式資源 (index.html)：打包後在 _MEIPASS，原始碼執行時在本資料夾
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
# 使用者資料 (設定、紀錄、瀏覽器快取)：打包後放 %APPDATA%\NowPlayingLyrics
DATA_DIR = (os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NowPlayingLyrics")
            if FROZEN else os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:  # 沒有主控台 (pythonw / .exe)：輸出寫到 app.log 方便除錯
    sys.stdout = sys.stderr = open(os.path.join(DATA_DIR, "app.log"), "w", encoding="utf-8", buffering=1)
import io
import json
import re
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web
from opencc import OpenCC
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)
from winrt.windows.storage.streams import DataReader

ROOT = Path(RES_DIR)
DATA = Path(DATA_DIR)
PORT = int(os.environ.get("PORT", 8765))
POLL_INTERVAL = 0.2  # 秒
VERSION = "1.7.3"
APP_NAME = "Hummi 哼哼"
UA = f"Hummi/{VERSION} (https://github.com/42102718/NowPlayingLyrics)"

PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING

# ───────────────────────── 設定檔 ─────────────────────────
CONFIG_PATH = DATA / "config.json"
DEFAULT_CONFIG = {
    "offset": 0.0,
    "notch": True,   # 螢幕頂端的小膠囊 (跟主視窗分開，一直都在)
    "ui_zoom": 100,    # 介面大小 % (字、按鈕一起放大，給看不清楚的人)
    "theme": "night",  # 主視窗風格：night 夜光 / dawn 晨霧 / mono 極簡 / neon 霓虹 / paper 紙本
    "auto_pause": {"lock": True, "unplug": True, "resume": True},  # 鎖定 / 拔耳機時暫停，回來接著播
    "taskbar_progress": True,  # 工作列圖示上顯示播放進度
    "hotkeys": True,     # 全域快捷鍵 Ctrl+Alt+… (遊戲裡也能切歌、調音量)
    "music_only": True,  # 只偵測音樂 App：不讀瀏覽器、抖音、影片播放器 (開台時不會露出其他分頁的標題)
    "notch_opts": {
        "battery": True,    # 藍牙裝置 / 筆電電量
        "peek": True,       # 換歌時彈出封面和歌名
        "spectrum": True,   # 真實頻譜 + 邊緣光暈 (只錄播放器的聲音)
        "gestures": True,   # 滾輪調播放器音量、觸控板左右滑切歌
    },
    "folia": {"enabled": False},  # 把歌和歌詞送到 Folia 舞台 (唱歌台用)
    "font": "",  # 字體家族 (英文名)；空白 = 預設 (Inter + 微軟正黑體)
    "ai": {
        "enabled": True,          # 找不到同步歌詞時用 AI 聽寫 / 對時
        "pause_fullscreen": True,  # 全螢幕遊戲時暫停 (避免把遊戲聲音當歌詞)
    },
    "background": {
        "mode": "album",      # album / color / gradient / image
        "color1": "#2a1f5c",
        "color2": "#a0346e",
        "image": "",          # DATA/backgrounds 裡的檔名
        "blur": 90,           # px
        "dim": 35,            # 0~90 %
        "blobs": True,        # 流動光暈
    },
    "overlay": {
        "enabled": True,
        "preset": "bottom-left",   # bottom-left / bottom-center / bottom-right / top-center / custom
        "x": 0, "y": 0,            # custom 時的位置 (實體像素)
        "align": "left",           # custom 時的對齊
        "font_size": 46,
        "width": 1200,
        "bg_opacity": 0,           # 0~100
        "show_translation": True,
        "show_next": True,
        "color": "album",          # white / album
        "auto_hide": True,         # 主視窗或舞台開著時自動隱藏 (避免同時好幾份歌詞)
        "monitor": "primary",      # primary / active (跟著正在用的螢幕) / mouse / 螢幕裝置名稱
        "fullscreen": "dim",       # 全螢幕遊戲時：normal 照常 / dim 變淡 / hide 隱藏
    },
    "notch_monitor": "primary",    # 膠囊在哪個螢幕 (同上)
    "wallpaper": {"enabled": False, "monitor": "primary"},
    "share": {"folder": "", "name": ""},
    "chat": {"channel": "", "token": "", "bot": "", "commands": "!song !歌名 !這什麼歌 !歌"},  # 直播聊天室點歌指令 (Twitch)  # 和朋友共享歌詞的雲端資料夾、顯示的名字  # 桌布模式：舞台放在桌面圖示後面
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg["offset"] = float(saved.get("offset", 0))
        cfg["notch"] = bool(saved.get("notch", True))
        try:
            cfg["ui_zoom"] = max(70, min(200, int(saved.get("ui_zoom", 100))))
        except (TypeError, ValueError):
            cfg["ui_zoom"] = 100
        cfg["theme"] = saved.get("theme") if saved.get("theme") in ("night", "dawn", "mono", "neon", "paper") else "night"
        cfg["music_only"] = bool(saved.get("music_only", True))
        cfg["hotkeys"] = bool(saved.get("hotkeys", True))
        cfg["taskbar_progress"] = bool(saved.get("taskbar_progress", True))
        cfg["notch_monitor"] = str(saved.get("notch_monitor", "primary") or "primary")
        ch = saved.get("chat") or {}
        cfg["chat"].update({k: str(ch.get(k) or "") for k in ("channel", "token", "bot") if k in ch})
        if ch.get("commands"):
            cfg["chat"]["commands"] = str(ch["commands"])[:200]
        sh = saved.get("share") or {}
        cfg["share"] = {"folder": str(sh.get("folder") or ""), "name": str(sh.get("name") or "")[:20]}
        wp = saved.get("wallpaper") or {}
        cfg["wallpaper"] = {"enabled": bool(wp.get("enabled", False)), "monitor": str(wp.get("monitor") or "primary")}
        ap = saved.get("auto_pause") or {}
        cfg["auto_pause"] = {k: bool(ap.get(k, True)) for k in ("lock", "unplug", "resume")}
        if isinstance(saved.get("window"), dict):
            cfg["window"] = saved["window"]
        cfg["notch_opts"].update({k: bool(v) for k, v in saved.get("notch_opts", {}).items() if k in cfg["notch_opts"]})
        cfg["font"] = str(saved.get("font", "") or "")
        cfg["background"].update({k: v for k, v in saved.get("background", {}).items() if k in cfg["background"]})
        cfg["ai"].update({k: v for k, v in saved.get("ai", {}).items() if k in cfg["ai"]})
        cfg["overlay"].update({k: v for k, v in saved.get("overlay", {}).items() if k in cfg["overlay"]})
        dv = saved.get("devices") or {}
        cfg["devices"] = {"low": int(dv.get("low", 15)), "tray": bool(dv.get("tray", True)),
                          "hidden": [str(x) for x in dv.get("hidden", [])][:50]}
        # 舊版 (1.4 以前) 沒有 seen_version：算是「更新上來的」，要給他看新功能
        cfg["seen_version"] = str(saved.get("seen_version", "1.4"))
        cfg["auto_update"] = bool(saved.get("auto_update", True))
        cfg["folia"]["enabled"] = bool((saved.get("folia") or {}).get("enabled", False))
        cfg["update_notified"] = str(saved.get("update_notified", ""))
    except Exception:
        cfg["seen_version"] = VERSION  # 第一次安裝：不用跳「新功能」
    return cfg


def save_config():
    try:
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("[config]", e)


config = load_config()
# 給浮動視窗的執行期旗標 (不存檔)
runtime = {"unlocked": False, "cfg_version": 0}


def config_changed():
    runtime["cfg_version"] += 1
    save_config()


# ───────────────────────── 繁體轉換 ─────────────────────────
_cc = OpenCC("s2tw")
KANA = re.compile(r"[぀-ヿ]")


def to_trad(s: str) -> str:
    # 含假名的是日文，不要轉 (避免把日文漢字改掉)
    if not s or KANA.search(s):
        return s
    return _cc.convert(s)


def traditionalize(result):
    for l in result.get("lines", []):
        l["text"] = to_trad(l["text"])
        if l.get("tr"):
            l["tr"] = to_trad(l["tr"])
        if l.get("w"):  # 逐字：整句一起轉 (詞的轉換比較準)，再照原本每個字的長度切回去
            joined = "".join(s[2] for s in l["w"])
            conv = to_trad(joined)
            if len(conv) == len(joined):
                i = 0
                for s in l["w"]:
                    n = len(s[2])
                    s[2] = conv[i:i + n]
                    i += n
    return result


# ───────────────────────── 來源辨識 ─────────────────────────
def classify_source(app_id: str, title: str, artist: str) -> str:
    a = app_id.lower()
    if "spotify" in a:
        return "Spotify"
    if "youtube music" in a or "ytmusic" in a or "cinhimbnkk" in a:  # cinhimbnkk…：YouTube Music 的 Chrome App
        return "YouTube Music"
    if any(b in a for b in ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi")):
        return "瀏覽器"
    return app_id.split("!")[0].split("\\")[-1].replace(".exe", "") or "未知"


# 瀏覽器裡播的不一定是音樂：Shorts / TikTok / 一般影片不該去找歌詞或讓 AI 聽寫
SHORT_TAG = re.compile(r"#(?:shorts?|tiktok|reels?|抖音)(?![a-z])", re.I)
MUSIC_HINT = re.compile(
    r"\s[-–—]\s|\b(?:m/?v|official|audio|lyrics?|cover|remix|live|acoustic|instrumental|karaoke|ost|"
    r"sped\s*up|slowed|nightcore)\b|歌詞|歌词|翻唱|音樂|音乐|歌曲|主題曲|主题曲|插曲|片尾曲|片頭曲|伴奏|純音樂|"
    r"가사|커버|歌ってみた|カバー", re.I)
MUSIC_CHANNEL = re.compile(r"\s-\sTopic$|VEVO$|\bmusic\b|\brecords?\b|音樂|音乐|唱片", re.I)


SHORT_APPS = ("douyin", "tiktok", "kuaishou", "instagram", "xiaohongshu")


def is_music_app(app_id, src):
    """「只偵測音樂 App」開著時，只有這些會被讀：Spotify、YouTube Music 桌面版、已知的音樂播放器。"""
    a = (app_id or "").lower()
    return src in ("Spotify", "YouTube Music") or any(m in a for m in MUSIC_APPS)

MUSIC_APPS = ("applemusic", "apple music", "itunes", "zunemusic", "tidal", "deezer", "amazon music", "amazonmusic",
              "cloudmusic", "netease", "qqmusic", "kugou", "kuwo", "foobar", "musicbee", "aimp", "winamp",
              "kkbox", "soundcloud", "pandora", "audirvana", "dopamine", "plexamp", "cider")


def media_kind(track) -> str:
    """music = 當成歌處理；video = 一般影片 (只在歌名、歌手都對得上時顯示歌詞，不讓 AI 聽)；short = 短影片 (完全不處理)。"""
    src, app = track.get("source"), (track.get("app") or "").lower()
    if src in ("Spotify", "YouTube Music"):
        return "music"
    if any(x in app for x in SHORT_APPS):
        return "short"
    if any(x in app for x in MUSIC_APPS):
        return "music"
    if src != "瀏覽器":
        return "video"  # 不認識的 App (影音平台、Discord…)：當一般影片
    title, artist = track.get("title") or "", track.get("artist") or ""
    dur = float(track.get("duration") or 0)
    if SHORT_TAG.search(title) or SHORT_TAG.search(artist) or 0 < dur < 60:
        return "short"
    if MUSIC_HINT.search(title) or MUSIC_CHANNEL.search(artist):
        return "music"
    return "video"


def source_priority(src: str) -> int:
    return {"Spotify": 3, "YouTube Music": 3, "瀏覽器": 2}.get(src, 1)


# ───────────────────────── 標題清理 ─────────────────────────
NOISE = re.compile(
    r"[\(\[【（「]?\s*(official\s*(music\s*)?(video|audio|mv|lyric[s]?\s*video)|"
    r"lyric[s]?\s*video|lyrics?|mv|m/v|audio|hd|4k|動態歌詞|歌詞版|官方版?|完整版|"
    r"官方\s*mv|高音質|中字|lyric)\s*[\)\]】）」]?",
    re.I,
)


def clean_artist(artist: str) -> str:
    artist = re.sub(r"\s*-\s*Topic$", "", artist or "", flags=re.I)
    artist = re.sub(r"VEVO$", "", artist, flags=re.I)
    return artist.strip()


# YouTube 影片標題的雜訊：【動態歌詞】[가사/해석]、#hashtag、表情符號、引號裡的歌詞摘句
TAG_BLOCK = re.compile(r"[【\[][^】\]]*(?:[】\]]|$)")
QUOTE_BLOCK = re.compile(r"[「『“\"《]([^」』”\"》]*)(?:[」』”\"》]|$)")
HASHTAG = re.compile(r"(?:^|\s)#\S+")
EMOJI = re.compile(r"[\U0001F000-\U0001FFFF☀-➿⬀-⯿️‍♪♫♬]")
FEAT = re.compile(r"\s*(?:[\*/]+\s*(?:with|feat\.?|ft\.?)\b.*|[\(（]\s*(?:feat\.?|ft\.?|with)\b[^\)）]*[\)）]"
                  r"|\s(?:feat\.?|ft\.?)\s.*)$", re.I)
PAREN = re.compile(r"\s*[\(（][^\)）]*[\)）]")


def _quote_repl(m):
    # 短的引號內容通常就是歌名 (周杰倫「晴天」)，長的或有空白、標點的是歌詞摘句
    inner = m.group(1).strip()
    if inner and len(inner) <= 12 and not re.search(r"[\s,，。！？!?、]", inner):
        return f" {inner} "
    return " "


def clean_title(title: str) -> str:
    t = TAG_BLOCK.sub(" ", title or "")
    t = QUOTE_BLOCK.sub(_quote_repl, t)
    t = HASHTAG.sub(" ", t)
    t = EMOJI.sub(" ", t)
    t = NOISE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|｜/*")
    return t


def _primary_artist(a: str) -> str:
    a = FEAT.sub("", a or "")
    return re.split(r"\s*(?:/|、|&|,|，|\bx\b|×)\s*", a, maxsplit=1)[0].strip() or a.strip()


def title_variants(title: str, artist: str):
    """產生幾組 (歌名, 歌手) 嘗試搜尋。"""
    t, a = clean_title(title), clean_artist(artist)
    # 「分明在 IG 聽過? : Unstable - Tom Frane」→ 冒號前面是影片標語
    m = re.search(r"\s[:：]\s|:\s", t)
    if m and " - " in t[m.end():]:
        t = t[m.end():].strip()
    out = [(t, a)]
    pairs = []
    for sep in ("｜", "|"):  # 「歌手｜歌名」或「歌名｜頻道」
        if sep in t:
            left, right = [x.strip() for x in t.split(sep, 1)]
            pairs.append((left, right))
            out.append((left, a))
    body = pairs[0][0] if pairs else t
    if " - " in body:  # YouTube 常見「歌手 - 歌名」
        pairs.append(tuple(x.strip() for x in body.split(" - ", 1)))
    elif re.search(r"\S-\S", body) and not pairs:  # 「416RevoltK1D-只愛你在星期五」
        pairs.append(tuple(x.strip() for x in body.split("-", 1)))
    for left, right in pairs:
        right, left = FEAT.sub("", right), FEAT.sub("", left)
        bare = PAREN.sub("", right).strip()  # 括號裡常是「可轉伴奏」之類的說明，先用沒括號的搜
        out += [(bare, left), (right, left), (bare, _primary_artist(left)), (left, right)]
    if pairs:  # 拆得出「歌手 - 歌名」時，整串標題 + 頻道名稱放最後 (容易配到別人上傳的翻唱)
        out.append(out.pop(0))
    # 去括號內容：「day after day (日復一日)」→ 兩種都試
    for tt, aa in list(out):
        mm = re.match(r"^(.*?)\s*[\(（](.+?)[\)）]\s*$", tt)
        if mm:
            out += [(mm.group(1).strip(), aa), (mm.group(2).strip(), aa)]
    seen, res = set(), []
    for x in out:
        x = (x[0].strip(" -–—/*"), x[1].strip(" -–—/*"))
        if x[0] and x not in seen:
            seen.add(x)
            res.append(x)
    return res[:8]


def title_matches(query: str, cand: str) -> int:
    """候選歌名跟要找的歌名像不像：2 = 一樣，1 = 互相包含且長度差不多，0 = 不是同一首。"""
    q, c = _norm(query), _norm(cand)
    if not q or not c:
        return 0
    if q == c or _norm(PAREN.sub("", cand)) == q or _norm(PAREN.sub("", query)) == c:
        return 2
    c2 = _norm(PAREN.sub("", cand)) or c
    short, long_ = sorted((q, c2), key=len)
    if short in long_ and len(short) >= 0.5 * len(long_):
        return 1
    return 0


def artist_matches(query: str, cand: str) -> bool:
    q, c = _norm(query), _norm(cand)
    if not q or not c:
        return False
    if q == c or (min(len(q), len(c)) >= 2 and (q in c or c in q)):
        return True
    def parts(s):
        return {_norm(x) for x in re.split(r"\s*(?:/|、|&|,|，|\bx\b|×|\band\b|和)\s*", s or "", flags=re.I)} - {""}
    return bool(parts(query) & parts(cand))


# ───────────────────────── LRC 解析 ─────────────────────────
TS = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


def parse_lrc(text: str):
    lines = []
    for raw in (text or "").splitlines():
        stamps = TS.findall(raw)
        body = TS.sub("", raw).strip()
        for mm, ss in stamps:
            lines.append({"t": int(mm) * 60 + float(ss.replace(":", ".")), "text": body})
    lines.sort(key=lambda l: l["t"])
    # 移除網易雲的「作詞/作曲」中繼資料行 (保留在第一句之前的，看起來比較乾淨就留)
    return lines


def merge_translation(lines, tlines):
    if not tlines:
        return lines
    tmap = {round(l["t"], 1): l["text"] for l in tlines if l["text"]}
    for l in lines:
        tr = tmap.get(round(l["t"], 1))
        if tr and tr != l["text"]:
            l["tr"] = tr
    return lines


# ───────────────────────── 歌詞來源 ─────────────────────────
CREDIT = re.compile(r"^(作词|作詞|作曲|编曲|編曲|制作人|製作人|监制|監製|混音|母带|母帶|和声|和聲|录音|錄音|吉他|贝斯|貝斯|鼓|"
                    r"出品|发行|發行|统筹|統籌|企划|企劃|策划|策劃|营销|營銷|宣传|宣傳|监修|監修|弦乐|弦樂|配唱|"
                    r"词|詞|曲|OP|SP|Lyricist|Lyrics|Composer|Arranger|Producer|Mixing|Mastering|Recording)"
                    r"[^:：]{0,24}[:：]", re.I)  # 允許「監製Executive Producer：」這種中英混合
SOURCES = {"lrclib": "LRCLIB", "netease": "網易雲音樂", "kugou": "酷狗音樂", "qq": "QQ音樂",
           "lyricsplus": "Apple Music (LyricsPlus)", "custom": "自訂"}
LYRICSPLUS_HOSTS = ["https://lyricsplus.prjktla.my.id", "https://lyricsplus.binimum.org"]
QQ_HEADERS = {"Referer": "https://y.qq.com/"}
NETEASE_HEADERS = {"Referer": "https://music.163.com/"}


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", to_trad(s or "").lower())


# 「短標籤 : 名字」的形狀 (編程 : xxx、Guitar : xxx…)，關鍵字清單永遠列不完，所以用形狀判斷
CREDIT_SHAPE = re.compile(r"^[^:：\s][^:：]{0,15}\s*[:：]\s*\S")


def has_real_lyrics(lines) -> bool:
    """扣掉製作資訊後至少要有 3 句，才算真的有歌詞。"""
    real = [l for l in clean_lines(lines) if l["text"].strip()]
    return len(real) >= 3


def clean_lines(lines, title="", artist="", *more):
    """去掉「作詞：xxx」這類製作資訊，以及開頭的「歌名 - 歌手」標題行。
    more：其他可能的 (歌名, 歌手)，例如手動選的那首歌。"""
    pairs = [(_norm(t), _norm(a)) for t, a in [(title, artist), *zip(more[::2], more[1::2])]]
    out = []
    started = False
    for i, l in enumerate(lines):
        txt = l["text"]
        if CREDIT.match(txt) or "未經" in txt or "未经" in txt:
            continue
        if not started and CREDIT_SHAPE.match(txt):  # 開頭的「xx : 名字」都是製作資訊
            continue
        if txt.strip():
            started = True
        if i < 4 and txt:
            n = _norm(txt)
            if any(t and t in n and (not a or a in n) for t, a in pairs):
                continue
            if " - " in txt and l.get("t") is not None and l["t"] < 3:  # 「歌名 - 歌手」標題行
                continue
        out.append(l)
    return out


class Lyrics:
    def __init__(self, override_path):
        self.cache: dict[str, dict] = {}
        self.lp_cache: dict = {}
        self.lp_down: dict = {}
        self.http: aiohttp.ClientSession | None = None
        self.override_path = override_path
        try:
            self.overrides = json.loads(Path(override_path).read_text(encoding="utf-8"))
        except Exception:
            self.overrides = {}

    @staticmethod
    def key(title, artist):
        return f"{title}\u0000{artist}"

    async def session(self):
        if self.http is None:
            self.http = aiohttp.ClientSession(
                headers={"User-Agent": UA}, timeout=aiohttp.ClientTimeout(total=8)
            )
        return self.http

    # ───── 手動指定 (記住每首歌的選擇) ─────
    def set_override(self, title, artist, result):
        k = self.key(title, artist)
        self.overrides[k] = result
        self.cache[k] = result
        self._save_overrides()

    def clear_override(self, title, artist):
        k = self.key(title, artist)
        self.overrides.pop(k, None)
        self.cache.pop(k, None)
        self._save_overrides()

    def _save_overrides(self):
        try:
            Path(self.override_path).write_text(json.dumps(self.overrides, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print("[overrides]", e)

    # ───── 自動尋找 ─────
    async def find(self, title, artist, album, duration, need_artist=False):
        k = self.key(title, artist)
        if k in self.overrides:
            return {**self.overrides[k], "override": True}
        if k in self.cache:
            return self.cache[k]
        result = await self._lrclib_exact(clean_title(title), clean_artist(artist), album, duration)
        if not (result and result["synced"]):
            for t, a in title_variants(title, artist):
                cands = self.rank(await self.search(t, a, duration=duration), t, a, duration,
                                  need_artist=need_artist or (bool(a) and a != clean_artist(artist)))
                for c in cands[:4]:
                    r = await self.fetch(c)
                    if r and (r["synced"] or not result):
                        result = r
                        if r["synced"]:
                            break
                if result and result["synced"]:
                    break
        result = result or {"source": None, "synced": False, "lines": []}
        if result["lines"]:
            result["lines"] = clean_lines(result["lines"], title, artist)
        result = traditionalize(result)
        self.cache[k] = result
        if not result["synced"] and AI is not None:
            saved = AI.get(k)
            if saved:  # 之前 AI 聽寫 / 對時過的結果
                return traditionalize({**saved, "lines": [dict(l) for l in saved["lines"]]})
        if not result["synced"] and SHARE is not None:
            shared = await asyncio.to_thread(SHARE.get, title, artist, duration)
            if shared and shared["synced"] and shared["lines"]:  # 朋友的 AI 聽過了：直接用，不用再聽一次
                return traditionalize({**shared, "lines": [dict(l) for l in shared["lines"]]})
        return result

    def rank(self, cands, title, artist, duration, strict=True, need_artist=False):
        def score(c):
            s = 0.0
            if duration and c.get("duration"):
                diff = abs(c["duration"] - duration)
                if diff > 10:
                    return None  # 長度差太多，八成不是同一首
                s += diff / 4
            tm = title_matches(title, c.get("title"))
            if tm == 0 and strict:
                return None  # 歌名對不上：寧可找不到 (交給 AI)，也不要顯示別首歌的歌詞
            am = artist_matches(artist, c.get("artist"))
            if strict and artist and not am and (tm == 1 or need_artist):
                return None  # 歌手是從影片標題拆出來的 (很可靠)，歌手不對就是別人的同名歌  # 歌名只是部分相同、歌手也不對
            s += 0 if tm == 2 else 1.5 if tm == 1 else 4
            s += 0 if am else 2 if artist else 0
            s += 0 if c.get("synced") is not False else 3
            return s
        scored = [(score(c), c) for c in cands]
        return [c for sc, c in sorted([x for x in scored if x[0] is not None], key=lambda x: x[0])]

    # ───── 搜尋 (五個來源同時) ─────
    async def search(self, title, artist, limit=8, duration=None):
        res = await asyncio.gather(self._s_lrclib(title, artist, limit), self._s_netease(title, artist, limit),
                                   self._s_kugou(title, artist, limit), self._s_qq(title, artist, limit),
                                   self._s_lyricsplus(title, artist, duration), return_exceptions=True)
        out = []
        for r in res:
            if isinstance(r, list):
                out.extend(r)
            elif isinstance(r, Exception):
                print("[search]", r)
        return out

    async def _s_lrclib(self, title, artist, limit):
        http = await self.session()
        async with http.get("https://lrclib.net/api/search", params={"q": f"{title} {artist}".strip()}) as r:
            if r.status != 200:
                return []
            items = await r.json()
        return [{"source": "lrclib", "id": i["id"], "title": i.get("trackName") or "", "artist": i.get("artistName") or "",
                 "album": i.get("albumName") or "", "duration": i.get("duration") or 0,
                 "synced": bool(i.get("syncedLyrics"))}
                for i in items if not i.get("instrumental") and (i.get("syncedLyrics") or i.get("plainLyrics"))][:limit]

    async def _s_netease(self, title, artist, limit):
        http = await self.session()
        async with http.get("https://music.163.com/api/search/get", headers=NETEASE_HEADERS,
                            params={"s": f"{title} {artist}".strip(), "type": 1, "limit": limit}) as r:
            d = json.loads(await r.text())
        return [{"source": "netease", "id": s["id"], "title": s.get("name") or "",
                 "artist": " / ".join(x.get("name") or "" for x in s.get("artists", [])),
                 "album": (s.get("album") or {}).get("name") or "", "duration": (s.get("duration") or 0) / 1000,
                 "synced": None} for s in ((d.get("result") or {}).get("songs") or [])]

    async def _s_kugou(self, title, artist, limit):
        http = await self.session()
        kw = f"{artist} - {title}" if artist else title
        async with http.get("https://lyrics.kugou.com/search",
                            params={"ver": 1, "man": "yes", "client": "pc", "keyword": kw, "duration": "", "hash": ""}) as r:
            d = json.loads(await r.text())
        return [{"source": "kugou", "id": str(c["id"]), "key": c.get("accesskey") or "", "title": c.get("song") or "",
                 "artist": c.get("singer") or "", "album": "", "duration": (c.get("duration") or 0) / 1000,
                 "synced": True} for c in (d.get("candidates") or [])][:limit]

    async def _s_qq(self, title, artist, limit):
        http = await self.session()
        body = {"comm": {"ct": 19, "cv": 1859, "uin": "0"},
                "req": {"method": "DoSearchForQQMusicDesktop", "module": "music.search.SearchCgiService",
                        "param": {"num_per_page": limit, "page_num": 1, "query": f"{title} {artist}".strip(),
                                  "search_type": 0}}}
        async with http.post("https://u.y.qq.com/cgi-bin/musicu.fcg", data=json.dumps(body)) as r:
            d = json.loads(await r.text())
        songs = (((d.get("req") or {}).get("data") or {}).get("body") or {}).get("song", {}).get("list") or []
        return [{"source": "qq", "id": s_["mid"], "title": s_.get("name") or "",
                 "artist": " / ".join(x.get("name") or "" for x in s_.get("singer", [])),
                 "album": (s_.get("album") or {}).get("name") or "", "duration": s_.get("interval") or 0,
                 "synced": None} for s_ in songs if s_.get("mid")]

    async def _lyricsplus_get(self, title, artist, duration=None):
        """LyricsPlus (Apple Music / QQ / Deezer 的聚合伺服器)。直接回傳歌詞，常常很忙，所以逾時設短一點。"""
        k = (title, artist)
        if k in self.lp_cache:
            return self.lp_cache[k]
        http = await self.session()
        params = {"title": title, "artist": artist}
        if duration:
            params["duration"] = str(round(duration))
        d = None
        now = time.monotonic()
        for host in LYRICSPLUS_HOSTS:
            if self.lp_down.get(host, 0) > now:
                continue  # 剛剛逾時過，先跳過 5 分鐘，免得拖慢其他來源
            try:
                async with http.get(f"{host}/v2/lyrics/get", params=params,
                                    timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status == 404:
                        break  # 伺服器有回應，只是沒有這首
                    if r.status == 200:
                        d = json.loads(await r.text())
                        break
            except Exception as e:
                self.lp_down[host] = now + 300
                print("[lyricsplus]", host, type(e).__name__)
        self.lp_cache[k] = d
        return d

    async def _s_lyricsplus(self, title, artist, duration=None):
        if not artist:
            return []  # 這個服務一定要歌手
        d = await self._lyricsplus_get(title, artist, duration)
        if not d or not d.get("lyrics"):
            return []
        meta = ((d.get("processingTime") or {}).get("selectedSongMetadata")) or {}
        return [{"source": "lyricsplus", "id": f"{title}\u0000{artist}", "q_title": title, "q_artist": artist,
                 "title": meta.get("title") or title, "artist": meta.get("artist") or artist,
                 "album": meta.get("album") or "", "duration": meta.get("duration") or 0,
                 "synced": d.get("type") in ("Line", "Word")}]

    # ───── 下載某一個候選 ─────
    async def fetch(self, c):
        try:
            src = c.get("source")
            http = await self.session()
            if src == "lyricsplus":
                d = await self._lyricsplus_get(str(c.get("q_title") or c.get("title") or ""),
                                               str(c.get("q_artist") or c.get("artist") or ""))
                lines = []
                for l in (d or {}).get("lyrics") or []:
                    if not isinstance(l.get("time"), (int, float)):
                        continue
                    line = {"t": l["time"] / 1000, "text": (l.get("text") or "").strip()}
                    syl = [s for s in l.get("syllabus") or [] if isinstance(s.get("time"), (int, float))]
                    if d.get("type") == "Word" and syl:  # 逐字時間 (給 Folia 舞台做逐字動畫)
                        line["w"] = [[s["time"] / 1000, (s.get("duration") or 0) / 1000, s.get("text") or ""] for s in syl]
                    lines.append(line)
                lines.sort(key=lambda l: l["t"])
                if not has_real_lyrics(lines):
                    return None
                synced = d.get("type") in ("Line", "Word")
                if not synced:
                    lines = [{"t": None, "text": l["text"]} for l in lines]
                return {"source": SOURCES["lyricsplus"], "synced": synced, "lines": lines}
            if src == "lrclib":
                async with http.get(f"https://lrclib.net/api/get/{int(c['id'])}") as r:
                    if r.status != 200:
                        return None
                    return self._from_lrclib(await r.json())
            if src == "netease":
                async with http.get("https://music.163.com/api/song/lyric", headers=NETEASE_HEADERS,
                                    params={"id": int(c["id"]), "lv": -1, "tv": -1}) as r:
                    ld = json.loads(await r.text())
                lines = parse_lrc((ld.get("lrc") or {}).get("lyric") or "")
                if not has_real_lyrics(lines):
                    return None  # 只有作詞作曲資訊，等於沒有歌詞
                tlines = parse_lrc((ld.get("tlyric") or {}).get("lyric") or "")
                return {"source": SOURCES["netease"], "synced": True, "lines": merge_translation(lines, tlines)}
            if src == "qq":
                import html
                async with http.get("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg", headers=QQ_HEADERS,
                                    params={"songmid": str(c["id"]), "format": "json", "nobase64": 1, "g_tk": 5381}) as r:
                    ld = json.loads(await r.text())
                lines = parse_lrc(html.unescape(ld.get("lyric") or ""))
                if not has_real_lyrics(lines):
                    return None  # 只有作詞作曲資訊，等於沒有歌詞
                tlines = parse_lrc(html.unescape(ld.get("trans") or ""))
                return {"source": SOURCES["qq"], "synced": True, "lines": merge_translation(lines, tlines)}
            if src == "kugou":
                async with http.get("https://lyrics.kugou.com/download", params={
                        "ver": 1, "client": "pc", "id": str(c["id"]), "accesskey": str(c.get("key", "")),
                        "fmt": "lrc", "charset": "utf8"}) as r:
                    d = json.loads(await r.text())
                import base64
                text = base64.b64decode(d.get("content") or "").decode("utf-8", "replace")
                lines = parse_lrc(text)
                return {"source": SOURCES["kugou"], "synced": True, "lines": lines} if has_real_lyrics(lines) else None
        except Exception as e:
            print("[fetch]", c.get("source"), e)
        return None

    async def _lrclib_exact(self, title, artist, album, duration):
        http = await self.session()
        try:
            params = {"track_name": title, "artist_name": artist}
            if album:
                params["album_name"] = album
            if duration:
                params["duration"] = str(round(duration))
            async with http.get("https://lrclib.net/api/get", params=params) as r:
                if r.status == 200:
                    return self._from_lrclib(await r.json())
        except Exception as e:
            print("[lrclib]", e)
        return None

    @staticmethod
    def _from_lrclib(d):
        if d.get("syncedLyrics") and has_real_lyrics(parse_lrc(d["syncedLyrics"])):
            return {"source": SOURCES["lrclib"], "synced": True, "lines": parse_lrc(d["syncedLyrics"])}
        if d.get("plainLyrics"):
            return {"source": SOURCES["lrclib"], "synced": False,
                    "lines": [{"t": None, "text": l} for l in d["plainLyrics"].splitlines()]}
        return None

    @staticmethod
    def from_text(text):
        """使用者貼上的歌詞：有時間標籤就當 LRC，沒有就當純文字。"""
        lines = parse_lrc(text)
        if lines:
            return {"source": SOURCES["custom"], "synced": True, "lines": lines}
        plain = [l.strip() for l in (text or "").splitlines()]
        return {"source": SOURCES["custom"], "synced": False, "lines": [{"t": None, "text": l} for l in plain]}


class Media:
    def __init__(self):
        self.mgr = None
        self.pinned: str | None = None  # 使用者手動選的 app id
        self.art: dict[str, bytes] = {}
        self.accent: dict[str, tuple] = {}
        self.current = None  # 目前選中的 WinRT session

    async def init(self):
        self.mgr = await SessionManager.request_async()

    async def snapshot(self):
        sessions = []
        for s in self.mgr.get_sessions():
            try:
                p = await s.try_get_media_properties_async()
                pb = s.get_playback_info()
                tl = s.get_timeline_properties()
            except Exception:
                continue
            app = s.source_app_user_model_id
            src = classify_source(app, p.title, p.artist)
            if config.get("music_only", True) and not is_music_app(app, src):
                continue  # 瀏覽器、抖音、影片播放器：不讀 (隱私，開台時不會露出其他東西)
            playing = pb.playback_status == PLAYING
            pos = tl.position.total_seconds()
            dur = (tl.end_time - tl.start_time).total_seconds()
            # 用 last_updated_time 推算目前真正位置 (Spotify 不會每秒更新)
            if playing:
                lu = tl.last_updated_time
                if lu.year > 1601:
                    pos += (datetime.now(timezone.utc) - lu).total_seconds() * (pb.playback_rate or 1.0)
            if dur > 0:
                pos = max(0.0, min(pos, dur))
            track_id = f"{app}|{p.title}|{p.artist}"
            sessions.append({
                "app": app, "source": src, "playing": playing,
                "title": p.title, "artist": p.artist, "album": p.album_title,
                "position": pos, "duration": dur, "track_id": track_id,
                "_thumb": p.thumbnail, "_session": s, "_raw": tl.position.total_seconds(),
            })
        return sessions

    def pick(self, sessions):
        if not sessions:
            return None
        if self.pinned:
            for s in sessions:
                if s["app"] == self.pinned:
                    return s
        cur = self.mgr.get_current_session()
        cur_app = cur.source_app_user_model_id if cur else None
        return max(sessions, key=lambda s: (s["playing"], source_priority(s["source"]), s["app"] == cur_app))

    async def load_art(self, track_id, thumb):
        if track_id in self.art or thumb is None:
            return
        try:
            stream = await thumb.open_read_async()
            size = stream.size
            reader = DataReader(stream.get_input_stream_at(0))
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            self.art[track_id] = bytes(buf)
            self.accent[track_id] = accent_color(self.art[track_id])
            if len(self.art) > 30:
                self.art.pop(next(iter(self.art)))
        except Exception as e:
            print("[art]", e)


# ───────────────────────── 伺服器 ─────────────────────────
media = Media()
lyrics = Lyrics(str(DATA / "lyrics_overrides.json"))

# ───────────────── 用一句歌詞找歌 ─────────────────
_cc_s = None
_COVER_WORDS = ("live", "remix", "cover", "翻唱", "伴奏", "instrumental", "ver.", "version", "dj", "現場", "现场",
                "karaoke", "8d", "slowed", "sped up", "加速", "降調", "女版", "男版", "鋼琴", "钢琴")


def _fnorm(s):
    """比對用：簡體、小寫、去掉標點空白。"""
    global _cc_s
    if _cc_s is None:
        _cc_s = OpenCC("tw2s")
    s = _cc_s.convert(str(s or "")).lower()
    return re.sub(r"[\s\W_]+", "", s)


def _best_line(text_lines, qn):
    """歌詞裡最像這句的那一行 (給結果顯示「是這句嗎」)。"""
    best, score = "", 0.0
    qs = set(qn)
    for ln in text_lines:
        n = _fnorm(ln)
        if not n:
            continue
        if qn and qn in n:
            return ln.strip()
        sc = len(qs & set(n)) / max(len(qs), 1)
        if sc > score:
            best, score = ln.strip(), sc
    return best if score >= 0.5 else ""


async def find_by_lyric(q):
    qn = _fnorm(q)
    if len(qn) < 2:
        return []
    out, seen = [], {}

    def add(title, artist, line, source, album=""):
        key = (_fnorm(re.sub(r"\s*[\(（\[【].*$", "", title)), _fnorm(artist.split("/")[0].split(",")[0]))
        if not title:
            return
        low = title.lower()
        item = {"title": title, "artist": artist, "album": album, "line": line, "source": source,
                "cover": any(w in low for w in _COVER_WORDS) or bool(re.search(r"[(（\[【][^)）\]】]*版", title))}
        if key in seen:  # 同一首：原版取代 Live / Remix 版
            old = seen[key]
            if old["cover"] and not item["cover"]:
                old.update({k: v for k, v in item.items() if k != "source" or old["source"] != "聽過的歌"})
            return None
        seen[key] = item
        out.append(item)
        return item

    # 1. 聽過的歌 (這次開著時抓過的、手動指定過的)
    pools = list(lyrics.overrides.items()) + list(lyrics.cache.items())
    for k, res in pools:
        texts = [l.get("text", "") for l in (res or {}).get("lines", [])]
        if any(qn in _fnorm(t) for t in texts):
            title, _, artist = k.partition("\u0000")
            add(title, artist, _best_line(texts, qn), "聽過的歌")

    # 2. 網路 (網易雲、QQ 音樂的歌詞搜尋)
    qs = _cc_s.convert(q)
    http = await lyrics.session()

    async def netease():
        async with http.get("https://music.163.com/api/search/get", headers=NETEASE_HEADERS,
                            params={"s": qs, "type": 1006, "limit": 12}) as r:
            d = json.loads(await r.text())
        res = []
        for s_ in ((d.get("result") or {}).get("songs") or []):
            txt = ((s_.get("lyrics") or {}).get("txt") if isinstance(s_.get("lyrics"), dict) else "") or ""
            res.append((s_.get("name") or "", " / ".join(a.get("name") or "" for a in s_.get("artists", [])),
                        _best_line(txt.split("\n"), qn), (s_.get("album") or {}).get("name") or "", txt))
        return res

    async def qq():
        body = {"comm": {"ct": 19, "cv": 1859, "uin": "0"},
                "req": {"method": "DoSearchForQQMusicDesktop", "module": "music.search.SearchCgiService",
                        "param": {"num_per_page": 10, "page_num": 1, "query": qs, "search_type": 7}}}
        async with http.post("https://u.y.qq.com/cgi-bin/musicu.fcg", data=json.dumps(body)) as r:
            d = json.loads(await r.text())
        songs = (((d.get("req") or {}).get("data") or {}).get("body") or {}).get("song", {}).get("list") or []
        out_ = []
        for s_ in songs:
            txt = (s_.get("lyric") or "").replace("\\n", "\n")
            out_.append((s_.get("name") or "", " / ".join(a.get("name") or "" for a in s_.get("singer", [])),
                         _best_line(txt.split("\n"), qn), (s_.get("album") or {}).get("name") or "", txt))
        return out_

    remote = []
    for name, r in zip(("網易雲", "QQ 音樂"), await asyncio.gather(netease(), qq(), return_exceptions=True)):
        if isinstance(r, Exception):
            print("[find]", name, repr(r))
            continue
        remote += [(name, *x) for x in r]
    # 翻唱的歌詞常會寫「原唱 / 作詞 / 作曲：某某」：被寫最多次的就是原唱
    credits = " ".join(_fnorm(ln) for *_, txt in remote for ln in txt.split("\n")[:12]
                       if re.search(r"原唱|作词|作曲|词|曲|lyric|compos", ln, re.I))
    for name, title, artist, line, album, txt in remote:
        if line or qn in _fnorm(title):  # 歌詞 (或歌名) 裡真的有這句才算
            it = add(title, artist, line, name, album)
            if it is not None:
                a0 = _fnorm(re.split(r"[/,、&]", artist)[0])
                it["credit"] = credits.count(a0) if len(a0) >= 2 else 0
    # 結果都是翻唱 (原唱不在這兩個平台，例如周杰倫)：從翻唱標的「原唱 / 演唱 / 曲」推測原唱是誰
    names = {}
    for *_, txt in remote:
        for m_ in re.finditer(r"(?:原唱|演唱|作曲|曲)\s*[:：]\s*([^\s/／,，、&()（）]{2,16})", txt[:400]):
            names[m_.group(1)] = names.get(m_.group(1), 0) + 1
    if names and out:
        who, n = max(names.items(), key=lambda kv: kv[1])
        artists = {_fnorm(re.split(r"[/,、&]", x["artist"])[0]) for x in out}
        if n >= 3 and _fnorm(who) not in artists:
            base = re.sub(r"\s*[(（\[【].*$", "", out[0]["title"]).strip()
            titles = [re.sub(r"\s*[(（\[【].*$", "", x["title"]).strip() for x in out]
            base = max(set(titles), key=titles.count) or base
            out.insert(0, {"title": base, "artist": who, "album": "", "line": out[0]["line"], "source": "推測的原唱",
                           "cover": False, "credit": n})
    # 聽過的歌 → 推測的原唱 → 不是翻唱 / Live / Remix → 原唱 (被標成作者最多次) → 來源原本的順序
    out.sort(key=lambda x: (x["source"] != "聽過的歌", x["source"] != "推測的原唱", x["cover"], -x.get("credit", 0)))
    for x in out:
        x["title"], x["artist"], x["line"] = to_trad(x["title"]), to_trad(x["artist"]), to_trad(x["line"])
    return out[:12]


def open_in_player(title, artist, where):
    """在 Spotify / YouTube Music 打開搜尋結果。"""
    import urllib.parse
    q = f"{title} {artist}".strip()
    if where == "youtube":
        webbrowser.open("https://music.youtube.com/search?q=" + urllib.parse.quote(q))
    else:
        try:
            os.startfile("spotify:search:" + urllib.parse.quote(q))
        except OSError:  # 沒裝 Spotify：開網頁版
            webbrowser.open("https://open.spotify.com/search/" + urllib.parse.quote(q))
AI = None    # ai_lyrics.AIEngine，在 poll_loop 開始時建立
SHARE = None  # share.Share：和朋友共享歌詞
LOOP = None
clients: set[web.WebSocketResponse] = set()
stage_clients: set[web.WebSocketResponse] = set()  # 舞台頁面 (要即時音量)
state = {"track": None, "lyrics": None, "sessions": []}


async def broadcast(msg):
    data = json.dumps(msg, ensure_ascii=False)
    for ws in list(clients):
        try:
            await ws.send_str(data)
        except Exception:
            clients.discard(ws)


async def fetch_lyrics(track):
    if track.get("source") == "瀏覽器" and not track.get("duration"):
        for _ in range(15):  # 瀏覽器常常先給標題、晚一點才給長度；判斷是不是短影片需要長度
            await asyncio.sleep(0.2)
            cur = state["track"]
            if not cur or cur["track_id"] != track["track_id"]:
                return
            if cur.get("duration"):
                track = cur
                break
    kind = media_kind(track)
    if kind == "short":
        print("[skip] short video:", track["title"], flush=True)
        if state["track"] and state["track"]["track_id"] == track["track_id"]:
            state["lyrics"] = {"track_id": track["track_id"], "source": None, "synced": False, "lines": [],
                               "kind": "short"}
            await broadcast({"type": "lyrics", **state["lyrics"]})
        return
    # 一般影片：歌手 (頻道) 也要對得上才算，避免標題剛好跟某首歌同名
    res = await lyrics.find(track["title"], track["artist"], track["album"], track["duration"],
                            need_artist=(kind == "video"))
    if kind == "video" and not res.get("synced") and not res.get("override"):
        res = {**res, "kind": "video"}
    if state["track"] and state["track"]["track_id"] == track["track_id"]:
        state["lyrics"] = {"track_id": track["track_id"], **res}
        await broadcast({"type": "lyrics", **state["lyrics"]})
        if AI is not None:
            if res.get("ai") and res.get("heard", 1.0) < 0.95:  # 上次沒聽完整：這次繼續聽，補齊後更新
                AI.set_job(track, Lyrics.key(track["title"], track["artist"]),
                           [{"t": None, "text": l["text"]} for l in res["lines"]] if res.get("ai") == "aligned" else None,
                           improve=True)
            elif res.get("synced") or res.get("override") or res.get("ai") or kind != "music":
                AI.set_job(None)
            else:  # 沒有同步歌詞 → AI 接手 (有純文字歌詞就只對時)
                AI.set_job(track, Lyrics.key(track["title"], track["artist"]),
                           [{"t": None, "text": l["text"], **({"tr": l["tr"]} if l.get("tr") else {})}
                            for l in res["lines"] if l["text"].strip()] or None)


def on_ai_update(track_id, key, result, live):
    """AI 執行緒呼叫：轉回 asyncio 執行緒廣播。"""
    if LOOP is not None:
        LOOP.call_soon_threadsafe(lambda: asyncio.ensure_future(_ai_push(track_id, key, result, live)))


async def _ai_push(track_id, key, result, live):
    if not live:
        lyrics.cache.pop(key, None)
        if SHARE is not None and result.get("synced") and result.get("lines"):
            title, _, artist = key.partition("\u0000")
            t0 = state["track"] or {}
            dur = t0.get("duration") if (t0.get("title"), t0.get("artist")) == (title, artist) else 0
            asyncio.ensure_future(asyncio.to_thread(SHARE.put, title, artist, dur, result,
                                                    "aligned" if result.get("ai") == "aligned" else "transcribed"))
    t = state["track"]
    if not t or t["track_id"] != track_id:
        return
    cur = state["lyrics"] or {}
    if cur.get("override") or (cur.get("synced") and not cur.get("ai")):
        return  # 已經有真的歌詞或手動指定，不蓋掉
    if live and not result["lines"] and cur.get("lines"):
        return
    state["lyrics"] = {"track_id": track_id, **traditionalize({**result, "lines": [dict(l) for l in result["lines"]]})}
    await broadcast({"type": "lyrics", **state["lyrics"]})


class PlaybackClock:
    """比 Windows 媒體資訊更穩的播放時鐘。

    Spotify 有時候回報錯的東西：
      ・明明還在播，卻說「暫停」→ 歌詞停住
      ・說「播放中」，位置卻一直是同一個數字 (一直「更新」同一個值) → 歌詞停住
    所以同時看播放器有沒有真的發出聲音 (音量表)：有聲音就當作在播，時間自己往前推；
    播放器一回報新的位置 (正常每幾秒一次、或是拖了進度條) 就馬上對齊。"""

    def __init__(self):
        self.key = None
        self.anchor = self.at = self.raw = 0.0
        self.rep_playing = self.playing = False
        self.loud = self.quiet = 0
        self.meter_at = 0.0

    def audio(self, app, now):
        """播放器有沒有在出聲 (連續兩次有聲音才算，避免誤判)。"""
        a = (app or "").lower()
        if any(b in a for b in ("chrome", "msedge", "edge", "firefox", "308046b0af4a39cb", "brave", "opera", "vivaldi")):
            return False  # 瀏覽器的聲音是整個瀏覽器一起算 (別的分頁在播影片也算)，不能用來判斷音樂有沒有在播
        if now - self.meter_at >= 0.4:
            self.meter_at = now
            try:
                import audio_ctl
                pk = audio_ctl.peak(app)
            except Exception:
                pk = None
            if pk is None:
                self.loud = self.quiet = 0
            elif pk > 0.003:
                self.loud, self.quiet = self.loud + 1, 0
            else:
                self.loud, self.quiet = 0, self.quiet + 1
        return self.loud >= 2

    def update(self, cur):
        now = time.monotonic()
        tid, raw, rep_pos, rep_playing = cur["track_id"], cur["_raw"], cur["position"], cur["playing"]
        sounding = self.audio(cur["app"], now)
        if tid != self.key:  # 換歌：直接相信回報
            self.key, self.anchor, self.at, self.raw = tid, rep_pos, now, raw
            self.rep_playing = self.playing = rep_playing
            return rep_pos, rep_playing
        est = self.anchor + ((now - self.at) if self.playing else 0.0)
        playing = rep_playing or sounding
        moved = abs(raw - self.raw) > 0.05
        # 只有「暫停 / 播放」狀態變了、位置數字沒變：如果本來就聽得到聲音 (我們已經當它在播)，那個位置是舊的，不要跳回去
        flipped = rep_playing != self.rep_playing and not (sounding and self.playing)
        new_info = moved or flipped
        self.raw, self.rep_playing = raw, rep_playing
        if new_info:
            if rep_playing or not sounding:
                self.anchor, self.at = rep_pos, now        # 正常更新、拖進度條、真的暫停
            elif abs(raw - est) > 2.5:
                self.anchor, self.at = raw, now            # 說暫停但還有聲音，而且位置跳了：拖過進度條
            else:
                self.anchor, self.at = est, now            # 說暫停但還有聲音：照自己的時間繼續走
        elif playing != self.playing:
            self.anchor, self.at = est, now
        self.playing = playing
        pos = self.anchor + ((now - self.at) if playing else 0.0)
        if cur["duration"] > 0:
            pos = max(0.0, min(pos, cur["duration"]))
        return pos, playing


clock = PlaybackClock()


async def poll_loop():
    global AI, LOOP
    await media.init()
    LOOP = asyncio.get_running_loop()
    start_chat()
    try:
        import ai_lyrics
        AI = ai_lyrics.AIEngine(state, config, on_ai_update, _norm, DATA / "ai_lyrics.json")
    except Exception as e:
        print("[ai] disabled:", repr(e))
    last_id = None
    while True:
        try:
            sessions = await media.snapshot()
            cur = media.pick(sessions)
            state["sessions"] = [
                {k: s[k] for k in ("app", "source", "title", "artist", "playing")} for s in sessions
            ]
            media.current = cur["_session"] if cur else None
            if cur:
                cur["position"], cur["playing"] = clock.update(cur)
                await media.load_art(cur["track_id"], cur["_thumb"])
                track = {k: v for k, v in cur.items() if not k.startswith("_")}
                track["has_art"] = cur["track_id"] in media.art
                track["accent"] = media.accent.get(cur["track_id"])
                track["ts"] = time.time()
                state["track"] = track
                if cur["track_id"] != last_id:
                    last_id = cur["track_id"]
                    print("[track]", cur["source"], cur["title"], "-", cur["artist"], "playing" if cur["playing"] else "paused", flush=True)
                    state["lyrics"] = None
                    if AI is not None:
                        AI.set_job(None)  # 上一首交給 AI 精修
                    await broadcast({"type": "lyrics", "track_id": last_id, "loading": True, "lines": []})
                    asyncio.create_task(fetch_lyrics(track))
            else:
                state["track"] = None
                last_id = None
            await broadcast({"type": "state", "track": state["track"], "sessions": state["sessions"],
                             "pinned": media.pinned, "config": public_config(), "unlocked": runtime["unlocked"],
                             "chat": CHAT.status if CHAT else "off",
                             "autostart": get_autostart(), "can_autostart": FROZEN, "version": VERSION,
                             "mica": MICA_OK,
                             "hotkeys_failed": hotkeys_failed(), "monitors": monitor_list(),
                             "folia": runtime["folia"].status if runtime.get("folia") else None,
                             "ai": AI.status if AI is not None else None})
        except Exception as e:
            print("[poll]", e)
        await asyncio.sleep(POLL_INTERVAL)


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    clients.add(ws)
    if state["lyrics"]:
        await ws.send_str(json.dumps({"type": "lyrics", **state["lyrics"]}, ensure_ascii=False))
    await ws.send_str(json.dumps(devices_msg(), ensure_ascii=False))
    if UPDATE.get("available") or UPDATE.get("whatsnew"):
        await ws.send_str(json.dumps(update_msg(), ensure_ascii=False))
    async for msg in ws:
        try:
            d = json.loads(msg.data)
            if d.get("type") == "hello":
                print("[page]", d, flush=True)
                if d.get("app"):  # 打開主視窗時：超過 10 分鐘沒檢查就再看一次有沒有新版
                    asyncio.create_task(check_update(min_gap=600))
                if d.get("stage"):  # 舞台頁面 (Hummi 舞台視窗 / OBS)：要即時音量
                    stage_clients.add(ws)
            elif d.get("type") == "pin":
                media.pinned = d.get("app") or None
            elif d.get("type") == "offset":
                config["offset"] = round(float(d["value"]), 2)
                config_changed()
            elif d.get("type") == "overlay":
                patch = {k: v for k, v in (d.get("patch") or {}).items() if k in config["overlay"]}
                config["overlay"].update(patch)
                config_changed()
            elif d.get("type") == "background":
                patch = {k: v for k, v in (d.get("patch") or {}).items() if k in config["background"] and k != "image"}
                config["background"].update(patch)
                config_changed()
                if "mode" in patch:
                    await asyncio.get_running_loop().run_in_executor(None, apply_window_style)
                    if settings_window is not None:
                        await asyncio.get_running_loop().run_in_executor(None, apply_window_style, settings_window)
            elif d.get("type") == "font":
                config["font"] = str(d.get("value") or "")
                config_changed()
            elif d.get("type") == "autostart":
                set_autostart(bool(d.get("value")))
            elif d.get("type") == "unlock":
                runtime["unlocked"] = bool(d.get("value"))
                runtime["cfg_version"] += 1
            elif d.get("type") == "seek" and media.current is not None:
                await media.current.try_change_playback_position_async(int(float(d["t"]) * 10_000_000))
            elif d.get("type") in ("next", "prev") and media.current is not None:  # 主視窗的上一首 / 下一首
                if d["type"] == "next":
                    await media.current.try_skip_next_async()
                else:
                    await media.current.try_skip_previous_async()
            elif d.get("type") == "toggle" and media.current is not None:
                await media.current.try_toggle_play_pause_async()
            elif d.get("type") == "notch":
                to_notch() if d.get("value") else set_notch(False)
            elif d.get("type") == "devices":  # 裝置頁的操作
                svc = runtime.get("devices")
                act = d.get("action")
                if svc is not None:
                    if act == "refresh":
                        svc.refresh()
                    elif act == "hide":
                        svc.set_hidden(str(d.get("id")), bool(d.get("value")))
                    elif act == "low":
                        svc.set_low(int(d.get("value") or 15))
                    elif act == "tray":
                        config.setdefault("devices", {})["tray"] = bool(d.get("value"))
                        save_config()
                        if runtime.get("tray_devices"):
                            runtime["tray_devices"].sync()
                        await broadcast(devices_msg())
                    elif act == "enabled":
                        set_option("battery", bool(d.get("value")))
                        svc.refresh()
                        await broadcast(devices_msg())
            elif d.get("type") == "update":  # 更新：檢查 / 安裝 / 看過新功能了
                act = d.get("action")
                if act == "check":
                    asyncio.create_task(check_update(force=True))
                elif act == "install":
                    asyncio.create_task(install_update())
                elif act == "seen":
                    config["seen_version"] = VERSION
                    save_config()
                    UPDATE["whatsnew"] = None
                elif act == "auto":
                    config["auto_update"] = bool(d.get("value"))
                    save_config()
            elif d.get("type") == "auto_pause" and d.get("key") in ("lock", "unplug", "resume"):
                config["auto_pause"][d["key"]] = bool(d.get("value"))
                save_config()
            elif d.get("type") == "theme" and d.get("value") in ("night", "dawn", "mono", "neon", "paper"):
                config["theme"] = d["value"]
                save_config()
                for w in (window, settings_window):
                    if w is not None:
                        threading.Thread(target=apply_window_style, args=(w,), daemon=True).start()
            elif d.get("type") == "ui_zoom":
                config["ui_zoom"] = max(70, min(200, int(d.get("value") or 100)))
                save_config()
                for w in (window, settings_window):
                    if w is not None:
                        apply_zoom(w)
            elif d.get("type") == "chat":
                for k in ("channel", "bot", "commands"):
                    if k in d:
                        config["chat"][k] = str(d.get(k) or "").strip()[:200]
                if d.get("token") is not None and d.get("token") != "":
                    config["chat"]["token"] = str(d["token"]).strip()[:200]
                if d.get("clear_token"):
                    config["chat"]["token"] = ""
                save_config()
                start_chat()
            elif d.get("type") == "share":
                if "name" in d:
                    config["share"]["name"] = str(d.get("name") or "")[:20]
                if "folder" in d:
                    config["share"]["folder"] = str(d.get("folder") or "")
                save_config()
                if d.get("folder"):
                    n = await asyncio.to_thread(share_everything)
                    total = await asyncio.to_thread(SHARE.count) if SHARE else 0
                    await ws.send_str(json.dumps({"type": "share_status", "added": n, "total": total}, ensure_ascii=False))
            elif d.get("type") == "wallpaper":
                if "monitor" in d:
                    config["wallpaper"]["monitor"] = str(d.get("monitor") or "primary")
                if "enabled" in d:
                    config["wallpaper"]["enabled"] = bool(d.get("enabled"))
                save_config()
                threading.Thread(target=sync_wallpaper, daemon=True).start()
            elif d.get("type") == "notch_set":  # 設定裡的膠囊開關 (不會把主視窗收起來)
                set_notch(bool(d.get("value")))
            elif d.get("type") == "notch_monitor":
                config["notch_monitor"] = str(d.get("value") or "primary")
                save_config()
            elif d.get("type") == "taskbar_progress":
                config["taskbar_progress"] = bool(d.get("value"))
                save_config()
            elif d.get("type") == "hotkeys":
                config["hotkeys"] = bool(d.get("value"))
                save_config()
                start_hotkeys()
            elif d.get("type") == "volume":  # 主視窗 ↑ / ↓
                await asyncio.to_thread(music_volume, float(d.get("delta") or 0))
            elif d.get("type") == "music_only":
                config["music_only"] = bool(d.get("value"))
                save_config()
            elif d.get("type") == "folia":
                set_option("folia", bool(d.get("value")))
            elif d.get("type") == "open_stage":
                await asyncio.get_running_loop().run_in_executor(None, open_stage)
            elif d.get("type") == "settings_window":
                await asyncio.get_running_loop().run_in_executor(None, open_settings)
            elif d.get("type") == "open_url":
                u = str(d.get("url") or "")
                if u.startswith("https://github.com/"):  # 只開 GitHub (下載頁、HaloBattery)
                    webbrowser.open(u)
            elif d.get("type") == "refetch" and state["track"]:
                t = state["track"]
                lyrics.cache.pop(Lyrics.key(t["title"], t["artist"]), None)
                if AI is not None:  # 重新搜尋也丟掉舊的 AI 結果，讓它重聽
                    AI.forget(Lyrics.key(t["title"], t["artist"]))
                    AI.set_job(None)
                asyncio.create_task(fetch_lyrics(t))
            elif d.get("type") == "ai":
                patch = {k: bool(v) for k, v in (d.get("patch") or {}).items() if k in config["ai"]}
                config["ai"].update(patch)
                config_changed()
                if AI is not None and not config["ai"]["enabled"]:
                    AI.set_job(None)
        except Exception:
            pass
    clients.discard(ws)
    stage_clients.discard(ws)
    return ws


async def art_handler(request):
    tid = request.query.get("id", "")
    data = media.art.get(tid)
    if not data:
        raise web.HTTPNotFound()
    ctype = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return web.Response(body=data, content_type=ctype, headers={"Cache-Control": "max-age=3600"})


async def index(request):
    return web.FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-store"})


# ───────────────────────── 更新 (GitHub Releases) ─────────────────────────
RELEASES_API = "https://api.github.com/repos/42102718/NowPlayingLyrics/releases/latest"
RELEASES_PAGE = "https://github.com/42102718/NowPlayingLyrics/releases/latest"
UPDATE = {"available": None, "state": "idle", "progress": None, "error": "", "whatsnew": None, "checked": 0.0}


def _ver(s):
    out = []
    for p in re.findall(r"\d+", str(s or ""))[:3]:
        out.append(int(p))
    return tuple(out + [0] * (3 - len(out)))


def update_msg():
    return {"type": "update", "current": VERSION, "available": UPDATE["available"], "state": UPDATE["state"],
            "progress": UPDATE["progress"], "error": UPDATE["error"], "whatsnew": UPDATE["whatsnew"],
            "page": RELEASES_PAGE, "can_install": FROZEN, "auto": config.get("auto_update", True)}


def init_whatsnew():
    """剛更新完：準備好「這版有什麼新功能」，主視窗和膠囊都會顯示一次。"""
    import whatsnew
    if _ver(config.get("seen_version")) < _ver(VERSION):
        n = whatsnew.notes_for(VERSION, config.get("seen_version"))  # 跳過好幾版的人：中間每一版都給他看
        if n:
            UPDATE["whatsnew"] = {"version": VERSION, **n}
            runtime["notch_event"] = ("updated", VERSION)
        else:
            config["seen_version"] = VERSION
            save_config()


async def check_update(force=False, min_gap=3600):
    if not force and (not config.get("auto_update", True) or time.time() - UPDATE["checked"] < min_gap):
        return
    UPDATE["checked"] = time.time()
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA, "Accept": "application/vnd.github+json"}) as s:
            async with s.get(RELEASES_API, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    raise RuntimeError(f"GitHub 回應 {r.status}")
                data = await r.json()
        tag = data.get("tag_name") or ""
        asset = next((a for a in data.get("assets") or []
                      if a.get("name", "").lower().endswith(".exe") and "setup" in a.get("name", "").lower()), None)
        if _ver(tag) > _ver(os.environ.get("HUMMI_PRETEND_VERSION") or VERSION):  # 環境變數：測試用
            UPDATE["available"] = {"version": tag.lstrip("vV"), "notes": (data.get("body") or "")[:6000],
                                   "url": asset.get("browser_download_url") if asset else None,
                                   "size": asset.get("size") if asset else None, "page": data.get("html_url") or RELEASES_PAGE}
            if config.get("update_notified") != UPDATE["available"]["version"]:
                config["update_notified"] = UPDATE["available"]["version"]
                save_config()
                runtime["notch_event"] = ("update", UPDATE["available"]["version"])
        else:
            UPDATE["available"] = None
        UPDATE["error"] = ""
        print("[update] latest", tag, "current", VERSION, flush=True)
    except Exception as e:
        UPDATE["error"] = "沒辦法檢查更新：" + str(e)[:120] if force else ""
        print("[update] check failed", repr(e), flush=True)
    await broadcast(update_msg())


async def update_loop():
    await asyncio.sleep(20)
    while True:
        await check_update()
        await asyncio.sleep(3600)  # 每小時看一次 (以前 6 小時，朋友發了新版要等太久)


async def install_update():
    """下載新版安裝程式 → 在背景安裝 → 裝完自動重新開啟。"""
    av = UPDATE.get("available")
    if not av or not av.get("url") or UPDATE["state"] in ("downloading", "installing"):
        return
    if not FROZEN:
        UPDATE["error"] = "開發版不能自動更新"
        await broadcast(update_msg())
        return
    import tempfile
    path = Path(tempfile.gettempdir()) / f"Hummi-Setup-{av['version']}.exe"
    UPDATE.update(state="downloading", progress=0.0, error="")
    await broadcast(update_msg())
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA}) as s:
            async with s.get(av["url"], timeout=aiohttp.ClientTimeout(total=900)) as r:
                if r.status != 200:
                    raise RuntimeError(f"下載失敗 ({r.status})")
                total = int(r.headers.get("Content-Length") or av.get("size") or 0)
                got, last = 0, 0.0
                with open(path, "wb") as f:
                    async for chunk in r.content.iter_chunked(1 << 16):
                        f.write(chunk)
                        got += len(chunk)
                        if total and time.time() - last > 0.3:
                            UPDATE["progress"] = got / total
                            last = time.time()
                            await broadcast(update_msg())
        if total and got < total * 0.98:
            raise RuntimeError("下載不完整")
        UPDATE.update(state="installing", progress=1.0)
        await broadcast(update_msg())
        import subprocess
        # /SILENT：顯示安裝進度；/RELAUNCH=1：裝完自動打開 (安裝程式會先關掉這個程式)
        subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        await asyncio.sleep(1.5)
        quit_app()
    except Exception as e:
        UPDATE.update(state="idle", progress=None, error="更新失敗：" + str(e)[:150])
        print("[update] install failed", repr(e), flush=True)
        await broadcast(update_msg())


def open_update():
    show_window()
    if LOOP is not None:
        asyncio.run_coroutine_threadsafe(broadcast({**update_msg(), "open": True}), LOOP)


runtime.update(open_update=open_update, update=UPDATE)


async def viz_pump():
    """舞台頁面開著時，每 1/25 秒把頻譜和響度推過去 (背景、節拍脈動用)。"""
    while True:
        await asyncio.sleep(0.04)
        viz = runtime.get("viz")
        if not stage_clients or viz is None or not viz.fresh():
            continue
        data = json.dumps({"type": "viz", "levels": [round(v, 3) for v in viz.levels], "energy": round(viz.energy, 3)})
        for ws in list(stage_clients):
            try:
                await ws.send_str(data)
            except Exception:
                stage_clients.discard(ws)


async def on_startup(app):
    app["poller"] = asyncio.create_task(poll_loop())
    app["updater"] = asyncio.create_task(update_loop())
    app["viz_pump"] = asyncio.create_task(viz_pump())
    if "playercap" in app:
        app["pc_loop"] = asyncio.create_task(app["playercap"].loop())


def accent_color(data: bytes):
    """從封面挑一個夠亮、夠飽和的顏色給浮動歌詞用。"""
    try:
        from PIL import Image
        import colorsys
        im = Image.open(io.BytesIO(data)).convert("RGB").resize((24, 24))
        best, score = (255, 255, 255), -1
        for r, g, b in (im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata()):
            h, l, s_ = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            sc = s_ * (1 - abs(l - 0.55))
            if sc > score:
                best, score = (h, l, s_), sc
        if score < 0.15:
            return None
        h, l, s_ = best
        r, g, b = colorsys.hls_to_rgb(h, max(0.72, l), min(1.0, s_ + 0.15))  # 提亮，遊戲畫面上才看得清楚
        return (int(r * 255), int(g * 255), int(b * 255))
    except Exception:
        return None


# ───────────────────────── 開機自動啟動 (HKCU\...\Run) ─────────────────────────
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "NowPlayingLyrics"
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup",
                           "Now Playing Lyrics.lnk")  # 舊版 (1.2 以前) 的捷徑名稱


def get_autostart() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool):
    if not FROZEN:  # 只有打包後的 .exe 才設定
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --background')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def migrate_startup_shortcut():
    """舊版是放「啟動」資料夾捷徑，改成登錄檔 Run，設定頁才能開關。"""
    if FROZEN and os.path.exists(STARTUP_LNK):
        try:
            os.remove(STARTUP_LNK)
            set_autostart(True)
        except Exception as e:
            print("[autostart]", e)
    # 1.5 改名 (exe 檔名變了)：開機啟動還指著舊的 exe 的話，改指到現在這個
    if FROZEN and get_autostart():
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                cur = winreg.QueryValueEx(k, RUN_NAME)[0]
            if sys.executable.lower() not in cur.lower():
                set_autostart(True)
        except OSError as e:
            print("[autostart]", e)


def start_tray():
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception as e:
        print("[tray]", e)
        return
    try:
        img = Image.open(ROOT / "icon.png").convert("RGBA").resize((64, 64), Image.LANCZOS)
    except Exception:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((2, 2, 62, 62), fill=(124, 92, 255))
    ov = config["overlay"]

    def toggle(key):
        def f(icon, item):
            ov[key] = not ov[key]
            config_changed()
        return f

    def notch_toggle(key):
        def f(icon, item):
            config["notch_opts"][key] = not config["notch_opts"][key]
            save_config()
        return f

    def unlock(icon, item):
        runtime["unlocked"] = not runtime["unlocked"]
        runtime["cfg_version"] += 1

    def preset(name):
        def f(icon, item):
            ov["preset"] = name
            config_changed()
        return f

    P = pystray.MenuItem
    menu = pystray.Menu(
        P("開啟主視窗", lambda i, it: show_window(), default=True),
        P("膠囊模式 (螢幕頂端)", lambda i, it: set_notch(not config["notch"]), checked=lambda it: config["notch"]),
        P("膠囊選項", pystray.Menu(*[
            P(label, notch_toggle(k), checked=(lambda k: lambda it: config["notch_opts"][k])(k))
            for k, label in [("peek", "換歌時彈出歌名"), ("spectrum", "真實頻譜和光暈"),
                             ("battery", "裝置電量"), ("gestures", "滾輪調音量、左右滑切歌")]
        ])),
        P("打開舞台", lambda i, it: open_stage()),
        P("裝置電量", lambda i, it: open_devices()),
        pystray.Menu.SEPARATOR,
        P("顯示浮動歌詞", toggle("enabled"), checked=lambda it: ov["enabled"]),
        P("解鎖位置 (可拖曳)", unlock, checked=lambda it: runtime["unlocked"]),
        P("位置", pystray.Menu(*[
            P(label, preset(k), checked=(lambda k: lambda it: ov["preset"] == k)(k), radio=True)
            for k, label in [("bottom-left", "左下"), ("bottom-center", "下方中間"), ("bottom-right", "右下"),
                             ("top-center", "上方中間"), ("custom", "自訂 (拖曳後)")]
        ])),
        P("顯示翻譯", toggle("show_translation"), checked=lambda it: ov["show_translation"]),
        P("顯示下一句", toggle("show_next"), checked=lambda it: ov["show_next"]),
        pystray.Menu.SEPARATOR,
        P("結束", lambda i, it: quit_app(i)),
    )
    icon = pystray.Icon("hummi", img, APP_NAME, menu)
    threading.Thread(target=icon.run, daemon=True).start()


# ───────────────────────── 主視窗 (獨立 App 視窗) ─────────────────────────
window = None
quitting = False


def show_window():
    if window is not None:
        window.show()
        window.restore()


def set_notch(on):
    """膠囊開關 (系統匣 / 膠囊設定)。膠囊跟主視窗是分開的：主視窗開著、關著膠囊都在。"""
    config["notch"] = bool(on)
    save_config()


def to_notch():
    """主視窗標題列的膠囊按鈕：把主視窗收起來，只留膠囊。"""
    set_notch(True)
    if window is not None:
        window.hide()


try:
    MICA_OK = sys.getwindowsversion().build >= 22621  # Windows 11 22H2 以後才有系統背景材質
except Exception:
    MICA_OK = False


def install_chrome(form):
    """主視窗的原生外框 (UI 執行緒)：四邊可拉、貼齊、工作列縮圖按鈕和進度。"""
    try:
        import winchrome
        ch = winchrome.Chrome(form, on_thumb=media_cmd)
        ch.install()
        runtime["chrome"] = ch

        def tb_state():
            t = state.get("track")
            if not t:
                return None, 0, 0
            return bool(t.get("playing")), float(t.get("position") or 0), float(t.get("duration") or 0)
        ch.thumbs = winchrome.Thumbs(ch.hwnd, tb_state, lambda: config.get("taskbar_progress", True)).start()
    except Exception as e:
        print("[chrome]", repr(e), flush=True)


def main_hwnd():
    import ctypes
    ch = runtime.get("chrome")
    if ch is not None and ch.hwnd:
        return ch.hwnd
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    return u.FindWindowW(None, APP_NAME)


def placement_loop():
    """記住主視窗的位置和大小 (有變才存)。"""
    import winchrome
    last = config.get("window")
    while not quitting:
        time.sleep(3)
        try:
            h = main_hwnd()
            import ctypes
            if not h or not ctypes.windll.user32.IsWindowVisible(h) or ctypes.windll.user32.IsIconic(h):
                continue
            if window is not None and getattr(window.native, "is_fullscreen", False):
                continue
            g = winchrome.get_placement(h)
            if g and g != last:
                config["window"] = last = g
                save_config()
        except Exception as e:
            print("[placement]", repr(e))


def apply_window_style(win=None):
    """主視窗 / 設定小視窗：Windows 11 圓角 + 深色邊框；背景選 Mica 時套用系統材質 (透出桌布色調)。"""
    win = win or window
    if win is None:
        return
    try:
        import ctypes
        from System import Action
        from System.Drawing import Color, ColorTranslator
        form = win.native
    except Exception as e:
        print("[style]", repr(e))
        return
    mica = MICA_OK and config["background"].get("mode") == "mica"

    class MARGINS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

    def go():
        try:
            hwnd = ctypes.c_void_p(form.Handle.ToInt64())
            dwm = ctypes.windll.dwmapi

            def attr(a, v):
                val = ctypes.c_int(v)
                return dwm.DwmSetWindowAttribute(hwnd, a, ctypes.byref(val), 4)
            attr(20, 0 if config.get("theme") in ("dawn", "paper") else 1)   # 深色 / 淺色風格的視窗邊框
            attr(33, 2)   # 圓角
            m = -1 if mica else 0
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(m, m, m, m)))
            attr(38, 2 if mica else 1)  # 系統背景：Mica / 無
            form.BackColor = Color.Black if mica else ColorTranslator.FromHtml("#0b0b12")
            form.webview.DefaultBackgroundColor = Color.Transparent if mica else ColorTranslator.FromHtml("#0b0b12")
            if win is window and runtime.get("chrome") is None:
                install_chrome(form)
        except Exception as e:
            print("[style]", repr(e), flush=True)
    try:
        form.Invoke(Action(go))
    except Exception as e:
        print("[style]", repr(e))


def media_cmd(cmd, arg=None):
    """膠囊的按鈕 (在別的執行緒) 呼叫：丟回 asyncio 執行緒控制播放器。"""
    async def go():
        s = media.current
        if s is None:
            return
        if cmd == "toggle":
            await s.try_toggle_play_pause_async()
        elif cmd == "next":
            await s.try_skip_next_async()
        elif cmd == "prev":
            await s.try_skip_previous_async()
        elif cmd == "pause":
            await s.try_pause_async()
        elif cmd == "play":
            await s.try_play_async()
        elif cmd == "seek":
            await s.try_change_playback_position_async(int(float(arg) * 10_000_000))
    if LOOP is not None:
        asyncio.run_coroutine_threadsafe(go(), LOOP)


def set_option(key, value):
    """膠囊裡的設定頁呼叫 (別的執行緒)。"""
    value = bool(value)
    if key in config["notch_opts"]:
        config["notch_opts"][key] = value
    elif key == "overlay":
        config["overlay"]["enabled"] = value
    elif key == "ai":
        config["ai"]["enabled"] = value
        if AI is not None and not value:
            AI.set_job(None)
    elif key == "autostart":
        set_autostart(value)
        return
    elif key == "notch":
        set_notch(value)
        return
    elif key == "folia":
        config["folia"]["enabled"] = value
        save_config()
        return
    config_changed()


def get_option(key):
    if key in config["notch_opts"]:
        return config["notch_opts"][key]
    if key == "overlay":
        return config["overlay"]["enabled"]
    if key == "ai":
        return config["ai"]["enabled"]
    if key == "autostart":
        return get_autostart()
    if key == "folia":
        return config["folia"]["enabled"]
    return False


stage_window = None


class StageApi:
    """舞台視窗：全螢幕 (F / F11 / 設定裡的按鈕)。"""

    def fullscreen(self):
        if stage_window is not None:
            stage_window.toggle_fullscreen()

    def exit_fullscreen(self):
        if stage_window is not None and getattr(stage_window.native, "is_fullscreen", False):
            stage_window.toggle_fullscreen()

    def back(self):
        """回主視窗：舞台收起來，主視窗叫出來。"""
        if stage_window is not None:
            stage_window.hide()
        show_window()


def open_stage():
    """Hummi 舞台：全螢幕歌詞演出的視窗 (唱歌台；OBS 也可以直接用網址)。"""
    global stage_window
    if window is None:  # 瀏覽器除錯模式
        webbrowser.open(f"http://127.0.0.1:{PORT}/stage")
        return
    try:
        import webview
        if stage_window is None:
            stage_window = webview.create_window("Hummi 舞台", f"http://127.0.0.1:{PORT}/stage?v={VERSION}",
                                                 width=1280, height=720, background_color="#05050a", js_api=StageApi())

            def on_closing():
                if quitting:
                    return True
                stage_window.hide()
                show_window()  # 關掉舞台 = 回主視窗
                return False
            stage_window.events.closing += on_closing
            stage_window.events.shown += lambda: apply_window_style(stage_window)
        else:
            stage_window.show()
            stage_window.restore()
        if window is not None:  # 舞台已經在演歌詞了：主視窗收起來 (膠囊還在，系統匣可以叫回來)
            window.hide()
    except Exception as e:
        print("[stage]", repr(e), flush=True)


wallpaper_window = None
_wp_lock = threading.Lock()


def sync_wallpaper():
    """桌布模式開 / 關 (設定改了、程式啟動時)。"""
    global wallpaper_window
    if window is None:
        return
    with _wp_lock:
        on = config["wallpaper"]["enabled"]
        if on and wallpaper_window is None:
            import webview
            wallpaper_window = webview.create_window(
                "Hummi 桌布", f"http://127.0.0.1:{PORT}/stage?obs=1&wallpaper=1&v={VERSION}",
                width=800, height=600, frameless=True, focus=False, background_color="#05050a")
            wallpaper_window.events.shown += lambda: threading.Thread(target=wallpaper_loop, args=(wallpaper_window,),
                                                                      daemon=True).start()
        elif not on and wallpaper_window is not None:
            w, wallpaper_window = wallpaper_window, None
            try:
                w.destroy()
            except Exception as e:
                print("[wallpaper]", repr(e))
            refresh_desktop()


def refresh_desktop():
    """拿掉桌布模式後，請 Windows 重畫原本的桌布 (不然會留著最後一格畫面)。"""
    import ctypes
    buf = ctypes.create_unicode_buffer(520)
    ctypes.windll.user32.SystemParametersInfoW(0x73, 520, buf, 0)  # SPI_GETDESKWALLPAPER
    ctypes.windll.user32.SystemParametersInfoW(0x14, 0, buf, 0)    # SPI_SETDESKWALLPAPER (同一張，不存檔)


def wallpaper_loop(w):
    """放到桌面圖示後面；之後每秒看一下：換螢幕、檔案總管重開了就重新放；桌面被蓋滿就暫停。"""
    import ctypes
    import screens
    import wallpaper
    from System import Action
    form = w.native
    hwnd = ctypes.c_void_p(form.Handle.ToInt64()).value
    picker = screens.Picker([hwnd])
    placed, progman, paused = None, None, False
    while wallpaper_window is w and not quitting:
        try:
            mon = picker.pick(config["wallpaper"]["monitor"])
            pm = ctypes.windll.user32.FindWindowW("Progman", None)
            if mon["rect"] != placed or pm != progman:
                form.Invoke(Action(lambda: wallpaper.attach(hwnd, mon["rect"])))
                placed, progman, paused = mon["rect"], pm, False
            cov = wallpaper.desktop_covered(mon["rect"], own=(hwnd,))
            if cov != paused:  # 桌面看不到：停止繪製 (WebView2 不可見就不跑動畫)
                paused = cov
                form.BeginInvoke(Action(lambda v=not cov: setattr(form.webview, "Visible", v)))
        except Exception as e:
            print("[wallpaper]", repr(e), flush=True)
        time.sleep(1)


settings_window = None


def set_zoom_now(form):
    """(UI 執行緒) 套用介面大小。"""
    try:
        z = config.get("ui_zoom", 100) / 100
        if abs(form.webview.ZoomFactor - z) > 0.005:
            form.webview.ZoomFactor = z
    except Exception as e:
        print("[zoom]", repr(e), flush=True)


def apply_zoom(win):
    try:
        from System import Action
        form = win.native
        form.BeginInvoke(Action(lambda: set_zoom_now(form)))
    except Exception as e:
        print("[zoom]", repr(e))


def enable_drag_regions(win):
    """讓網頁裡 CSS `app-region: drag` 的地方變成真的標題列 (WebView2 原生)：拖得動、可以拖到螢幕邊緣貼齊。"""
    try:
        from System import Action
        form = win.native

        def go():
            try:
                st = form.webview.CoreWebView2.Settings
                if not st.IsNonClientRegionSupportEnabled:
                    st.IsNonClientRegionSupportEnabled = True
                    form.webview.CoreWebView2.Reload()  # 這個設定要下一次載入頁面才生效 (只會發生一次)
            except Exception as e:
                print("[drag]", repr(e), flush=True)
            set_zoom_now(form)
            if not getattr(form, "_hummi_zoom_hook", False):
                # Ctrl + 滾輪 / Ctrl + + − 改的大小也記下來
                def changed(sender, e):
                    z = int(round(sender.ZoomFactor * 100))
                    if abs(z - config.get("ui_zoom", 100)) >= 1 and 70 <= z <= 200:
                        config["ui_zoom"] = z
                        save_config()
                        for w in (window, settings_window):  # 主視窗和設定小視窗一起
                            if w is not None and w.native is not form:
                                apply_zoom(w)
                form.webview.ZoomFactorChanged += changed
                form._hummi_zoom_hook = True
        form.BeginInvoke(Action(go))
    except Exception as e:
        print("[drag]", repr(e))


def native_drag(title):
    """拖曳無邊框視窗：交給 Windows 原生的移動 (SC_DRAGMOVE)，順便支援拖到螢幕邊緣貼齊。
    pywebview 內建的拖曳是 JS 一直回報滑鼠位置再 SetWindowPos，在 WebView2 上常常拖不動。
    用 PostMessage 非同步送，不會跨執行緒卡住 UI。"""
    import ctypes, ctypes.wintypes as wt
    u = ctypes.windll.user32
    u.FindWindowW.restype = wt.HWND
    u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    hwnd = u.FindWindowW(None, title)
    if hwnd:
        u.ReleaseCapture()
        u.PostMessageW(hwnd, 0x0112, 0xF012, 0)  # WM_SYSCOMMAND, SC_MOVE | HTCAPTION


class SettingsApi:
    """設定小視窗的標題列按鈕。"""

    def close(self):
        if settings_window is not None:
            settings_window.hide()

    def drag(self):
        native_drag("Hummi 設定")

    def expand(self):
        """打開完整的主視窗 (設定面板也打開)，小視窗收起來。"""
        self.close()
        open_settings_main()


def open_settings_main():
    show_window()
    if LOOP is not None:
        asyncio.run_coroutine_threadsafe(broadcast({"type": "open_settings"}), LOOP)


def open_settings():
    """膠囊的「更多設定」：只跳出設定面板的小視窗 (在膠囊正下方)。"""
    global settings_window
    if window is None:  # 瀏覽器除錯模式：沒有獨立視窗
        return open_settings_main()
    try:
        import ctypes
        import webview
        s = ctypes.windll.user32.GetDpiForSystem() / 96
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        w, h = 440, 720
        x, y = int(sw / s / 2 - w / 2), 64
        if settings_window is None:
            settings_window = webview.create_window(
                "Hummi 設定", f"http://127.0.0.1:{PORT}/?app=1&settings=1&v={VERSION}", width=w, height=h, x=x, y=y,
                frameless=True, easy_drag=False, on_top=True, background_color="#0b0b12", js_api=SettingsApi())

            def on_closing():
                if quitting:
                    return True
                settings_window.hide()
                return False
            settings_window.events.closing += on_closing
            settings_window.events.shown += lambda: apply_window_style(settings_window)
            settings_window.events.loaded += lambda: enable_drag_regions(settings_window)
        else:
            settings_window.show()
            settings_window.restore()
    except Exception as e:
        print("[settings]", repr(e), flush=True)
        open_settings_main()


runtime.update(media_cmd=media_cmd, art=lambda tid: media.art.get(tid), open_main=show_window,
               set_option=set_option, get_option=get_option, open_settings=open_settings,
               can_autostart=FROZEN)


def devices_msg():
    svc = runtime.get("devices")
    lst = svc.snapshot() if svc is not None else []
    return {"type": "devices", "list": lst, "low": config.get("devices", {}).get("low", 15),
            "tray": config.get("devices", {}).get("tray", True), "enabled": config["notch_opts"]["battery"]}


def push_devices():
    """裝置清單變了 (裝置服務的執行緒呼叫)：推給主視窗。"""
    if LOOP is not None:
        asyncio.run_coroutine_threadsafe(broadcast(devices_msg()), LOOP)


def open_devices():
    show_window()
    if LOOP is not None:
        asyncio.run_coroutine_threadsafe(broadcast({"type": "open_devices"}), LOOP)


runtime["open_devices"] = open_devices


def start_devices(tray=True):
    try:
        import devices
        svc = devices.DeviceService(config, save_config, DATA).start()
        svc.listeners.append(push_devices)
        runtime["devices"] = svc
        if tray:
            import tray_devices
            runtime["tray_devices"] = tray_devices.TrayDevices(svc, config, open_devices).start()
    except Exception as e:
        print("[devices] disabled:", repr(e))


def start_notch():
    import notch
    try:
        import viz
        runtime["viz"] = viz.Visualizer(state, config, extra=lambda: bool(stage_clients)).start()
    except Exception as e:
        print("[viz] disabled:", repr(e))
    start_devices()
    try:
        import folia_bridge
        runtime["folia"] = folia_bridge.FoliaBridge(state, config, lambda tid: media.art.get(tid)).start()
    except Exception as e:
        print("[folia] disabled:", repr(e))
    threading.Thread(target=notch.run, args=(state, config, runtime), daemon=True).start()


def music_volume(delta):
    """只調播放器自己的音量 (快捷鍵 / 主視窗 ↑↓)，膠囊會跳出音量條。"""
    t = state.get("track")
    if not t:
        return None
    import audio_ctl
    app = t.get("app", "")
    cur = audio_ctl.get_volume(app)
    if cur is None:
        return None
    lv = audio_ctl.set_volume(app, round(min(1.0, max(0.0, cur + delta)), 2))
    n = runtime.get("notch_obj")
    if n is not None and lv is not None:
        n.volume = (app, lv, n.last_frame)
        n.push("volume", 1.4, level=lv, app=t.get("source") or "")
    return lv


def toggle_main_window():
    if window is None:
        return
    import ctypes
    u = ctypes.windll.user32
    u.FindWindowW.restype = ctypes.c_void_p
    h = u.FindWindowW(None, APP_NAME)
    if h and u.IsWindowVisible(ctypes.c_void_p(h)) and not u.IsIconic(ctypes.c_void_p(h)):
        window.hide()
    else:
        show_window()


def start_hotkeys():
    """全域快捷鍵 (Ctrl+Alt+…)：設定裡關掉就全部釋放，讓給別的程式。"""
    hk = runtime.get("hotkeys")
    if hk is not None:
        hk.stop()
        runtime["hotkeys"] = None
    if not config.get("hotkeys", True):
        return
    try:
        import hotkeys
        runtime["hotkeys"] = hotkeys.Hotkeys({
            "toggle": lambda: media_cmd("toggle"),
            "next": lambda: media_cmd("next"),
            "prev": lambda: media_cmd("prev"),
            "vol_up": lambda: music_volume(0.05),
            "vol_down": lambda: music_volume(-0.05),
            "window": toggle_main_window,
            "stage": open_stage,
        }).start()
    except Exception as e:
        print("[hotkeys] disabled:", repr(e))


_mon_cache = [0.0, []]


def monitor_list():
    """螢幕清單 (設定裡的「顯示在」)；每 5 秒才重新列一次。"""
    if time.time() - _mon_cache[0] > 5:
        try:
            import screens
            _mon_cache[1] = [{"name": m["name"], "label": screens.label(m, i)} for i, m in enumerate(screens.monitors())]
        except Exception:
            _mon_cache[1] = []
        _mon_cache[0] = time.time()
    return _mon_cache[1]


CHAT = None


def public_config():
    """送給網頁的設定：權杖不送出去 (OBS 的網頁也連得到 /ws)。"""
    c = dict(config)
    c["chat"] = {**config["chat"], "token": "", "has_token": bool(config["chat"].get("token"))}
    return c


def start_chat():
    global CHAT
    try:
        import chat

        def now_playing():
            t = state.get("track")
            return (t["title"], t["artist"]) if t else None

        def on_ask(user, title, artist):  # 舞台 / OBS 跳出「現在播放」卡片
            asyncio.ensure_future(broadcast({"type": "chat_song", "user": user, "title": title, "artist": artist}))
        if CHAT is None:
            CHAT = chat.TwitchChat(lambda: config["chat"], now_playing, on_ask)
        CHAT.restart(LOOP)
    except Exception as e:
        print("[chat] disabled:", repr(e))


def init_share():
    global SHARE
    try:
        import share
        SHARE = share.Share(config, _norm, clean_title, clean_artist)
    except Exception as e:
        print("[share] disabled:", repr(e))


def share_everything():
    """剛設定好共享資料夾：把之前 AI 聽過的、手動指定的歌詞都放一份進去。回傳放了幾首。"""
    if SHARE is None or SHARE.folder() is None:
        return 0
    n = 0
    for k, res in list((AI.store if AI is not None else {}).items()):
        title, _, artist = k.partition("\u0000")
        if res.get("synced") and SHARE.put(title, artist, 0, res, "aligned" if res.get("ai") == "aligned" else "transcribed"):
            n += 1
    for k, res in list(lyrics.overrides.items()):
        title, _, artist = k.partition("\u0000")
        if res.get("synced") and SHARE.put(title, artist, 0, res, "manual"):
            n += 1
    return n


def start_autopause():
    try:
        import autopause
        runtime["autopause"] = autopause.AutoPause(
            config, lambda: bool((state.get("track") or {}).get("playing")),
            lambda: media_cmd("pause"), lambda: media_cmd("play")).start()
    except Exception as e:
        print("[autopause] disabled:", repr(e))


def hotkeys_failed():
    hk = runtime.get("hotkeys")
    if hk is None or not hk.failed:
        return []
    import hotkeys
    return [hotkeys.label(k) for k in hk.failed]


def quit_app(icon=None):
    global quitting
    quitting = True
    try:
        if wallpaper_window is not None:
            refresh_desktop()
    except Exception:
        pass
    try:
        if icon:
            icon.stop()
        if window is not None:
            window.destroy()
    finally:
        os._exit(0)


class WindowApi:
    """給網頁標題列按鈕呼叫 (window.pywebview.api.xxx)。"""

    def __init__(self):
        self._on_top = False
        self._normal_size = (1180, 800)

    def minimize(self):
        window.minimize()

    def hide(self):
        window.hide()  # 縮到系統匣，浮動歌詞照常運作

    def toggle_maximize(self):
        # 看視窗真正的狀態 (標題列連點兩下是 Windows 自己放大的，不會經過這裡)
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        hwnd = u.FindWindowW(None, APP_NAME)
        if hwnd and u.IsZoomed(ctypes.c_void_p(hwnd)):
            window.restore()
        else:
            window.maximize()

    def drag(self):
        native_drag(APP_NAME)

    def pick_folder(self):
        """選擇共享資料夾 (Windows 的資料夾對話框)。"""
        import webview
        r = window.create_file_dialog(webview.FileDialog.FOLDER)
        return r[0] if r else ""

    def fullscreen(self):
        """F：主視窗全螢幕 (網頁的全螢幕在 App 視窗裡沒有作用)。"""
        window.toggle_fullscreen()
        self._restyle()

    def exit_fullscreen(self):
        if getattr(window.native, "is_fullscreen", False):
            window.toggle_fullscreen()
            self._restyle()
            return True
        return False

    def _restyle(self):
        """離開全螢幕後 WinForms 會把外框樣式蓋掉，要加回來 (四邊可拉、貼齊)。"""
        ch = runtime.get("chrome")
        if ch is not None and not getattr(window.native, "is_fullscreen", False):
            from System import Action
            window.native.BeginInvoke(Action(ch.restyle))

    def toggle_on_top(self):
        self._on_top = on = not self._on_top
        # 不能用 window.on_top = ...：pywebview 會在這條 (js_api) 執行緒直接改 WinForms 的 TopMost，
        # 跨執行緒改視窗 → 整個程式卡死 (Windows 回報「沒有回應」)。直接用 Win32 非同步設定，不會等 UI 執行緒。
        import ctypes, ctypes.wintypes as wt
        u = ctypes.windll.user32
        u.FindWindowW.restype = wt.HWND
        u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
        hwnd = u.FindWindowW(None, APP_NAME)
        if hwnd:
            # HWND_TOPMOST(-1) / HWND_NOTOPMOST(-2)；SWP_NOSIZE|NOMOVE|NOACTIVATE|ASYNCWINDOWPOS
            u.SetWindowPos(hwnd, wt.HWND(-1 if on else -2), 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x4000)
        return on

    def notch(self):
        """膠囊按鈕：主視窗收起來，只留螢幕頂端的小膠囊。"""
        to_notch()


def make_app():
    app = web.Application(client_max_size=40 * 1024 * 1024)  # 背景圖片可能比較大
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/art", art_handler)

    import playercap  # 內嵌 Folia 歌詞效果的資料來源 (PlayerCap 格式) + /folia/ 靜態檔
    hub = playercap.PlayerCapHub(state, config, VERSION)
    hub.port = PORT
    hub.routes(app, ROOT)
    app["playercap"] = hub

    async def stage_page(request):
        return web.FileResponse(ROOT / "stage.html", headers={"Cache-Control": "no-store"})
    app.router.add_get("/stage", stage_page)

    async def icon_png(request):
        return web.FileResponse(ROOT / "icon.png", headers={"Cache-Control": "max-age=86400"})
    app.router.add_get("/icon.png", icon_png)

    async def show(request):
        show_window()
        return web.Response(text="ok")
    app.router.add_get("/show", show)

    async def font_list(request):
        import fonts
        items = await asyncio.get_running_loop().run_in_executor(None, fonts.listing)
        return web.json_response(items)
    app.router.add_get("/fonts", font_list)

    # ───── 歌詞：手動搜尋 / 指定 / 匯入 ─────
    async def lyr_search(request):
        t = state["track"] or {}
        title = request.query.get("title", "").strip()
        artist = request.query.get("artist", "").strip()
        if not title:
            return web.json_response([])
        cands = await lyrics.search(title, artist, limit=10)
        ranked = lyrics.rank(cands, title, artist, None, strict=False)
        dur = t.get("duration") or 0
        ranked.sort(key=lambda c: (abs((c.get("duration") or 0) - dur) > 10 if dur and c.get("duration") else False))
        return web.json_response(ranked[:30])

    async def apply_override(result, extra=()):
        t = state["track"]
        if not t:
            return web.json_response({"ok": False, "error": "沒有正在播放的歌曲"}, status=400)
        if result["lines"]:
            result["lines"] = clean_lines(result["lines"], t["title"], t["artist"], *extra)
        result = traditionalize(result)
        lyrics.set_override(t["title"], t["artist"], result)
        if SHARE is not None and result.get("synced"):
            asyncio.ensure_future(asyncio.to_thread(SHARE.put, t["title"], t["artist"], t.get("duration") or 0,
                                                    result, "manual"))
        if AI is not None:
            AI.set_job(None)
        state["lyrics"] = {"track_id": t["track_id"], **result, "override": True}
        await broadcast({"type": "lyrics", **state["lyrics"]})
        return web.json_response({"ok": True, "source": result["source"], "synced": result["synced"]})

    async def lyr_use(request):
        c = await request.json()
        if c.get("source") not in ("lrclib", "netease", "kugou", "qq", "lyricsplus"):
            return web.json_response({"ok": False, "error": "未知來源"}, status=400)
        res = await lyrics.fetch(c)
        if not res or not res["lines"]:
            return web.json_response({"ok": False, "error": "這個版本沒有歌詞內容，換一個試試"}, status=404)
        return await apply_override(res, (str(c.get("title") or ""), str(c.get("artist") or "")))

    async def lyr_import(request):
        d = await request.json()
        text = str(d.get("text") or "")[:200_000]
        if not text.strip():
            return web.json_response({"ok": False, "error": "內容是空的"}, status=400)
        return await apply_override(Lyrics.from_text(text))

    async def lyr_reset(request):
        t = state["track"]
        if t:
            lyrics.clear_override(t["title"], t["artist"])
            state["lyrics"] = None
            await broadcast({"type": "lyrics", "track_id": t["track_id"], "loading": True, "lines": []})
            asyncio.create_task(fetch_lyrics(t))
        return web.json_response({"ok": True})

    async def lyr_find(request):
        return web.json_response(await find_by_lyric(request.query.get("q", "").strip()[:120]))

    async def lyr_open(request):
        d = await request.json()
        open_in_player(str(d.get("title") or ""), str(d.get("artist") or ""), str(d.get("where") or "spotify"))
        return web.json_response({"ok": True})

    async def lyr_publish(request):
        """把目前這首歌的同步歌詞上傳到 LRCLIB (公開資料庫)。"""
        t, cur = state["track"], state["lyrics"] or {}
        if not t or not cur.get("synced") or not cur.get("lines") or cur.get("track_id") != t["track_id"]:
            return web.json_response({"ok": False, "error": "這首歌現在沒有同步歌詞"})
        import share
        try:
            ok, msg = await share.publish_lrclib(await lyrics.session(), UA, clean_title(t["title"]),
                                                 clean_artist(t["artist"]), t.get("album") or "",
                                                 t.get("duration") or 0, cur["lines"])
        except Exception as e:
            ok, msg = False, f"上傳失敗：{e!r}"[:160]
        return web.json_response({"ok": ok, "error": "" if ok else msg, "msg": msg})

    async def share_status(request):
        total = await asyncio.to_thread(SHARE.count) if SHARE else 0
        return web.json_response({"folder": config["share"]["folder"], "ok": bool(SHARE and SHARE.folder()), "total": total})

    app.router.add_post("/lyrics/publish", lyr_publish)
    app.router.add_get("/share/status", share_status)
    app.router.add_get("/lyrics/find", lyr_find)
    app.router.add_post("/lyrics/open", lyr_open)
    app.router.add_get("/lyrics/search", lyr_search)
    app.router.add_post("/lyrics/use", lyr_use)
    app.router.add_post("/lyrics/import", lyr_import)
    app.router.add_post("/lyrics/reset", lyr_reset)

    # ───── 自訂背景圖片 ─────
    bg_dir = DATA / "backgrounds"

    async def bg_upload(request):
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(
            request.content_type)
        if not ext:
            return web.json_response({"ok": False, "error": "只支援 JPG、PNG、GIF、WebP"}, status=400)
        data = await request.read()
        bg_dir.mkdir(parents=True, exist_ok=True)
        old = config["background"].get("image")
        name = f"bg_{int(time.time())}{ext}"
        (bg_dir / name).write_bytes(data)
        if old and old != name:  # 只保留目前用的那張
            try:
                (bg_dir / Path(old).name).unlink()
            except OSError:
                pass
        config["background"].update(image=name, mode="image")
        config_changed()
        return web.json_response({"ok": True, "image": name})

    async def bg_file(request):
        f = bg_dir / Path(request.match_info["name"]).name  # 只取檔名，避免路徑跳脫
        if not f.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Cache-Control": "max-age=31536000"})

    app.router.add_post("/background", bg_upload)
    app.router.add_get("/bg/{name}", bg_file)
    app.on_startup.append(on_startup)
    return app


def serve_in_thread(ready: threading.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(make_app())
    loop.run_until_complete(runner.setup())
    loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", PORT).start())
    ready.set()
    loop.run_forever()


def consume_startup_show_state():
    """Windows 會把啟動者指定的「最小化/隱藏」套用在程式第一次 ShowWindow 上。
    先用一個用完即丟的視窗把它吃掉，主視窗才會正常顯示。"""
    import ctypes
    u = ctypes.windll.user32
    u.CreateWindowExW.restype = ctypes.c_void_p
    u.CreateWindowExW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint] + \
        [ctypes.c_int] * 4 + [ctypes.c_void_p] * 4
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.DestroyWindow.argtypes = [ctypes.c_void_p]
    h = u.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, None, None, None, None)
    if h:
        u.ShowWindow(h, 0)
        u.DestroyWindow(h)


def already_running():
    """已經開著的話，叫它把視窗拿出來就好。"""
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/show", timeout=1)
        return True
    except Exception:
        return False


def set_dpi_awareness():
    """在任何視窗建立前設定一次，主視窗和浮動歌詞才會用同一套縮放。"""
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-monitor v2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def main():
    global window
    init_share()
    set_dpi_awareness()
    try:
        # 程式忙的時候 (電腦在跑遊戲、開別的程式)，膠囊 / 浮動歌詞的執行緒可能幾秒沒回應，
        # Windows 就會蓋一個「沒有回應」的殘影視窗在上面：透明視窗的殘影是一整塊黑框，而且常常不會消失。
        # 這兩個視窗不需要那個殘影，整個關掉。
        import ctypes
        ctypes.windll.user32.DisableProcessWindowsGhosting()
    except Exception:
        pass
    if "--ai-selftest" in sys.argv:  # 檢查 AI 元件：下載 (需要時)、載入模型、跑一次辨識，結果寫在 app.log
        import ai_lyrics
        import numpy as np
        eng = ai_lyrics.AIEngine.__new__(ai_lyrics.AIEngine)
        eng.status, eng.device = {}, None
        eng._set_status = lambda st, detail="", progress=None: print("[selftest]", st, detail,
                                                                     f"{progress:.0%}" if progress else "", flush=True)
        t0 = time.time()
        eng.model = eng._load_model()
        job = ai_lyrics.Job({"track_id": "t", "title": "test", "artist": "", "duration": 60}, "t", None)
        eng._transcribe(np.zeros(16000 * 3, np.float32), job)
        print(f"[selftest] OK device={eng.device} in {time.time() - t0:.1f}s", flush=True)
        return
    import overlay
    import notch
    import fonts
    fonts.start(str(DATA / "fonts_cache.json"))

    def _fonts_ready():  # 字體清單掃完後，讓浮動歌詞重新套用自選字體
        fonts._ready.wait(60)
        runtime["cfg_version"] += 1
    threading.Thread(target=_fonts_ready, daemon=True).start()
    migrate_startup_shortcut()
    try:
        init_whatsnew()
    except Exception as e:
        print("[whatsnew]", repr(e))
    if "--web" in sys.argv:  # 只開伺服器 (除錯用，用瀏覽器看)
        if "--headless" not in sys.argv:  # --headless：測試用，不開浮動歌詞和系統匣 (不跟已安裝的版本重複)
            threading.Thread(target=overlay.run, args=(state, config, runtime, save_config), daemon=True).start()
            start_notch()
            start_tray()
        print(f"🎵 Hummi → http://localhost:{PORT}", flush=True)
        web.run_app(make_app(), host="127.0.0.1", port=PORT, print=None)
        return

    if already_running():
        return
    consume_startup_show_state()
    import webview
    ready = threading.Event()
    threading.Thread(target=serve_in_thread, args=(ready,), daemon=True).start()
    ready.wait(10)
    threading.Thread(target=overlay.run, args=(state, config, runtime, save_config), daemon=True).start()
    start_notch()
    start_tray()
    start_hotkeys()
    start_autopause()

    window = webview.create_window(
        APP_NAME, f"http://127.0.0.1:{PORT}/?app=1&v={VERSION}",
        width=1180, height=800, min_size=(380, 420),
        frameless=True, easy_drag=False, background_color="#0b0b12", js_api=WindowApi(),
    )

    def on_closing():
        if quitting:
            return True
        window.hide()  # 關閉 = 縮到系統匣
        return False
    window.events.closing += on_closing

    background = "--background" in sys.argv  # 開機自動啟動：只留系統匣 + 浮動歌詞

    def on_shown():
        # 保險：不管怎麼被啟動，都以正常大小建立 (縮小狀態下 WebView2 會空白)
        window.restore()
        window.resize(1180, 800)
        apply_window_style()
        g = config.get("window")
        if g:  # 上次的位置和大小 (含放大)
            import winchrome
            winchrome.set_placement(main_hwnd(), g, show=not background)
        threading.Thread(target=placement_loop, daemon=True).start()
        if config["wallpaper"]["enabled"]:
            threading.Thread(target=sync_wallpaper, daemon=True).start()
        if background:
            window.hide()  # 先以正常大小建立好再藏起來，之後從系統匣叫出來才不會空白
    window.events.shown += on_shown
    window.events.loaded += lambda: enable_drag_regions(window)
    webview.start(private_mode=False, storage_path=str(DATA / ".webview"))
    quit_app()


if __name__ == "__main__":
    main()
