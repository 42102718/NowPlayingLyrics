"""直播點歌單：觀眾在聊天室點歌，實況主決定唱不唱，唱完自動換下一首；也記錄今天唱了幾首。

流程：
- 觀眾打「!點歌 歌名」→ 進「等你決定」(pending)
- 實況主按「唱」→ 進「待唱清單」(queued)；按「不唱」→ 拒絕 (聊天室會告訴他)
- 「下一首」：把待唱清單第一首變成「現在唱」(now)，並在選好的平台打開搜尋
  (Hummi 沒辦法直接幫播放器按播放，所以打開搜尋讓實況主挑版本，例如伴奏版)
- 「自動下一首」開著：現在唱的那首播完，就自動換下一首、打開搜尋

「今天唱了幾首」：舞台 (或 OBS 歌詞畫面) 開著的時候，每播完一首歌自動 +1 (早上 6 點換日，半夜的直播不會被切成兩天)。
實況主自己在播放器放了點歌單裡的歌 (歌名對得上)，那首就自動變成「正在唱」，播完就從清單拿掉。
"""
import datetime
import json
import threading
import time


def stream_day(now=None):
    """直播日：早上 6 點才換日。"""
    t = datetime.datetime.fromtimestamp(now or time.time()) - datetime.timedelta(hours=6)
    return t.date().isoformat()


