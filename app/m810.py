"""Settings model for the Redragon M810 Taipan Pro, recovered from OemDrv.exe 3.1.

The mouse keeps its settings in an EEPROM that the host reads (cmd 0x08) and writes (cmd 0x07) 10 bytes at a
time. Almost every field is stored with a check byte so the firmware can reject torn writes:
    pair   [v, 0x55 - v]
    quad   [a, b, c, 0x55 - a - b - c]
    block  [... n bytes ..., 0x55 - sum(n bytes)]

EEPROM map (addresses in hex):
    00-01  polling rate code (1 = 1000 Hz, 2 = 500, 4 = 250, 8 = 125)        pair
    02-03  number of active DPI stages                                      pair
    04-05  current DPI stage, 0-based (changed by the mouse's DPI buttons)  pair
    0C-2B  8 DPI stages: [x code, y code, x2 flag]                          quad
    2C-4B  8 DPI stage colours: [r, g, b]                                   quad
    4C-51  DPI indicator LED: mode, speed, brightness                       3 pairs
    54-5D  logo LED (not fitted on the M810; left untouched)
    60-9F  16 button slots: [type, a, b]                                    quad
    A0-A6  main LED: [mode, r, g, b, speed, brightness]                     block
    100+   16 x 0x20: keystroke / media data for button slot i              block
    300+   16 x 0x180: macro for button slot i
Unknown bytes are kept as read and written back unchanged.
"""
import copy
import time

from hidio import DeviceError

POLLING = {1: 1000, 2: 500, 4: 250, 8: 125}
POLLING_CODE = {v: k for k, v in POLLING.items()}

# PMW3325 resolution codes; a stage with the x2 flag set reads as double (up to 10000 DPI).
DPI_VALUES = list(range(200, 5001, 100))
DPI_CODES = [0x04, 0x06, 0x08, 0x0B, 0x0D, 0x0F, 0x12, 0x14, 0x16, 0x19, 0x1B, 0x1D, 0x20, 0x22, 0x24, 0x27,
             0x29, 0x2B, 0x2E, 0x30, 0x32, 0x34, 0x37, 0x39, 0x3B, 0x3E, 0x40, 0x42, 0x45, 0x47, 0x49, 0x4C,
             0x4E, 0x50, 0x53, 0x55, 0x57, 0x5A, 0x5C, 0x5E, 0x61, 0x63, 0x65, 0x68, 0x6A, 0x6C, 0x6F, 0x71,
             0x73]
DPI_MIN, DPI_MAX = 200, 10000
MAX_STAGES = 5

# Main LED (0xA0). The original UI's 1..10 sliders index these tables.
MAIN_MODES = {'streaming': 0, 'breathing': 1, 'steady': 2, 'off': 4}
MAIN_MODE_NAMES = {v: k for k, v in MAIN_MODES.items()}
BREATH_SPEED = [0xFF, 0xE6, 0xD2, 0xBE, 0xAA, 0x96, 0x82, 0x6E, 0x46, 0x28]
STREAM_SPEED = [0x2D, 0x28, 0x23, 0x1E, 0x19, 0x13, 0x0F, 0x0A, 0x05, 0x03]
MAIN_BRIGHT = [0x19, 0x32, 0x4B, 0x64, 0x7D, 0x96, 0xAF, 0xC8, 0xE1, 0xFF]

# DPI indicator LED (0x4C)
DPI_LED_MODES = {'off': 0, 'steady': 1, 'breathing': 2}
DPI_LED_MODE_NAMES = {v: k for k, v in DPI_LED_MODES.items()}
DPI_LED_SPEED = [0x01, 0x1E, 0x3C, 0x5A, 0x80, 0x96, 0xB4, 0xD2, 0xE6, 0xFF]

SLOTS = 16
KEYDATA_BASE, KEYDATA_SIZE = 0x100, 0x20
MACRO_BASE, MACRO_SIZE = 0x300, 0x180
MACRO_HEADER = 0x20
MACRO_MAX_EVENTS = (MACRO_SIZE - 4 - MACRO_HEADER) // 5
EEPROM_END = MACRO_BASE + SLOTS * MACRO_SIZE          # 0x1B00

