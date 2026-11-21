import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import combos

VOL_UP = {'type': 'media', 'key': 'volume_up', 'step': 1}
VOL_DOWN = {'type': 'media', 'key': 'volume_down', 'step': 1}
LAUNCH = {'type': 'launch', 'target': 'notepad.exe'}


def engine(cfg=None, fg='chrome.exe'):
    log = {'run': [], 'replay': [], 'rec': []}
    e = combos.ComboEngine(log['run'].append, log['replay'].append, lambda s, q: log['rec'].append((s, q)),
                           lambda: fg)
    e.configure(cfg or {'enabled': True, 'exclude': ['game.exe'], 'list': combos.DEFAULT['list'] + [
        {'starter': 'forward', 'seq': ['back', 'right', 'left'], 'action': LAUNCH}]})
    return e, log


def click(e, name, t):
    return e.button(name, True, t), e.button(name, False, t + 30)


def test_tap_replays():
    e, log = engine()
    assert click(e, 'forward', 100) == (True, True)
    assert log['replay'] == ['forward'] and log['run'] == []


def test_hold_and_scroll():
    e, log = engine()
    assert e.button('forward', True, 0)
    assert e.wheel(120, 50) and e.wheel(120, 90)
    assert e.wheel(-120, 130)
    assert e.button('forward', False, 200)
    assert log['run'] == [VOL_UP, VOL_UP, VOL_DOWN]
    assert log['replay'] == []


def test_smooth_wheel_adds_up():
    e, log = engine()
    e.button('forward', True, 0)
    for t in range(4):
        e.wheel(30, t)
    e.wheel(-240, 10)
    e.button('forward', False, 20)
    assert log['run'] == [VOL_UP, VOL_DOWN, VOL_DOWN]


def test_other_buttons_untouched():
    e, log = engine()
    assert click(e, 'left', 0) == (False, False)
    assert click(e, 'back', 100) == (False, False)
    assert e.wheel(120, 200) is False
    assert log == {'run': [], 'replay': [], 'rec': []}


def test_click_sequence():
    e, log = engine()
    e.button('forward', True, 0)
    for i, b in enumerate(['back', 'right', 'left']):
        assert click(e, b, 100 + i * 100) == (True, True)
    e.button('forward', False, 500)
    assert log['run'] == [LAUNCH] and log['replay'] == []


def test_complete_sequence_runs_at_once():
    orbit = {'type': 'launch', 'target': 'Orbit.exe'}
    e, log = engine({'enabled': True, 'list': [{'starter': 'forward', 'seq': ['middle'], 'action': orbit}]})
    e.button('forward', True, 0)
    assert e.button('middle', True, 100) is True
    assert log['run'] == [orbit]
    assert e.button('middle', False, 130) is True
    click(e, 'middle', 200)
    assert e.button('forward', False, 400) is True
    assert log['run'] == [orbit, orbit] and log['replay'] == []


def test_prefix_waits_for_release():
    a, b = {'type': 'lock'}, LAUNCH
    e, log = engine({'enabled': True, 'list': [{'starter': 'back', 'seq': ['right'], 'action': a},
                                               {'starter': 'back', 'seq': ['right', 'left'], 'action': b}]})
    e.button('back', True, 0)
    click(e, 'right', 100)
    assert log['run'] == []
    e.button('back', False, 300)
    assert log['run'] == [a]
    e.button('back', True, 400)
    click(e, 'right', 500)
    click(e, 'left', 600)
    assert log['run'] == [a, b]
    e.button('back', False, 800)
    assert log['run'] == [a, b] and log['replay'] == []


def test_unknown_sequence_does_nothing():
    e, log = engine()
    e.button('forward', True, 0)
    click(e, 'right', 100)
    e.button('forward', False, 300)
    assert log['run'] == [] and log['replay'] == []


def test_drag_release_passes():
    e, log = engine()
    assert e.button('left', True, 0) is False
    e.button('forward', True, 100)
    assert e.button('left', False, 200) is False
    e.button('forward', False, 300)
    assert log['replay'] == ['forward']


def test_excluded_app():
    e, log = engine(fg='game.exe')
    assert click(e, 'forward', 0) == (False, False)
    assert e.wheel(120, 100) is False
    assert log == {'run': [], 'replay': [], 'rec': []}


