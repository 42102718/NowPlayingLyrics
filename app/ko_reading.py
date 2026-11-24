"""韓文歌詞的羅馬拼音 (國語羅馬字 Revised Romanization，照實際發音)。
不用額外套件：把韓文字拆成 初聲 / 中聲 / 終聲，再套常見的發音規則：
- 連音：받침 後面接 ㅇ 開頭的字，받침 移到下一個字 (사랑해 → saranghae、있어 → isseo)
- 鼻音化：ㄱ ㄷ ㅂ 類的 받침 遇到 ㄴ ㅁ 變成 ng n m (합니다 → hamnida)
- 流音化：ㄴ + ㄹ、ㄹ + ㄴ、ㄹ + ㄹ 都唸 ll (신라 → silla)
- 激音化：ㅎ 碰到 ㄱ ㄷ ㅂ ㅈ 合成 k t p ch (좋고 → joko、축하 → chuka)
- ㅎ 받침 + ㅇ：ㅎ 不發音 (좋아 → joa)
- 口蓋音化：ㄷ ㅌ 받침 + 이 唸 j ch (같이 → gachi)
"""
import re

INITIAL = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
VOWEL = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu",
         "eu", "ui", "i"]
# 終聲 (index 0 = 沒有)
FINAL_JAMO = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ",
              "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