# The eight buttons the original app shows, as (slot index 0-15, name, factory action).
BUTTONS = [
    (0, 'Left click', {'kind': 'mouse', 'button': 'left'}),
    (1, 'Right click', {'kind': 'mouse', 'button': 'right'}),
    (2, 'Wheel click', {'kind': 'mouse', 'button': 'middle'}),
    (4, 'Side front', {'kind': 'mouse', 'button': 'forward'}),
    (3, 'Side rear', {'kind': 'mouse', 'button': 'back'}),
    (10, 'DPI up', {'kind': 'dpi', 'op': 'up'}),
    (11, 'DPI down', {'kind': 'dpi', 'op': 'down'}),
    (8, 'Fire button', {'kind': 'fire', 'interval': 58, 'times': 3}),
]
# Two more slots the factory profile fills but the original UI hides.
HIDDEN_BUTTONS = [
    (9, 'Bottom button', {'kind': 'polling'}),
    (6, 'Slot 7', {'kind': 'disabled'}),
]

MOUSE_BITS = {'left': 0x01, 'right': 0x02, 'middle': 0x04, 'back': 0x08, 'forward': 0x10}
MOUSE_NAMES = {v: k for k, v in MOUSE_BITS.items()}
DPI_OPS = {'cycle': 1, 'up': 2, 'down': 3}
DPI_OP_NAMES = {v: k for k, v in DPI_OPS.items()}
SIMPLE_KINDS = {7: 'polling', 8: 'rgb', 9: 'profile'}
SIMPLE_CODES = {v: k for k, v in SIMPLE_KINDS.items()}

MOD_BITS = {'ctrl': 0x01, 'shift': 0x02, 'alt': 0x04, 'win': 0x08}
MACRO_MOD_BITS = {'ctrl': 0x01, 'shift': 0x02, 'alt': 0x04, 'win': 0x08,
                  'rctrl': 0x10, 'rshift': 0x20, 'ralt': 0x40, 'rwin': 0x80}
MACRO_MOD_NAMES = {v: k for k, v in MACRO_MOD_BITS.items()}
MEDIA = {  # consumer-page usages the original offers
    'play_pause': 0xCD, 'next': 0xB5, 'previous': 0xB6, 'stop': 0xB7, 'mute': 0xE2, 'volume_up': 0xE9,
    'volume_down': 0xEA, 'media_player': 0x183, 'mail': 0x18A, 'calculator': 0x192, 'my_computer': 0x194,
    'web_search': 0x221, 'web_home': 0x223, 'web_back': 0x224, 'web_forward': 0x225, 'web_stop': 0x226,
    'web_refresh': 0x227, 'web_favorites': 0x22A,
}
MEDIA_NAMES = {v: k for k, v in MEDIA.items()}


def chk(*vals):
    return (0x55 - sum(vals)) & 0xFF


def _ok_pair(b, i):
    return (b[i] + b[i + 1]) & 0xFF == 0x55


def _ok_quad(b, i):
    return sum(b[i:i + 4]) & 0xFF == 0x55


# ------------------------------------------------------------------------------------------------ DPI
def dpi_encode(x, y):
    """(x code, y code, x2 flag). Both axes share the flag, so above 5000 both use 200-DPI steps."""
    hi = x > 5000 or y > 5000
    codes = []
    for v in (x, y):
        base = v / 2 if hi else v
        base = min(DPI_VALUES, key=lambda d: abs(d - base))
        codes.append(DPI_CODES[DPI_VALUES.index(base)])
    return codes[0], codes[1], 1 if hi else 0


def dpi_decode(code, hi):
    if code not in DPI_CODES:
        return None
    v = DPI_VALUES[DPI_CODES.index(code)]
    return v * 2 if hi else v


def dpi_snap(v):
    """Nearest value the sensor can store."""
    v = max(DPI_MIN, min(DPI_MAX, int(v)))
    if v > 5000:
        return int(round(v / 200.0)) * 200
    return int(round(v / 100.0)) * 100


def dpi_actual(x, y):
    """What the mouse will really use for a stage (the shared x2 flag can move the smaller axis)."""
    cx, cy, hi = dpi_encode(dpi_snap(x), dpi_snap(y))
    return dpi_decode(cx, hi), dpi_decode(cy, hi)


