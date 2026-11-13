"""Offline tests for the combo state machine (no hook, no input is sent): python tests/test_combos.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import combos  # noqa: E402

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
    assert click(e, 'forward', 100) == (True, True)      # kept, then sent again as a plain click
    assert log['replay'] == ['forward'] and log['run'] == []


def test_hold_and_scroll():
    e, log = engine()
    assert e.button('forward', True, 0)
    assert e.wheel(120, 50) and e.wheel(120, 90)
    assert e.wheel(-120, 130)
    assert e.button('forward', False, 200)
    assert log['run'] == [VOL_UP, VOL_UP, VOL_DOWN]
    assert log['replay'] == []                           # the scroll used the button, so no Forward click


def test_smooth_wheel_adds_up():
    e, log = engine()
    e.button('forward', True, 0)
    for t in range(4):
        e.wheel(30, t)                                   # four quarter notches = one notch
    e.wheel(-240, 10)
    e.button('forward', False, 20)
    assert log['run'] == [VOL_UP, VOL_DOWN, VOL_DOWN]


def test_other_buttons_untouched():
    e, log = engine()
    assert click(e, 'left', 0) == (False, False)
    assert click(e, 'back', 100) == (False, False)       # Back starts no combo here, so it stays Back
    assert e.wheel(120, 200) is False                    # plain scrolling is never touched
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
    assert log['run'] == [orbit]                         # on the middle press, not when MB5 is let go
    assert e.button('middle', False, 130) is True        # its release is still kept from apps
    click(e, 'middle', 200)                              # again while still held: runs again
    assert e.button('forward', False, 400) is True
    assert log['run'] == [orbit, orbit] and log['replay'] == []


def test_prefix_waits_for_release():
    a, b = {'type': 'lock'}, LAUNCH
    e, log = engine({'enabled': True, 'list': [{'starter': 'back', 'seq': ['right'], 'action': a},
                                               {'starter': 'back', 'seq': ['right', 'left'], 'action': b}]})
    e.button('back', True, 0)
    click(e, 'right', 100)
    assert log['run'] == []                              # Right alone could still become Right, Left
    e.button('back', False, 300)
    assert log['run'] == [a]
    e.button('back', True, 400)
    click(e, 'right', 500)
    click(e, 'left', 600)
    assert log['run'] == [a, b]                          # nothing longer starts with Right, Left: at once
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
    assert e.button('left', True, 0) is False            # a drag started before the starter
    e.button('forward', True, 100)
    assert e.button('left', False, 200) is False         # its release must still reach the app
    e.button('forward', False, 300)
    assert log['replay'] == ['forward']


def test_excluded_app():
    e, log = engine(fg='game.exe')
    assert click(e, 'forward', 0) == (False, False)
    assert e.wheel(120, 100) is False
    assert log == {'run': [], 'replay': [], 'rec': []}


def test_lost_release():
    e, log = engine()
    e.button('forward', True, 0)                         # its release went to the secure desktop
    assert e.button('left', True, combos.STALE_MS + 1000) is False   # clicks work again afterwards
    assert e.button('left', False, combos.STALE_MS + 1050) is False
    e, log = engine()
    e.button('forward', True, 0xFFFFFF00)                # tick counter wraps around
    assert e.wheel(120, 0x40) and log['run'] == [VOL_UP]
    e.button('forward', True, 0x80)                      # pressed again: start over
    e.button('forward', False, 0x100)
    assert log['replay'] == ['forward']


def test_recording():
    e, log = engine({'enabled': False})
    assert click(e, 'forward', 0) == (False, False)      # combos off: nothing is touched
    e.recording = True
    e.button('back', True, 100)                          # any starter works while recording
    e.wheel(-120, 150)
    e.button('back', False, 200)
    e.button('forward', True, 300)
    click(e, 'right', 350)
    click(e, 'left', 450)
    e.button('forward', False, 600)
    assert log['rec'] == [('back', ['wheel_down']), ('forward', ['right', 'left'])]
    assert log['run'] == []


def test_actions_are_valid():
    for c in combos.DEFAULT['list']:
        assert c['action']['key'] in combos.MEDIA_VK
    assert combos.C.sizeof(combos.INPUT) == (40 if combos.C.sizeof(combos.C.c_void_p) == 8 else 28)


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f()
            print('ok', n)
