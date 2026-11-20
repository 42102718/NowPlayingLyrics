"""
裝置電量：
  ・藍牙耳機 / 手把 / 鍵盤 (Windows 在「設定 → 藍牙與裝置」顯示的那個電量，裝置屬性 DEVPKEY_Bluetooth_Battery)
  ・2.4G 接收器 / USB 的滑鼠、耳機 (羅技 LIGHTSPEED、Razer、SteelSeries…，用 HaloBattery 的 providers)
  ・筆電電池

    poll(receivers()) -> [{"id", "name", "level" (0~100), "connected" (bool), "kind", "charging", "src"}]
"""
import ctypes
import ctypes.wintypes as wt

cfg = ctypes.WinDLL("cfgmgr32")
kernel32 = ctypes.WinDLL("kernel32")


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s):
        s = s.strip("{}").replace("-", "")
        b = bytes.fromhex(s)
        g = cls()
        g.Data1 = int.from_bytes(b[0:4], "big")
        g.Data2 = int.from_bytes(b[4:6], "big")
        g.Data3 = int.from_bytes(b[6:8], "big")
        for i in range(8):
            g.Data4[i] = b[8 + i]
        return g


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


def _key(fmtid, pid):
    return DEVPROPKEY(GUID.parse(fmtid), pid)


KEY_BATTERY = _key("104EA319-6EE2-4701-BD47-8DDBF425BBE5", 2)         # DEVPKEY_Bluetooth_Battery (BYTE)
KEY_CONNECTED = _key("83DA6326-97A6-4088-9453-A1923F573B29", 15)      # 藍牙裝置是否連線中 (BOOLEAN)
KEY_FRIENDLY = _key("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)       # DEVPKEY_NAME
KEY_CLASS = _key("A45C254E-DF1C-4EFD-8020-67D146A850E0", 10)          # DEVPKEY_Device_Class (字串)
KEY_CONTAINER = _key("8C7ED206-3F8A-4827-B3AB-AE9E1FAEFC6C", 2)       # DEVPKEY_Device_ContainerId (GUID)

cfg.CM_Get_Device_ID_List_SizeW.argtypes = [ctypes.POINTER(ctypes.c_ulong), wt.LPCWSTR, ctypes.c_ulong]
cfg.CM_Get_Device_ID_ListW.argtypes = [wt.LPCWSTR, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(ctypes.c_uint32), wt.LPCWSTR, ctypes.c_ulong]
cfg.CM_Get_DevNode_PropertyW.argtypes = [ctypes.c_uint32, ctypes.POINTER(DEVPROPKEY), ctypes.POINTER(ctypes.c_ulong),
                                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong]
CM_GETIDLIST_FILTER_PRESENT = 0x100
CM_LOCATE_DEVNODE_NORMAL = 0


def _ids(enumerator):
    n = ctypes.c_ulong()
    flags = CM_GETIDLIST_FILTER_PRESENT | 0x1  # FILTER_ENUMERATOR
    if cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(n), enumerator, flags) != 0 or not n.value:
        return []
    buf = ctypes.create_unicode_buffer(n.value)
    if cfg.CM_Get_Device_ID_ListW(enumerator, buf, n.value, flags) != 0:
        return []
    return [s for s in ctypes.wstring_at(ctypes.addressof(buf), n.value).split("\0") if s]


def _prop(node, key):
    t, size = ctypes.c_ulong(), ctypes.c_ulong(0)
    cfg.CM_Get_DevNode_PropertyW(node, ctypes.byref(key), ctypes.byref(t), None, ctypes.byref(size), 0)
    if not size.value:
        return None
    buf = (ctypes.c_ubyte * size.value)()
    if cfg.CM_Get_DevNode_PropertyW(node, ctypes.byref(key), ctypes.byref(t), buf, ctypes.byref(size), 0) != 0:
        return None
    ty = t.value
    raw = bytes(buf)
    if ty == 0x12:                      # DEVPROP_TYPE_STRING
        return raw.decode("utf-16-le").rstrip("\0")
    if ty in (0x03, 0x11):              # BYTE / BOOLEAN
        return raw[0]
    if ty == 0x0D:                      # GUID
        return raw.hex()
    if ty == 0x07:                      # UINT32
        return int.from_bytes(raw[:4], "little")
    return raw


def _kind(name, dev_id):
    n = (name or "").lower()
    if any(k in n for k in ("buds", "pods", "headset", "headphone", "earphone", "耳機", "wh-", "wf-", "ear", "airpods",
                            "bose", "jabra", "qc", "soundcore", "beats")):
        return "headphones"
    if any(k in n for k in ("controller", "gamepad", "xbox", "dualsense", "dualshock", "pro controller", "8bitdo", "手把")):
        return "controller"
    if any(k in n for k in ("mouse", "mx master", "mx anywhere", "滑鼠", "trackpad")):
        return "mouse"
    if any(k in n for k in ("keyboard", "keys", "鍵盤", "keychron", "azoth", "nuphy", "akko", "ducky", "k2 ", "k2 max",
                            "huntsman", "apex", "g915", "g715", "mx keys", "craft")):
        return "keyboard"
    if any(k in n for k in ("pen", "stylus", "筆")):
        return "pen"
    if any(k in n for k in ("phone", "iphone", "galaxy", "pixel", "手機")):
        return "phone"
    if any(k in n for k in ("speaker", "jbl", "喇叭", "boom")):
        return "speaker"
    return "device"


