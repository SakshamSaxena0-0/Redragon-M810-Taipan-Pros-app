"""Offline tests for the settings encoder/decoder (no mouse needed): python tests/test_m810.py"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import m810 as M  # noqa: E402


def image_from(profile):
    img = bytearray(M.EEPROM_END)
    for parts in M.encode_sections(profile).values():
        for addr, data in parts:
            img[addr:addr + len(data)] = data
    img[4], img[5] = profile['stage'], M.chk(profile['stage'])
    return img


def test_checksums():
    assert M.chk(0x08, 0x08, 0x00, 0x00, 0x00, 0x0A) == 0x3B          # matches the packet OemDrv sends
    s = M.encode_sections(M.factory_profile())
    assert s['polling'] == [(0, bytes([1, 0x54]))]                     # same bytes as OemDrv's 1000 Hz write
    for name, parts in s.items():
        for addr, data in parts:
            if 0x0C <= addr < 0x4C or 0x60 <= addr < 0xA0:
                for i in range(0, len(data), 4):
                    assert sum(data[i:i + 4]) & 0xFF == 0x55, (name, hex(addr), i)


def test_dpi_table():
    assert M.dpi_encode(800, 800) == (0x12, 0x12, 0)
    assert M.dpi_encode(10000, 10000) == (0x73, 0x73, 1)
    assert M.dpi_decode(0x73, 1) == 10000
    assert M.dpi_actual(1600, 7200) == (1600, 7200)
    assert M.dpi_actual(300, 7200) == (400, 7200)                      # shared x2 flag pushes 300 up to 400
    assert M.dpi_snap(5150) == 5200 and M.dpi_snap(10) == 200
    for v in M.DPI_VALUES:
        assert M.dpi_decode(*M.dpi_encode(v, v)[::2]) == v


def test_roundtrip():
    p = M.factory_profile()
    p['polling'] = 500
    p['stage_count'] = 4
    p['stage'] = 2
    p['stages'][1] = {'x': 1600, 'y': 1200, 'color': '#12ab34'}
    p['dpi_led'] = {'mode': 'breathing', 'speed': 7, 'brightness': 200}
    p['main_led'] = {'mode': 'breathing', 'color': '#00e5ff', 'speed': 3, 'brightness': 6}
    p['buttons'][3] = {'kind': 'keys', 'mods': ['ctrl', 'shift'], 'keys': [0x06]}        # Ctrl+Shift+C
    p['buttons'][4] = {'kind': 'media', 'key': 'volume_up'}
    p['buttons'][8] = {'kind': 'macro', 'slot': 8, 'repeat': 'hold'}
    p['macros'][8] = {'name': 'Spam E', 'events': [
        {'type': 'key', 'code': 0x08, 'down': True, 'delay': 25},
        {'type': 'key', 'code': 0x08, 'down': False, 'delay': 40},
        {'type': 'mouse', 'code': 'left', 'down': True, 'delay': 5},
        {'type': 'mouse', 'code': 'left', 'down': False, 'delay': 5}]}
    p['buttons'][10] = {'kind': 'triple'}
    p['buttons'][11] = {'kind': 'fire', 'interval': 40, 'times': 2}
    img = image_from(p)
    q, warn = M.decode_profile(img)
    assert warn == [], warn
    assert q['polling'] == 500 and q['stage_count'] == 4 and q['stage'] == 2
    assert q['stages'][1] == p['stages'][1]
    assert q['dpi_led'] == p['dpi_led'] and q['main_led'] == p['main_led']
    assert q['buttons'][3] == p['buttons'][3]
    assert q['buttons'][4] == p['buttons'][4]
    assert q['buttons'][8] == p['buttons'][8]
    assert q['macros'][8]['valid'] and q['macros'][8]['name'] == 'Spam E'
    assert [dict(e) for e in q['macros'][8]['events']] == p['macros'][8]['events']
    assert q['buttons'][10] == {'kind': 'triple'}
    assert q['buttons'][11] == {'kind': 'fire', 'interval': 40, 'times': 2}
    # decode(encode(decode(x))) is stable
    assert image_from(q) == img


def test_off_states():
    p = M.factory_profile()
    p['dpi_led']['mode'] = 'off'
    p['main_led']['mode'] = 'off'
    q, warn = M.decode_profile(image_from(p))
    assert q['dpi_led']['mode'] == 'off' and q['main_led']['mode'] == 'off', (q['dpi_led'], q['main_led'])


def test_diff():
    a = M.encode_sections(M.factory_profile())
    p = M.factory_profile()
    p['main_led']['color'] = '#ffffff'
    assert M.diff_sections(a, M.encode_sections(p)) == ['main_led']
    p2 = copy.deepcopy(p)
    p2['buttons'][0] = {'kind': 'mouse', 'button': 'right'}
    assert M.diff_sections(M.encode_sections(p), M.encode_sections(p2)) == ['buttons']


def test_garbage_image():
    q, warn = M.decode_profile(bytearray(b'\xff' * M.EEPROM_END))
    assert warn and q['polling'] == 1000


def test_stale_reply_is_skipped():
    """A late reply to an earlier read must not be taken as the answer to the next one."""
    import queue
    import threading
    import hidio

    class FakeHid:
        def HidD_SetFeature(self, h, raw, n):
            sent = bytes(raw)
            for addr in ((sent[3] << 8 | sent[4]) - 10 & 0xFFFF, sent[3] << 8 | sent[4]):   # stale first, then the real one
                r = bytearray(17)
                r[0], r[1], r[3], r[4], r[5] = 0x09, sent[1], addr >> 8, addr & 0xFF, sent[5]
                r[6] = addr & 0xFF
                mouse._replies.put(bytes(r))
            return True

    mouse = object.__new__(hidio.Mouse)
    mouse._replies, mouse._lock, mouse._cmd = queue.Queue(), threading.Lock(), None
    real, hidio._hid = hidio._hid, FakeHid()
    try:
        r = mouse.send(0x08, 0x1200, length=10)
        assert (r[2] << 8 | r[3], r[5]) == (0x1200, 0x00), r.hex()
        assert mouse.send(0x03)[0] == 0x03                   # status commands still match on cmd alone
    finally:
        hidio._hid = real


class LossyMouse:
    """Acknowledges every write but drops the first `drop` writes to chosen addresses."""

    def __init__(self, drop):
        self.eeprom, self.drop, self.writes = bytearray(M.EEPROM_END), dict(drop), 0

    def send(self, cmd, addr=0, data=b'', length=None, status=0, timeout=1.0, retries=3):
        n = len(data) if length is None else length
        out = bytearray(15)
        out[0], out[2], out[3], out[4] = cmd, addr >> 8, addr & 0xFF, n
        if cmd == 0x08:
            out[5:5 + n] = self.eeprom[addr:addr + n]
        elif cmd == 0x07:
            self.writes += 1
            if self.drop.get(addr, 0) > 0:
                self.drop[addr] -= 1
            else:
                self.eeprom[addr:addr + n] = data[:n]
        return bytes(out)


def test_dropped_write_is_written_again():
    M.time.sleep, real = (lambda s: None), M.time.sleep
    try:
        mac = M.encode_macro('sush jump', [{'type': 'key', 'code': 6, 'down': True, 'delay': 3}] * 4)
        lossy = LossyMouse({0x120A: 1, 0x1214: 2})        # two packets lost, one of them twice
        M.M810(lossy).write_verified(0x1200, mac)
        assert lossy.eeprom[0x1200:0x1200 + len(mac)] == mac
        dead = LossyMouse({0x120A: 99})                   # never kept: give up with a clear error
        try:
            M.M810(dead).write_verified(0x1200, mac)
            raise AssertionError('expected a DeviceError')
        except M.DeviceError as e:
            assert '0x1200' in str(e)
    finally:
        M.time.sleep = real


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f()
            print('ok', n)
