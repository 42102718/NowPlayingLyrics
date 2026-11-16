"""
PlayerCap 相容服務：讓內嵌的 Folia 歌詞效果 (/folia/?obsSource=playercap...) 拿到 Hummi 正在播的歌。

Folia 的 OBS 頁面原本是接 PlayerCap (Metabox-Nexus-PlayerCap) 這個本機服務；Hummi 講同一套格式：
  GET  /service-status          → {code, msg, data: {player_support: ["hummi"], ...}}
  WS   /hummi/ws                → 事件 {type, player, data}：
       status_update     {status: playing|paused|standby, detail}
       song_info_update  {name, singer, title, cover, cover_base64}
       all_lyrics        {title, duration, position, progress, count, lyrics: [...], lyrics_detailed: [...]}
       lyric_update      {index, text, sub_text, timestamp, play_time, position, progress, text_detailed}
       playback_pause / playback_resume {position, progress}   (resume 也代表「拖動了」)
       player_clear
(Hummi 自己的頁面用 /ws，所以 Folia 要指定 player=hummi，走 /hummi/ws。舞台只要原文歌詞，所以不送翻譯 sub_text。)
"""
import asyncio
import json
import time
import urllib.parse

from aiohttp import web

PLAYER = "hummi"


class PlayerCapHub:
    def __init__(self, state, config, version):
        self.state, self.config, self.version = state, config, version
        self.clients = set()
        self.last = {}      # 上一次送出的狀態：track_id、歌詞 key、是否在播、目前第幾句、位置
        self.port = 8765

    # ───── 工具 ─────
    def position(self, t):
        return max(0.0, t["position"] + ((time.time() - t["ts"]) if t["playing"] else 0))

    def offset(self):
        return float(self.config.get("offset", 0) or 0)

    @staticmethod
    def ev(kind, data):
        return json.dumps({"type": kind, "player": PLAYER, "data": data}, ensure_ascii=False)

    def song_info(self, t):
        cover = ""
        if t.get("has_art"):
            cover = f"http://127.0.0.1:{self.port}/art?id=" + urllib.parse.quote(t["track_id"], safe="")
        return {"name": t.get("title") or "", "singer": t.get("artist") or "", "title": t.get("title") or "",
                "cover": cover, "cover_base64": ""}

    def lines(self, t):
        lyr = self.state.get("lyrics")
        if not lyr or lyr.get("track_id") != t["track_id"] or lyr.get("loading") or not lyr.get("synced") \
                or lyr.get("kind") == "short":
            return []
        return [l for l in lyr.get("lines") or [] if l.get("t") is not None and (l.get("text") or "").strip()]

    def all_lyrics(self, t, lines):
        off = self.offset()
        dur = float(t.get("duration") or 0)
        pos = self.position(t)
        out, detailed = [], []
        for i, l in enumerate(lines):
            ts = float(l["t"])
            words = []
            for w in l.get("w") or []:
                if not w[2]:
                    continue
                words.append({"timestamp": w[0], "play_time": w[0] - off, "duration": w[1] or 0.25, "text": w[2]})
            td = {}
            if words:
                end = max(w["timestamp"] + w["duration"] for w in words)
                td = {"timestamp": ts, "play_time": ts - off, "duration": end - ts, "words": words}
                detailed.append({"lyric_index": i, **td})
            out.append({"index": i, "timestamp": ts, "play_time": ts - off, "text": l.get("text") or "",
                        "sub_text": "", "text_detailed": td})
        return {"title": t.get("title") or "", "duration": dur, "position": pos,
                "progress": (pos / dur) if dur else 0, "count": len(out), "lyrics": out, "lyrics_detailed": detailed}

    def line_index(self, lines, pos):
        tt = pos + self.offset()
        idx = -1
        for i, l in enumerate(lines):
            if l["t"] <= tt:
                idx = i
            else:
                break
        return idx

    # ───── HTTP / WS ─────
    async def service_status(self, request):
        self.port = request.url.port or self.port
        t = self.state.get("track")
        st = ("playing" if t["playing"] else "paused") if t else "standby"
        data = {"version": f"hummi-{self.version}", "config": {"offset": int(self.offset() * 1000)},
                "player_support": [PLAYER], "player_running": [PLAYER] if t else [], "player_status": {PLAYER: st},
                "endpoints": {"ws": f"/{PLAYER}/ws"}, "client_count": len(self.clients),
                "ws_connected": {"connected": bool(self.clients), "clients": len(self.clients)}}
        return web.json_response({"code": 0, "msg": "ok", "data": data},
                                 headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

    async def ws_handler(self, request):
        self.port = request.url.port or self.port
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        try:
            for msg in self.snapshot():  # 一連上就把目前的歌、歌詞、時間都送過去
                await ws.send_str(msg)
            async for _ in ws:
                pass
        finally:
            self.clients.discard(ws)
        return ws

    def snapshot(self):
        t = self.state.get("track")
        if not t:
            return [self.ev("status_update", {"status": "standby", "detail": ""})]
        lines = self.lines(t)
        return [self.ev("status_update", {"status": "playing" if t["playing"] else "paused",
                                          "detail": f"{t.get('title')} - {t.get('artist')}"}),
                self.ev("song_info_update", self.song_info(t)),
                self.ev("all_lyrics", self.all_lyrics(t, lines))]

    async def send(self, msgs):
        for ws in list(self.clients):
            try:
                for m in msgs:
                    await ws.send_str(m)
            except Exception:
                self.clients.discard(ws)

    async def loop(self):
        """每 0.2 秒比對一次狀態，有變化才推事件 (換歌、歌詞到了、播放/暫停、拖動、換句)。"""
        while True:
            await asyncio.sleep(0.2)
            if not self.clients:
                self.last = {}
                continue
            try:
                await self.tick()
            except Exception as e:
                print("[playercap]", repr(e), flush=True)

    async def tick(self):
        t = self.state.get("track")
        last = self.last
        if not t:
            if last.get("tid"):
                await self.send([self.ev("player_clear", {}), self.ev("status_update", {"status": "standby", "detail": ""})])
            self.last = {}
            return
        lines = self.lines(t)
        lyr = self.state.get("lyrics") or {}
        lkey = (lyr.get("track_id"), lyr.get("source"), len(lines), lines[0]["text"] if lines else "", self.offset())
        pos = self.position(t)
        msgs = []
        if t["track_id"] != last.get("tid"):
            msgs += self.snapshot()
        else:
            if lkey != last.get("lkey"):
                msgs.append(self.ev("all_lyrics", self.all_lyrics(t, lines)))
            if t.get("has_art") != last.get("art"):
                msgs.append(self.ev("song_info_update", self.song_info(t)))
            dur = float(t.get("duration") or 0)
            pp = {"position": pos, "progress": (pos / dur) if dur else 0}
            expected = last.get("pos", pos) + ((time.time() - last.get("at", time.time())) if last.get("playing") else 0)
            if t["playing"] != last.get("playing"):
                msgs.append(self.ev("status_update", {"status": "playing" if t["playing"] else "paused",
                                                      "detail": f"{t.get('title')} - {t.get('artist')}"}))
                msgs.append(self.ev("playback_resume" if t["playing"] else "playback_pause", pp))
            elif abs(pos - expected) > 1.2:  # 拖動了
                msgs.append(self.ev("playback_resume" if t["playing"] else "playback_pause", pp))
            idx = self.line_index(lines, pos)
            if idx != last.get("idx") and idx >= 0:  # 換句：順便校正時間
                l = lines[idx]
                msgs.append(self.ev("lyric_update", {"index": idx, "text": l.get("text") or "", "sub_text": "",
                                                     "timestamp": l["t"], "play_time": l["t"] - self.offset(),
                                                     "position": pos, "progress": pp["progress"], "text_detailed": {}}))
        if msgs:
            await self.send(msgs)
        self.last = {"tid": t["track_id"], "lkey": lkey, "playing": t["playing"], "pos": pos, "at": time.time(),
                     "idx": self.line_index(lines, pos), "art": t.get("has_art")}

    def routes(self, app, root):
        app.router.add_get("/service-status", self.service_status)
        app.router.add_get(f"/{PLAYER}/ws", self.ws_handler)
        folia = root / "folia_web"

        async def folia_index(request):
            return web.FileResponse(folia / "index.html", headers={"Cache-Control": "no-store"})
        if folia.is_dir():
            app.router.add_get("/folia/", folia_index)
            app.router.add_static("/folia/", folia, append_version=False)
