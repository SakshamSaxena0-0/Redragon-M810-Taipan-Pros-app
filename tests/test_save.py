"""Save path against the demo mouse, including a mouse that changes under the app: python tests/test_save.py"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ['TAIPAN_MOCK'] = '1'
os.environ['TAIPAN_DATA'] = tempfile.mkdtemp(prefix='taipan-test-')
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
import hidio  # noqa: E402
import m810 as M  # noqa: E402
import taipan  # noqa: E402

SUSH = {'name': 'sush jump', 'events': [{'type': 'key', 'code': 6, 'down': True, 'delay': 3},
                                        {'type': 'key', 'code': 44, 'down': True, 'delay': 3},
                                        {'type': 'key', 'code': 6, 'down': False, 'delay': 3},
                                        {'type': 'key', 'code': 44, 'down': False, 'delay': 3}]}


def device():
    pushed = []
    d = taipan.Device(lambda kind, data: pushed.append(kind))
    for _ in range(200):
        if d.profile:
            return d, pushed
        time.sleep(0.02)
    raise AssertionError('demo mouse never read')


def with_macro(profile):
    p = dict(profile, buttons=list(profile['buttons']), macros=list(profile['macros']))
    p['buttons'][10] = {'kind': 'macro', 'slot': 10, 'repeat': 1}
    p['macros'][10] = SUSH
    return p


def test_save_writes_and_backs_up():
    d, _ = device()
    r = d.save(with_macro(d.profile))
    assert r['written'] == ['buttons'], r
    assert M.decode_macro(d.mouse.eeprom[0x1200:0x1380])['name'] == 'sush jump'
    assert os.listdir(os.path.join(taipan.DATA_DIR, 'backups'))
    assert d.save(with_macro(d.profile))['written'] == []      # nothing left to write
    d.stop()


def test_reset_during_save_is_reported_and_fixed_by_saving_again():
    d, pushed = device()
    fake, real_send = d.mouse, d.mouse.send

    reads_of_0 = []

    def resetting_send(cmd, addr=0, *a, **k):                 # after the verified writes, before the final check,
        if cmd == 0x08 and addr == 0:                          # the mouse falls back to DPI up on its own
            reads_of_0.append(1)
            if len(reads_of_0) == 2:
                fake.eeprom[0x88:0x8C] = bytes([2, 2, 0, M.chk(2, 2, 0)])
        return real_send(cmd, addr, *a, **k)
    fake.send = resetting_send
    want = with_macro(d.profile)
    try:
        d.save(want)
        raise AssertionError('expected a DeviceError')
    except hidio.DeviceError as e:
        assert 'buttons and macros' in str(e), e
    assert d.profile['buttons'][10] == {'kind': 'dpi', 'op': 'up'}         # the app now knows what the mouse has
    assert 'profile' in pushed
    log = open(os.path.join(taipan.DATA_DIR, 'save-log.txt'), encoding='utf-8').read()
    assert 'buttons 0x0060' in log and 'got' in log, log
    fake.send = real_send
    assert d.save(want)['written'] == ['buttons']                          # saving again puts it back
    assert d.profile['buttons'][10]['kind'] == 'macro'
    d.stop()


def test_save_never_sends_0x09():
    """0x09 puts the real mouse back to its defaults; a save must never send it."""
    d, _ = device()
    sent, real_send = [], d.mouse.send
    d.mouse.send = lambda cmd, *a, **k: sent.append(cmd) or real_send(cmd, *a, **k)
    d.save(with_macro(d.profile))
    d.restore(bytes(d.mouse.eeprom))
    assert sent and 0x09 not in sent, sorted(set(sent))
    d.stop()


def test_change_made_on_the_mouse_is_written_back():
    d, _ = device()
    p = dict(d.profile, main_led=dict(d.profile['main_led'], color='#00ff00'))
    d.mouse.eeprom[0:2] = bytes([8, M.chk(8)])     # the bottom button switched to 125 Hz behind the app's back
    r = d.save(p)
    assert sorted(r['written']) == ['main_led', 'polling'], r
    assert d.mouse.eeprom[0] == 1 and d.profile['polling'] == 1000
    d.stop()


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f()
            print('ok', n)
