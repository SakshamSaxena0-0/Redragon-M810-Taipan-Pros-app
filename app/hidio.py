"""Raw HID transport for the Redragon M810 Taipan Pro (VID 0x25A7).

Recovered from OemDrv.exe 3.1 (SendCommand 0x443ea0, checksum 0x443000, reader thread 0x4428e0):
  host -> mouse : 17-byte feature report, id 0x08, on the interface-1 collection with usage page 0xFF02
                  [0x08, cmd, status, addr_hi, addr_lo, len, data x10, checksum]
  mouse -> host : 17-byte input report, id 0x09, on the collection with usage page 0xFF01, same layout.
                  cmd 0x0A is an unsolicited event (DPI button pressed, etc.).
  checksum      : (0x55 - sum(bytes 0..15)) & 0xFF
"""
import ctypes as C
import ctypes.wintypes as W
import queue
import threading
import time

VID = 0x25A7
PIDS = {0xFA7E: '2.4G receiver', 0xFA7F: 'wired'}

_hid = C.WinDLL('hid')
_sa = C.WinDLL('setupapi')
_k32 = C.WinDLL('kernel32', use_last_error=True)


class _GUID(C.Structure):
    _fields_ = [('a', W.DWORD), ('b', W.WORD), ('c', W.WORD), ('d', C.c_ubyte * 8)]


class _IFD(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('g', _GUID), ('Flags', W.DWORD), ('Res', C.c_void_p)]


class _ATTR(C.Structure):
    _fields_ = [('Size', W.ULONG), ('VID', W.USHORT), ('PID', W.USHORT), ('Ver', W.USHORT)]


class _CAPS(C.Structure):
    _fields_ = ([('Usage', W.USHORT), ('UsagePage', W.USHORT), ('In', W.USHORT), ('Out', W.USHORT),
                 ('Feat', W.USHORT), ('Res', W.USHORT * 17)] + [(f'n{i}', W.USHORT) for i in range(10)])


class _OVL(C.Structure):
    _fields_ = [('Internal', C.c_void_p), ('InternalHigh', C.c_void_p), ('Offset', W.DWORD),
                ('OffsetHigh', W.DWORD), ('hEvent', W.HANDLE)]


_sa.SetupDiGetClassDevsW.restype = C.c_void_p
_sa.SetupDiGetClassDevsW.argtypes = [C.POINTER(_GUID), C.c_wchar_p, C.c_void_p, W.DWORD]
_sa.SetupDiEnumDeviceInterfaces.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(_GUID), W.DWORD, C.POINTER(_IFD)]
_sa.SetupDiGetDeviceInterfaceDetailW.argtypes = [C.c_void_p, C.POINTER(_IFD), C.c_void_p, W.DWORD,
                                                 C.POINTER(W.DWORD), C.c_void_p]
_sa.SetupDiDestroyDeviceInfoList.argtypes = [C.c_void_p]
_k32.CreateFileW.restype = W.HANDLE
_k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
_k32.CreateEventW.restype = W.HANDLE
_k32.CreateEventW.argtypes = [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
_k32.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.c_void_p, C.c_void_p]
_k32.GetOverlappedResult.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(W.DWORD), W.BOOL]
_k32.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
_k32.ResetEvent.argtypes = [W.HANDLE]
_k32.CancelIoEx.argtypes = [W.HANDLE, C.c_void_p]
_k32.CloseHandle.argtypes = [W.HANDLE]
_hid.HidD_SetFeature.argtypes = [W.HANDLE, C.c_void_p, W.ULONG]
_hid.HidD_GetAttributes.argtypes = [W.HANDLE, C.POINTER(_ATTR)]
_hid.HidD_GetPreparsedData.argtypes = [W.HANDLE, C.POINTER(C.c_void_p)]
_hid.HidD_FreePreparsedData.argtypes = [C.c_void_p]
_hid.HidP_GetCaps.argtypes = [C.c_void_p, C.POINTER(_CAPS)]

INVALID = W.HANDLE(-1).value
EVENT = 0x0A
ADDRESSED = (0x07, 0x08)    # EEPROM write and read


class DeviceError(Exception):
    pass


def checksum(buf):
    return (0x55 - sum(buf[:16])) & 0xFF


def _open(path, flags=0):
    h = _k32.CreateFileW(path, 0xC0000000, 3, None, 3, flags, None)
    return None if h in (None, INVALID) else h


