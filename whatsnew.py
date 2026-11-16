"""每一版的新功能 (更新後第一次打開時給使用者看)。最新的放最上面。
跳過好幾版才更新的人 (例如從 1.4 直接裝 1.5.1)，會把中間每一版的新功能合在一起看到。"""
import re

NOTES = {
    "1.5.1": {
        "title": "開台更安心，主視窗不再被擠掉",
        "items": [
            ("🔒", "只偵測音樂 App", "預設不再讀瀏覽器、抖音、影片播放器在播什麼，開台時不會露出其他分頁的標題。"
                                  "用瀏覽器分頁聽 YouTube Music 的話，到 設定 → 一般 → 隱私 關掉。"),
            ("🪟", "主視窗版面", "歌名很長或視窗比較矮時，下面的功能按鈕不會再被擠出畫面。"),
        ],
    },
    "1.5.0": {
        "title": "改名 Hummi 哼哼，還有螢幕頂端的小膠囊",
        "items": [
            ("✨", "膠囊", "螢幕頂端中間多了一顆黑色小膠囊：平常顯示現在唱到的那句歌詞，滑鼠移上去就展開成迷你播放器。"),
            ("🎵", "換歌會彈出", "換歌時膠囊會長出封面和歌名，3 秒後收回。"),
            ("🎧", "裝置電量", "藍牙和 2.4G 接收器的耳機、滑鼠、鍵盤、手把都看得到電量 (羅技、Razer、SteelSeries 等 30 多個牌子)。"
                            "充電時圓環會呼吸，快沒電會提醒；系統匣每個裝置一個圖示。"),
            ("🔊", "滾輪調音量", "滑鼠在膠囊上滾輪，只調音樂的音量，不會動到遊戲和 Discord；觸控板左右滑可以切歌。"),
            ("🌈", "真的頻譜", "膠囊的音量條跟著音樂真的在跳，邊緣還會亮起專輯的顏色。"),
            ("🪟", "Windows 11 質感", "主視窗圓角，背景可以選 Mica 系統材質。"),
            ("🌫️", "Folia 歌詞效果", "舞台裡直接選 Folia 的 17 種歌詞動畫：流光、霧箋、花火、町景、雲階…不用另外開 Folia，也能放進 OBS。"),
            ("🎬", "Hummi 舞台", "全螢幕的歌詞演出，13 種效果：浮空、飛入、彈跳、霓虹、景深、KTV、霧窗、雨夜、散字、階梯、圓環、霓虹街、煙火。背景會跟著節拍動，還能直接當 OBS 疊加層。"),
            ("🎤", "唱歌台 · Folia 舞台", "把歌和同步歌詞送進 Folia，用它超華麗的歌詞動畫演出，OBS 擷取就是唱歌台畫面。"),
            ("🔔", "自動更新", "有新版本時膠囊會告訴你，按一下就更新。"),
        ],
    },
}


def _ver(s):
    out = [int(x) for x in re.findall(r"\d+", str(s or ""))[:3]]
    return tuple(out + [0] * (3 - len(out)))


def notes_for(version, since=None):
    """version 這一版的新功能；給了 since (上次看過的版本) 就把中間每一版都合在一起，最新的在上面。"""
    if since is None:
        return NOTES.get(version)
    vs = sorted((v for v in NOTES if _ver(since) < _ver(v) <= _ver(version)), key=_ver, reverse=True)
    if not vs:
        return None
    items = []
    for v in vs:
        items += NOTES[v]["items"]
    return {"title": NOTES[vs[-1]]["title"] if len(vs) > 1 else NOTES[vs[0]]["title"], "items": items}
