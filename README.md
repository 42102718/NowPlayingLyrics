# Now Playing Lyrics

即時顯示 **Spotify / YouTube Music** 正在播放歌曲的同步歌詞，還有可以浮在遊戲上的浮動歌詞。Windows 10 / 11。

## 下載

到 [**Releases**](../../releases/latest) 下載 `NowPlayingLyrics-Setup-x.y.z.exe`，雙擊安裝即可，不需要 Python。

> 第一次執行如果出現「Windows 已保護您的電腦」，點「其他資訊」→「仍要執行」。
> 這是因為程式沒有付費的數位簽章。

## 功能

- **自動抓歌詞**：讀取 Windows 媒體控制 (Spotify 桌面版、瀏覽器、YouTube Music)，同時搜尋 LRCLIB、網易雲、酷狗、QQ音樂、Apple Music，自動轉成繁體中文。YouTube 影片標題裡的歌詞摘句、【動態歌詞】標籤會自動去掉，並檢查歌名、歌手，不會抓到別首歌
- **AI 歌詞**：所有歌詞庫都沒有的歌，AI (Whisper) 會聽歌自動寫出同步歌詞；有歌詞沒時間的會自動對時。只錄播放器的聲音，不會錄到遊戲或 Discord，全部在本機處理
- **浮動歌詞**：透明、永遠置頂、滑鼠可穿透，可以邊玩遊戲邊看 (遊戲請用「無邊框視窗」模式)
- **自訂**：字體、背景 (專輯封面 / 純色 / 漸層 / 圖片)、浮動歌詞位置與大小、卡拉 OK 掃光
- **手動補救**：搜尋並選擇歌詞版本、貼上或匯入 LRC，選過會記住

### 快捷鍵

| 按鍵 | 功能 |
|---|---|
| `Ctrl+Alt+L` | 解鎖 / 鎖定浮動歌詞位置 (解鎖時可拖曳) |
| `Ctrl+Alt+H` | 隱藏 / 顯示浮動歌詞 |
| `Ctrl+Alt+←` / `→` | 歌詞提早 / 延後 0.25 秒 |

## AI 歌詞的下載

第一次用到 AI 時會自動下載到 `%LOCALAPPDATA%\NowPlayingLyrics`：

- Whisper large-v3-turbo 模型 (約 1.6 GB)
- NVIDIA 顯示卡的電腦再加 cuBLAS (約 0.6 GB)；沒有 NVIDIA 顯示卡會用 CPU，比較慢

可以在設定裡關閉 AI 歌詞。

## 隱私

為了找歌詞，程式會把歌名、歌手、專輯、長度傳給上面列的歌詞服務。AI 聽歌完全在本機處理，聲音不會上傳。設定存在 `%APPDATA%\NowPlayingLyrics`。

## 從原始碼建置

需要 Python 3.14、[Inno Setup 6](https://jrsoftware.org/isinfo.php)。

```bat
pip install -r requirements.txt
build.bat
installer\build-installer.bat
```

輸出在 `release\NowPlayingLyrics-Setup-<版本>.exe`。開發時可以用 `start-debug.bat` (瀏覽器模式)。

## 歌詞來源

[LRCLIB](https://lrclib.net)、網易雲音樂、酷狗音樂、QQ音樂、Apple Music (透過 [LyricsPlus](https://lyricsplus.prjktla.my.id) 共用伺服器)。歌詞版權屬於原作者；網易雲 / 酷狗 / QQ音樂 / LyricsPlus 使用的是非官方或第三方介面，隨時可能失效。