class SongQueue:
    def __init__(self, path, get_cfg, on_change, open_search, say):
        self.path = path
        self.get_cfg = get_cfg          # -> config["queue"]
        self.on_change = on_change      # 狀態變了：廣播給所有畫面
        self.open_search = open_search  # open_search(query)：在選好的平台打開搜尋
        self.say = say                  # say(text)：在聊天室說話 (沒有權杖就什麼都不做)
        self.lock = threading.RLock()
        self.items = []                 # {"id", "user", "query", "status", "at"}
        self.next_id = 1
        self.day = stream_day()
        self.count = 0
        self.sung = []                  # 今天唱過的歌 (最多留 60 首)
        self.user_last = {}
        # 「現在唱」綁到哪一首正在播的歌 (點下一首之後第一首開始播的歌)
        self.now_since = 0.0
        self.bound = None               # track_id
        self.bound_peak = 0.0           # 那首歌播到的最大比例
        # 計算「今天唱了幾首」用
        self.cur_tid = None
        self.cur_counted = False
        self.load()

    # ───── 存檔 ─────
    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if d.get("day") == stream_day():
            self.day, self.count, self.sung = d["day"], int(d.get("count") or 0), list(d.get("sung") or [])[:60]
        self.items = [i for i in d.get("items") or [] if i.get("status") in ("pending", "queued", "now")]
        self.next_id = max([i["id"] for i in self.items] + [0]) + 1

    def save(self):
        try:
            self.path.write_text(json.dumps({"day": self.day, "count": self.count, "sung": self.sung,
                                             "items": self.items}, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print("[queue] save", repr(e), flush=True)

    def changed(self):
        self.save()
        self.on_change()

    def snapshot(self):
        with self.lock:
            self.roll_day()
            cfg = self.get_cfg()
            return {"open": bool(cfg.get("open")), "auto_next": bool(cfg.get("auto_next")), "suffix": cfg.get("suffix") or "",
                    "mode": cfg.get("mode") or "off", "kind": cfg.get("kind"),
                    "items": [dict(i) for i in self.items], "count": self.count, "sung": self.sung[:20]}

    def _sing(self):
        return self.get_cfg().get("kind") != "play"  # 點唱 (唱歌台)；點播是放歌給大家聽

    def roll_day(self):
        d = stream_day()
        if d != self.day:
            self.day, self.count, self.sung = d, 0, []

    # ───── 聊天室 ─────
    def request(self, user, query, is_mod=False):
        """觀眾點歌，回傳要回覆的話 (None = 不回)。"""
        query = " ".join((query or "").split())[:80]
        cfg = self.get_cfg()
        with self.lock:
            if not cfg.get("open"):
                if cfg.get("mode") == "game":
                    return f"@{user} 今天是遊戲台，不開放點歌喔 🎮"
                return f"@{user} 現在沒有開放點歌喔"
            if not query:
                cmd = (cfg.get("cmds") or "!點歌").split()[0]
                return f"@{user} 用法：{cmd} 歌名 (可以加歌手)"
            now = time.time()
            if now - self.user_last.get(user, 0) < 10 and not is_mod:
                return None
            self.user_last[user] = now
            if any(i["query"].lower() == query.lower() and i["status"] in ("pending", "queued", "now") for i in self.items):
                return f"@{user} 「{query}」已經有人點了"
            limit = int(cfg.get("per_user") or 0)
            mine = sum(1 for i in self.items if i["user"] == user and i["status"] in ("pending", "queued"))
            if limit and mine >= limit and not is_mod:
                return f"@{user} 每人最多同時點 {limit} 首，等唱完再點喔"
            self.items.append({"id": self.next_id, "user": user, "query": query, "status": "pending", "at": now})
            self.next_id += 1
            self.changed()
            if self._sing():
                return f"@{user} 收到點歌：{query}，等實況主決定要不要唱 🎤"
            return f"@{user} 收到點歌：{query}，等實況主確認後播放 🎵"

    # ───── 實況主操作 ─────
    def _find(self, iid):
        return next((i for i in self.items if i["id"] == iid), None)

    def act(self, action, iid=None, value=None):
        with self.lock:
            it = self._find(iid) if iid is not None else None
            if action == "accept" and it and it["status"] == "pending":
                it["status"] = "queued"
                pos = sum(1 for i in self.items if i["status"] == "queued")
                if self._sing():
                    self.say(f"@{it['user']} 你點的「{it['query']}」排進待唱清單了 (第 {pos} 首) ✨")
                else:
                    self.say(f"@{it['user']} 你點的「{it['query']}」排進播放清單了 (第 {pos} 首) 🎵")
                if not any(i["status"] == "now" for i in self.items) and self.get_cfg().get("auto_next"):
                    self._advance()
            elif action == "reject" and it and it["status"] == "pending":
                self.items.remove(it)
                self.say(f"@{it['user']} 「{it['query']}」這次先不{'唱' if self._sing() else '播'}，謝謝你點歌 🙏")
            elif action == "remove" and it:
                self.items.remove(it)
            elif action in ("up", "down") and it and it["status"] == "queued":
                q = [i for i in self.items if i["status"] == "queued"]
                k = q.index(it)
                j = k - 1 if action == "up" else k + 1
                if 0 <= j < len(q):
                    a, b = self.items.index(q[k]), self.items.index(q[j])
                    self.items[a], self.items[b] = self.items[b], self.items[a]
            elif action == "sing" and it and it["status"] in ("queued", "pending"):
                self._finish_now(count=False)
                it["status"] = "now"
                self._start_now(it)
            elif action == "next":
                self._finish_now(count=False)
                self._advance()
            elif action == "add" and value:
                q = " ".join(str(value).split())[:80]
                if q:
                    self.items.append({"id": self.next_id, "user": "", "query": q, "status": "queued", "at": time.time()})
                    self.next_id += 1
            elif action == "count":
                self.roll_day()
                self.count = max(0, self.count + int(value or 0))
            elif action == "reset_count":
                self.count, self.sung = 0, []
            elif action == "clear":
                self.items = [i for i in self.items if i["status"] == "now"]
            else:
                return
            self.changed()

    def _finish_now(self, count):
        for i in [i for i in self.items if i["status"] == "now"]:
            self.items.remove(i)
        self.bound = None

    def _advance(self):
        nxt = next((i for i in self.items if i["status"] == "queued"), None)
        if nxt is None:
            return False
        nxt["status"] = "now"
        self._start_now(nxt)
        return True

    def _start_now(self, it):
        self.now_since = time.time()
        self.bound, self.bound_peak = None, 0.0
        if it.get("user"):
            self.say(f"🎤 接下來唱 @{it['user']} 點的「{it['query']}」" if self._sing() else f"🎵 接下來播 @{it['user']} 點的「{it['query']}」")
        try:
            self.open_search(it["query"])
        except Exception as e:
            print("[queue] open", repr(e), flush=True)

    # ───── 每秒看一次正在播的歌 ─────
    @staticmethod
    def _norm(s):
        import re
        s = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", (s or "").lower())
        return re.sub(r"[\s\-_.,，。、!！?？'\"「」『』~～:：/／&]+", "", s)

    def _match(self, track):
        """正在播的歌是不是點歌單裡的某一首 (歌名對得上)。"""
        title, artist = self._norm(track.get("title")), self._norm(track.get("artist"))
        if len(title) < 2:
            return None
        for it in self.items:
            if it["status"] not in ("queued", "pending"):
                continue
            q = self._norm(it["query"])
            if len(q) >= 2 and (title in q or q in title + artist or q.startswith(title)):
                return it
        return None

    def on_track(self, track, singing=True):
        with self.lock:
            self.roll_day()
            dirty = False
            tid = track.get("track_id") if track else None
            dur = float((track or {}).get("duration") or 0)
            pos = float((track or {}).get("position") or 0)
            frac = pos / dur if dur > 20 else 0.0
            # 今天唱了幾首：舞台開著時，同一首播到 9 成以上算一次
            if tid != self.cur_tid:
                self.cur_tid, self.cur_counted = tid, False
                # 自己放了點歌單裡的歌：那首變成「正在唱」
                hit = self._match(track) if tid else None
                if hit is not None:
                    self._finish_now(count=False)
                    hit["status"] = "now"
                    self.now_since = time.time()
                    self.bound, self.bound_peak = tid, frac
                    if hit.get("user"):
                        self.say(f"🎤 正在唱 @{hit['user']} 點的「{hit['query']}」" if self._sing() else f"🎵 正在播 @{hit['user']} 點的「{hit['query']}」")
                    dirty = True
            if tid and self.get_cfg().get("mode") == "sing" and not self.cur_counted and frac >= 0.9:  # 只有唱歌台才算
                self.cur_counted = True
                self.count += 1
                self.sung.insert(0, {"title": track.get("title"), "artist": track.get("artist"), "at": time.time()})
                del self.sung[60:]
                dirty = True
            # 自動下一首：「現在唱」綁定點下一首之後開始播的第一首歌，那首播完 (或播超過 90% 後換歌) 就換下一首
            now_item = next((i for i in self.items if i["status"] == "now"), None)
            if now_item is not None:
                if self.bound is None and tid and track.get("playing") and time.time() - self.now_since > 2 and frac < 0.5:
                    self.bound, self.bound_peak = tid, frac
                if self.bound is not None:
                    if tid == self.bound:
                        self.bound_peak = max(self.bound_peak, frac)
                    ended = (tid == self.bound and dur > 20 and pos >= dur - 2.5 and not track.get("playing")) or \
                            (tid != self.bound and self.bound_peak >= 0.9)
                    if ended:
                        self._finish_now(count=False)
                        if self.get_cfg().get("auto_next"):
                            self._advance()
                        dirty = True
            if dirty:
                self.changed()