def enumerate_devices():
    """Return [{pid, kind, version, paths: {usage_page: path}}] for every M810 interface set present."""
    g = _GUID()
    _hid.HidD_GetHidGuid(C.byref(g))
    info = _sa.SetupDiGetClassDevsW(C.byref(g), None, None, 0x12)
    found = {}
    i = 0
    try:
        while True:
            d = _IFD()
            d.cbSize = C.sizeof(_IFD)
            if not _sa.SetupDiEnumDeviceInterfaces(info, None, C.byref(g), i, C.byref(d)):
                break
            i += 1
            need = W.DWORD()
            _sa.SetupDiGetDeviceInterfaceDetailW(info, C.byref(d), None, 0, C.byref(need), None)
            buf = C.create_string_buffer(need.value)
            C.cast(buf, C.POINTER(W.DWORD))[0] = 8 if C.sizeof(C.c_void_p) == 8 else 6
            if not _sa.SetupDiGetDeviceInterfaceDetailW(info, C.byref(d), buf, need, None, None):
                continue
            path = C.wstring_at(C.addressof(buf) + 4)
            if f'vid_{VID:04x}' not in path.lower():
                continue
            h = _k32.CreateFileW(path, 0, 3, None, 3, 0, None)
            if h in (None, INVALID):
                continue
            try:
                a = _ATTR()
                a.Size = C.sizeof(_ATTR)
                _hid.HidD_GetAttributes(h, C.byref(a))
                pp = C.c_void_p()
                _hid.HidD_GetPreparsedData(h, C.byref(pp))
                caps = _CAPS()
                _hid.HidP_GetCaps(pp, C.byref(caps))
                _hid.HidD_FreePreparsedData(pp)
            finally:
                _k32.CloseHandle(h)
            if a.PID not in PIDS:
                continue
            dev = found.setdefault(a.PID, {'pid': a.PID, 'kind': PIDS[a.PID], 'version': a.Ver, 'paths': {}})
            dev['paths'][caps.UsagePage] = path
    finally:
        _sa.SetupDiDestroyDeviceInfoList(info)
    return [d for d in found.values() if 0xFF02 in d['paths'] and 0xFF01 in d['paths']]


class Mouse:
    """One open M810. send() is thread-safe; events (cmd 0x0A) go to on_event(payload)."""

    def __init__(self, dev, on_event=None):
        self.info = dev
        self.on_event = on_event
        self._cmd = _open(dev['paths'][0xFF02])
        self._in = _open(dev['paths'][0xFF01], 0x40000000)
        if not self._cmd or not self._in:
            self.close()
            raise DeviceError('Could not open the mouse. Close the Redragon software and try again.')
        self._replies = queue.Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        ev = _k32.CreateEventW(None, True, False, None)
        buf = (C.c_ubyte * 17)()
        ovl = _OVL()
        ovl.hEvent = ev
        n = W.DWORD()
        try:
            while not self._stop.is_set():
                _k32.ResetEvent(ev)
                if not _k32.ReadFile(self._in, buf, 17, None, C.byref(ovl)) and C.get_last_error() != 997:
                    self._replies.put(None)
                    return
                while _k32.WaitForSingleObject(ev, 200) != 0:
                    if self._stop.is_set():
                        _k32.CancelIoEx(self._in, C.byref(ovl))
                        return
                if not _k32.GetOverlappedResult(self._in, C.byref(ovl), C.byref(n), False):
                    self._replies.put(None)
                    return
                pkt = bytes(buf[:n.value])
                if len(pkt) < 17 or pkt[0] != 0x09:
                    continue
                if pkt[1] == EVENT:
                    if self.on_event:
                        try:
                            self.on_event(pkt[1:16])
                        except Exception:
                            pass
                else:
                    self._replies.put(pkt)
        finally:
            _k32.CloseHandle(ev)

    def send(self, cmd, addr=0, data=b'', length=None, status=0, timeout=1.0, retries=3):
        """Send one command and return the matching 15-byte reply payload [cmd, status, ahi, alo, len, data x10].
        length defaults to len(data); reads pass the byte count they want back instead."""
        data = bytes(data)
        if len(data) > 10:
            raise ValueError('at most 10 data bytes per packet')
        pkt = bytearray(17)
        pkt[0] = 0x08
        pkt[1] = cmd
        pkt[2] = status
        pkt[3] = (addr >> 8) & 0xFF
        pkt[4] = addr & 0xFF
        pkt[5] = len(data) if length is None else length
        pkt[6:6 + len(data)] = data
        pkt[16] = checksum(pkt)
        raw = (C.c_ubyte * 17).from_buffer(pkt)
        with self._lock:
            for _ in range(retries):
                while not self._replies.empty():
                    self._replies.get_nowait()
                if not _hid.HidD_SetFeature(self._cmd, raw, 17):
                    time.sleep(0.1)
                    continue
                end = time.monotonic() + timeout
                while True:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    try:
                        r = self._replies.get(timeout=left)
                    except queue.Empty:
                        break
                    if r is None:
                        raise DeviceError('The mouse was disconnected.')
                    # reads and writes must also echo the address: over the 2.4G link a reply can come after its
                    # command timed out, and taking it for the next one shifts read data by a whole packet
                    if r[1] == cmd and (cmd not in ADDRESSED or r[3:5] == pkt[3:5]):
                        return r[1:16]
        raise DeviceError(f'No reply from the mouse to command 0x{cmd:02X}.')

    def close(self):
        if getattr(self, '_stop', None):
            self._stop.set()
            self._reader.join(1.0)
        for h in (getattr(self, '_cmd', None), getattr(self, '_in', None)):
            if h:
                _k32.CloseHandle(h)
        self._cmd = self._in = None
