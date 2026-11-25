"""找歌詞的大型「陷阱」測試。
每一首：用播放器上會看到的樣子 (英文歌名、羅馬拼音歌手、各種後綴、多位歌手…) 去找，
再跟「用原文歌名 + 原文歌手找到的正確歌詞」比對內容，確認不是別首歌。
期待 none 的是純音樂 / 不存在的歌：不能硬塞別首歌的歌詞。
只印 OK / BAD 和來源，不印歌詞內容。"""
import asyncio, re, sys, time, warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", "..", "app"))
import server

S, Y = "Spotify", "YouTube Music"
# (分類, 播放器歌名, 播放器歌手, 播放器專輯, 原文歌名, 原文歌手, 期待)   期待：found / none
CASES = [
    # ── 英文翻譯歌名 (日) ──
    ("jp-en", "Kaikai Kitan", "Eve", "", "廻廻奇譚", "Eve", "found"),
    ("jp-en", "Gurenge", "LiSA", "", "紅蓮華", "LiSA", "found"),
    ("jp-en", "Shinunoga E-Wa", "Fujii Kaze", "", "死ぬのがいいわ", "藤井風", "found"),
    ("jp-en", "Odoriko", "Vaundy", "", "踊り子", "Vaundy", "found"),
    ("jp-en", "Kaibutsu", "YOASOBI", "", "怪物", "YOASOBI", "found"),
    ("jp-en", "Bling-Bang-Bang-Born", "Creepy Nuts", "", "Bling-Bang-Bang-Born", "Creepy Nuts", "found"),
    ("jp-en", "Mixed Nuts", "Official HIGE DANdism", "", "ミックスナッツ", "Official髭男dism", "found"),
    ("jp-en", "Suzume (feat. Toaka)", "RADWIMPS", "", "すずめ", "RADWIMPS", "found"),
    ("jp-en", "Sparkle", "RADWIMPS", "", "スパークル", "RADWIMPS", "found"),
    ("jp-en", "Haruka", "YOASOBI", "", "ハルカ", "YOASOBI", "found"),
    ("jp-en", "Hana ni Bourei", "Yorushika", "", "花に亡霊", "ヨルシカ", "found"),
    ("jp-en", "Sakura", "Ikimonogakari", "", "SAKURA", "いきものがかり", "found"),
    # ── 英文翻譯歌名 (韓) ──
    ("kr-en", "Seven (feat. Latto)", "Jung Kook, Latto", "", "Seven", "Jung Kook", "found"),
    ("kr-en", "Love Lee", "AKMU", "", "Love Lee", "AKMU", "found"),
    ("kr-en", "Supernova", "aespa", "", "Supernova", "aespa", "found"),
    ("kr-en", "Magnetic", "ILLIT", "", "Magnetic", "ILLIT", "found"),
    ("kr-en", "Love Poem", "IU", "", "Love poem", "아이유", "found"),
    ("kr-en", "Eight (Prod. & Feat. SUGA of BTS)", "IU", "", "에잇", "아이유", "found"),
    ("kr-en", "Stay With Me", "CHANYEOL, Punch", "", "Stay With Me", "찬열", "found"),
    ("kr-en", "Rain Drop", "IU", "", "Rain Drop", "아이유", "found"),
    ("kr-en", "Lilac", "IU", "", "라일락", "아이유", "found"),
    ("kr-en", "Celebrity", "IU", "", "Celebrity", "아이유", "found"),
    # ── 英文 / 羅馬拼音歌手 (華、粵) ──
    ("zh-rom", "稻香", "Jay Chou", "", "稻香", "周杰倫", "found"),
    ("zh-rom", "小幸運", "Hebe Tien", "", "小幸運", "田馥甄", "found"),
    ("zh-rom", "倔強", "Mayday", "", "倔強", "五月天", "found"),
    ("zh-rom", "愛你", "Cyndi Wang", "", "愛你", "王心凌", "found"),
    ("zh-rom", "我們的愛", "F.I.R.", "", "我們的愛", "F.I.R.", "found"),
    ("zh-rom", "紅豆", "Faye Wong", "", "紅豆", "王菲", "found"),
    ("zh-rom", "千千闕歌", "Priscilla Chan", "", "千千闕歌", "陳慧嫻", "found"),
    ("zh-rom", "浮誇", "Eason Chan", "", "浮誇", "陳奕迅", "found"),
    ("zh-rom", "泡沫", "G.E.M.", "", "泡沫", "鄧紫棋", "found"),
    ("zh-rom", "追光者", "Yisa Yu", "", "追光者", "岑寧兒", "found"),
    # ── 簡繁 / 中英並列 ──
    ("zh-mix", "后来", "Rene Liu", "", "後來", "劉若英", "found"),
    ("zh-mix", "Sunny Day 晴天", "Jay Chou", "", "晴天", "周杰倫", "found"),
    ("zh-mix", "說好的幸福呢", "周杰倫", "", "說好的幸福呢", "周杰倫", "found"),
    # ── 各種後綴 ──
    ("suffix", "Anti-Hero (Taylor's Version)", "Taylor Swift", "", "Anti-Hero", "Taylor Swift", "found"),
    ("suffix", "Love Story (Taylor's Version)", "Taylor Swift", "", "Love Story", "Taylor Swift", "found"),
    ("suffix", "Bohemian Rhapsody - Remastered 2011", "Queen", "", "Bohemian Rhapsody", "Queen", "found"),
    ("suffix", "Take On Me - 2015 Remaster", "a-ha", "", "Take On Me", "a-ha", "found"),
    ("suffix", "Levitating (feat. DaBaby)", "Dua Lipa, DaBaby", "", "Levitating", "Dua Lipa", "found"),
    ("suffix", "Uptown Funk (feat. Bruno Mars)", "Mark Ronson, Bruno Mars", "", "Uptown Funk", "Mark Ronson", "found"),
    ("suffix", "Stay (with Justin Bieber)", "The Kid LAROI, Justin Bieber", "", "STAY", "The Kid LAROI", "found"),
    ("suffix", "Calm Down (with Selena Gomez)", "Rema, Selena Gomez", "", "Calm Down", "Rema", "found"),
    ("suffix", "Unholy (feat. Kim Petras)", "Sam Smith, Kim Petras", "", "Unholy", "Sam Smith", "found"),
    ("suffix", "Die With A Smile", "Lady Gaga, Bruno Mars", "", "Die With A Smile", "Lady Gaga", "found"),
    ("suffix", "Shallow - Radio Edit", "Lady Gaga, Bradley Cooper", "", "Shallow", "Lady Gaga", "found"),
    ("suffix", "Gurenge - TV Size", "LiSA", "", "紅蓮華", "LiSA", "found"),
    ("suffix", "My Heart Will Go On - Love Theme from \"Titanic\"", "Céline Dion", "", "My Heart Will Go On", "Celine Dion", "found"),
    ("suffix", "Let It Go - From \"Frozen\"/Soundtrack Version", "Idina Menzel", "", "Let It Go", "Idina Menzel", "found"),
    ("suffix", "Golden", "HUNTR/X, EJAE, AUDREY NUNA, REI AMI", "KPop Demon Hunters (Soundtrack from the Netflix Film)", "Golden", "HUNTR/X", "found"),
    ("suffix", "Espresso [Explicit]", "Sabrina Carpenter", "", "Espresso", "Sabrina Carpenter", "found"),
    ("suffix", "Flowers - Single Version", "Miley Cyrus", "", "Flowers", "Miley Cyrus", "found"),
    # ── 特殊字元 / 重音 ──
    ("chars", "Don’t Stop Me Now", "Queen", "", "Don't Stop Me Now", "Queen", "found"),
    ("chars", "CUFF IT", "Beyoncé", "", "CUFF IT", "Beyoncé", "found"),
    ("chars", "La Bachata", "Manuel Turizo", "", "La Bachata", "Manuel Turizo", "found"),
    ("chars", "Dákiti", "Bad Bunny, Jhay Cortez", "", "DÁKITI", "Bad Bunny", "found"),
    ("chars", "Tití Me Preguntó", "Bad Bunny", "", "Tití Me Preguntó", "Bad Bunny", "found"),
    ("chars", "Ｌｅｍｏｎ", "米津玄師", "", "Lemon", "米津玄師", "found"),
    ("chars", "7 rings", "Ariana Grande", "", "7 rings", "Ariana Grande", "found"),
    ("chars", "22", "Taylor Swift", "", "22", "Taylor Swift", "found"),
    ("chars", "#thatPOWER", "will.i.am, Justin Bieber", "", "#thatPOWER", "will.i.am", "found"),
    ("chars", "Lạc Trôi", "Son Tung M-TP", "", "Lạc Trôi", "Sơn Tùng M-TP", "found"),
    # ── 很常見的歌名 (一定要對到正確歌手) ──
    ("common", "Hello", "Adele", "", "Hello", "Adele", "found"),
    ("common", "Stay", "Rihanna, Mikky Ekko", "", "Stay", "Rihanna", "found"),
    ("common", "Yesterday - Remastered 2009", "The Beatles", "", "Yesterday", "The Beatles", "found"),
    ("common", "Home", "Michael Bublé", "", "Home", "Michael Bublé", "found"),
    ("common", "Love", "Kendrick Lamar, Zacari", "", "LOVE.", "Kendrick Lamar", "found"),
    ("common", "Butterfly", "BTS", "", "Butterfly", "BTS", "found"),
    ("common", "Butterfly", "Crazy Town", "", "Butterfly", "Crazy Town", "found"),
    # ── 其他語言 ──
    ("other", "Gangnam Style", "PSY", "", "강남스타일", "PSY", "found"),
    ("other", "Mala Mujer", "C. Tangana", "", "Mala Mujer", "C. Tangana", "found"),
    ("other", "Je veux", "ZAZ", "", "Je veux", "ZAZ", "found"),
    ("other", "Du hast", "Rammstein", "", "Du hast", "Rammstein", "found"),
    ("other", "Bella ciao", "Manu Pilas", "", "Bella ciao", "Manu Pilas", "found"),
    ("other", "Nandemonaiya", "RADWIMPS", "", "なんでもないや", "RADWIMPS", "found"),
    ("other", "Kun Faya Kun", "A.R. Rahman, Javed Ali, Mohit Chauhan", "", "Kun Faya Kun", "A.R. Rahman", "any"),  # 印地語歌常見羅馬拼音歌詞，找到同步歌詞就算
    ("other", "Rasputin", "Boney M.", "", "Rasputin", "Boney M.", "found"),
    ("other", "Despacito - Remix", "Luis Fonsi, Daddy Yankee, Justin Bieber", "", "Despacito", "Luis Fonsi", "found"),
    # ── 沒有重音 / 原文歌名配英文歌手 / 簡體 / 合唱 ──
    ("fold", "Lac Troi", "Son Tung MTP", "", "Lạc Trôi", "Sơn Tùng M-TP", "found"),
    ("fold", "Cuff It", "Beyonce", "", "CUFF IT", "Beyoncé", "found"),
    ("fold", "Titi Me Pregunto", "Bad Bunny", "", "Tití Me Preguntó", "Bad Bunny", "found"),
    ("fold", "밤편지", "IU", "", "밤편지", "아이유", "found"),
    ("fold", "봄날", "BTS", "", "봄날", "BTS", "found"),
    ("fold", "演员", "Joker Xue", "", "演員", "薛之謙", "found"),
    ("fold", "光年之外", "GEM", "", "光年之外", "鄧紫棋", "found"),
    ("fold", "うっせえわ", "Ado", "", "うっせぇわ", "Ado", "found"),
    ("fold", "Shallow", "Lady Gaga & Bradley Cooper", "", "Shallow", "Lady Gaga", "found"),
    ("fold", "Under Pressure - Remastered 2011", "Queen & David Bowie", "", "Under Pressure", "Queen", "found"),
    ("fold", "Something Just Like This", "The Chainsmokers & Coldplay", "", "Something Just Like This", "The Chainsmokers", "found"),
    ("fold", "後來", "René Liu", "", "後來", "劉若英", "found"),
    # ── 不該找到別首歌 (純音樂 / 不存在) ──
    ("none", "Clair de lune", "Claude Debussy, Paul Barton", "", "", "", "none"),
    ("none", "Merry Christmas Mr. Lawrence", "Ryuichi Sakamoto", "", "", "", "none"),
    ("none", "River Flows In You", "Yiruma", "", "", "", "none"),
    ("none", "Lofi Study Beat 47", "Chillhop Testers", "", "", "", "none"),
    ("none", "完全不存在的測試歌名九七", "不存在的歌手", "", "", "", "none"),
    ("none", "Interstellar Main Theme", "Hans Zimmer", "", "", "", "none"),
]


