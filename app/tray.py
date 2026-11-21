import ctypes as C
import ctypes.wintypes as W
import queue
import threading

user32 = C.WinDLL('user32', use_last_error=True)
kernel32 = C.WinDLL('kernel32', use_last_error=True)
shell32 = C.WinDLL('shell32', use_last_error=True)
wtsapi32 = C.WinDLL('wtsapi32')
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

WM_DESTROY, WM_NULL, WM_TIMER, WM_POWERBROADCAST = 0x02, 0x00, 0x113, 0x218
WM_DEVICECHANGE, DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE, DBT_DEVNODES_CHANGED = 0x219, 0x8000, 0x8004, 0x07
WM_INPUT, RIDEV_REMOVE, RIDEV_INPUTSINK, RID_HEADER, RIDI_DEVICENAME = 0xFF, 0x01, 0x100, 0x10000005, 0x20000007
TIMER_REFRESH, TIMER_RAW = 1, 2
RAW_PAUSE_MS = 1000
WM_WTSSESSION_CHANGE, WM_LBUTTONUP, WM_RBUTTONUP, WM_CLOSE = 0x2B1, 0x202, 0x205, 0x10
WM_APP = 0x8000
WM_TRAY, WM_SHOW, WM_CALL, WM_QUIT_APP = WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_USER, NIIF_LARGE_ICON = 0x04, 0x20
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0x0, 0x8, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
REFRESH_MS = 60_000
MENU_OPEN, MENU_COMBOS, MENU_QUIT = 1, 2, 3
_mutex = None


class WNDCLASSEXW(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('style', W.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', C.c_int),
                ('cbWndExtra', C.c_int), ('hInstance', W.HINSTANCE), ('hIcon', W.HICON), ('hCursor', W.HANDLE),
                ('hbrBackground', W.HBRUSH), ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR),
                ('hIconSm', W.HICON)]


class DEV_BROADCAST_DEVICEINTERFACE_W(C.Structure):
    _fields_ = [('dbcc_size', W.DWORD), ('dbcc_devicetype', W.DWORD), ('dbcc_reserved', W.DWORD),
                ('dbcc_classguid', C.c_byte * 16), ('dbcc_name', W.WCHAR * 1)]


class RAWINPUTDEVICE(C.Structure):
    _fields_ = [('usUsagePage', W.USHORT), ('usUsage', W.USHORT), ('dwFlags', W.DWORD), ('hwndTarget', W.HWND)]


class RAWINPUTHEADER(C.Structure):
    _fields_ = [('dwType', W.DWORD), ('dwSize', W.DWORD), ('hDevice', W.HANDLE), ('wParam', W.WPARAM)]


HID_GUID = bytes.fromhex('b2551e4d6ff1cf1188cb001111000030')


class NOTIFYICONDATAW(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('hWnd', W.HWND), ('uID', W.UINT), ('uFlags', W.UINT),
                ('uCallbackMessage', W.UINT), ('hIcon', W.HICON), ('szTip', W.WCHAR * 128), ('dwState', W.DWORD),
                ('dwStateMask', W.DWORD), ('szInfo', W.WCHAR * 256), ('uVersion', W.UINT),
                ('szInfoTitle', W.WCHAR * 64), ('dwInfoFlags', W.DWORD), ('guidItem', C.c_byte * 16),
                ('hBalloonIcon', W.HICON)]


user32.RegisterClassExW.argtypes = [C.POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.restype = W.HWND
user32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int,
                                   W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.DestroyWindow.argtypes = [W.HWND]
user32.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
user32.LoadImageW.restype = W.HANDLE
user32.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, C.c_int, C.c_int, W.UINT]
user32.SetTimer.argtypes = [W.HWND, C.c_size_t, W.UINT, C.c_void_p]
user32.CreatePopupMenu.restype = W.HMENU
user32.AppendMenuW.argtypes = [W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR]
user32.SetMenuDefaultItem.argtypes = [W.HMENU, W.UINT, W.UINT]
user32.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p]
user32.DestroyMenu.argtypes = [W.HMENU]
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.FindWindowW.restype = W.HWND
user32.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user32.AllowSetForegroundWindow.argtypes = [W.DWORD]
user32.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
user32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
shell32.Shell_NotifyIconW.argtypes = [W.DWORD, C.POINTER(NOTIFYICONDATAW)]
wtsapi32.WTSRegisterSessionNotification.argtypes = [W.HWND, W.DWORD]
user32.RegisterDeviceNotificationW.restype = W.HANDLE
user32.RegisterRawInputDevices.argtypes = [C.POINTER(RAWINPUTDEVICE), W.UINT, W.UINT]
user32.GetRawInputData.restype = W.UINT
user32.GetRawInputData.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT), W.UINT]
user32.GetRawInputDeviceInfoW.restype = W.UINT
user32.GetRawInputDeviceInfoW.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT)]
user32.KillTimer.argtypes = [W.HWND, C.c_size_t]
user32.RegisterDeviceNotificationW.argtypes = [W.HANDLE, C.c_void_p, W.DWORD]
kernel32.GetModuleHandleW.restype = W.HMODULE
kernel32.CreateMutexW.restype = W.HANDLE
kernel32.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]


