"""
掃描電腦上已安裝的字體 (系統 + 使用者安裝)，整理成「字體家族」清單：
  - 英文家族名 (給網頁 CSS 用)、中文名稱 (給選單顯示)
  - 是否支援 中文 / 日文 / 韓文 (查字體的 cmap，不用猜)
  - 浮動歌詞要用的字檔 (優先挑粗體)
結果快取在 fonts_cache.json，字體沒變就不會重掃。
"""
import json
import os
import threading

from fontTools.ttLib import TTFont, TTCollection

WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
USER_FONTS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
EXTS = (".ttf", ".otf", ".ttc", ".otc")
STYLE_PREF = ["Bold", "SemiBold", "Semibold", "DemiBold", "Demibold", "Medium", "Black", "Heavy",
              "Regular", "Normal", "Book"]
PROBE = {"zh": [0x611B, 0x6B4C, 0x8A5E], "ja": [0x3042, 0x30A2], "ko": [0xAC00, 0xD55C],  # 愛歌詞 / あア / 가한
         "latin": [0x41, 0x61, 0x65, 0x30]}  # A a e 0：沒有的是符號字體 (Wingdings 之類)

_index: dict = {}        # family -> info
_ready = threading.Event()


def _name(font, nid, lang):
    rec = font["name"].getName(nid, 3, 1, lang)
    return str(rec) if rec else None


def _faces(path):
    if path.lower().endswith((".ttc", ".otc")):
        try:
            n = len(TTCollection(path, lazy=True).fonts)
        except Exception:
            n = 1
        return range(n)
    return range(1)


def _scan_file(path):
    out = []
    for i in _faces(path):
        try:
            f = TTFont(path, fontNumber=i, lazy=True)
            name = f["name"]
            family = name.getBestFamilyName()
            style = name.getBestSubFamilyName() or "Regular"
            if not family or family.startswith("."):
                continue
            local = _name(f, 16, 0x404) or _name(f, 1, 0x404) or _name(f, 16, 0x804) or _name(f, 1, 0x804) \
                or _name(f, 16, 0x411) or _name(f, 1, 0x411)
            cmap = f.getBestCmap() or {}
            langs = {k: all(c in cmap for c in v) for k, v in PROBE.items()}
            out.append({"family": family, "style": style, "local": local, "path": path, "index": i, **langs})
        except Exception:
            continue
    return out


def _build(cache_path):
    files = []
    for d in (WIN_FONTS, USER_FONTS):
        try:
            for fn in os.listdir(d):
                if fn.lower().endswith(EXTS):
                    p = os.path.join(d, fn)
                    st = os.stat(p)
                    files.append((p, int(st.st_mtime), st.st_size))
        except Exception:
            pass
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:
        cache = {}
    faces, new_cache = [], {}
    for p, mt, sz in files:
        key = f"v2|{p}|{mt}|{sz}"
        entry = cache.get(key)
        if entry is None:
            entry = _scan_file(p)
        new_cache[key] = entry
        faces.extend(entry)
    try:
        json.dump(new_cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

    fam: dict = {}
    for fc in faces:
        info = fam.setdefault(fc["family"], {"family": fc["family"], "local": fc["local"], "faces": [],
                                              **{k: False for k in PROBE}})
        info["faces"].append(fc)
        info["local"] = info["local"] or fc["local"]
        for k in PROBE:
            info[k] = info[k] or fc[k]
    fam = {k: v for k, v in fam.items() if v["latin"]}  # 拿掉符號字體
    for info in fam.values():
        def rank(fc):
            st = fc["style"].replace(" ", "")
            return STYLE_PREF.index(st) if st in STYLE_PREF else len(STYLE_PREF) + ("Italic" in st)
        best = min(info["faces"], key=rank)
        info["path"], info["index"] = best["path"], best["index"]
    _index.clear()
    _index.update(fam)
    _ready.set()


def start(cache_path):
    threading.Thread(target=_build, args=(cache_path,), daemon=True).start()


def get(family):
    return _index.get(family) if family else None


def listing():
    """給設定頁的清單：支援中文的排前面，再依名稱排序。"""
    _ready.wait(20)
    items = [{"family": i["family"], "local": i["local"], "zh": i["zh"], "ja": i["ja"], "ko": i["ko"]}
             for i in _index.values()]
    items.sort(key=lambda i: (not i["zh"], (i["local"] or i["family"]).lower()))
    return items
