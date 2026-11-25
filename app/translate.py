"""歌詞翻譯：機器翻譯 (MyMemory 免費 / DeepL 自己的金鑰) + 翻譯結果存檔。

人工翻譯 (網易雲、QQ 音樂的網友翻譯) 在 server.py 的 Lyrics.human_translation 找，這裡只管機器翻譯。
每首歌翻過一次就存起來 (translations.json)，之後不再送出去、也不吃額度。
"""
import asyncio
import json
import re
import threading
import time

# 可以選的翻譯語言 (代碼, 顯示名稱)
LANGS = [("zh-TW", "繁體中文"), ("en", "English"), ("ja", "日本語"), ("ko", "한국어"), ("es", "Español"),
         ("fr", "Français"), ("de", "Deutsch"), ("th", "ไทย"), ("vi", "Tiếng Việt"), ("id", "Bahasa Indonesia")]
LANG_CODES = {c for c, _ in LANGS}
DEEPL_CODES = {"zh-TW": "ZH-HANT", "en": "EN-US", "ja": "JA", "ko": "KO", "es": "ES", "fr": "FR", "de": "DE",
               "id": "ID"}  # DeepL 沒有泰文、越南文：那兩種改用 MyMemory

KANA = re.compile(r"[぀-ヿ]")
HANGUL = re.compile(r"[가-힣]")
HAN = re.compile(r"[一-鿿]")
THAI = re.compile(r"[฀-๿]")


class QuotaError(Exception):
    """今天的免費額度用完了。"""


def song_lang(lines):
    """粗略判斷這首歌是什麼語言 (看文字的種類)：zh / ja / ko / th / other。"""
    text = " ".join((l.get("text") or "") for l in lines)
    n = max(1, len(re.sub(r"\s", "", text)))
    if len(KANA.findall(text)) / n > 0.05:
        return "ja"
    if len(HANGUL.findall(text)) / n > 0.15:
        return "ko"
    if len(THAI.findall(text)) / n > 0.2:
        return "th"
    if len(HAN.findall(text)) / n > 0.3:
        return "zh"
    return "other"


def same_language(sl, target):
    """歌本身就是要翻成的語言 (中文歌翻成中文…)：不用翻。英文歌翻成英文交給翻譯服務判斷 (翻出來一樣的會被丟掉)。"""
    return (sl == "zh" and target.startswith("zh")) or sl == target


class Translator:
    def __init__(self, path, get_cfg):
        self.path = path
        self.get_cfg = get_cfg   # -> config["translate"]
        self.lock = threading.Lock()
        try:
            self.cache = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.cache = {}
        self.quota_until = 0.0   # 額度用完：這個時間以前不再送 (避免一直被拒絕)
        self.last_error = ""

    def save(self):
        with self.lock:
            if len(self.cache) > 1500:  # 只留最近的 1500 首
                for k in sorted(self.cache, key=lambda k: self.cache[k].get("at", 0))[:len(self.cache) - 1500]:
                    self.cache.pop(k, None)
            try:
                self.path.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                print("[translate] save", repr(e), flush=True)

    def cached(self, song_key, target):
        return self.cache.get(f"{song_key}\u0000{target}")

    def remember(self, song_key, target, mapping, provider):
        self.cache[f"{song_key}\u0000{target}"] = {"map": mapping, "by": provider, "at": time.time()}
        self.save()

    def provider(self, target):
        cfg = self.get_cfg()
        if cfg.get("provider") == "deepl" and cfg.get("deepl_key") and target in DEEPL_CODES:
            return "deepl"
        return "mymemory"

    async def translate(self, http, texts, target):
        """一串句子 → {原句: 翻譯}。重複的句子只翻一次。額度用完丟 QuotaError。"""
        uniq = list(dict.fromkeys(t.strip() for t in texts if t and t.strip()))
        if not uniq:
            return {}, None
        if time.time() < self.quota_until:
            raise QuotaError(self.last_error or "今天的免費額度用完了")
        prov = self.provider(target)
        out = await (self._deepl(http, uniq, target) if prov == "deepl" else self._mymemory(http, uniq, target))
        return {k: v for k, v in out.items() if v and v.strip() and v.strip().lower() != k.strip().lower()}, prov

    async def _deepl(self, http, texts, target):
        cfg = self.get_cfg()
        key = cfg.get("deepl_key", "").strip()
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        out = {}
        for i in range(0, len(texts), 50):
            part = texts[i:i + 50]
            data = [("text", t) for t in part] + [("target_lang", DEEPL_CODES[target])]
            async with http.post(f"https://{host}/v2/translate", data=data,
                                 headers={"Authorization": f"DeepL-Auth-Key {key}"}, timeout=15) as r:
                if r.status in (456, 429):
                    self.quota_until = time.time() + 3600
                    self.last_error = "DeepL 這個月的免費額度用完了" if r.status == 456 else "DeepL 太忙，等一下再試"
                    raise QuotaError(self.last_error)
                if r.status == 403:
                    self.last_error = "DeepL 金鑰不對"
                    raise QuotaError(self.last_error)
                d = await r.json(content_type=None)
            for src, tr in zip(part, d.get("translations") or []):
                out[src] = tr.get("text") or ""
        return out

    async def _mymemory(self, http, texts, target):
        """MyMemory：一次最多約 500 位元組，照句子分批 (換行分隔，翻譯也會照行回來)。"""
        cfg = self.get_cfg()
        out, batch, size = {}, [], 0
        batches = []
        for t in texts:
            b = len(t.encode("utf-8")) + 1
            if batch and size + b > 450:
                batches.append(batch)
                batch, size = [], 0
            batch.append(t)
            size += b
        if batch:
            batches.append(batch)
        for part in batches:
            params = {"q": "\n".join(part), "langpair": f"autodetect|{target}"}
            if cfg.get("email"):
                params["de"] = cfg["email"]  # 有填 email 額度變 10 倍 (MyMemory 的規定)
            async with http.get("https://api.mymemory.translated.net/get", params=params, timeout=15) as r:
                d = await r.json(content_type=None)
            if d.get("quotaFinished") or str(d.get("responseStatus")) in ("429",):
                self.quota_until = time.time() + 3 * 3600
                self.last_error = "機器翻譯今天的免費額度用完了 (可以在設定填 email 或 DeepL 金鑰)"
                raise QuotaError(self.last_error)
            res = ((d.get("responseData") or {}).get("translatedText") or "")
            got = res.split("\n")
            if len(got) == len(part):
                out.update(zip(part, got))
            else:  # 行數對不上：這一批改成一句一句翻 (最多 12 句，避免吃掉太多額度)
                for t in part[:12]:
                    async with http.get("https://api.mymemory.translated.net/get",
                                        params={**params, "q": t}, timeout=15) as r2:
                        d2 = await r2.json(content_type=None)
                    out[t] = ((d2.get("responseData") or {}).get("translatedText") or "")
                    await asyncio.sleep(0.2)
            await asyncio.sleep(0.3)  # 不要打太快
        return out
