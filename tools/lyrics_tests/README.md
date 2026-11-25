# 找歌詞的回歸測試

改了找歌詞的程式 (app/server.py 的 Lyrics、title_variants…) 之後跑一次，確認沒有改壞：

```
python tools/lyrics_tests/traps.py      # 96 首「容易找錯」的歌：比對歌詞內容，確認是同一首；純音樂不能硬塞歌詞
python tools/lyrics_tests/world.py      # 46 首各國歌曲 (照 Spotify 顯示的樣子)
python tools/lyrics_tests/traps.py jp-en kr-en   # 只跑某幾類
```

只印 OK / BAD、來源、相似度，不會印出歌詞內容。需要網路 (直接查各歌詞來源)。