def test_lost_release():
    e, log = engine()
    e.button('forward', True, 0)
    assert e.button('left', True, combos.STALE_MS + 1000) is False
    assert e.button('left', False, combos.STALE_MS + 1050) is False
    e, log = engine()
    e.button('forward', True, 0xFFFFFF00)
    assert e.wheel(120, 0x40) and log['run'] == [VOL_UP]
    e.button('forward', True, 0x80)
    e.button('forward', False, 0x100)
    assert log['replay'] == ['forward']


def test_recording():
    e, log = engine({'enabled': False})
    assert click(e, 'forward', 0) == (False, False)
    e.recording = True
    e.button('back', True, 100)
    e.wheel(-120, 150)
    e.button('back', False, 200)
    e.button('forward', True, 300)
    click(e, 'right', 350)
    click(e, 'left', 450)
    e.button('forward', False, 600)
    assert log['rec'] == [('back', ['wheel_down']), ('forward', ['right', 'left'])]
    assert log['run'] == []


SUSH = {'name': 'sush jump', 'events': [{'type': 'key', 'code': 6, 'down': True, 'delay': 3},
                                        {'type': 'key', 'code': 44, 'down': True, 'delay': 3},
                                        {'type': 'key', 'code': 6, 'down': False, 'delay': 3},
                                        {'type': 'key', 'code': 44, 'down': False, 'delay': 3}]}
B6 = {'kind': 'macro', 'slot': 10, 'repeat': 1}
B7 = {'kind': 'keys', 'mods': ['alt'], 'keys': [43]}
VK_C, VK_SPACE, VK_ALT, VK_TAB, VK_CTRL, VK_SHIFT = 0x43, 0x20, 0xA4, 0x09, 0xA2, 0xA0
SUSH_KEYS = [(VK_C, True), (VK_SPACE, True), (VK_C, False), (VK_SPACE, False)]
ALT_TAB = [(VK_ALT, True), (VK_TAB, True), (VK_TAB, False), (VK_ALT, False)]
LOCK = {'type': 'lock'}


def keyed_engine(b6=(B6, SUSH), b7=(B7,)):
    e, log = engine({'enabled': True, 'list': combos.DEFAULT['list'] + [
        {'starter': 'forward', 'seq': ['b6'], 'action': LAUNCH}, {'starter': 'forward', 'seq': ['b7'], 'action': LOCK}]})
    e.set_button_keys({'b6': combos.button_keys(*b6), 'b7': combos.button_keys(*b7)})
    return e, log


def keys(e, events, t):
    return [e.key(vk, down, t + 3 * i) for i, (vk, down) in enumerate(events)]


def test_button_keys():
    assert combos.button_keys(B6, SUSH) == {'trigger': VK_C, 'keys': [VK_SPACE, VK_C], 'mods': [], 'per': 1, 'gap': 303}
    assert combos.button_keys(B7) == {'trigger': VK_TAB, 'keys': [VK_TAB, VK_ALT], 'mods': [VK_ALT], 'per': 1,
                                      'gap': combos.BURST_MS}
    assert combos.button_keys({'kind': 'media', 'key': 'volume_up'})['trigger'] == 0xAF
    assert combos.button_keys({'kind': 'keys', 'mods': [], 'keys': [10]})['trigger'] == 0x47
    assert combos.button_keys({'kind': 'keys', 'mods': ['ctrl'], 'keys': []})['trigger'] == VK_CTRL
    assert combos.button_keys(dict(B6, repeat='hold'), SUSH)['per'] == 0
    assert combos.button_keys(dict(B6, repeat=3), SUSH)['per'] == 3
    for a in ({'kind': 'dpi', 'op': 'up'}, {'kind': 'mouse', 'button': 'left'}, {'kind': 'disabled'},
              {'kind': 'fire', 'interval': 40, 'times': 3}, {'kind': 'polling'}, None):
        assert combos.button_keys(a) is None, a
    clicks = {'name': 'x', 'events': [{'type': 'mouse', 'code': 'left', 'down': True, 'delay': 5}]}
    assert combos.button_keys(B6, clicks) is None