# ------------------------------------------------------------------------------------------------ buttons
def action_to_code(a):
    """Button action dict -> (type, a, b). 'keys'/'media' (type 5) also need key data; macros (6) a macro."""
    k = a.get('kind')
    if k == 'disabled':
        return 0, 0, 0
    if k == 'mouse':
        return 1, MOUSE_BITS[a['button']], 0
    if k == 'dpi':
        return 2, DPI_OPS[a['op']], 0
    if k == 'double':
        return 4, 0x32, 2
    if k == 'triple':
        return 4, 0x32, 3
    if k == 'fire':
        return 4, max(3, min(255, int(a.get('interval', 58)))), max(0, min(3, int(a.get('times', 3))))
    if k in ('keys', 'media'):
        return 5, 0, 0
    if k == 'macro':
        rep = a.get('repeat', 1)
        if rep == 'hold':        # repeat while the button is held
            b = 0xFF
        elif rep == 'toggle':    # repeat until any key is pressed
            b = 0xFE
        else:
            b = max(1, min(0xFD, int(rep)))
        return 6, int(a['slot']), b
    if k in SIMPLE_CODES:
        return SIMPLE_CODES[k], 0, 0
    if k == 'raw':
        return int(a['type']) & 0xFF, int(a['a']) & 0xFF, int(a['b']) & 0xFF
    raise ValueError(f'unknown action {a!r}')


def code_to_action(t, a, b, keydata=None):
    if t == 0:
        return {'kind': 'disabled'}
    if t == 1 and a in MOUSE_NAMES:
        return {'kind': 'mouse', 'button': MOUSE_NAMES[a]}
    if t == 2 and a in DPI_OP_NAMES:
        return {'kind': 'dpi', 'op': DPI_OP_NAMES[a]}
    if t == 4:
        if a == 0x32 and b == 3:
            return {'kind': 'triple'}
        if a == 0x32 and b == 2:
            return {'kind': 'double'}
        return {'kind': 'fire', 'interval': a, 'times': b}
    if t == 5:
        return decode_keydata(keydata) if keydata else {'kind': 'keys', 'mods': [], 'keys': []}
    if t == 6:
        rep = {0xFF: 'hold', 0xFE: 'toggle'}.get(b, b)
        return {'kind': 'macro', 'slot': a, 'repeat': rep}
    if t in SIMPLE_KINDS:
        return {'kind': SIMPLE_KINDS[t]}
    return {'kind': 'raw', 'type': t, 'a': a, 'b': b}


def encode_keydata(a):
    """Keystroke/media action -> bytes for the 0x20 block (count, 3-byte events, checksum)."""
    if a['kind'] == 'media':
        u = MEDIA[a['key']] if isinstance(a['key'], str) else int(a['key'])
        body = [2, 0x82, u & 0xFF, u >> 8, 0x42, u & 0xFF, u >> 8]
    else:
        mods = [MOD_BITS[m] for m in ('ctrl', 'shift', 'alt', 'win') if m in a.get('mods', [])]
        keys = [int(k) for k in a.get('keys', [])][:3]
        ev = [[0x80, m, 0] for m in mods] + [[0x81, k, 0] for k in keys]
        ev += [[0x40, m, 0] for m in mods] + [[0x41, k, 0] for k in reversed(keys)]
        body = [len(ev)] + [x for e in ev for x in e]
    if len(body) + 1 > KEYDATA_SIZE:
        raise ValueError('key combination too long')
    return bytes(body + [chk(*body)])


def decode_keydata(buf):
    n = buf[0]
    if n == 0 or 1 + 3 * n > len(buf):
        return {'kind': 'keys', 'mods': [], 'keys': []}
    if buf[1] & 0x0F == 2:
        u = buf[2] | buf[3] << 8
        return {'kind': 'media', 'key': MEDIA_NAMES.get(u, u)}
    mods, keys = [], []
    for i in range(n):
        f, c = buf[1 + 3 * i], buf[2 + 3 * i]
        if f & 0xC0 == 0x40:
            break
        if f & 0x0F == 0:
            mods += [m for m, bit in MOD_BITS.items() if c & bit and m not in mods]
        elif c not in keys:
            keys.append(c)
    return {'kind': 'keys', 'mods': mods, 'keys': keys}


