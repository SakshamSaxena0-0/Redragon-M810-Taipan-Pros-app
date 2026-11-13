"""Mouse-button combos: hold a starter button (Forward / MB5 by default) and scroll or click to run an action.

A low-level mouse hook (WH_MOUSE_LL) sees every button and wheel event before any app does, so the clicks that
make up a combo can be kept from the window under the cursor. Mouse moves are almost all of the traffic (up to
1000 a second at 1000 Hz), so a 40-byte x64 thunk hands them straight to CallNextHookEx: they never enter
Python or wait for the GIL, and cost only Windows' own hook dispatch. Measured on the dev PC: about 55k CPU
cycles per move with the thunk against about 90k with a plain ctypes callback. Without input, the hook costs nothing.

How a starter behaves while combos are on:
  pressed               kept from apps; a combo starts
  scroll while held     each 120-unit notch runs that starter's wheel combo at once (volume steps, ...)
  clicks while held     kept from apps and collected; a sequence that completes a combo runs at once, unless a
                        longer combo starts the same way, then it runs on release
  released untouched    the starter's own click is replayed, so a plain tap still works (on release)
ComboEngine is the state machine with no Win32 in it; tests/test_combos.py drives it directly.
"""
import ctypes as C
import ctypes.wintypes as W
import os
import platform
import queue
import struct
import threading

STARTERS = ('forward', 'back', 'middle')
BUTTONS = ('left', 'right', 'middle', 'back', 'forward')
WHEEL = ('wheel_up', 'wheel_down')
NOTCH = 120
STALE_MS = 8000         # a combo with no input for this long lost its release (Ctrl+Alt+Del, UAC prompt, ...)
MAX_SEQ = 6
OWN_TAG = 0x7A1B0000    # dwExtraInfo of the clicks and keys we send
TEST_TAG = 0x7A1B0001   # TAIPAN_HOOK_TEST=1: injected events with this tag are treated like real ones
TEST = os.environ.get('TAIPAN_HOOK_TEST') == '1'

DEFAULT = {'enabled': True, 'exclude': [], 'list': [
    {'id': 'vol-up', 'starter': 'forward', 'seq': ['wheel_up'], 'action': {'type': 'media', 'key': 'volume_up', 'step': 1}},
    {'id': 'vol-down', 'starter': 'forward', 'seq': ['wheel_down'],
     'action': {'type': 'media', 'key': 'volume_down', 'step': 1}},
]}


class ComboEngine:
    """run(action) carries out a combo, replay(button) sends a plain click of a starter that was tapped on its
    own, recorded(starter, seq) receives gestures while recording, foreground() names the exe in front.
    All of them are called on the hook thread, so they must return quickly."""

    def __init__(self, run, replay, recorded=None, foreground=None):
        self.run = run
        self.replay = replay
        self.recorded = recorded
        self.foreground = foreground or (lambda: '')
        self.recording = False
        self.configure({})
        self.reset()

    def configure(self, cfg):
        table = {}
        if cfg.get('enabled'):
            for c in cfg.get('list', []):
                seq = tuple(c.get('seq') or ())
                if c.get('starter') in STARTERS and seq and c.get('action'):
                    table.setdefault((c['starter'], seq), c['action'])
        # each attribute is replaced whole, so the hook thread never sees a half-built table
        self._table = table
        self._prefixes = frozenset((s, seq[:i]) for s, seq in table for i in range(1, len(seq)))
        self._starters = frozenset(s for s, _ in table)
        self._exclude = frozenset(x.lower() for x in cfg.get('exclude', []))

    @property
    def active(self):
        return bool(self._starters) or self.recording

    @property
    def armed(self):
        return self._held is not None

    def reset(self):
        self._held = None
        self._seq = []
        self._used = False
        self._acc = 0
        self._kept = set()      # buttons whose press we kept from apps; their release is kept too
        self._last = 0

    def _stale(self, t):
        if self._held is not None and (t - self._last) & 0xFFFFFFFF > STALE_MS:
            self.reset()

    def button(self, name, down, t):
        """A button press or release at tick t (ms). Returns True to keep it from apps."""
        self._stale(t)
        if down:
            if self._held is None:
                if name not in (STARTERS if self.recording else self._starters):
                    return False
                if self._exclude and not self.recording and self.foreground() in self._exclude:
                    return False
                self._held, self._seq, self._used, self._acc, self._last = name, [], False, 0, t
                return True
            self._last = t
            if name == self._held:          # pressed again: its release never reached us, start over
                self._seq, self._used, self._acc = [], False, 0
                return True
            if len(self._seq) < MAX_SEQ:
                self._seq.append(name)
            self._kept.add(name)
            self._used = True
            key = (self._held, tuple(self._seq))
            if not self.recording and key in self._table and key not in self._prefixes:
                self._seq = []          # nothing longer can follow: run it now, and the next click starts afresh
                self.run(self._table[key])
            return True
        if name == self._held:
            self._finish()
            return True
        if name in self._kept:
            self._kept.discard(name)
            return True
        return False

    def wheel(self, delta, t):
        """A wheel event (delta in 1/120 notches). Returns True to keep it from apps."""
        self._stale(t)
        if self._held is None:
            return False
        self._used = True
        self._last = t
        self._acc += delta
        while abs(self._acc) >= NOTCH:
            up = self._acc > 0
            self._acc -= NOTCH if up else -NOTCH
            token = 'wheel_up' if up else 'wheel_down'
            if self.recording:
                self.recorded and self.recorded(self._held, [token])
            else:
                action = self._table.get((self._held, (token,)))
                if action:
                    self.run(action)
        return True

    def _finish(self):
        held, seq, used = self._held, self._seq, self._used
        self._held = None
        if seq:
            if self.recording:
                self.recorded and self.recorded(held, seq)
            else:
                action = self._table.get((held, tuple(seq)))
                if action:
                    self.run(action)
        elif not used:
            self.replay(held)


