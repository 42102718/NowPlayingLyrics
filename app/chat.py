"""直播聊天室點歌指令：觀眾打 !song 就告訴他現在播什麼。

Twitch：
- 只填頻道名稱：匿名讀聊天室 (不用登入)，有人問就在舞台 / OBS 畫面跳出「現在播放」卡片
- 再填聊天機器人的 OAuth 權杖：也會直接在聊天室回覆
同一個觀眾 / 整個聊天室都有冷卻時間，不會洗版。
"""
import asyncio
import random
import re
import time

import aiohttp

DEFAULT_CMDS = "!song !歌名 !這什麼歌 !歌"
POSITION_CMDS = ("!排隊", "!我的歌", "!queue", "!position", "!順序")  # 觀眾查自己點的歌排第幾首


class TwitchChat:
    def __init__(self, get_cfg, now_playing, on_ask, log=print, on_request=None, request_cmds=None, on_position=None):
        self.get_cfg = get_cfg          # -> {"channel", "token", "bot", "commands", "reply"}
        self.now_playing = now_playing  # -> (title, artist) 或 None
        self.on_ask = on_ask            # on_ask(觀眾, 歌名, 歌手)：舞台跳卡片
        self.log = log
        self.task = None
        self.status = "off"
        self.last_reply = 0.0
        self.user_last = {}
        self.on_request = on_request          # on_request(觀眾, 歌名, 是不是實況主本人) -> 要回覆的話
        self.request_cmds = request_cmds      # -> "!點歌 !sr" (點歌指令)
        self.on_position = on_position        # on_position(觀眾) -> 「你點的歌排第幾首」
        self.ws = None
        self.channel = ""
        self.can_reply = False
        self.loop = None

    def say(self, text):
        """在聊天室說話 (有權杖才會說)。哪個執行緒呼叫都可以。"""
        if not (self.ws and self.can_reply and self.loop and text):
            return
        ws, ch = self.ws, self.channel
        self.loop.call_soon_threadsafe(lambda: self.loop.create_task(ws.send_str(f"PRIVMSG #{ch} :{text[:450]}")))

    def restart(self, loop):
        self.loop = loop
        if self.task:
            self.task.cancel()
        cfg = self.get_cfg()
        if cfg.get("channel"):
            self.task = loop.create_task(self._run())
        else:
            self.task, self.status = None, "off"

    async def _run(self):
        delay = 2
        while True:
            try:
                await self._session()
                delay = 2
            except asyncio.CancelledError:
                self.status = "off"
                raise
            except Exception as e:
                self.status = f"斷線，重新連線中 ({type(e).__name__})"
                self.log("[chat]", repr(e))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _session(self):
        cfg = self.get_cfg()
        channel = cfg["channel"].strip().lstrip("#").lower()
        token = (cfg.get("token") or "").strip()
        bot = (cfg.get("bot") or "").strip().lower() or channel
        cmds = [c.lower() for c in (cfg.get("commands") or DEFAULT_CMDS).split() if c.strip()]
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect("wss://irc-ws.chat.twitch.tv:443", heartbeat=60) as ws:
                if token:
                    await ws.send_str("PASS oauth:" + token.removeprefix("oauth:"))
                    await ws.send_str("NICK " + bot)
                else:
                    await ws.send_str(f"NICK justinfan{random.randint(10000, 99999)}")
                await ws.send_str(f"JOIN #{channel}")
                self.status = f"已連上 #{channel}" + ("" if token else " (只讀，不會回覆)")
                self.ws, self.channel, self.can_reply = ws, channel, bool(token)
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    for line in msg.data.split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send_str("PONG" + line[4:])
                        elif "Login authentication failed" in line or "Improperly formatted auth" in line:
                            self.status = "權杖錯誤：只能讀，不能回覆"
                            token = ""
                            self.can_reply = False
                        elif " PRIVMSG #" in line:
                            await self._on_msg(ws, line, channel, cmds, bool(token))

    async def _on_msg(self, ws, line, channel, cmds, can_reply):
        m = re.match(r":(\w+)!\S+ PRIVMSG #\S+ :(.*)", line)
        if not m:
            return
        user, text = m.group(1), m.group(2).strip()
        word = text.split()[0].lower() if text else ""
        req = [c.lower() for c in ((self.request_cmds() if self.request_cmds else "") or "").split() if c.strip()]
        if word in POSITION_CMDS and self.on_position:  # 查自己點的歌排到第幾首
            reply = self.on_position(user)
            if reply and can_reply:
                await ws.send_str(f"PRIVMSG #{channel} :{reply[:450]}")
            return
        if word in req and self.on_request:  # 點歌
            reply = self.on_request(user, text[len(word):].strip(), user == channel)
            if reply and can_reply:
                await ws.send_str(f"PRIVMSG #{channel} :{reply[:450]}")
            return
        if word not in cmds:
            return
        now = time.time()
        if now - self.user_last.get(user, 0) < 30 or now - self.last_reply < 8:
            return  # 冷卻：同一人 30 秒、整個聊天室 8 秒
        self.user_last[user] = now
        self.last_reply = now
        np = self.now_playing()
        if np is None:
            reply = f"@{user} 現在沒有在放歌喔"
        else:
            title, artist = np
            self.on_ask(user, title, artist)
            reply = f"@{user} 🎵 現在播放：{title}" + (f" — {artist}" if artist else "")
        if can_reply:
            await ws.send_str(f"PRIVMSG #{channel} :{reply[:450]}")