# ------------------------------------------------------------------------------------------------ macros
def encode_macro(name, events):
    """events: [{'type': 'key'|'mod'|'mouse', 'code': int|str, 'down': bool, 'delay': ms}] -> slot bytes.
    The checksum covers the count and events only: the original computes it before copying the name in."""
    events = events[:MACRO_MAX_EVENTS]
    buf = bytearray(MACRO_HEADER)
    buf[0x1F] = len(events)
    for e in events:
        if e['type'] == 'mod':
            flags, code = 0, MACRO_MOD_BITS[e['code']] if isinstance(e['code'], str) else e['code']
        elif e['type'] == 'mouse':
            flags, code = 4, MOUSE_BITS[e['code']] if isinstance(e['code'], str) else e['code']
        else:
            flags, code = 1, int(e['code'])
        d = max(3, min(0xFFFF, int(e.get('delay', 10))))
        buf += bytes([flags | (0x80 if e['down'] else 0x40), code & 0xFF, 0, d >> 8, d & 0xFF])
    buf.append(chk(*buf))
    buf += b'\0\0\0'
    raw = name.encode('utf-16-le')[:30]
    buf[0] = len(raw)
    buf[1:1 + len(raw)] = raw
    return bytes(buf)


def decode_macro(buf):
    n = buf[0]
    name = bytes(buf[1:1 + min(n, 30)]).decode('utf-16-le', 'replace').rstrip('\0') if 0 < n <= 30 else ''
    count = buf[0x1F]
    if count > MACRO_MAX_EVENTS or MACRO_HEADER + 5 * count > len(buf):
        return {'name': name, 'events': [], 'valid': False}
    events = []
    for i in range(count):
        f, c, _, dh, dl = buf[MACRO_HEADER + 5 * i:MACRO_HEADER + 5 * i + 5]
        t = f & 7
        if t == 0:
            e = {'type': 'mod', 'code': MACRO_MOD_NAMES.get(c, c)}
        elif t == 4:
            e = {'type': 'mouse', 'code': MOUSE_NAMES.get(c, c)}
        else:
            e = {'type': 'key', 'code': c}
        e['down'] = f >> 6 == 2
        e['delay'] = dh << 8 | dl
        events.append(e)
    end = MACRO_HEADER + 5 * count
    valid = end < len(buf) and buf[end] == chk(*buf[0x1F:end])
    return {'name': name, 'events': events, 'valid': valid}


# ------------------------------------------------------------------------------------------------ profile
def factory_profile():
    p = {
        'polling': 1000,
        'stage_count': 5,
        'stage': 0,
        'stages': [{'x': d, 'y': d, 'color': c} for d, c in zip(
            (500, 1000, 2000, 3000, 10000),
            ('#ff0000', '#0000ff', '#00ff00', '#ffff00', '#ff00ff'))],
        'dpi_led': {'mode': 'steady', 'speed': 5, 'brightness': 255},
        'main_led': {'mode': 'streaming', 'color': '#ff0000', 'speed': 5, 'brightness': 10},
        'buttons': [{'kind': 'disabled'} for _ in range(SLOTS)],
        'macros': [None] * SLOTS,
    }
    for slot, _, act in BUTTONS + HIDDEN_BUTTONS:
        p['buttons'][slot] = copy.deepcopy(act)
    return p


def _hex(r, g, b):
    return f'#{r:02x}{g:02x}{b:02x}'


def _rgb(h):
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _index(table, v):
    return table.index(v) + 1 if v in table else None