# ------------------------------------------------------------------------------------- Win32
user32 = C.WinDLL('user32', use_last_error=True)
kernel32 = C.WinDLL('kernel32', use_last_error=True)
LRESULT = C.c_ssize_t
HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, W.WPARAM, W.LPARAM)
WH_MOUSE_LL = 14
WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP = 0x200, 0x20A, 0x20B, 0x20C
BUTTON_MSG = {0x201: ('left', True), 0x202: ('left', False), 0x204: ('right', True), 0x205: ('right', False),
              0x207: ('middle', True), 0x208: ('middle', False)}
XBUTTONS = {1: 'back', 2: 'forward'}
LLMHF_INJECTED = 0x01


class MSLLHOOKSTRUCT(C.Structure):
    _fields_ = [('x', W.LONG), ('y', W.LONG), ('mouseData', W.DWORD), ('flags', W.DWORD), ('time', W.DWORD),
                ('extra', C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('mouseData', W.DWORD), ('dwFlags', W.DWORD), ('time', W.DWORD),
                ('dwExtraInfo', C.c_size_t)]


class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', W.WORD), ('wScan', W.WORD), ('dwFlags', W.DWORD), ('time', W.DWORD),
                ('dwExtraInfo', C.c_size_t)]


class INPUT(C.Structure):
    class _U(C.Union):
        _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]
    _anonymous_ = ('u',)
    _fields_ = [('type', W.DWORD), ('u', _U)]


user32.SetWindowsHookExW.restype = W.HHOOK
user32.SetWindowsHookExW.argtypes = [C.c_int, C.c_void_p, W.HINSTANCE, W.DWORD]
user32.UnhookWindowsHookEx.argtypes = [W.HHOOK]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [W.HHOOK, C.c_int, W.WPARAM, W.LPARAM]
user32.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
user32.MapVirtualKeyW.argtypes = [W.UINT, W.UINT]
user32.GetForegroundWindow.restype = W.HWND
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.GetWindowTextLengthW.argtypes = [W.HWND]
user32.GetWindow.restype = W.HWND
user32.GetWindow.argtypes = [W.HWND, W.UINT]
user32.GetWindowLongW.argtypes = [W.HWND, C.c_int]
WNDENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, W.LPARAM]
kernel32.GetModuleHandleW.restype = W.HMODULE
kernel32.GetProcAddress.restype = C.c_void_p
kernel32.GetProcAddress.argtypes = [W.HMODULE, C.c_char_p]
kernel32.VirtualAlloc.restype = C.c_void_p
kernel32.VirtualAlloc.argtypes = [C.c_void_p, C.c_size_t, W.DWORD, W.DWORD]
kernel32.VirtualProtect.argtypes = [C.c_void_p, C.c_size_t, W.DWORD, C.POINTER(W.DWORD)]
kernel32.GetCurrentProcess.restype = W.HANDLE
kernel32.FlushInstructionCache.argtypes = [W.HANDLE, C.c_void_p, C.c_size_t]
kernel32.OpenProcess.restype = W.HANDLE
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
kernel32.CloseHandle.argtypes = [W.HANDLE]


