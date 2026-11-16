"""
單一程式的音量 (就是 Windows「音量混音程式」裡每個 App 自己的那條)，只動播放器，不動遊戲 / Discord / 系統音量。

    get_volume("spotify.exe") -> 0.0~1.0 或 None
    set_volume("spotify.exe", 0.5)
同一個執行緒裡呼叫 (第一次會初始化 COM)。
"""
import ctypes
import ctypes.wintypes as wt
import sys

_api = None
IAudioMeter = None


def _load():
    global _api
    if _api is not None:
        return _api
    if "comtypes" not in sys.modules:
        sys.coinit_flags = 0
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class ISimpleAudioVolume(IUnknown):
        _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], ctypes.c_float, "level"), (["in"], ctypes.POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], ctypes.POINTER(ctypes.c_float), "level")),
            COMMETHOD([], HRESULT, "SetMute", (["in"], wt.BOOL, "mute"), (["in"], ctypes.POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetMute", (["out"], ctypes.POINTER(wt.BOOL), "mute")),
        ]

    class IAudioSessionControl2(IUnknown):
        _iid_ = GUID("{bfb7ff88-7239-4fc9-8fa2-07c950be9c6d}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetState", (["out"], ctypes.POINTER(ctypes.c_int), "s")),
            COMMETHOD([], HRESULT, "GetDisplayName", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "n")),
            COMMETHOD([], HRESULT, "SetDisplayName", (["in"], ctypes.c_wchar_p, "n"), (["in"], ctypes.POINTER(GUID), "c")),
            COMMETHOD([], HRESULT, "GetIconPath", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "p")),
            COMMETHOD([], HRESULT, "SetIconPath", (["in"], ctypes.c_wchar_p, "p"), (["in"], ctypes.POINTER(GUID), "c")),
            COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], ctypes.POINTER(GUID), "g")),
            COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], ctypes.POINTER(GUID), "g"), (["in"], ctypes.POINTER(GUID), "c")),
            COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], ctypes.c_void_p, "n")),
            COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], ctypes.c_void_p, "n")),
            COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "i")),
            COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "i")),
            COMMETHOD([], HRESULT, "GetProcessId", (["out"], ctypes.POINTER(wt.DWORD), "pid")),
            COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
            COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], wt.BOOL, "o")),
        ]

    class IAudioSessionEnumerator(IUnknown):
        _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], ctypes.POINTER(ctypes.c_int), "n")),
            COMMETHOD([], HRESULT, "GetSession", (["in"], ctypes.c_int, "i"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "s")),
        ]

    class IAudioSessionManager2(IUnknown):
        _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], ctypes.POINTER(GUID), "g"), (["in"], wt.DWORD, "f"),
                      (["out"], ctypes.POINTER(ctypes.c_void_p), "c")),
            COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], ctypes.POINTER(GUID), "g"), (["in"], wt.DWORD, "f"),
                      (["out"], ctypes.POINTER(ctypes.c_void_p), "v")),
            COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionEnumerator)), "e")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], ctypes.POINTER(GUID), "iid"), (["in"], wt.DWORD, "ctx"),
                      (["in"], ctypes.c_void_p, "p"), (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "o")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int, "f"), (["in"], wt.DWORD, "m"),
                      (["out"], ctypes.POINTER(ctypes.c_void_p), "d")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int, "f"), (["in"], ctypes.c_int, "r"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "d")),
        ]

    class IAudioMeterInformation(IUnknown):
        _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetPeakValue", (["out"], ctypes.POINTER(ctypes.c_float), "peak")),
        ]

    global IAudioMeter
    IAudioMeter = IAudioMeterInformation
    _api = (comtypes, GUID, IMMDeviceEnumerator, IAudioSessionManager2, IAudioSessionControl2, ISimpleAudioVolume)
    return _api


_proc_cache = (0.0, None)


def _procs():
    """程序清單 (列舉要十幾 ms，2 秒內重用)。"""
    global _proc_cache
    import time
    from procloop import _processes
    if time.time() - _proc_cache[0] > 2 or _proc_cache[1] is None:
        _proc_cache = (time.time(), _processes())
    return _proc_cache[1]