def decode_profile(img):
    """Full EEPROM image (bytearray, at least 0x300 bytes, macros optional) -> profile dict + warnings."""
    warn = []
    p = factory_profile()
    p['polling'] = POLLING.get(img[0], 1000) if _ok_pair(img, 0) else (warn.append('polling') or 1000)
    if _ok_pair(img, 2) and 1 <= img[2] <= 8:
        p['stage_count'] = img[2]
    else:
        warn.append('stage count')
    p['stage'] = img[4] if _ok_pair(img, 4) and img[4] < p['stage_count'] else 0
    stages = []
    for i in range(MAX_STAGES):
        a, c = 0x0C + 4 * i, 0x2C + 4 * i
        x, y = dpi_decode(img[a], img[a + 2]), dpi_decode(img[a + 1], img[a + 2])
        if not _ok_quad(img, a) or x is None or y is None:
            warn.append(f'DPI stage {i + 1}')
            x = y = p['stages'][i]['x'] if i < len(p['stages']) else 800
        color = _hex(*img[c:c + 3]) if _ok_quad(img, c) else p['stages'][i]['color']
        stages.append({'x': x, 'y': y, 'color': color})
    p['stages'] = stages
    if all(_ok_pair(img, 0x4C + k) for k in (0, 2, 4)):
        p['dpi_led'] = {'mode': DPI_LED_MODE_NAMES.get(img[0x4C], 'steady'),
                        'speed': _index(DPI_LED_SPEED, img[0x4E]) or 5, 'brightness': img[0x50]}
    elif img[0x4C] == 0 and img[0x4D] == 0x55:
        p['dpi_led']['mode'] = 'off'
    else:
        warn.append('DPI LED')
    m = img[0xA0:0xA7]
    if m[6] == chk(*m[:6]):
        mode = MAIN_MODE_NAMES.get(m[0], 'steady')
        table = STREAM_SPEED if m[0] == 0 else BREATH_SPEED
        p['main_led'] = {'mode': mode, 'color': _hex(*m[1:4]), 'speed': _index(table, m[4]) or 5,
                         'brightness': _index(MAIN_BRIGHT, m[5]) or 10}
    else:
        warn.append('main LED')
    for s in range(SLOTS):
        a = 0x60 + 4 * s
        if not _ok_quad(img, a):
            warn.append(f'button slot {s + 1}')
            continue
        t, x, y = img[a:a + 3]
        kd = img[KEYDATA_BASE + KEYDATA_SIZE * s:KEYDATA_BASE + KEYDATA_SIZE * (s + 1)]
        p['buttons'][s] = code_to_action(t, x, y, kd if t == 5 else None)
        if t == 6 and len(img) >= MACRO_BASE + MACRO_SIZE * (s + 1):
            p['macros'][s] = decode_macro(img[MACRO_BASE + MACRO_SIZE * s:MACRO_BASE + MACRO_SIZE * (s + 1)])
    return p, warn


def encode_sections(p):
    """Profile -> {name: [(addr, bytes), ...]} in the order the original app writes them."""
    s = {}
    s['polling'] = [(0x00, bytes([POLLING_CODE[int(p['polling'])], chk(POLLING_CODE[int(p['polling'])])]))]
    n = max(1, min(MAX_STAGES, int(p['stage_count'])))
    dpi, col = bytearray(), bytearray()
    for st in p['stages'][:MAX_STAGES]:
        x, y, hi = dpi_encode(dpi_snap(st['x']), dpi_snap(st['y']))
        dpi += bytes([x, y, hi, chk(x, y, hi)])
        r, g, b = _rgb(st['color'])
        col += bytes([r, g, b, chk(r, g, b)])
    s['dpi'] = [(0x0C, bytes(dpi)), (0x02, bytes([n, chk(n)]))]
    s['dpi_color'] = [(0x2C, bytes(col))]
    d = p['dpi_led']
    if d['mode'] == 'off':
        s['dpi_led'] = [(0x4C, bytes([0, 0x55, 0, 0, 0, 0]))]
    else:
        mo, sp, br = DPI_LED_MODES[d['mode']], DPI_LED_SPEED[int(d['speed']) - 1], int(d['brightness']) & 0xFF
        s['dpi_led'] = [(0x4C, bytes([mo, chk(mo), sp, chk(sp), br, chk(br)]))]
    m = p['main_led']
    r, g, b = _rgb(m['color'])
    mo = MAIN_MODES[m['mode']]
    sp = (STREAM_SPEED if mo == 0 else BREATH_SPEED)[int(m['speed']) - 1]
    br = MAIN_BRIGHT[int(m['brightness']) - 1]
    s['main_led'] = [(0xA0, bytes([mo, r, g, b, sp, br, chk(mo, r, g, b, sp, br)]))]
    keys = []
    matrix = bytearray()
    for slot, a in enumerate(p['buttons']):
        t, x, y = action_to_code(dict(a, slot=slot) if a.get('kind') == 'macro' else a)
        matrix += bytes([t, x, y, chk(t, x, y)])
        if t == 5:
            keys.append((KEYDATA_BASE + KEYDATA_SIZE * slot, encode_keydata(a)))
        elif t == 6:
            mac = (p.get('macros') or [None] * SLOTS)[slot] or {'name': '', 'events': []}
            keys.append((MACRO_BASE + MACRO_SIZE * slot, encode_macro(mac.get('name', ''), mac.get('events', []))))
    s['buttons'] = keys + [(0x60, bytes(matrix))]
    return s


def diff_sections(old, new):
    """Names of sections whose bytes differ (old may be None = everything)."""
    if old is None:
        return list(new)
    return [k for k in new if new[k] != old.get(k)]


