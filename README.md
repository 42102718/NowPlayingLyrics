# Hummi 哼哼

<img src="icon.png" width="96" align="right">

即時顯示 **Spotify / YouTube Music** 正在播放歌曲的同步歌詞：主視窗、遊戲上的浮動歌詞、螢幕頂端的小膠囊，還有給唱歌台用的全螢幕舞台。Windows 10 / 11。(1.4 以前叫 Now Playing Lyrics)

## 下載

到 [**Releases**](../../releases/latest) 下載 `Hummi-Setup-x.y.z.exe`，雙擊安裝即可，不需要 Python。之後有新版本，Hummi 會自己提醒你，按一下就更新。

> 第一次執行如果出現「Windows 已保護您的電腦」，點「其他資訊」→「仍要執行」。
> 這是因為程式沒有付費的數位簽章。

## 功能

**歌詞**
- 同時搜尋 LRCLIB、網易雲、酷狗、QQ音樂、Apple Music，自動轉成繁體中文；會檢查歌名、歌手，不會抓到別首歌
- **AI 歌詞**：歌詞庫都沒有的歌，AI (Whisper) 會聽歌寫出同步歌詞；只錄播放器的聲音，全部在本機處理
- 手動搜尋、貼上或匯入 LRC，選過會記住；歌詞快慢可以微調
- Shorts、TikTok、抖音這類短影片不會被當成歌
- Spotify 偶爾回報錯的播放狀態時，Hummi 會看它有沒有真的在出聲音，歌詞不會卡住
- **用一句歌詞找歌** (Ctrl+K)：記得一句就找得到是哪首，一鍵在 Spotify / YouTube Music 打開
- **和朋友共享歌詞**：大家選同一個雲端共用資料夾，AI 聽完的歌詞朋友直接用；確認過的歌詞也能上傳到 LRCLIB

**顯示**
- **主視窗**：五種風格 (夜光、晨霧、極簡、霓虹、紙本)，專輯封面背景或 Windows 11 Mica 材質；四邊可拉、貼齊螢幕邊緣、記住位置，工作列縮圖有播放按鈕和進度
- **歌詞桌布**：舞台的歌詞和動態背景放到桌面圖示後面，桌面被蓋住時自動暫停
- **浮動歌詞**：透明、置頂、滑鼠可穿透，可以邊玩遊戲邊看 (遊戲請用「無邊框視窗」)；主視窗或舞台開著時自動隱藏；可以選放在哪個螢幕，全螢幕遊戲時自動變淡
- **膠囊**：螢幕頂端的黑色小膠囊。平常顯示現在唱到的那句；滑鼠移上去變成迷你播放器；換歌、裝置連上、快沒電時會彈出提示。在膠囊上滾輪只調音樂的音量，觸控板左右滑可以切歌

**舞台 (唱歌台、直播)**
- 全螢幕的歌詞演出，只顯示原文歌詞
- **Folia 的 17 種歌詞動畫** (流光、心象、雲階、霧箋、花火、町景…)，直接內建，不用另外安裝 Folia
- **Hummi 自己的 13 種效果** (浮空、飛入、彈跳、霓虹、景深、KTV、霧窗、雨夜、散字、階梯、圓環、霓虹街、煙火)，背景會跟著真的節拍動
- 一鍵複製 OBS 網址，當「瀏覽器」來源疊在直播畫面上 (可以選透明背景)
- **聊天室 !song**：觀眾在 Twitch 聊天室打 !song，畫面會跳出現在播的歌 (設定權杖還會直接回覆)

**其他**
- **自動暫停**：鎖定電腦、睡眠、耳機拔掉或藍牙斷線時暫停，回來接著播

**裝置電量**
- 藍牙和 2.4G 接收器的耳機、滑鼠、鍵盤、手把 (羅技、Razer、SteelSeries、HyperX 等 30 多個牌子)
- 系統匣每個裝置一個電量圓環圖示，充電時會呼吸；主視窗和膠囊裡都有裝置頁；快沒電會提醒，還會估計大約還能用多久

