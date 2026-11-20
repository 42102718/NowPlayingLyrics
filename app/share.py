"""和朋友共享歌詞。

1. 共用資料夾 (私人，自動)：
   大家把「共享資料夾」設成同一個雲端硬碟資料夾 (OneDrive / Google 雲端硬碟 / Dropbox 共用資料夾)。
   AI 聽寫 / 對時完的歌詞、手動指定的歌詞，會存一份到 <資料夾>/Hummi 歌詞/；
   朋友的 Hummi 找不到同步歌詞時會先看這裡，有就直接用，AI 不用再聽一次。
2. LRCLIB (公開，手動)：
   自己確認過沒問題的歌詞，可以按一下上傳到 LRCLIB 開放歌詞資料庫，所有人 (包括 Hummi) 之後都查得到。
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

SUBDIR = "Hummi 歌詞"
# 品質排序：手動指定 > AI 對時 (有官方純文字歌詞) > AI 聽寫
RANK = {"manual": 3, "aligned": 2, "transcribed": 1}


class Share:
    def __init__(self, config, norm, clean_title, clean_artist):
        self.config = config
        self.norm, self.clean_title, self.clean_artist = norm, clean_title, clean_artist
        self._cache = {}  # key -> (讀取時間, 結果)

    # ───── 共用資料夾 ─────
    def folder(self):
        f = (self.config.get("share") or {}).get("folder") or ""
        if not f or not os.path.isdir(f):
            return None
        d = Path(f) / SUBDIR
        d.mkdir(exist_ok=True)
        return d

    def key(self, title, artist):
        k = self.norm(self.clean_title(title)) + "|" + self.norm(self.clean_artist(artist))
        return hashlib.sha1(k.encode("utf-8")).hexdigest()[:20]

    def get(self, title, artist, duration=0):
        """朋友 (或自己別台電腦) 分享的歌詞；長度差太多 (不同版本) 就不用。"""
        d = self.folder()
        if d is None:
            return None
        k = self.key(title, artist)
        hit = self._cache.get(k)
        if hit and time.time() - hit[0] < 60:
            data = hit[1]
        else:
            data = None
            try:
                p = d / f"{k}.json"
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print("[share] read", repr(e))
            self._cache[k] = (time.time(), data)
        if not data:
            return None
        if duration and data.get("duration") and abs(float(data["duration"]) - float(duration)) > 4:
            return None  # 長度差太多：是別的版本 (Live / 剪輯版)
        who = data.get("by") or "朋友"
        return {"source": f"{who} 分享", "synced": bool(data.get("synced", True)),
                "lines": data.get("lines") or [], "shared": True, "kind": data.get("kind", "")}

    def put(self, title, artist, duration, result, kind):
        """存一份到共用資料夾 (比資料夾裡現有的差就不蓋掉)。"""
        d = self.folder()
        if d is None or not result or not result.get("lines"):
            return False
        k = self.key(title, artist)
        p = d / f"{k}.json"
        try:
            if p.exists():
                old = json.loads(p.read_text(encoding="utf-8"))
                if RANK.get(old.get("kind"), 0) > RANK.get(kind, 0):
                    return False
                if old.get("kind") == kind and old.get("heard", 1) > result.get("heard", 1) + 0.03:
                    return False
            lines = [{k_: v for k_, v in l.items() if k_ in ("t", "text", "words", "end")} for l in result["lines"]]
            data = {"title": title, "artist": artist, "duration": round(float(duration or 0), 1), "kind": kind,
                    "synced": bool(result.get("synced", True)), "heard": result.get("heard", 1),
                    "by": (self.config.get("share") or {}).get("name") or "", "at": int(time.time()), "lines": lines}
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
            self._cache.pop(k, None)
            return True
        except Exception as e:
            print("[share] write", repr(e))
            return False

    def count(self):
        d = self.folder()
        return len(list(d.glob("*.json"))) if d else 0


# ───────────────── LRCLIB 上傳 ─────────────────
def to_lrc(lines):
    out = []
    for l in lines:
        t = l.get("t")
        if t is None:
            continue
        m, s = divmod(max(0.0, float(t)), 60)
        out.append(f"[{int(m):02d}:{s:05.2f}]{l.get('text', '')}")
    return "\n".join(out)


def solve_challenge(prefix, target_hex, stop=lambda: False):
    """LRCLIB 的工作量證明：找一個整數 nonce，讓 sha256(prefix + nonce) <= target。"""
    target = bytes.fromhex(target_hex)
    pre = prefix.encode()
    n = 0
    while not stop():
        if hashlib.sha256(pre + str(n).encode()).digest() <= target:
            return str(n)
        n += 1
    return None


async def publish_lrclib(http, ua, title, artist, album, duration, lines):
    """回傳 (成功嗎, 訊息)。"""
    import asyncio
    synced = to_lrc(lines)
    plain = "\n".join(l.get("text", "") for l in lines if l.get("text"))
    if not synced or not duration:
        return False, "這首歌沒有同步歌詞或長度，不能上傳"
    hdr = {"User-Agent": ua, "Lrclib-Client": ua}
    async with http.post("https://lrclib.net/api/request-challenge", headers=hdr) as r:
        if r.status != 200:
            return False, f"LRCLIB 沒有回應 ({r.status})"
        ch = await r.json()
    nonce = await asyncio.to_thread(solve_challenge, ch["prefix"], ch["target"])
    body = {"trackName": title, "artistName": artist, "albumName": album or "", "duration": round(float(duration)),
            "plainLyrics": plain, "syncedLyrics": synced}
    async with http.post("https://lrclib.net/api/publish", json=body,
                         headers={**hdr, "X-Publish-Token": f"{ch['prefix']}:{nonce}"}) as r:
        if r.status in (200, 201):
            return True, "已上傳到 LRCLIB，大家之後都查得到這首歌的歌詞了"
        try:
            msg = (await r.json()).get("message") or ""
        except Exception:
            msg = await r.text()
        return False, f"上傳失敗：{msg[:120] or r.status}"