def test_button6_and_7_while_held():
    e, log = keyed_engine()
    assert e.wants_keys
    e.button('forward', True, 0)
    assert keys(e, SUSH_KEYS, 100) == [True] * 4
    assert log['run'] == [LAUNCH]
    assert keys(e, ALT_TAB, 200) == [True] * 4
    assert log['run'] == [LAUNCH, LOCK]
    assert keys(e, SUSH_KEYS, 300) == [True] * 4
    assert e.button('forward', False, 400) is True
    assert log['run'] == [LAUNCH, LOCK, LAUNCH] and log['replay'] == []


def test_button6_and_7_alone_work_as_usual():
    e, log = keyed_engine()
    assert keys(e, SUSH_KEYS, 0) == [False] * 4
    assert keys(e, ALT_TAB, 100) == [False] * 4
    click(e, 'forward', 200)
    assert keys(e, SUSH_KEYS, 2000) == [False] * 4
    assert log['run'] == [] and log['replay'] == ['forward']


def test_button_keys_outlast_the_starter():
    e, log = keyed_engine()
    e.button('forward', True, 0)
    assert e.key(VK_C, True, 100)
    e.button('forward', False, 101)
    assert keys(e, SUSH_KEYS[1:], 102) == [True] * 3
    assert keys(e, [(VK_SPACE, True), (VK_SPACE, False)], 1500) == [False, False]
    assert log['run'] == [LAUNCH] and log['replay'] == []


def test_held_key_combo_and_auto_repeat():
    e, log = keyed_engine()
    e.button('forward', True, 0)
    assert keys(e, [(VK_ALT, True), (VK_TAB, True)], 100) == [True, True]
    assert keys(e, [(VK_TAB, True)] * 5 + [(VK_ALT, True)] * 3, 700) == [True] * 8
    e.button('forward', False, 900)
    assert keys(e, [(VK_TAB, False), (VK_ALT, False)], 1000) == [True, True]
    assert log['run'] == [LOCK]


def test_keyboard_keys_held_before_the_starter_keep_working():
    e, log = keyed_engine()
    assert e.key(VK_C, True, 0) is False
    assert e.key(VK_ALT, True, 10) is False
    e.button('forward', True, 100)
    assert keys(e, [(VK_C, True), (VK_ALT, True)] * 3, 600) == [False] * 6
    assert keys(e, [(VK_C, False), (VK_ALT, False)], 700) == [False, False]
    e.button('forward', False, 800)
    assert log['run'] == []


def test_same_key_told_apart_by_modifier():
    e, log = keyed_engine(b6=({'kind': 'keys', 'mods': ['ctrl'], 'keys': [6]},),
                          b7=({'kind': 'keys', 'mods': ['shift'], 'keys': [6]},))
    e.button('forward', True, 0)
    keys(e, [(VK_SHIFT, True), (VK_C, True), (VK_C, False), (VK_SHIFT, False)], 100)
    keys(e, [(VK_CTRL, True), (VK_C, True), (VK_C, False), (VK_CTRL, False)], 200)
    e.button('forward', False, 300)
    assert log['run'] == [LOCK, LAUNCH]


def test_keyed_buttons_need_keys_and_a_shortcut():
    e, log = keyed_engine(b6=({'kind': 'dpi', 'op': 'up'},), b7=({'kind': 'dpi', 'op': 'down'},))
    assert not e.wants_keys
    e, log = engine()
    e.set_button_keys({'b6': combos.button_keys(B6, SUSH)})
    assert not e.wants_keys
    e.recording = True
    assert e.wants_keys


def test_recording_button6():
    e, log = keyed_engine()
    e.recording = True
    e.button('forward', True, 0)
    keys(e, SUSH_KEYS, 100)
    e.button('forward', False, 300)
    assert log['rec'] == [('forward', ['b6'])] and log['run'] == []


def test_actions_are_valid():
    for c in combos.DEFAULT['list']:
        assert c['action']['key'] in combos.MEDIA_VK
    assert combos.C.sizeof(combos.INPUT) == (40 if combos.C.sizeof(combos.C.c_void_p) == 8 else 28)


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f()
            print('ok', n)