### 快捷鍵

| 按鍵 | 功能 |
|---|---|
| `Ctrl+Alt+P` | 播放 / 暫停 |
| `Ctrl+Alt+.` / `,` | 下一首 / 上一首 |
| `Ctrl+Alt+↑` / `↓` | 只調音樂的音量 |
| `Ctrl+Alt+W` | 主視窗 顯示 / 隱藏 |
| `Ctrl+Alt+S` | 打開舞台 |
| `Ctrl+Alt+L` | 解鎖 / 鎖定浮動歌詞位置 (解鎖時可拖曳) |
| `Ctrl+Alt+H` | 隱藏 / 顯示浮動歌詞 |
| `Ctrl+Alt+←` / `→` | 歌詞提早 / 延後 0.25 秒 |
| `F` (舞台裡) | 全螢幕 |
| `?` (主視窗裡) | 看所有快捷鍵 (空白鍵、Ctrl+← / →、↑ / ↓、H、S、D、R…) |

## AI 歌詞的下載

第一次用到 AI 時會自動下載到 `%LOCALAPPDATA%\NowPlayingLyrics`：

- Whisper large-v3-turbo 模型 (約 1.6 GB)
- NVIDIA 顯示卡的電腦再加 cuBLAS (約 0.6 GB)；沒有 NVIDIA 顯示卡會用 CPU，比較慢

可以在設定裡關閉 AI 歌詞。

## 隱私

- 為了找歌詞，程式會把歌名、歌手、專輯、長度傳給上面列的歌詞服務。
- 檢查更新時會連到 GitHub 讀取最新版本的資訊 (可以在設定裡關掉自動檢查)。
- 「用一句歌詞找歌」會把你打的那句送到網易雲、QQ 音樂搜尋。
- 共享歌詞只寫到你自己選的資料夾；上傳到 LRCLIB 只有你按了才會 (公開)。
- 聊天室指令只連 Twitch 的聊天室；權杖只存在這台電腦，不會給 OBS 或其他網頁。
- AI 聽歌、裝置電量、頻譜都只在你的電腦上處理，聲音和裝置資訊不會上傳。
- 設定存在 `%APPDATA%\NowPlayingLyrics`。

## 從原始碼建置

需要 Python 3.14、[Inno Setup 6](https://jrsoftware.org/isinfo.php)。

```bat
pip install -r requirements.txt
build.bat
installer\build-installer.bat
```

輸出在 `release\Hummi-Setup-<版本>.exe`。開發時可以用 `start-debug.bat` (瀏覽器模式)。

`folia_web/` 是打包好的 Folia 網頁版。要更新它，照 `folia_web/FOLIA_SOURCE.txt` 的方式用 Node.js 重新打包。

## 授權

Hummi 以 [GNU AGPL-3.0](LICENSE) 授權釋出 (因為內嵌了同樣是 AGPL-3.0 的 Folia)。原始碼就在這個 GitHub 倉庫。

- 舞台的 Folia 歌詞效果來自 [Folia (folia-major)](https://github.com/chthollyphile/folia-major) (AGPL-3.0)，版本與建置方式見 `folia_web/FOLIA_SOURCE.txt`，第三方授權在 `folia_web/licenses/`。
- 裝置電量的各品牌讀取程式來自 [HaloBattery](https://github.com/HeyOkay/HaloBattery) (MIT，見 `halo_providers/LICENSE-HaloBattery.txt`)。

## 歌詞來源

[LRCLIB](https://lrclib.net)、網易雲音樂、酷狗音樂、QQ音樂、Apple Music (透過 [LyricsPlus](https://lyricsplus.prjktla.my.id) 共用伺服器)。歌詞版權屬於原作者；網易雲 / 酷狗 / QQ音樂 / LyricsPlus 使用的是非官方或第三方介面，隨時可能失效。
