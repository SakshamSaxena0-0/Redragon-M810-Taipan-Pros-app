import ctypes as C
import ctypes.wintypes as W
import os
import platform
import queue
import struct
import threading

STARTERS = ('forward', 'back', 'middle')
BUTTONS = ('left', 'right', 'middle', 'back', 'forward')
KEYED = {'b6': 10, 'b7': 11}
WHEEL = ('wheel_up', 'wheel_down')
NOTCH = 120
STALE_MS = 8000
BURST_MS = 300
MAX_SEQ = 6
OWN_TAG = 0x7A1B0000
TEST_TAG = 0x7A1B0001
TEST = os.environ.get('TAIPAN_HOOK_TEST') == '1'

DEFAULT = {'enabled': True, 'exclude': [], 'list': [
    {'id': 'vol-up', 'starter': 'forward', 'seq': ['wheel_up'], 'action': {'type': 'media', 'key': 'volume_up', 'step': 1}},
    {'id': 'vol-down', 'starter': 'forward', 'seq': ['wheel_down'],
     'action': {'type': 'media', 'key': 'volume_down', 'step': 1}},
]}


class ComboEngine:
    def __init__(self, run, replay, recorded=None, foreground=None):
        self.run = run
        self.replay = replay
        self.recorded = recorded
        self.foreground = foreground or (lambda: '')
        self.recording = False
        self._keys = {}
        self._mods = frozenset()
        self._passed = set()
        self.configure({})
        self.reset()

    def configure(self, cfg):
        table = {}
        if cfg.get('enabled'):
            for c in cfg.get('list', []):
                seq = tuple(c.get('seq') or ())
                if c.get('starter') in STARTERS and seq and c.get('action'):
                    table.setdefault((c['starter'], seq), c['action'])
        self._table = table
        self._prefixes = frozenset((s, seq[:i]) for s, seq in table for i in range(1, len(seq)))
        self._starters = frozenset(s for s, _ in table)
        self._keyed = frozenset(t for _, seq in table for t in seq if t in KEYED)
        self._exclude = frozenset(x.lower() for x in cfg.get('exclude', []))

    def set_button_keys(self, sigs):
        keys, mods = {}, set()
        for token in KEYED:
            s = (sigs or {}).get(token)
            if s:
                keys.setdefault(s['trigger'], []).append((token, frozenset(s['keys']), frozenset(s['mods']),
                                                          s['per'], s['gap']))
                mods |= set(s['mods'])
        self._keys, self._mods = keys, frozenset(mods)

    @property
    def active(self):
        return bool(self._starters) or self.recording

    @property
    def wants_keys(self):
        return bool(self._keys) and (bool(self._keyed) or self.recording)

    @property
    def armed(self):
        return self._held is not None

    def reset(self):
        self._held = None
        self._seq = []
        self._used = False
        self._acc = 0
        self._kept = set()
        self._kept_keys = set()
        self._burst = None
        self._last = 0

    def _stale(self, t):
        if self._held is not None and (t - self._last) & 0xFFFFFFFF > STALE_MS:
            self.reset()

    def button(self, name, down, t):
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
            if name == self._held:
                self._seq, self._used, self._acc = [], False, 0
                return True
            self._kept.add(name)
            self._click(name)
            return True
        if name == self._held:
            self._finish()
            return True
        if name in self._kept:
            self._kept.discard(name)
            return True
        return False

    def _click(self, name):
        if len(self._seq) < MAX_SEQ:
            self._seq.append(name)
        self._used = True
        key = (self._held, tuple(self._seq))
        if not self.recording and key in self._table and key not in self._prefixes:
            self._seq = []
            self.run(self._table[key])

    def key(self, vk, down, t):
        if not down:
            self._passed.discard(vk)
            if vk in self._kept_keys:
                self._kept_keys.discard(vk)
                return True
            return False
        if vk in self._kept_keys:
            return True
        if vk in self._passed:
            return False
        if self._key_down(vk, t):
            return True
        if len(self._passed) > 32:
            self._passed.clear()
        self._passed.add(vk)
        return False

    def _key_down(self, vk, t):
        b = self._burst
        if b and (t - b['last']) & 0xFFFFFFFF <= b['gap'] and vk in b['keys'] and (vk != b['trigger'] or b['left']):
            if vk == b['trigger'] and b['left'] > 0:
                b['left'] -= 1
            b['last'] = t
            self._kept_keys.add(vk)
            return True
        self._stale(t)
        if self._held is None:
            return False
        cands = self._keys.get(vk)
        if cands:
            held = self._kept_keys & self._mods
            token, keys, _, per, gap = max(cands, key=lambda c: (c[2] <= held, len(c[2] & held)))
            self._burst = {'trigger': vk, 'keys': keys, 'left': per - 1 if per > 0 else -1, 'gap': gap, 'last': t}
            self._kept_keys.add(vk)
            self._last = t
            self._click(token)
            return True
        if vk in self._mods:
            self._kept_keys.add(vk)
            self._last = t
            return True
        return False

    def wheel(self, delta, t):
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
    if C.sizeof(C.c_void_p) != 8 or platform.machine().upper() not in ('AMD64', 'X86_64'):
        return None
    cnh = kernel32.GetProcAddress(user32._handle, b'CallNextHookEx')
    code = (b'\x48\x81\xFA' + struct.pack('<I', WM_MOUSEMOVE) +
            b'\x75\x16'
            b'\x4D\x89\xC1'
            b'\x49\x89\xD0'
            b'\x89\xCA'
            b'\x31\xC9'
            b'\x48\xB8' + struct.pack('<Q', cnh) +
            b'\xFF\xE0'
            b'\x48\xB8' + struct.pack('<Q', python_proc) +
            b'\xFF\xE0')
    mem = kernel32.VirtualAlloc(None, 4096, 0x3000, 0x04)
    if not cnh or not mem:
        return None
    C.memmove(mem, code, len(code))
    old = W.DWORD()
    if not kernel32.VirtualProtect(mem, 4096, 0x20, C.byref(old)):
        return None
    kernel32.FlushInstructionCache(kernel32.GetCurrentProcess(), mem, len(code))
    return mem