INITIAL_JAMO = ["ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
# 單獨唸 (後面是子音或句尾) 的 받침
CODA = {"": "", "ㄱ": "k", "ㄲ": "k", "ㄳ": "k", "ㄴ": "n", "ㄵ": "n", "ㄶ": "n", "ㄷ": "t", "ㄹ": "l", "ㄺ": "k", "ㄻ": "m",
        "ㄼ": "l", "ㄽ": "l", "ㄾ": "l", "ㄿ": "p", "ㅀ": "l", "ㅁ": "m", "ㅂ": "p", "ㅄ": "p", "ㅅ": "t", "ㅆ": "t",
        "ㅇ": "ng", "ㅈ": "t", "ㅊ": "t", "ㅋ": "k", "ㅌ": "t", "ㅍ": "p", "ㅎ": "t"}
# 連音：(留在這個字的部分, 移到下一個字當初聲的 jamo)
LINK = {"ㄱ": ("", "ㄱ"), "ㄲ": ("", "ㄲ"), "ㄳ": ("ㄱ", "ㅅ"), "ㄴ": ("", "ㄴ"), "ㄵ": ("ㄴ", "ㅈ"), "ㄶ": ("", "ㄴ"),
        "ㄷ": ("", "ㄷ"), "ㄹ": ("", "ㄹ"), "ㄺ": ("ㄹ", "ㄱ"), "ㄻ": ("ㄹ", "ㅁ"), "ㄼ": ("ㄹ", "ㅂ"), "ㄽ": ("ㄹ", "ㅅ"),
        "ㄾ": ("ㄹ", "ㅌ"), "ㄿ": ("ㄹ", "ㅍ"), "ㅀ": ("", "ㄹ"), "ㅁ": ("", "ㅁ"), "ㅂ": ("", "ㅂ"), "ㅄ": ("ㅂ", "ㅅ"),
        "ㅅ": ("", "ㅅ"), "ㅆ": ("", "ㅆ"), "ㅈ": ("", "ㅈ"), "ㅊ": ("", "ㅊ"), "ㅋ": ("", "ㅋ"), "ㅌ": ("", "ㅌ"),
        "ㅍ": ("", "ㅍ"), "ㅎ": ("", "ㅇ")}
K_CLASS, T_CLASS, P_CLASS = set("ㄱㄲㅋㄳㄺ"), set("ㄷㅅㅆㅈㅊㅌㅎ"), set("ㅂㅍㅄㄿ")
ASPIRATE = {"ㄱ": "ㅋ", "ㄷ": "ㅌ", "ㅂ": "ㅍ", "ㅈ": "ㅊ"}
HANGUL = re.compile(r"[가-힣]")


def _split(ch):
    n = ord(ch) - 0xAC00
    return INITIAL_JAMO[n // 588], VOWEL[(n % 588) // 28], FINAL_JAMO[n % 28]


def _word(syls):
    """一個連在一起的韓文字串 (沒有空白)。syls = [(初聲, 中聲, 終聲), ...]。"""
    syls = [list(s) for s in syls]
    out = []
    for i, (ini, vow, fin) in enumerate(syls):
        nxt = syls[i + 1] if i + 1 < len(syls) else None
        coda = CODA[fin]
        if nxt is not None and fin:
            ni = nxt[0]
            if ni == "ㅇ":  # 連音
                stay, move = LINK[fin]
                coda = CODA[stay] if stay else ""
                if stay == "ㄹ" and move:
                    coda = "l"
                if move in ("ㄷ", "ㅌ") and nxt[1] == "i":  # 口蓋音化：같이 → gachi、굳이 → guji
                    move = "ㅈ" if move == "ㄷ" else "ㅊ"
                nxt[0] = move
            elif ni == "ㅎ" and (fin in K_CLASS or fin in T_CLASS or fin in P_CLASS):  # 激音化 (축하 → chuka)
                coda = ""
                nxt[0] = "ㅋ" if fin in K_CLASS else "ㅍ" if fin in P_CLASS else ("ㅊ" if fin in "ㅈㅊ" else "ㅌ")
            elif fin in ("ㅎ", "ㄶ", "ㅀ") and ni in ASPIRATE:  # 좋고 → joko
                coda = "n" if fin == "ㄶ" else "l" if fin == "ㅀ" else ""
                nxt[0] = ASPIRATE[ni]
            elif ni in ("ㄴ", "ㅁ"):  # 鼻音化
                if fin in K_CLASS:
                    coda = "ng"
                elif fin in T_CLASS:
                    coda = "n"
                elif fin in P_CLASS:
                    coda = "m"
                elif fin == "ㄹ" and ni == "ㄴ":  # 流音化 (ㄹ + ㄴ → ll)
                    coda, nxt[0] = "l", "ㄹ_l"
            elif ni == "ㄹ":
                if fin in ("ㄹ", "ㄴ"):  # 신라 → silla、설날 → seollal
                    coda, nxt[0] = "l", "ㄹ_l"
                elif fin in ("ㅁ", "ㅇ"):  # 종로 → jongno
                    nxt[0] = "ㄴ"
                elif fin in K_CLASS:
                    coda, nxt[0] = "ng", "ㄴ"
                elif fin in P_CLASS:
                    coda, nxt[0] = "m", "ㄴ"
        if ini == "ㄹ_l":
            head = "l"
        else:
            head = INITIAL[INITIAL_JAMO.index(ini)] if ini in INITIAL_JAMO else ""
        if i == 0 and ini == "ㄹ":
            head = "r"
        out.append(head + vow + coda)
    return "".join(out)


def romanize(text):
    """一句韓文 → 羅馬拼音 (其他文字照原樣留著)。"""
    text = text or ""
    out, buf = [], []

    def flush():
        if buf:
            out.append(_word([_split(c) for c in buf]))
            buf.clear()
    for ch in text:
        if HANGUL.match(ch):
            buf.append(ch)
        else:
            flush()
            out.append(ch)
    flush()
    return re.sub(r"\s+", " ", "".join(out)).strip()


def is_korean(lines):
    """這首歌是不是韓文歌 (有三分之一以上的句子有韓文字)。"""
    texts = [(l.get("text") or "") for l in lines if (l.get("text") or "").strip()]
    if not texts:
        return False
    return sum(1 for t in texts if HANGUL.search(t)) >= max(1, len(texts) // 3)


def apply(lines):
    """回傳加上羅馬拼音 (rd) 的新 lines；不是韓文歌就原樣回傳。已經有讀音的句子不動。"""
    if not is_korean(lines):
        return lines
    out = []
    for l in lines:
        t = l.get("text") or ""
        n = dict(l)
        if HANGUL.search(t) and not n.get("rd"):
            r = romanize(t)
            if r and r != t:
                n["rd"] = r
        out.append(n)
    return out
