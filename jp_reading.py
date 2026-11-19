"""日文歌詞的讀音：漢字標平假名 (furigana)、整句平假名、羅馬拼音。

用 pykakasi (MIT 授權) 把漢字轉成假名。設定在 設定 → 歌詞 → 日文讀音：
  off        關 (預設)
  furigana   漢字上方標平假名 (只標漢字，送り仮名不重複標)
  hiragana   歌詞下面多一行整句平假名
  romaji     歌詞下面多一行羅馬拼音 (平文式)
"""
import re
from functools import lru_cache

MODES = ("off", "furigana", "hiragana", "romaji")
KANJI = "㐀-䶿一-鿿豈-﫿々〆ヶ"
_KANJI_RE = re.compile(f"[{KANJI}]")
_RUN_RE = re.compile(f"[{KANJI}]+|[^{KANJI}]+")
KANA = re.compile(r"[ぁ-ゖァ-ヺー]")

_kks = None


def _k():
    global _kks
    if _kks is None:
        import pykakasi
        _kks = pykakasi.kakasi()
    return _kks


def kata2hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_japanese(lines):
    """整首有兩成以上的句子有假名才當日文歌 (中文歌偶爾出現一個「の」不算)。"""
    texts = [l.get("text") or "" for l in lines if (l.get("text") or "").strip()]
    if not texts:
        return False
    return sum(1 for t in texts if KANA.search(t)) >= max(1, len(texts) * 0.2)


@lru_cache(maxsize=4000)
def ruby(text):
    """[[開始位置, 字數, 讀音], ...]：只標漢字，例如「思い出」→ 思(おも) い 出(で)。"""
    out, pos = [], 0
    try:
        items = _k().convert(text)
    except Exception:
        return ()
    for it in items:
        o, h = it.get("orig") or "", kata2hira(it.get("hira") or "")
        i = text.find(o, pos)
        if i < 0 or not o:
            continue
        pos = i + len(o)
        if not _KANJI_RE.search(o) or not h or h == o:
            continue
        runs = _RUN_RE.findall(o)
        # 漢字的部分讀音未知、假名的部分要一樣：「取り消せ」+「とりけせ」→ 取(と) り 消(け) せ
        pat = "^" + "".join("(.+?)" if _KANJI_RE.match(r) else re.escape(kata2hira(r)) for r in runs) + "$"
        m = re.match(pat, h)
        if m:
            off, g = i, 0
            for r in runs:
                if _KANJI_RE.match(r):
                    out.append((off, len(r), m.group(g + 1)))
                    g += 1
                off += len(r)
        else:
            out.append((i, len(o), h))
    return tuple(out)


@lru_cache(maxsize=4000)
def hiragana(text):
    try:
        return "".join(kata2hira(it.get("hira") or it.get("orig") or "") for it in _k().convert(text)).strip()
    except Exception:
        return ""


@lru_cache(maxsize=4000)
def romaji(text):
    try:
        parts = [(it.get("hepburn") or it.get("orig") or "").strip() for it in _k().convert(text)]
    except Exception:
        return ""
    s = " ".join(p for p in parts if p)
    s = re.sub(r"\s+([,.!?、。！？」』）)])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def apply(lines, mode):
    """回傳加上讀音的新 lines (原本的不動)；不是日文歌或模式是 off 就原樣回傳。"""
    if mode not in MODES or mode == "off" or not is_japanese(lines):
        return lines
    out = []
    for l in lines:
        t = l.get("text") or ""
        n = dict(l)
        if t.strip() and (_KANJI_RE.search(t) or KANA.search(t)):
            if mode == "furigana":
                r = ruby(t)
                if r:
                    n["ruby"] = [list(x) for x in r]
            elif mode == "hiragana":
                h = hiragana(t)
                if h and h != t:
                    n["rd"] = h
            elif mode == "romaji":
                r = romaji(t)
                if r:
                    n["rd"] = r
        out.append(n)
    return out