def dark_menus():
    try:
        C.WinDLL('uxtheme')[135](2)
    except (OSError, AttributeError):
        pass


def already_running(name, quit=False):
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, 'Local\\' + name)
    if C.get_last_error() != 183:
        return False
    hwnd = user32.FindWindowW(name, None)
    if hwnd:
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
        user32.AllowSetForegroundWindow(pid.value)
        user32.PostMessageW(hwnd, WM_QUIT_APP if quit else WM_SHOW, 0, 0)
    return True


class Tray:
    def __init__(self, name, icon_path, on_open, on_quit, on_combos, combos_on, on_refresh, on_ready=None,
                 on_devices=None, on_motion=None, motion_from=''):
        self.name = name
        self.icon_path = icon_path
        self.on_open, self.on_quit, self.on_combos = on_open, on_quit, on_combos
        self.combos_on, self.on_refresh, self.on_ready = combos_on, on_refresh, on_ready
        self.on_devices = on_devices
        self.on_motion, self.motion_from = on_motion, motion_from.lower()
        self._watching = self._raw_on = False
        self._devnames = {}
        self.hwnd = None
        self.tip = 'Taipan Studio'
        self._calls = queue.Queue()
        self._wndproc = WNDPROC(self._proc)
        self._thread = threading.Thread(target=self._run, daemon=True, name='tray')
        self._thread.start()

    def _run(self):
        dark_menus()
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=C.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc, hInstance=hinst,
                         lpszClassName=self.name)
        user32.RegisterClassExW(C.byref(wc))
        self._taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')
        self.hwnd = user32.CreateWindowExW(0, self.name, 'Taipan Studio', 0, 0, 0, 0, 0, None, None, hinst, None)
        cx, cy = user32.GetSystemMetrics(49), user32.GetSystemMetrics(50)
        self._icon = user32.LoadImageW(None, self.icon_path, 1, cx, cy, 0x10)
        self._big_icon = user32.LoadImageW(None, self.icon_path, 1, 64, 64, 0x10)
        self._notify(NIM_ADD)
        wtsapi32.WTSRegisterSessionNotification(self.hwnd, 0)
        f = DEV_BROADCAST_DEVICEINTERFACE_W(dbcc_size=C.sizeof(DEV_BROADCAST_DEVICEINTERFACE_W), dbcc_devicetype=5)
        C.memmove(f.dbcc_classguid, HID_GUID, 16)
        self._devnotify = user32.RegisterDeviceNotificationW(self.hwnd, C.byref(f), 0)
        user32.SetTimer(self.hwnd, TIMER_REFRESH, REFRESH_MS, None)
        if self.on_ready:
            self._safe(self.on_ready)
        if not self._calls.empty():
            user32.PostMessageW(self.hwnd, WM_CALL, 0, 0)
        msg = W.MSG()
        while user32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(C.byref(msg))
            user32.DispatchMessageW(C.byref(msg))

    def _safe(self, fn, *a):
        try:
            return fn(*a)
        except Exception:
            import traceback
            traceback.print_exc()

    def _proc(self, hwnd, msg, wp, lp):
        if msg == WM_TRAY:
            if lp == WM_LBUTTONUP:
                self._safe(self.on_open)
            elif lp == WM_RBUTTONUP:
                self._menu()
            return 0
        if msg == WM_SHOW:
            self._safe(self.on_open)
            return 0
        if msg == WM_QUIT_APP:
            self._safe(self.on_quit)
            return 0
        if msg == WM_CALL:
            while not self._calls.empty():
                self._safe(self._calls.get_nowait())
            return 0
        if msg == WM_TIMER:
            if wp == TIMER_RAW:
                user32.KillTimer(self.hwnd, TIMER_RAW)
                self._raw(self._watching)
            else:
                self._safe(self.on_refresh, 'timer')
            return 0
        if msg == WM_INPUT:
            self._raw(False)
            if self._watching:
                user32.SetTimer(self.hwnd, TIMER_RAW, RAW_PAUSE_MS, None)
                if self._from_watched(lp) and self.on_motion:
                    self._safe(self.on_motion)
            return user32.DefWindowProcW(hwnd, msg, wp, lp)
        if msg == WM_WTSSESSION_CHANGE or (msg == WM_POWERBROADCAST and wp in (0x07, 0x12)):
            self._safe(self.on_refresh, 'session' if msg == WM_WTSSESSION_CHANGE else 'resume')
            return 0 if msg == WM_WTSSESSION_CHANGE else 1
        if msg == WM_DEVICECHANGE:
            if wp in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE, DBT_DEVNODES_CHANGED) and self.on_devices:
                self._safe(self.on_devices)
            return 1
        if msg == self._taskbar_created:
            self._notify(NIM_ADD)
            return 0
        if msg == WM_DESTROY:
            self._notify(NIM_DELETE)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _notify(self, op, info=None, title=''):
        d = NOTIFYICONDATAW(cbSize=C.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                            uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=WM_TRAY, hIcon=self._icon)
        d.szTip = self.tip[:127]
        if info:
            d.uFlags |= NIF_INFO
            d.szInfo, d.szInfoTitle = info[:255], title[:63]
            d.dwInfoFlags, d.hBalloonIcon = NIIF_USER | NIIF_LARGE_ICON, self._big_icon
        shell32.Shell_NotifyIconW(op, C.byref(d))

    def _menu(self):
        m = user32.CreatePopupMenu()
        user32.AppendMenuW(m, MF_STRING, MENU_OPEN, 'Open Taipan Studio')
        user32.AppendMenuW(m, MF_STRING | (MF_CHECKED if self._safe(self.combos_on) else 0), MENU_COMBOS, 'Shortcuts on')
        user32.AppendMenuW(m, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(m, MF_STRING, MENU_QUIT, 'Quit')
        user32.SetMenuDefaultItem(m, MENU_OPEN, 0)
        pt = W.POINT()
        user32.GetCursorPos(C.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        if cmd == MENU_OPEN:
            self._safe(self.on_open)
        elif cmd == MENU_COMBOS:
            self._safe(self.on_combos)
        elif cmd == MENU_QUIT:
            self._safe(self.on_quit)

    def watch_mouse(self, on):
        def apply():
            self._watching = bool(on)
            if not on:
                user32.KillTimer(self.hwnd, TIMER_RAW)
            self._raw(bool(on))
        self.call(apply)

    def _raw(self, on):
        if on == self._raw_on or not self.hwnd:
            return
        dev = RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK if on else RIDEV_REMOVE, self.hwnd if on else None)
        if user32.RegisterRawInputDevices(C.byref(dev), 1, C.sizeof(RAWINPUTDEVICE)):
            self._raw_on = on

    def _from_watched(self, lp):
        hdr, size = RAWINPUTHEADER(), W.UINT(C.sizeof(RAWINPUTHEADER))
        if user32.GetRawInputData(lp, RID_HEADER, C.byref(hdr), C.byref(size), C.sizeof(RAWINPUTHEADER)) == 0xFFFFFFFF:
            return False
        h = hdr.hDevice
        if not h:
            return False
        name = self._devnames.get(h)
        if name is None:
            n = W.UINT(0)
            user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None, C.byref(n))
            buf = C.create_unicode_buffer(n.value + 1)
            user32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, C.byref(n))
            name = self._devnames[h] = buf.value.lower()
        return bool(self.motion_from) and self.motion_from in name

    def call(self, fn):
        self._calls.put(fn)
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CALL, 0, 0)

    def set_tip(self, tip):
        self.tip = tip
        self.call(lambda: self._notify(NIM_MODIFY))

    def balloon(self, text, title='Taipan Studio'):
        self.call(lambda: self._notify(NIM_MODIFY, text, title))

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