def bigrams(text):
    t = re.sub(r"[\W_]+", "", (text or "").lower())
    t = server.to_trad(t) if hasattr(server, "to_trad") else t
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similar(a, b):
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


async def canonical(title, artist):
    """原文歌名 + 原文歌手：正確的歌詞和長度。"""
    L = server.lyrics
    try:
        cands = L.rank(await L.search(title, artist), title, artist, None)
    except Exception:
        return None, 0
    for c in cands[:6]:
        r = await L.fetch(c)
        if r and r["synced"] and len([l for l in r["lines"] if l.get("text")]) >= 5 and (c.get("duration") or 0) > 60:
            return " ".join(l.get("text") or "" for l in r["lines"]), c.get("duration") or 0
    return None, 0


async def main():
    only = set(sys.argv[1:])
    bad, t0, n = [], time.time(), 0
    for cat, title, artist, album, ct, ca, want in CASES:
        if only and cat not in only:
            continue
        n += 1
        server.lyrics.cache.clear()
        ref, dur = (await canonical(ct, ca)) if want in ("found", "any") else (None, 213)
        FIX = {("后来", "Rene Liu"): 341, ("光年之外", "GEM"): 235}  # 原文查到的是 Live / 热爱版：用正式版長度
        if (title, artist) in FIX:
            dur = FIX[(title, artist)]
            ref = None
            r = await server.lyrics.find(ct, ca, "", dur); server.lyrics.cache.clear()
            ref = " ".join(l.get("text") or "" for l in r["lines"])
        if want == "found" and "TV Size" in title:
            dur = 90  # TV 版只有一分半
        track = {"title": title, "artist": artist, "album": album, "duration": dur, "source": S,
                 "app": "Spotify.exe", "track_id": title + artist}
        try:
            res, inst = await server.resolve_lyrics(track, server.media_kind(track))
        except Exception as e:
            res, inst = {"error": repr(e)}, None
        lines = [l for l in (res.get("lines") or []) if l.get("text")]
        got = " ".join(l["text"] for l in lines)
        synced = bool(res.get("synced"))
        if want == "any":
            ok = synced and len(lines) >= 5
            note = "找到" if ok else "沒找到"
        elif want == "none":
            ok = not lines
            note = "沒有硬塞歌詞" if ok else f"錯：塞了 {res.get('source')} 的歌詞 ({res.get('guessed_title') or ''})"
        else:
            sim = similar(got, ref) if ref else None
            if not synced or len(lines) < 5:
                ok, note = False, f"沒找到 (source={res.get('source')} synced={synced})"
            elif ref is None:
                ok, note = True, "找到 (沒有原文可比對)"
            else:
                ok = sim >= 0.35
                note = f"相似度 {sim:.2f}" + ("" if ok else " ← 可能是別首歌")
        if not ok:
            bad.append(f"{cat}: {title} / {artist}")
        print(f"{'OK ' if ok else 'BAD'} [{cat:6}] {title[:34]:36} / {artist[:22]:24} dur={dur:4.0f} {str(res.get('source'))[:8]:8} {note}"
              + (f"  配對到「{res.get('guessed_title')}」" if res.get("guessed_title") else ""))
    print(f"\n{n - len(bad)}/{n} OK ({time.time() - t0:.0f}s)")
    for b in bad:
        print("  BAD", b)

asyncio.run(main())