class MouseHook:
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


WH_KEYBOARD_LL = 13
LLKHF_INJECTED, LLKHF_UP = 0x10, 0x80


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [('vk', W.DWORD), ('scan', W.DWORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]


class KeyHook:
    def __init__(self, engine):
        self.engine = engine
        self.handle = None
        self._proc = HOOKPROC(self._on_event)

    def install(self):
        self.uninstall()
        self.handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        return bool(self.handle)

    def uninstall(self):
        if self.handle:
            user32.UnhookWindowsHookEx(self.handle)
            self.handle = None

    def _on_event(self, code, msg, lparam):
        if code == 0:
            k = KBDLLHOOKSTRUCT.from_address(lparam)
            if not k.flags & LLKHF_INJECTED or (TEST and k.extra == TEST_TAG):
                try:
                    keep = self.engine.key(k.vk, not k.flags & LLKHF_UP, k.time)
                except Exception:
                    keep = False
                if keep:
                    return 1
        return user32.CallNextHookEx(None, code, msg, lparam)


_LAYOUT_SCAN = dict(zip(range(4, 57), [
    0x1E, 0x30, 0x2E, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17, 0x24, 0x25, 0x26, 0x32, 0x31, 0x18, 0x19, 0x10, 0x13,
    0x1F, 0x14, 0x16, 0x2F, 0x11, 0x2D, 0x15, 0x2C,
    0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,
    0x1C, 0x01, 0x0E, 0x0F, 0x39, 0x0C, 0x0D, 0x1A, 0x1B, 0x2B, 0x2B, 0x27, 0x28, 0x29, 0x33, 0x34, 0x35]))
_LAYOUT_SCAN[100] = 0x56
_FIXED_VK = {57: 0x14, 70: 0x2C, 71: 0x91, 72: 0x13, 73: 0x2D, 74: 0x24, 75: 0x21, 76: 0x2E, 77: 0x23, 78: 0x22,
             79: 0x27, 80: 0x25, 81: 0x28, 82: 0x26, 83: 0x90, 84: 0x6F, 85: 0x6A, 86: 0x6D, 87: 0x6B, 88: 0x0D,
             98: 0x60, 99: 0x6E, 101: 0x5D,
             **{u: 0x70 + u - 58 for u in range(58, 70)},
             **{u: 0x61 + u - 89 for u in range(89, 98)},
             **{u: 0x7C + u - 104 for u in range(104, 116)}}
_MOD_VK = {'ctrl': 0xA2, 'shift': 0xA0, 'alt': 0xA4, 'win': 0x5B, 'rctrl': 0xA3, 'rshift': 0xA1, 'ralt': 0xA5,
           'rwin': 0x5C}
_MOD_BITS = {'ctrl': 0x01, 'shift': 0x02, 'alt': 0x04, 'win': 0x08, 'rctrl': 0x10, 'rshift': 0x20, 'ralt': 0x40,
             'rwin': 0x80}


def usage_vk(u):
    if u in _LAYOUT_SCAN:
        vk = user32.MapVirtualKeyW(_LAYOUT_SCAN[u], 1)
        return vk or None
    return _FIXED_VK.get(u)


def _mod_vk(code):
    if isinstance(code, str):
        return _MOD_VK.get(code)
    names = [n for n, bit in _MOD_BITS.items() if code & bit]
    return _MOD_VK[names[0]] if names else None


def button_keys(action, macro=None):
    kind = (action or {}).get('kind')
    downs, delays, per = [], [0], 1
    if kind == 'keys':
        downs = [_MOD_VK[m] for m in ('ctrl', 'shift', 'alt', 'win') if m in action.get('mods', [])]
        downs += [usage_vk(int(u)) for u in action.get('keys', [])]
    elif kind == 'media':
        downs = [MEDIA_KEY_VK.get(action.get('key'))]
    elif kind == 'macro':
        events = (macro or {}).get('events') or []
        for e in events:
            if e.get('down') and e.get('type') in ('key', 'mod'):
                downs.append(usage_vk(int(e['code'])) if e['type'] == 'key' else _mod_vk(e['code']))
        delays = [int(e.get('delay', 0)) for e in events] or [0]
        rep = action.get('repeat', 1)
        per = int(rep) if isinstance(rep, int) or str(rep).isdigit() else 0
    if not downs or None in downs:
        return None
    mods = [v for v in downs if v in _MOD_VK.values()]
    trigger = next((v for v in downs if v not in mods), downs[0])
    return {'trigger': trigger, 'keys': sorted(set(downs)), 'mods': sorted(set(mods)) if trigger not in mods else [],
            'per': per * downs.count(trigger), 'gap': BURST_MS + max(delays)}


CLICK_FLAGS = {'left': (0x02, 0x04), 'right': (0x08, 0x10), 'middle': (0x20, 0x40), 'back': (0x80, 0x100),
               'forward': (0x80, 0x100)}
MEDIA_VK = {'volume_up': 0xAF, 'volume_down': 0xAE, 'mute': 0xAD, 'next': 0xB0, 'previous': 0xB1, 'stop': 0xB2,
            'play_pause': 0xB3}
MEDIA_KEY_VK = {**MEDIA_VK, 'mail': 0xB4, 'media_player': 0xB5, 'my_computer': 0xB6, 'calculator': 0xB7,
                'web_back': 0xA6, 'web_forward': 0xA7, 'web_refresh': 0xA8, 'web_stop': 0xA9, 'web_search': 0xAA,
                'web_favorites': 0xAB, 'web_home': 0xAC}
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


_exe_cache = {}


def process_exe(pid):
    name = _exe_cache.get(pid)
    if name is None:
        name = ''
        h = kernel32.OpenProcess(0x1000, False, pid)
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
    names, own = set(), os.getpid()

    def visit(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) and not user32.GetWindow(hwnd, 4)
                and not user32.GetWindowLongW(hwnd, -20) & 0x80):
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
    def __init__(self, on_record=None, on_error=None):
        self.runner = Runner(on_error)
        self.engine = ComboEngine(self.runner.run, self.runner.replay, on_record, foreground_exe)
        self.hook = MouseHook(self.engine)
        self.keys = KeyHook(self.engine)

    def configure(self, cfg):
        self.engine.configure(cfg)

    def set_button_keys(self, sigs):
        self.engine.set_button_keys(sigs)
