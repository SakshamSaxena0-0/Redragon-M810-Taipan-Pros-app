"""Notification-area icon and the hidden window behind it.

The tray thread also runs the message loop the combos' low-level mouse hook needs (Windows calls a hook on the
thread that installed it), and its hidden window is how a second copy of the app asks this one to show itself.
The thread sleeps in GetMessageW, so it uses no CPU between events.
"""
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
WM_WTSSESSION_CHANGE, WM_LBUTTONUP, WM_RBUTTONUP, WM_CLOSE = 0x2B1, 0x202, 0x205, 0x10
WM_APP = 0x8000
WM_TRAY, WM_SHOW, WM_CALL, WM_QUIT_APP = WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_USER, NIIF_LARGE_ICON = 0x04, 0x20
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0x0, 0x8, 0x800
TPM_RIGHTBUTTON, TPM_NONOTIFY, TPM_RETURNCMD = 0x2, 0x80, 0x100
REFRESH_MS = 60_000      # re-install the hook now and then: Windows silently drops one that was ever too slow
MENU_OPEN, MENU_COMBOS, MENU_QUIT = 1, 2, 3
_mutex = None


class WNDCLASSEXW(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('style', W.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', C.c_int),
                ('cbWndExtra', C.c_int), ('hInstance', W.HINSTANCE), ('hIcon', W.HICON), ('hCursor', W.HANDLE),
                ('hbrBackground', W.HBRUSH), ('lpszMenuName', W.LPCWSTR), ('lpszClassName', W.LPCWSTR),
                ('hIconSm', W.HICON)]


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
kernel32.GetModuleHandleW.restype = W.HMODULE
kernel32.CreateMutexW.restype = W.HANDLE
kernel32.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]


def dark_menus():
    """Ask Windows for dark popup menus (uxtheme ordinal 135, SetPreferredAppMode: 2 = force dark). Undocumented
    but stable since Windows 10 1903; on anything else menus simply stay light."""
    try:
        C.WinDLL('uxtheme')[135](2)
    except (OSError, AttributeError):
        pass


def already_running(name, quit=False):
    """True if another copy owns `name`; that copy is then asked to show its window, or to exit if quit is set
    (`--quit`, so a rebuild can replace the exe). Keeps the mutex open."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, 'Local\\' + name)
    if C.get_last_error() != 183:           # ERROR_ALREADY_EXISTS
        return False
    hwnd = user32.FindWindowW(name, None)
    if hwnd:
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
        user32.AllowSetForegroundWindow(pid.value)
        user32.PostMessageW(hwnd, WM_QUIT_APP if quit else WM_SHOW, 0, 0)
    return True


class Tray:
    """Callbacks run on the tray thread: on_open() shows the main window, on_quit() exits, on_combos() toggles
    combos and combos_on() reports whether they are on (for the menu tick), on_refresh(reason) re-installs the
    hook (reason is 'timer', 'session' or 'resume'). call(fn) runs fn on the tray thread."""

    def __init__(self, name, icon_path, on_open, on_quit, on_combos, combos_on, on_refresh, on_ready=None):
        self.name = name
        self.icon_path = icon_path
        self.on_open, self.on_quit, self.on_combos = on_open, on_quit, on_combos
        self.combos_on, self.on_refresh, self.on_ready = combos_on, on_refresh, on_ready
        self.hwnd = None
        self.tip = 'Taipan Studio'
        self._calls = queue.Queue()
        self._wndproc = WNDPROC(self._proc)
        self._thread = threading.Thread(target=self._run, daemon=True, name='tray')
        self._thread.start()

    # -- thread
    def _run(self):
        dark_menus()
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=C.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc, hInstance=hinst,
                         lpszClassName=self.name)
        user32.RegisterClassExW(C.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.name, 'Taipan Studio', 0, 0, 0, 0, 0, None, None, hinst, None)
        self._taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')
        cx, cy = user32.GetSystemMetrics(49), user32.GetSystemMetrics(50)      # SM_CXSMICON, SM_CYSMICON
        self._icon = user32.LoadImageW(None, self.icon_path, 1, cx, cy, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
        self._big_icon = user32.LoadImageW(None, self.icon_path, 1, 64, 64, 0x10)
        self._notify(NIM_ADD)
        wtsapi32.WTSRegisterSessionNotification(self.hwnd, 0)
        user32.SetTimer(self.hwnd, 1, REFRESH_MS, None)
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
            self._safe(self.on_refresh, 'timer')
            return 0
        if msg == WM_WTSSESSION_CHANGE or (msg == WM_POWERBROADCAST and wp in (0x07, 0x12)):  # resume suspend/auto
            self._safe(self.on_refresh, 'session' if msg == WM_WTSSESSION_CHANGE else 'resume')
            return 0 if msg == WM_WTSSESSION_CHANGE else 1
        if msg == self._taskbar_created:        # Explorer restarted: put the icon back
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
        user32.SetForegroundWindow(self.hwnd)       # so the menu closes when you click elsewhere
        cmd = user32.TrackPopupMenu(m, TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(m)
        if cmd == MENU_OPEN:
            self._safe(self.on_open)
        elif cmd == MENU_COMBOS:
            self._safe(self.on_combos)
        elif cmd == MENU_QUIT:
            self._safe(self.on_quit)

    # -- any thread
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