SUFFIXES = (" hands-free ag", " hands-free hf", " hands-free", " avrcp transport", " a2dp snk", " a2dp", " stereo",
            " le", " 免持")


def _clean(name):
    n = name.strip()
    low = n.lower()
    for s in SUFFIXES:
        if low.endswith(s):
            return n[: -len(s)].strip()
    return n


def bluetooth():
    """所有回報電量的藍牙裝置 (同一個實體裝置有好幾個節點，用 ContainerId 合併)。"""
    nodes = []
    connected = {}
    for enum in ("BTHENUM", "BTHLE", "BTHLEDEVICE", "BTHHFENUM"):
        for dev_id in _ids(enum):
            node = ctypes.c_uint32()
            if cfg.CM_Locate_DevNodeW(ctypes.byref(node), dev_id, CM_LOCATE_DEVNODE_NORMAL) != 0:
                continue
            container = _prop(node.value, KEY_CONTAINER) or dev_id
            conn = _prop(node.value, KEY_CONNECTED)
            if conn:
                connected[container] = True
            nodes.append((node.value, dev_id, container))
    found = {}
    for node, dev_id, container in nodes:
        level = _prop(node, KEY_BATTERY)
        if level is None or not isinstance(level, int) or level > 100:
            continue
        raw = _prop(node, KEY_FRIENDLY) or ""
        name = _clean(raw)
        kind = "headphones" if "hands-free" in raw.lower() else _kind(name, dev_id)
        entry = {"id": str(container), "name": name, "level": int(level), "connected": connected.get(container, False),
                 "kind": kind, "charging": False, "src": "bt"}
        cur = found.get(container)
        if cur is None:
            found[container] = entry
        elif cur["kind"] == "device" and kind != "device":
            cur["kind"] = kind
    return list(found.values())


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]


def laptop():
    s = SYSTEM_POWER_STATUS()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    if s.BatteryFlag & 128 or s.BatteryFlag == 255 or s.BatteryLifePercent > 100:
        return None  # 桌機：沒有電池
    return {"id": "laptop", "name": "這台電腦", "level": int(s.BatteryLifePercent), "connected": True,
            "kind": "laptop", "charging": s.ACLineStatus == 1, "src": "sys"}


# ───── 2.4G 接收器 / USB 線的滑鼠、耳機、鍵盤、手把 ─────
# 用 HaloBattery (MIT 授權，github.com/HeyOkay/HaloBattery) 的各品牌 providers：
# 羅技、Razer、SteelSeries、HyperX、Corsair、ASUS ROG… 直接跟接收器溝通，不用開原廠軟體。
_providers = None
HID_KIND = {"headset": "headphones", "gamepad": "controller", "controller": "controller", "mouse": "mouse",
            "keyboard": "keyboard"}


def _make_providers():
    import inspect
    import logging
    logging.getLogger("halo_battery").setLevel(logging.CRITICAL)
    import halo_providers as hp
    out = []
    for name, cls in vars(hp).items():
        # 藍牙由上面自己讀 (不用開 PowerShell)
        if inspect.isclass(cls) and issubclass(cls, hp.Provider) and name not in ("Provider", "BluetoothProvider"):
            try:
                out.append(cls())
            except Exception as e:
                print("[battery] provider", name, repr(e), flush=True)
    return out


def receivers():
    """跑一輪所有品牌 (約 0.5 秒)，回傳有電量的裝置。"""
    global _providers
    if _providers is None:
        _providers = _make_providers()
    out = []
    for p in _providers:
        try:
            found = p.poll()
        except Exception:
            continue
        for st in found:
            if st.level is None:
                continue
            name = st.name or ""
            kind = HID_KIND.get(st.kind or "", "") or _kind(name, "")
            out.append({"id": "hid:" + st.key, "name": name, "level": int(st.level), "connected": bool(st.online),
                        "kind": kind, "charging": bool(st.charging), "src": "hid"})
    return out


def _norm(name):
    return "".join(c for c in name.lower() if c.isalnum())


def merge(bt, hid):
    """同一個裝置可能藍牙和接收器都看得到 (例如 PRO X 2)：以連線中的、接收器的為準。"""
    out = list(hid)
    for d in bt:
        n = _norm(d["name"])
        dup = next((h for h in hid if n and (n in _norm(h["name"]) or _norm(h["name"]) in n)), None)
        if dup is None:
            out.append(d)
        elif d["connected"] and not dup["connected"]:
            out[out.index(dup)] = d
    return out


def poll(hid_cache=None):
    """hid_cache：上一次 receivers() 的結果 (接收器比較慢，呼叫端自己決定多久讀一次)。"""
    out = []
    try:
        out += merge(bluetooth(), hid_cache or [])
    except Exception as e:
        print("[battery]", repr(e), flush=True)
    try:
        lp = laptop()
        if lp:
            out.append(lp)
    except Exception as e:
        print("[battery]", repr(e), flush=True)
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for d in poll():
        print(d)
    print("all BT nodes with names:")
    for enum in ("BTHENUM", "BTHLE", "BTHLEDEVICE"):
        for dev_id in _ids(enum):
            node = ctypes.c_uint32()
            if cfg.CM_Locate_DevNodeW(ctypes.byref(node), dev_id, 0) == 0:
                print(" ", enum, _prop(node.value, KEY_FRIENDLY), "| batt", _prop(node.value, KEY_BATTERY),
                      "| conn", _prop(node.value, KEY_CONNECTED))