def _move_filter(python_proc):
    """Executable thunk: WM_MOUSEMOVE -> CallNextHookEx(NULL, nCode, wParam, lParam), anything else -> python_proc.
    x64 only (args arrive in rcx, rdx, r8; CallNextHookEx takes rcx, rdx, r8, r9). Returns None elsewhere."""
    if C.sizeof(C.c_void_p) != 8 or platform.machine().upper() not in ('AMD64', 'X86_64'):
        return None
    cnh = kernel32.GetProcAddress(user32._handle, b'CallNextHookEx')
    code = (b'\x48\x81\xFA' + struct.pack('<I', WM_MOUSEMOVE) +   # cmp rdx, WM_MOUSEMOVE
            b'\x75\x16'                                           # jne python
            b'\x4D\x89\xC1'                                       # mov r9, r8     lParam
            b'\x49\x89\xD0'                                       # mov r8, rdx    wParam
            b'\x89\xCA'                                           # mov edx, ecx   nCode
            b'\x31\xC9'                                           # xor ecx, ecx   hhk = NULL
            b'\x48\xB8' + struct.pack('<Q', cnh) +                # mov rax, CallNextHookEx
            b'\xFF\xE0'                                           # jmp rax
            b'\x48\xB8' + struct.pack('<Q', python_proc) +        # python: mov rax, python_proc
            b'\xFF\xE0')                                          # jmp rax
    mem = kernel32.VirtualAlloc(None, 4096, 0x3000, 0x04)         # MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    if not cnh or not mem:
        return None
    C.memmove(mem, code, len(code))
    old = W.DWORD()
    if not kernel32.VirtualProtect(mem, 4096, 0x20, C.byref(old)):   # PAGE_EXECUTE_READ
        return None
    kernel32.FlushInstructionCache(kernel32.GetCurrentProcess(), mem, len(code))
    return mem


class MouseHook:
    """The WH_MOUSE_LL hook feeding a ComboEngine. install() and uninstall() must run on a thread that pumps
    messages (tray.Tray's thread); Windows calls the hook on that thread."""

    def __init__(self, engine):
        self.engine = engine
        self.handle = None
        self._proc = HOOKPROC(self._on_event)
        py = C.cast(self._proc, C.c_void_p).value
        self._entry = _move_filter(py)
        self.native = self._entry is not None
        if not self.native:
            self._entry = py

    def install(self):
        self.uninstall()
        self.handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._entry, kernel32.GetModuleHandleW(None), 0)
        return bool(self.handle)

    def uninstall(self):
        if self.handle:
            user32.UnhookWindowsHookEx(self.handle)
            self.handle = None
        self.engine.reset()

    def _on_event(self, code, msg, lparam):
        if code == 0 and msg != WM_MOUSEMOVE:
            m = MSLLHOOKSTRUCT.from_address(lparam)
            if not m.flags & LLMHF_INJECTED or (TEST and m.extra == TEST_TAG):
                try:
                    if msg == WM_MOUSEWHEEL:
                        delta = m.mouseData >> 16
                        keep = self.engine.wheel(delta - 0x10000 if delta & 0x8000 else delta, m.time)
                    elif msg in BUTTON_MSG:
                        keep = self.engine.button(*BUTTON_MSG[msg], m.time)
                    elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP) and (m.mouseData >> 16) in XBUTTONS:
                        keep = self.engine.button(XBUTTONS[m.mouseData >> 16], msg == WM_XBUTTONDOWN, m.time)
                    else:
                        keep = False
                except Exception:
                    keep = False
                if keep:
                    return 1
        return user32.CallNextHookEx(None, code, msg, lparam)


# ------------------------------------------------------------------------------------- sending input
CLICK_FLAGS = {'left': (0x02, 0x04), 'right': (0x08, 0x10), 'middle': (0x20, 0x40), 'back': (0x80, 0x100),
               'forward': (0x80, 0x100)}
MEDIA_VK = {'volume_up': 0xAF, 'volume_down': 0xAE, 'mute': 0xAD, 'next': 0xB0, 'previous': 0xB1, 'stop': 0xB2,
            'play_pause': 0xB3}
EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90,
               0xA3, 0xA5, *MEDIA_VK.values()}


def _send(inputs, tag=OWN_TAG):
    arr = (INPUT * len(inputs))()
    for i, (kind, s) in enumerate(inputs):
        arr[i].type = kind
        s.dwExtraInfo = tag
        if kind == 0:
            arr[i].mi = s
        else:
            arr[i].ki = s
    return user32.SendInput(len(inputs), arr, C.sizeof(INPUT))


def click(button, tag=OWN_TAG):
    down, up = CLICK_FLAGS[button]
    data = {'back': 1, 'forward': 2}.get(button, 0)
    return _send([(0, MOUSEINPUT(0, 0, data, down, 0, 0)), (0, MOUSEINPUT(0, 0, data, up, 0, 0))], tag)


def _key(vk, up):
    flags = (0x01 if vk in EXTENDED_VK else 0) | (0x02 if up else 0)
    return 1, KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, 0)


def tap_keys(vks, times=1):
    """Press vks in order and release them in reverse (a shortcut such as Ctrl+Shift+Esc), `times` times."""
    one = [_key(v, False) for v in vks] + [_key(v, True) for v in reversed(vks)]
    return _send(one * max(1, times))


def perform(action):
    kind = action.get('type')
    if kind == 'media':
        step = max(1, min(10, int(action.get('step', 1)))) if action['key'] in ('volume_up', 'volume_down') else 1
        tap_keys([MEDIA_VK[action['key']]], step)
    elif kind == 'keys':
        if action.get('vks'):
            tap_keys([int(v) for v in action['vks']])
    elif kind == 'launch':
        target = os.path.expandvars(action.get('target', '').strip().strip('"'))
        folder = os.path.dirname(target) if os.path.isfile(target) else None
        os.startfile(target, arguments=action.get('args', ''), cwd=folder)
    elif kind == 'url':
        url = action.get('url', '').strip()
        os.startfile(url if '://' in url else 'https://' + url)
    elif kind == 'lock':
        user32.LockWorkStation()
    else:
        raise ValueError(f'Unknown combo action {kind!r}')


class Runner:
    """Carries out actions and replays on a worker thread, so the hook returns at once. Blocks in get() when
    idle, so it costs nothing until a combo fires."""

    def __init__(self, on_error=None):
        self.on_error = on_error
        self._q = queue.Queue()
        threading.Thread(target=self._loop, daemon=True, name='combo-actions').start()

    def run(self, action):
        self._q.put(('action', action))

    def replay(self, button):
        self._q.put(('click', button))

    def _loop(self):
        while True:
            kind, arg = self._q.get()
            try:
                if kind == 'click':
                    click(arg)
                else:
                    perform(arg)
            except Exception as e:
                if self.on_error:
                    self.on_error(arg, e)


# ------------------------------------------------------------------------------------- which app is in front
_exe_cache = {}


def process_exe(pid):
    name = _exe_cache.get(pid)
    if name is None:
        name = ''
        h = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf, size = C.create_unicode_buffer(520), W.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, C.byref(size)):
                name = os.path.basename(buf.value).lower()
            kernel32.CloseHandle(h)
        if len(_exe_cache) > 256:
            _exe_cache.clear()
        _exe_cache[pid] = name
    return name


def foreground_exe():
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), C.byref(pid))
    return process_exe(pid.value) if pid.value else ''


def window_apps():
    """Exe names of apps with a visible top-level window, for the "pause in these apps" picker."""
    names, own = set(), os.getpid()

    def visit(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) and not user32.GetWindow(hwnd, 4)
                and not user32.GetWindowLongW(hwnd, -20) & 0x80):     # no owner, not WS_EX_TOOLWINDOW
            pid = W.DWORD()
            user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
            if pid.value != own:
                name = process_exe(pid.value)
                if name and name not in ('explorer.exe', 'applicationframehost.exe', 'textinputhost.exe'):
                    names.add(name)
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return sorted(names)


class Combos:
    """Engine + hook + action runner, wired together. The hook is installed by whoever owns a message loop."""

    def __init__(self, on_record=None, on_error=None):
        self.runner = Runner(on_error)
        self.engine = ComboEngine(self.runner.run, self.runner.replay, on_record, foreground_exe)
        self.hook = MouseHook(self.engine)

    def configure(self, cfg):
        self.engine.configure(cfg)