def hints_for(app_id):
    """從媒體工作階段的 App ID 推出要找的東西：exe 檔名，以及 Store App 的套件名稱。
    例："Spotify.exe" → spotify.exe；"AppleInc.AppleMusicWin_nzyj5cx40ttqa!App" → 套件 applemusicwin_nzyj5cx40ttqa；
    "C:\\...\\foobar2000.exe" → foobar2000.exe；"Chrome._crx_…" → chrome.exe"""
    from procloop import exe_for_app
    a = (app_id or "").lower()
    exe = exe_for_app(a)
    pkg = None
    if "!" in a:
        pkg = a.split("!", 1)[0]
        if "." in pkg:
            pkg = pkg.split(".", 1)[1]  # 去掉發行者前綴，套件資料夾名稱一定包含這段
    if not exe and not pkg and a:
        exe = a.rsplit("\\", 1)[-1] + ("" if a.endswith(".exe") else ".exe")  # 有些 App ID 就是程式名稱
    return exe, pkg


def _sessions(app_id, with_pid=False):
    """回傳這個播放器 (含子程序) 在預設輸出裝置上的所有音量控制。"""
    comtypes, GUID, IMMDeviceEnumerator, IAudioSessionManager2, IAudioSessionControl2, ISimpleAudioVolume = _load()
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    exe, pkg = hints_for(app_id)
    procs = _procs()
    names = {p: n for p, _, n in procs}
    parents = {p: pp for p, pp, _ in procs}

    def belongs(pid, ident):
        if pkg and pkg in ident:   # Store App：工作階段識別碼裡有套件路徑
            return True
        for _ in range(8):  # 往上找幾層父程序 (瀏覽器的聲音在子程序裡)
            if exe and names.get(pid) == exe:
                return True
            pid = parents.get(pid)
            if not pid:
                return False
        return False

    en = comtypes.CoCreateInstance(GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"), IMMDeviceEnumerator,
                                   comtypes.CLSCTX_ALL)
    dev = en.GetDefaultAudioEndpoint(0, 1)  # eRender, eMultimedia
    mgr = dev.Activate(ctypes.byref(IAudioSessionManager2._iid_), 23, None).QueryInterface(IAudioSessionManager2)
    se = mgr.GetSessionEnumerator()
    out = []
    for i in range(se.GetCount()):
        ctl = se.GetSession(i).QueryInterface(IAudioSessionControl2)
        try:
            pid = ctl.GetProcessId()
            ident = (ctl.GetSessionIdentifier() or "").lower()
        except Exception:
            continue
        if pid and belongs(pid, ident):
            if with_pid == "meter":
                out.append(ctl.QueryInterface(IAudioMeter))
            else:
                out.append((pid, ctl.QueryInterface(ISimpleAudioVolume)) if with_pid else ctl.QueryInterface(ISimpleAudioVolume))
    return out


def peak(app_id):
    """播放器現在有沒有發出聲音 (音量混音程式裡那條會跳的綠色音量表，0~1)。不用錄音，很省資源。"""
    meters = _sessions(app_id, with_pid="meter")
    return max((m.GetPeakValue() for m in meters), default=0.0)


def get_volume(app_id):
    vols = _sessions(app_id)
    return max((v.GetMasterVolume() for v in vols), default=None)


def set_volume(app_id, level):
    level = max(0.0, min(1.0, float(level)))
    vols = _sessions(app_id)
    for v in vols:
        v.SetMasterVolume(level, None)
    return level if vols else None


def session_pid(app_id):
    """播放器發出聲音的程序 (給頻譜錄音用；找不到 exe 的 Store App 也找得到)。"""
    s = _sessions(app_id, with_pid=True)
    return s[0][0] if s else None


if __name__ == "__main__":
    app = sys.argv[1] if len(sys.argv) > 1 else "Spotify.exe"
    print(app, hints_for(app), "volume", get_volume(app), "pid", session_pid(app))
