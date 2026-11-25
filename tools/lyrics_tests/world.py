"""各國歌曲的歌詞搜尋測試：用「Spotify / YouTube 上看到的樣子」(英文歌名、羅馬拼音歌手名…) 去找。
長度用「原文歌名」在 LRCLIB 查到的長度 (播放器一定會給長度)。只檢查找不找得到同步歌詞，不印歌詞內容。"""
import asyncio, sys, time, warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", "..", "app"))
import server

S = "Spotify"
# (國家, 播放器上的歌名, 播放器上的歌手, 原文歌名, 原文歌手)
CASES = [
    # 日本：歌手常是英文 / 羅馬拼音，歌名有時是英文
    ("JP", "Yoru ni Kakeru", "YOASOBI", "夜に駆ける", "YOASOBI"),
    ("JP", "Racing into the Night", "YOASOBI", "夜に駆ける", "YOASOBI"),
    ("JP", "Lemon", "Kenshi Yonezu", "Lemon", "米津玄師"),
    ("JP", "KICK BACK", "Kenshi Yonezu", "KICK BACK", "米津玄師"),
    ("JP", "Tada Kimi ni Hare", "Yorushika", "ただ君に晴れ", "ヨルシカ"),
    ("JP", "ただ君に晴れ", "Yorushika", "ただ君に晴れ", "ヨルシカ"),
    ("JP", "Zankyosanka", "Aimer", "残響散歌", "Aimer"),
    ("JP", "Marigold", "Aimyon", "マリーゴールド", "あいみょん"),
    ("JP", "Pretender", "Official HIGE DANdism", "Pretender", "Official髭男dism"),
    ("JP", "Usseewa", "Ado", "うっせぇわ", "Ado"),
    ("JP", "新時代", "Ado", "新時代", "Ado"),
    ("JP", "Dry Flower", "Yuuri", "ドライフラワー", "優里"),
    ("JP", "Idol", "YOASOBI", "アイドル", "YOASOBI"),
    # 韓國：歌名常是英文翻譯
    ("KR", "Spring Day", "BTS", "봄날", "BTS"),
    ("KR", "Through the Night", "IU", "밤편지", "아이유"),
    ("KR", "Palette (feat. G-DRAGON)", "IU", "팔레트", "아이유"),
    ("KR", "Hype Boy", "NewJeans", "Hype Boy", "NewJeans"),
    ("KR", "Eleven", "IVE", "ELEVEN", "IVE"),
    ("KR", "Fine", "TAEYEON", "Fine", "태연"),
    ("KR", "Love wins all", "IU", "Love wins all", "아이유"),
    ("KR", "Event Horizon", "YOUNHA", "사건의 지평선", "윤하"),
    ("KR", "Love Dive", "IVE", "LOVE DIVE", "IVE"),
    # 華語：歌手英文名、簡繁
    ("TW", "說好不哭 (with 五月天阿信)", "Jay Chou", "說好不哭", "周杰倫"),
    ("TW", "突然好想你", "Mayday", "突然好想你", "五月天"),
    ("TW", "光年之外", "G.E.M.", "光年之外", "鄧紫棋"),
    ("TW", "年少有為", "Li Ronghao", "年少有為", "李榮浩"),
    ("TW", "那些年", "Hu Xia", "那些年", "胡夏"),
    ("TW", "刻在我心底的名字", "Lu Huang", "刻在我心底的名字", "盧廣仲"),
    ("CN", "大鱼", "Zhou Shen", "大鱼", "周深"),
    ("HK", "喜帖街", "Kay Tse", "喜帖街", "謝安琪"),
    ("HK", "海闊天空", "Beyond", "海闊天空", "Beyond"),
    # 東南亞
    ("TH", "ทรงอย่างแบด (Bad Boy)", "Paper Planes", "ทรงอย่างแบด", "Paper Planes"),
    ("VN", "Lạc Trôi", "Sơn Tùng M-TP", "Lạc Trôi", "Sơn Tùng M-TP"),
    ("VN", "Hãy Trao Cho Anh", "Sơn Tùng M-TP", "Hãy Trao Cho Anh", "Sơn Tùng M-TP"),
    ("ID", "Sial", "Mahalini", "Sial", "Mahalini"),
    ("PH", "Mundo", "IV Of Spades", "Mundo", "IV Of Spades"),
    # 南亞
    ("IN", "Kesariya (From \"Brahmastra\")", "Pritam, Arijit Singh, Amitabh Bhattacharya", "Kesariya", "Arijit Singh"),
    ("IN", "Tum Hi Ho", "Mithoon, Arijit Singh", "Tum Hi Ho", "Arijit Singh"),
    # 歐美 / 拉丁
    ("ES", "Despacito", "Luis Fonsi, Daddy Yankee", "Despacito", "Luis Fonsi"),
    ("CO", "TQG", "KAROL G, Shakira", "TQG", "KAROL G"),
    ("FR", "Papaoutai", "Stromae", "Papaoutai", "Stromae"),
    ("DE", "99 Luftballons", "Nena", "99 Luftballons", "Nena"),
    ("BR", "Ai Se Eu Te Pego", "Michel Teló", "Ai Se Eu Te Pego", "Michel Teló"),
    ("IT", "Vivo per lei", "Andrea Bocelli, Giorgia", "Vivo per lei", "Andrea Bocelli"),
    ("RU", "Кукла колдуна", "Korol i Shut", "Кукла колдуна", "Король и Шут"),
    ("TR", "Şımarık", "Tarkan", "Şımarık", "Tarkan"),
]


async def canon_duration(title, artist):
    """原文歌名的長度：LRCLIB 有 (而且不是幾十秒的片段) 就用，沒有就用網易雲第一個結果。"""
    for src in (server.lyrics._s_lrclib, server.lyrics._s_netease):
        try:
            c = await src(server.to_simp(title) if src is server.lyrics._s_netease else title,
                          server.to_simp(artist) if src is server.lyrics._s_netease else artist, 5)
            c = [x for x in c if (x.get("duration") or 0) > 90]
            if c:
                return c[0]["duration"]
        except Exception:
            pass
    return 0


async def main():
    bad, t0 = [], time.time()
    only = set(sys.argv[1:])
    for cc, title, artist, ct, ca in CASES:
        if only and cc not in only:
            continue
        server.lyrics.cache.clear()
        dur = {"說好不哭 (with 五月天阿信)": 222}.get(title) or await canon_duration(ct, ca)  # 原文查到的是別的版本
        track = {"title": title, "artist": artist, "album": "", "duration": dur, "source": S, "app": "Spotify.exe", "track_id": title}
        kind = server.media_kind(track)
        try:
            res, inst = await server.resolve_lyrics(track, kind)
        except Exception as e:
            res, inst = {"error": repr(e)}, None
        lines = res.get("lines") or []
        ok = bool(res.get("synced")) and len([l for l in lines if l.get("text")]) >= 5
        if not ok:
            bad.append(f"{cc} {title}")
        guess = res.get("guessed_title") or ""
        print(f"{'OK ' if ok else 'BAD'} {cc} {title[:30]:32} / {artist[:18]:20} dur={dur:5.0f} {str(res.get('source'))[:10]:10} "
              f"synced={res.get('synced')!s:5} 行={len(lines):3} {('配對到 ' + guess) if guess else ''}")
    print(f"\n{len(CASES) - len(bad) if not only else '?'}/{len(CASES)} OK ({time.time() - t0:.0f}s)\n失敗: {bad}")

asyncio.run(main())
