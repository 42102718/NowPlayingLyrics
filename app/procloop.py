"""
只錄某一個程式 (以及它的子程序) 發出的聲音 —— Windows 10 2004+ 的 Process Loopback。
這樣 AI 只會聽到 Spotify / 瀏覽器的音樂，不會混進遊戲、Discord、其他影片的聲音。

用法 (要在同一個執行緒裡呼叫)：
    cap = ProcessLoopback(pid); cap.start()
    samples = cap.read(0.25)   # numpy float32, 16 kHz 單聲道
    cap.close()
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading

import numpy as np

SR = 16000

# ───────────────────────── 找程序 ─────────────────────────
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def _processes():
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k.CreateToolhelp32Snapshot(2, 0)
    out = []
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    ok = k.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out.append((e.th32ProcessID, e.th32ParentProcessID, e.szExeFile.lower()))
        ok = k.Process32NextW(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    return out


APP_EXE = {"spotify": "spotify.exe", "chrome": "chrome.exe", "msedge": "msedge.exe", "edge": "msedge.exe",
           "308046b0af4a39cb": "firefox.exe", "firefox": "firefox.exe", "brave": "brave.exe", "opera": "opera.exe",
           "vivaldi": "vivaldi.exe"}


def exe_for_app(app_id: str):
    a = (app_id or "").lower()
    if a.endswith(".exe"):
        return a.rsplit("\\", 1)[-1]
    for k, v in APP_EXE.items():
        if k in a:
            return v
    return None


def root_pid(exe: str):
    """同名程序裡最上層的那個 (例如 Spotify 的主程序)，錄它整棵程序樹。"""
    procs = _processes()
    pids = {p for p, _, n in procs if n == exe}
    for p, parent, n in procs:
        if n == exe and parent not in pids:
            return p
    return next(iter(pids), None)


# ───────────────────────── COM 介面 ─────────────────────────
def _com():
    if "comtypes" not in sys.modules:
        sys.coinit_flags = 0  # 讓 comtypes 用 COINIT_MULTITHREADED 初始化 (完成通知會從別的執行緒回來)
    import comtypes
    from comtypes import GUID, COMMETHOD, HRESULT, IUnknown, COMObject

    class IAgileObject(IUnknown):
        _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
        _methods_ = []

    class IActivateAudioInterfaceAsyncOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult",
                               (["out"], ctypes.POINTER(HRESULT), "activateResult"),
                               (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "activatedInterface"))]

    class IActivateAudioInterfaceCompletionHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted",
                               (["in"], ctypes.POINTER(IActivateAudioInterfaceAsyncOperation), "op"))]

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wt.WORD), ("nChannels", wt.WORD), ("nSamplesPerSec", wt.DWORD),
                    ("nAvgBytesPerSec", wt.DWORD), ("nBlockAlign", wt.WORD), ("wBitsPerSample", wt.WORD),
                    ("cbSize", wt.WORD)]

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer", (["out"], ctypes.POINTER(ctypes.c_void_p), "data"),
                      (["out"], ctypes.POINTER(ctypes.c_uint32), "frames"), (["out"], ctypes.POINTER(wt.DWORD), "flags"),
                      (["out"], ctypes.POINTER(ctypes.c_uint64), "devpos"), (["out"], ctypes.POINTER(ctypes.c_uint64), "qpc")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], ctypes.c_uint32, "frames")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], ctypes.POINTER(ctypes.c_uint32), "frames")),
        ]

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Initialize", (["in"], ctypes.c_int, "mode"), (["in"], wt.DWORD, "flags"),
                      (["in"], ctypes.c_longlong, "buf"), (["in"], ctypes.c_longlong, "period"),
                      (["in"], ctypes.POINTER(WAVEFORMATEX), "fmt"), (["in"], ctypes.POINTER(GUID), "session")),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], ctypes.POINTER(ctypes.c_uint32), "n")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], ctypes.POINTER(ctypes.c_longlong), "l")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], ctypes.POINTER(ctypes.c_uint32), "p")),
            COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int, "m"), (["in"], ctypes.c_void_p, "f"),
                      (["in"], ctypes.c_void_p, "c")),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], ctypes.POINTER(ctypes.c_void_p), "f")),
            COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], ctypes.POINTER(ctypes.c_longlong), "a"),
                      (["out"], ctypes.POINTER(ctypes.c_longlong), "b")),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wt.HANDLE, "h")),
            COMMETHOD([], HRESULT, "GetService", (["in"], ctypes.POINTER(GUID), "iid"),
                      (["out"], ctypes.POINTER(ctypes.c_void_p), "obj")),
        ]

    class Handler(COMObject):
        _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

        def __init__(self):
            super().__init__()
            self.done = threading.Event()

        def ActivateCompleted(self, op):
            self.done.set()
            return 0

    return comtypes, GUID, IUnknown, IActivateAudioInterfaceAsyncOperation, IActivateAudioInterfaceCompletionHandler, \
        IAudioClient, IAudioCaptureClient, WAVEFORMATEX, Handler


class _ActParams(ctypes.Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", wt.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class _PropVariantBlob(ctypes.Structure):  # PROPVARIANT (VT_BLOB)
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("cbSize", ctypes.c_ulong), ("pBlobData", ctypes.c_void_p)]


class ProcessLoopback:
    def __init__(self, pid: int):
        self.pid = pid
        self.client = self.capture = None
        self.event = None

    def start(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # 這個執行緒用 MTA；已初始化過的話回傳錯誤碼，無妨
        (comtypes, GUID, IUnknown, IOp, IHandler, IAudioClient, IAudioCaptureClient,
         WAVEFORMATEX, Handler) = _com()
        params = _ActParams(1, self.pid, 0)  # PROCESS_LOOPBACK, INCLUDE_TARGET_PROCESS_TREE
        pv = _PropVariantBlob(65, 0, 0, 0, ctypes.sizeof(params), ctypes.cast(ctypes.pointer(params), ctypes.c_void_p))
        handler = Handler()
        op = ctypes.POINTER(IOp)()
        fn = ctypes.windll.Mmdevapi.ActivateAudioInterfaceAsync
        fn.argtypes = [wt.LPCWSTR, ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        fn.restype = ctypes.HRESULT
        iid = IAudioClient._iid_
        hptr = handler.QueryInterface(IHandler)
        fn("VAD\\Process_Loopback", ctypes.byref(iid), ctypes.byref(pv), hptr, ctypes.byref(op))
        if not handler.done.wait(5):
            raise TimeoutError("音訊擷取啟動逾時")
        hr, unk = op.GetActivateResult()
        if hr != 0:
            raise OSError(f"ActivateAudioInterfaceAsync 失敗 0x{hr & 0xffffffff:08x}")
        self.client = unk.QueryInterface(IAudioClient)
        fmt = WAVEFORMATEX(1, 1, SR, SR * 2, 2, 16, 0)  # 16 kHz 單聲道 16-bit PCM，系統幫我們轉換
        flags = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000  # LOOPBACK|EVENTCALLBACK|AUTOCONVERTPCM|SRC_DEFAULT_QUALITY
        self.client.Initialize(0, flags, 2_000_000, 0, ctypes.byref(fmt), None)
        self.event = ctypes.windll.kernel32.CreateEventW(None, False, False, None)
        self.client.SetEventHandle(self.event)
        p = self.client.GetService(ctypes.byref(IAudioCaptureClient._iid_))
        self.capture = ctypes.cast(p, ctypes.POINTER(IAudioCaptureClient))
        self.client.Start()
        return self

    def read(self, seconds=0.25):
        """收集大約 seconds 秒的聲音。程式沒發出聲音時會回傳較短 (甚至空) 的陣列。"""
        want = int(SR * seconds)
        chunks, got = [], 0
        wait = ctypes.windll.kernel32.WaitForSingleObject
        deadline_ms = int(seconds * 1000) + 200
        while got < want:
            if wait(self.event, deadline_ms) != 0:
                break  # 逾時：程式沒在發聲
            while True:
                n = self.capture.GetNextPacketSize()
                if not n:
                    break
                data, frames, flags, _, _ = self.capture.GetBuffer()
                if frames:
                    if flags & 0x2:  # AUDCLNT_BUFFERFLAGS_SILENT
                        chunks.append(np.zeros(frames, np.float32))
                    else:
                        buf = (ctypes.c_int16 * frames).from_address(data)
                        chunks.append(np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0)
                    got += frames
                self.capture.ReleaseBuffer(frames)
        return np.concatenate(chunks) if chunks else np.zeros(0, np.float32)

    def close(self):
        try:
            if self.client:
                self.client.Stop()
        except Exception:
            pass
        if self.event:
            ctypes.windll.kernel32.CloseHandle(self.event)
        self.client = self.capture = self.event = None


if __name__ == "__main__":  # 測試：python procloop.py spotify.exe
    sys.stdout.reconfigure(encoding="utf-8")
    exe = sys.argv[1] if len(sys.argv) > 1 else "spotify.exe"
    pid = root_pid(exe)
    print(exe, "pid", pid)
    cap = ProcessLoopback(pid).start()
    total = []
    for _ in range(12):
        a = cap.read(0.25)
        total.append(a)
    a = np.concatenate(total)
    print(f"captured {len(a) / SR:.2f}s, rms {float(np.sqrt((a ** 2).mean())) if len(a) else 0:.4f}")
    cap.close()