# ------------------------------------------------------------------------------------------------ device
class M810:
    """High-level operations over an open hidio.Mouse."""

    def __init__(self, mouse):
        self.m = mouse

    def hello(self):
        """Tell the receiver a driver is attached; returns True when the mouse itself is linked and awake."""
        self.m.send(0x02, data=b'\x01')
        return self.online()

    def bye(self):
        try:
            self.m.send(0x02, data=b'\x00', retries=1)
        except DeviceError:
            pass

    def online(self):
        r = self.m.send(0x03)
        return r[4] >= 1 and r[5] == 1

    def probe06(self):
        """Undocumented: the receiver answers cmd 0x06 with 2 bytes that change with the mouse's state
        (01 17 while awake, 02 00 asleep; maybe battery). The original app never sends it."""
        r = self.m.send(0x06, retries=1)
        return bytes(r[5:5 + min(r[4], 10)])

    def read(self, addr, n, progress=None):
        out = bytearray()
        while len(out) < n:
            k = min(10, n - len(out))
            r = self.m.send(0x08, addr + len(out), length=k, timeout=0.6)
            if r[1] != 0:
                raise DeviceError(f'The mouse refused to read 0x{addr + len(out):04X}.')
            out += r[5:5 + k]
            if progress:
                progress(len(out), n)
        return out

    def write(self, addr, data):
        for i in range(0, len(data), 10):
            chunk = data[i:i + 10]
            for _ in range(3):              # a non-zero status is retried, as the original does
                r = self.m.send(0x07, addr + i, chunk, timeout=1.0)
                if r[1] == 0:
                    break
                time.sleep(0.05)
            else:
                raise DeviceError(f'The mouse refused to write 0x{addr + i:04X}.')
            time.sleep(0.01)

    def write_verified(self, addr, data, tries=3):
        """Write, read it back, and write again any 10-byte piece the mouse did not keep: over the 2.4G link a
        write can be acknowledged and still not reach the EEPROM."""
        self.write(addr, data)
        for _ in range(tries):
            got = self.read(addr, len(data))
            bad = [i for i in range(0, len(data), 10) if got[i:i + 10] != data[i:i + 10]]
            if not bad:
                return
            for i in bad:
                self.write(addr + i, data[i:i + 10])
        raise DeviceError(f'The mouse did not keep the settings at 0x{addr:04X}. Move it closer to the receiver '
                          'and save again.')

    def read_settings(self, progress=None):
        """Read 0x000-0x2FF plus the macros the buttons use. Returns the image (macro area padded)."""
        img = bytearray(EEPROM_END)
        img[0:0xA0] = self.read(0x00, 0xA0, progress and (lambda a, b: progress(a, 0x2A0)))
        img[0xA0:0xA7] = self.read(0xA0, 7)
        for s in range(SLOTS):
            a = KEYDATA_BASE + KEYDATA_SIZE * s
            img[a:a + KEYDATA_SIZE] = self.read(a, KEYDATA_SIZE)
            if progress:
                progress(0xA0 + KEYDATA_SIZE * (s + 1), 0x2A0)
        for s in range(SLOTS):
            if img[0x60 + 4 * s] == 6:
                a = MACRO_BASE + MACRO_SIZE * s
                img[a:a + MACRO_SIZE] = self.read(a, MACRO_SIZE)
        return img

    def backup(self, progress=None):
        """The whole 0x0000-0x1AFF EEPROM."""
        return self.read(0, EEPROM_END, progress)

    # Writes take effect as they land; nothing needs committing. Never send cmd 0x09 after a save: it puts the
    # mouse back to its built-in defaults (the original sends it only for its 0x100 apply flag). Seen on a real
    # M810 on 2026-10-27: a verified write read back fine until 0x09, then the defaults were back.

    def apply(self, sections, names=None, progress=None):
        jobs = [(a, d) for name in (names or list(sections)) for a, d in sections[name]]
        total, done = sum(len(d) for _, d in jobs), 0
        for addr, data in jobs:
            self.write_verified(addr, data)
            done += len(data)
            if progress:
                progress(done, total)

    def restore(self, image, progress=None):
        image = bytes(image[:EEPROM_END])
        for i in range(0, len(image), 0x100):
            self.write_verified(i, image[i:i + 0x100])
            if progress:
                progress(min(i + 0x100, len(image)), len(image))

    def ping(self):
        """The original polls cmd 0x04 every 30 s while open."""
        self.m.send(0x04, retries=1)
